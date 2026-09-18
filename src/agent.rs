use std::sync::Arc;

use anyhow::{anyhow, Result};
use tokio::sync::{mpsc, oneshot, Mutex};

use crate::event::{ApprovalDecision, Event, QuestionAnswer, QuestionItem};
use crate::llm::LlmClient;
use crate::message::Message;
use crate::tools;

/// Máximo de steps por turno para evitar loops infinitos.
const MAX_STEPS: usize = 25;

/// Handle compartilhado para resolver aprovações/questions pendentes.
/// Usado pela UI para responder sem precisar de `&mut Agent` (evita conflito
/// de borrowing com o task do turno).
#[derive(Clone)]
pub struct Resolver {
    approvals: Arc<Mutex<Vec<(String, oneshot::Sender<ApprovalDecision>)>>>,
    questions: Arc<Mutex<Vec<(String, oneshot::Sender<QuestionAnswer>)>>>,
}

impl Resolver {
    pub fn new() -> Self {
        Resolver {
            approvals: Arc::new(Mutex::new(Vec::new())),
            questions: Arc::new(Mutex::new(Vec::new())),
        }
    }

    pub async fn resolve_approval(&self, request_id: &str, decision: ApprovalDecision) {
        let mut slots = self.approvals.lock().await;
        if let Some(idx) = slots.iter().position(|(id, _)| id == request_id) {
            let (_, tx) = slots.remove(idx);
            let _ = tx.send(decision);
        }
    }

    pub async fn resolve_question(&self, request_id: &str, answer: QuestionAnswer) {
        let mut slots = self.questions.lock().await;
        if let Some(idx) = slots.iter().position(|(id, _)| id == request_id) {
            let (_, tx) = slots.remove(idx);
            let _ = tx.send(answer);
        }
    }
}

/// O agente: mantém o histórico de mensagens e executa o loop de steps,
/// emitindo eventos para a UI via um canal.
pub struct Agent {
    llm: Arc<LlmClient>,
    system_prompt: String,
    history: Vec<Message>,
    /// Canal para enviar eventos à UI.
    events: mpsc::Sender<Event>,
    /// Handle para resolver aprovações/questions (compartilhado com a UI).
    resolver: Resolver,
    /// Auto-aprova todas as tools.
    yolo: bool,
}

impl Agent {
    pub fn new(llm: Arc<LlmClient>, system_prompt: impl Into<String>) -> Self {
        let (events, _) = mpsc::channel(256);
        Agent {
            llm,
            system_prompt: system_prompt.into(),
            history: Vec::new(),
            events,
            resolver: Resolver::new(),
            yolo: false,
        }
    }

    pub fn with_events(
        llm: Arc<LlmClient>,
        system_prompt: impl Into<String>,
    ) -> (Self, mpsc::Receiver<Event>, Resolver) {
        let (events, rx) = mpsc::channel(256);
        let resolver = Resolver::new();
        let agent = Agent {
            llm,
            system_prompt: system_prompt.into(),
            history: Vec::new(),
            events,
            resolver: resolver.clone(),
            yolo: false,
        };
        (agent, rx, resolver)
    }

    pub fn set_yolo(&mut self, yolo: bool) {
        self.yolo = yolo;
    }

    pub fn resolver(&self) -> Resolver {
        self.resolver.clone()
    }

    /// Envia um evento ao canal (ignora erro se UI desconectou).
    fn emit(&self, ev: Event) {
        let _ = self.events.try_send(ev);
    }

    /// Executa um turno completo do agente com o input do usuário.
    pub async fn run(&mut self, user_input: &str) -> Result<String> {
        self.emit(Event::TurnBegin {
            user_input: user_input.to_string(),
        });
        self.history.push(Message::user(user_input));

        let tools = tools::tool_defs();

        for step in 1..=MAX_STEPS {
            self.emit(Event::StepBegin { n: step });

            let response = self
                .llm
                .chat_stream(&self.system_prompt, &tools, &self.history, &mut |delta: &str| {
                    self.emit(Event::TextDelta {
                        text: delta.to_string(),
                    });
                })
                .await?;

            self.history.push(Message::assistant(
                response.content.clone(),
                if response.tool_calls.is_empty() {
                    None
                } else {
                    Some(response.tool_calls.clone())
                },
            ));

            if response.tool_calls.is_empty() {
                let text = response.content.unwrap_or_default();
                self.emit(Event::TurnEnd);
                return Ok(text);
            }

            // Executa cada tool call, pedindo aprovação quando necessário.
            for tc in &response.tool_calls {
                let name = tc.function.name.clone();
                let args = tc.function.arguments.clone();
                self.emit(Event::ToolCallBegin {
                    tool_call_id: tc.id.clone(),
                    name: name.clone(),
                    arguments: args.clone(),
                });

                // Pedido de aprovação (funcional).
                let approved = if self.yolo {
                    true
                } else {
                    self.request_approval(tc, &name, &args).await
                };

                if !approved {
                    let content = "Ação rejeitada pelo usuário.".to_string();
                    self.emit(Event::ToolResult {
                        tool_call_id: tc.id.clone(),
                        name: name.clone(),
                        is_error: true,
                        output: content.clone(),
                    });
                    self.history.push(Message::tool(tc.id.clone(), content));
                    continue;
                }

                let (out, is_err) = match tools::run_tool(&name, &args).await {
                    Ok((out, is_err)) => (out, is_err),
                    Err(e) => (format!("Erro ao executar {}: {}", name, e), true),
                };
                let content = if is_err {
                    format!("[erro] {}", out)
                } else {
                    out
                };
                self.emit(Event::ToolResult {
                    tool_call_id: tc.id.clone(),
                    name: name.clone(),
                    is_error: is_err,
                    output: content.clone(),
                });
                self.history.push(Message::tool(tc.id.clone(), content));
            }
        }

        Err(anyhow!("Número máximo de steps ({}) atingido.", MAX_STEPS))
    }

    /// Pede aprovação ao usuário via UI (funcional) e aguarda a decisão.
    async fn request_approval(
        &self,
        tc: &crate::message::ToolCall,
        name: &str,
        args: &str,
    ) -> bool {
        let id = format!("approval-{}", tc.id);
        let (tx, rx) = oneshot::channel();
        {
            let mut slots = self.resolver.approvals.lock().await;
            slots.push((id.clone(), tx));
        }

        self.emit(Event::ApprovalRequest {
            id: id.clone(),
            tool_call_id: tc.id.clone(),
            sender: name.to_string(),
            action: format!("{} {}", name, args),
            description: format!("Chamar a ferramenta `{}` com argumentos: {}", name, args),
        });

        match rx.await {
            Ok(ApprovalDecision::Approve) | Ok(ApprovalDecision::ApproveForSession) => true,
            _ => false,
        }
    }

    /// Pede uma pergunta estruturada ao usuário via UI (funcional).
    #[allow(dead_code)]
    pub async fn ask_question(&self, questions: Vec<QuestionItem>) -> Result<QuestionAnswer> {
        let id = format!("question-{}", uuid_fallback());
        let (tx, rx) = oneshot::channel();
        {
            let mut slots = self.resolver.questions.lock().await;
            slots.push((id.clone(), tx));
        }

        self.emit(Event::QuestionRequest {
            id: id.clone(),
            tool_call_id: String::new(),
            questions,
        });

        rx.await.map_err(|_| anyhow!("Question não respondida"))
    }
}

fn uuid_fallback() -> u64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}
