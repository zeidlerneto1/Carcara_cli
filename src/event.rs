use serde_json::Value;

/// Eventos emitidos pelo agente para a UI, análogos ao protocolo wire do Carcara.
#[derive(Debug, Clone)]
pub enum Event {
    /// Início de um turno com o input do usuário.
    TurnBegin { user_input: String },
    /// Fim de um turno.
    TurnEnd,
    /// Início de um step (n começa em 1).
    StepBegin { n: usize },
    /// Um trecho de texto streamado (conteúdo do assistant).
    TextDelta { text: String },
    /// Um trecho de raciocínio/thinking streamado.
    ThinkDelta { think: String },
    /// Thinking finalizado com tempo e tokens.
    ThinkDone { elapsed_s: f64, tokens: usize },
    /// Início de uma chamada de tool (nome + argumentos).
    ToolCallBegin {
        tool_call_id: String,
        name: String,
        arguments: String,
    },
    /// Resultado de uma tool executada.
    ToolResult {
        tool_call_id: String,
        name: String,
        is_error: bool,
        output: String,
    },
    /// Pedido de aprovação de uma tool (funcional: aguarda resposta da UI).
    ApprovalRequest {
        id: String,
        tool_call_id: String,
        sender: String,
        action: String,
        description: String,
    },
    /// Pedido estruturado de pergunta ao usuário (funcional).
    QuestionRequest {
        id: String,
        tool_call_id: String,
        questions: Vec<QuestionItem>,
    },
    /// Atualização de status (uso de contexto, etc.).
    StatusUpdate { context_usage: Option<f64> },
    /// Início de compactação de contexto.
    CompactionBegin,
    /// Fim de compactação.
    CompactionEnd,
    /// Banner de retry.
    Retry {
        n: usize,
        next_attempt: usize,
        max_attempts: usize,
        wait_s: f64,
        reason: String,
    },
    /// Entrada steerada (injetada durante o turno atual).
    SteerInput { user_input: String },
    /// Mensagem de erro exibida ao usuário.
    Error { message: String },
    /// Mensagem de log/status informativo.
    Info { message: String },
    /// Exibição de um plano.
    PlanDisplay { content: String, file_path: String },
}

/// Item de pergunta (para QuestionRequest).
#[derive(Debug, Clone)]
pub struct QuestionItem {
    pub question: String,
    pub header: String,
    pub options: Vec<QuestionOption>,
    pub multi_select: bool,
}

/// Opção de uma pergunta.
#[derive(Debug, Clone)]
pub struct QuestionOption {
    pub label: String,
    pub description: String,
}

/// Resposta a uma aprovação.
#[derive(Debug, Clone)]
pub enum ApprovalDecision {
    Approve,
    ApproveForSession,
    Reject,
}

/// Resposta a uma pergunta estruturada.
#[derive(Debug, Clone)]
pub struct QuestionAnswer {
    /// Índice da pergunta (0-based) → resposta.
    pub answers: Vec<Vec<String>>,
}

/// Argumentos extras de uma tool (para aprovação). Mantém compatibilidade com tools.rs.
#[allow(dead_code)]
pub fn parse_args(json: &str) -> Value {
    serde_json::from_str(json).unwrap_or(Value::Null)
}
