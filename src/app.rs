use std::sync::Arc;

use tokio::sync::{mpsc, Mutex};

use crate::agent::{Agent, Resolver};
use crate::event::Event;
use crate::llm::LlmClient;
use crate::ui::blocks::{ContentBlock, NotificationBlock, NotificationSeverity, ToolCallBlock, ToolCallState};
use crate::ui::panels::{ApprovalPanelState, PlanPanelState, QuestionPanelState};
use crate::ui::prompt::PromptState;
use crate::ui::toolbar::ToolbarState;

/// Estado do aplicativo, consumido pela UI.
#[derive(Debug)]
pub struct AppState {
    pub content: ContentBlock,
    pub tool_blocks: Vec<ToolCallBlock>,
    pub notifications: Vec<NotificationBlock>,
    pub approval: Option<ApprovalPanelState>,
    pub question: Option<QuestionPanelState>,
    pub plan: Option<PlanPanelState>,
    pub prompt: PromptState,
    pub toolbar: ToolbarState,
    pub running: bool,
    /// Exibe o banner de boas-vindas até o primeiro turno.
    pub show_banner: bool,
}

impl AppState {
    pub fn new(model: &str) -> Self {
        AppState {
            content: ContentBlock::new(),
            tool_blocks: Vec::new(),
            notifications: Vec::new(),
            approval: None,
            question: None,
            plan: None,
            prompt: PromptState {
                agent_mode: true,
                ..Default::default()
            },
            toolbar: ToolbarState::new(model),
            running: true,
            show_banner: true,
        }
    }
}

/// O núcleo do app: conecta eventos do agente ao estado da UI e processa input.
pub struct App {
    pub state: AppState,
    /// Agente compartilhado entre a UI e o task do turno.
    agent: Arc<Mutex<Agent>>,
    /// Resolver para aprovações/questions (clonado, usado pela UI).
    resolver: Resolver,
    events: mpsc::Receiver<Event>,
    /// Fila de inputs do usuário (para enfileirar turnos).
    pub input_queue: Vec<String>,
    /// Inputs steerados (injetados durante o turno atual).
    pub steer_queue: Vec<String>,
}

impl App {
    pub fn new(llm: Arc<LlmClient>, model: &str, system_prompt: &str) -> Self {
        let (agent, events, resolver) = Agent::with_events(llm, system_prompt);
        App {
            state: AppState::new(model),
            agent: Arc::new(Mutex::new(agent)),
            resolver,
            events,
            input_queue: Vec::new(),
            steer_queue: Vec::new(),
        }
    }

    /// Processa os eventos pendentes do agente, atualizando o estado da UI.
    pub fn poll_events(&mut self) {
        while let Ok(ev) = self.events.try_recv() {
            self.apply_event(ev);
        }
    }

