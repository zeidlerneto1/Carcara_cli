use ratatui::layout::{Constraint, Direction, Layout, Rect};

/// Estrutura da tela dividida em áreas.
#[derive(Debug, Clone, Copy)]
pub struct Areas {
    /// Área de streaming do agente (blocos de conteúdo, tools, notificações).
    pub stream: Rect,
    /// Área do cabeçalho do input.
    pub input_header: Rect,
    /// Área da linha do prompt.
    pub prompt: Rect,
    /// Área da toolbar inferior (2 linhas).
    pub toolbar: Rect,
}

/// Calcula as áreas da tela a partir do frame.
pub fn compute(area: Rect) -> Areas {
    // Terminal muito baixo: prioriza prompt+toolbar e encolhe o stream,
    // para que as áreas fixas sempre caibam sem cortar a UI.
    let stream_min = if area.height >= 6 { 1 } else { 0 };
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Min(stream_min), // stream (flexível)
            Constraint::Length(1), // input header
            Constraint::Length(1), // prompt
            Constraint::Length(2), // toolbar
        ])
        .split(area);

    Areas {
        stream: outer[0],
        input_header: outer[1],
        prompt: outer[2],
        toolbar: outer[3],
    }
}