    fn apply_event(&mut self, ev: Event) {
        match ev {
            Event::TurnBegin { user_input } => {
                self.state.content = ContentBlock::new();
                self.state.tool_blocks.clear();
                self.state.show_banner = false;
                self.state.notifications.push(NotificationBlock {
                    severity: NotificationSeverity::Info,
                    text: format!("✨ {}", user_input),
                });
            }
            Event::TurnEnd => {
                self.state.content.composing = false;
                self.state.content.thinking = String::new();
            }
            Event::StepBegin { n } => {
                self.state.notifications.push(NotificationBlock {
                    severity: NotificationSeverity::Info,
                    text: format!("[step {}]", n),
                });
            }
            Event::TextDelta { text } => {
                self.state.content.composing = true;
                self.state.content.buffer.push_str(&text);
            }
            Event::ThinkDelta { think } => {
                self.state.content.thinking = think;
            }
            Event::ThinkDone { elapsed_s, tokens } => {
                self.state.content.thinking_elapsed_s = elapsed_s;
                self.state.content.thinking_tokens = tokens;
                self.state.notifications.push(NotificationBlock {
                    severity: NotificationSeverity::Info,
                    text: format!("Thought for {:.1}s · {} tokens", elapsed_s, tokens),
                });
            }
            Event::ToolCallBegin {
                tool_call_id,
                name,
                arguments,
            } => {
                self.state.content.composing = false;
                self.state.tool_blocks.push(ToolCallBlock::new(
                    tool_call_id,
                    name,
                    arguments,
                ));
            }
            Event::ToolResult {
                tool_call_id,
                name: _,
                is_error,
                output,
            } => {
                if let Some(block) = self
                    .state
                    .tool_blocks
                    .iter_mut()
                    .find(|b| b.tool_call_id == tool_call_id)
                {
                    block.state = ToolCallState::Done { is_error };
                }
                if is_error {
                    self.state.notifications.push(NotificationBlock {
                        severity: NotificationSeverity::Error,
                        text: output,
                    });
                }
            }
            Event::ApprovalRequest {
                id,
                tool_call_id: _,
                sender,
                action,
                description,
            } => {
                self.state.approval = Some(ApprovalPanelState {
                    id,
                    sender,
                    action,
                    description,
                });
            }
            Event::QuestionRequest {
                id,
                tool_call_id: _,
                questions,
            } => {
                self.state.question = Some(QuestionPanelState::new(id, questions));
            }
            Event::StatusUpdate { context_usage } => {
                self.state.toolbar.context_usage = context_usage;
            }
            Event::CompactionBegin => {
                self.state.notifications.push(NotificationBlock {
                    severity: NotificationSeverity::Info,
                    text: "Compacting...".to_string(),
                });
            }
            Event::CompactionEnd => {}
            Event::Retry {
                n,
                next_attempt,
                max_attempts,
                wait_s,
                reason,
            } => {
                self.state.notifications.push(NotificationBlock {
                    severity: NotificationSeverity::Warn,
                    text: format!(
                        "Retrying after {} · attempt {}/{} · {:.1}s (step {})",
                        reason, next_attempt, max_attempts, wait_s, n
                    ),
                });
            }
            Event::SteerInput { user_input } => {
                self.state.notifications.push(NotificationBlock {
                    severity: NotificationSeverity::Info,
                    text: format!("❯ {}", user_input),
                });
            }
            Event::Error { message } => {
                self.state.notifications.push(NotificationBlock {
                    severity: NotificationSeverity::Error,
                    text: message,
                });
            }
            Event::Info { message } => {
                self.state.notifications.push(NotificationBlock {
                    severity: NotificationSeverity::Info,
                    text: message,
                });
            }
            Event::PlanDisplay { content, file_path } => {
                self.state.plan = Some(PlanPanelState { content, file_path });
            }
        }
    }

    /// Resolve uma aprovação com a decisão dada.
    pub async fn resolve_approval(&mut self, decision: crate::event::ApprovalDecision) {
        if let Some(panel) = self.state.approval.take() {
            let id = panel.id;
            self.resolver.resolve_approval(&id, decision).await;
        }
    }

    /// Resolve uma question com as respostas.
    pub async fn resolve_question(&mut self, answer: crate::event::QuestionAnswer) {
        if let Some(panel) = self.state.question.take() {
            let id = panel.id;
            self.resolver.resolve_question(&id, answer).await;
        }
    }

    /// Enfileira um novo input de turno.
    pub fn queue_input(&mut self, input: String) {
        self.input_queue.push(input);
    }

    /// Adiciona um input steerado.
    pub fn steer(&mut self, input: String) {
        self.steer_queue.push(input);
    }

    /// Dispara a execução do próximo turno enfileirado numa task separada.
    /// O task pega o lock do agente e roda o turno; os eventos vão para o canal.
    pub fn spawn_turn(&self) -> Option<tokio::task::JoinHandle<()>> {
        let input = self.input_queue.first()?.clone();
        let agent = self.agent.clone();
        Some(tokio::spawn(async move {
            let mut agent = agent.lock().await;
            let _ = agent.run(&input).await;
        }))
    }

    /// Consome o primeiro input enfileirado (marca como iniciado).
    pub fn dequeue_turn(&mut self) -> Option<String> {
        if self.input_queue.is_empty() {
            None
        } else {
            Some(self.input_queue.remove(0))
        }
    }

    pub fn set_yolo(&self, yolo: bool) {
        let agent = self.agent.clone();
        tokio::spawn(async move {
            let mut agent = agent.lock().await;
            agent.set_yolo(yolo);
        });
    }

    pub fn resolver(&self) -> Resolver {
        self.resolver.clone()
    }
}
