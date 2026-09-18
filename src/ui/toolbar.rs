use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use ratatui::Frame;

use crate::ui::theme::Theme;

/// Flags e estado exibidos na toolbar inferior.
#[derive(Debug, Clone, Default)]
pub struct ToolbarState {
    pub yolo: bool,
    pub afk: bool,
    pub plan: bool,
    pub agent_mode: bool, // true = agent, false = shell
    pub model: String,
    pub thinking_on: bool,
    pub cwd: String,
    pub git_branch: Option<String>,
    pub git_dirty: bool,
    pub bg_tasks: u32,
    pub toast: Option<String>,
    pub context_usage: Option<f64>,
    pub context_tokens: u64,
    pub max_context_tokens: u64,
    pub tip: String,
}

impl ToolbarState {
    pub fn new(model: &str) -> Self {
        ToolbarState {
            model: model.to_string(),
            ..Default::default()
        }
    }
}

/// Formata contagem de tokens compacta (ex: 1.2k, 8.0k).
fn format_token_count(n: u64) -> String {
    if n >= 1_000_000 {
        format!("{:.1}m", n as f64 / 1_000_000.0)
    } else if n >= 1_000 {
        format!("{:.1}k", n as f64 / 1_000.0)
    } else {
        n.to_string()
    }
}

/// Desenha a toolbar inferior (2 linhas) na parte de baixo do frame.
pub fn draw(frame: &mut Frame, area: ratatui::layout::Rect, state: &ToolbarState, theme: &Theme) {
    let chunks = ratatui::layout::Layout::default()
        .direction(ratatui::layout::Direction::Vertical)
        .constraints([
            ratatui::layout::Constraint::Length(1),
            ratatui::layout::Constraint::Length(1),
        ])
        .split(area);

    // Linha 1: flags + modo/modelo + cwd + git + tasks
    let mut line1: Vec<Span> = Vec::new();
    if state.yolo {
        line1.push(Span::styled(
            "yolo",
            theme.bold(theme.toolbar_flag_yolo),
        ));
        line1.push(Span::styled(" ", theme.dim()));
    }
    if state.afk {
        line1.push(Span::styled(
            "afk",
            theme.bold(theme.toolbar_flag_afk),
        ));
        line1.push(Span::styled(" ", theme.dim()));
    }
    if state.plan {
        line1.push(Span::styled(
            "plan",
            theme.bold(theme.toolbar_flag_plan),
        ));
        line1.push(Span::styled(" ", theme.dim()));
    }

    let mode_sym = if state.agent_mode { "agent" } else { "shell" };
    let think = if state.thinking_on { "●" } else { "○" };
    line1.push(Span::styled(
        format!("{} ({}{})", mode_sym, think, state.model),
        theme.dim(),
    ));

    // cwd (truncado à esquerda)
    let cwd = truncate_left(&state.cwd, 30);
    line1.push(Span::styled(format!(" {}", cwd), theme.dim()));

    // git
    if let Some(branch) = &state.git_branch {
        let badge = if state.git_dirty { "●" } else { "" };
        line1.push(Span::styled(
            format!(" ({}{})", branch, badge),
            theme.dim(),
        ));
    }

    // background tasks
    if state.bg_tasks > 0 {
        line1.push(Span::styled(format!(" ⚙ bash: {}", state.bg_tasks), theme.dim()));
    }

    // tip rotativa (curta)
    if !state.tip.is_empty() {
        line1.push(Span::styled(format!(" {}", state.tip), theme.dim()));
    }

    let p1 = Paragraph::new(Line::from(line1)).block(Block::default().borders(Borders::NONE));
    frame.render_widget(p1, chunks[0]);

    // Linha 2: toast (esquerda) + contexto (direita)
    let mut left_spans: Vec<Span> = Vec::new();
    if let Some(toast) = &state.toast {
        left_spans.push(Span::styled(toast.as_str(), Style::default().fg(theme.info).add_modifier(Modifier::BOLD)));
    }

    let context_text = match state.context_usage {
        Some(usage) if state.max_context_tokens > 0 => {
            let used = format_token_count(state.context_tokens);
            let total = format_token_count(state.max_context_tokens);
            format!("context: {:.1}% ({}/{})", usage * 100.0, used, total)
        }
        Some(usage) => format!("context: {:.1}%", usage * 100.0),
        None => String::new(),
    };

    // Monta a linha com toast à esquerda e contexto alinhado à direita.
    let mut line2_spans = left_spans;
    if !context_text.is_empty() {
        let left_len: usize = line2_spans.iter().map(|s| s.content.chars().count()).sum();
        let width = area.width as usize;
        if left_len >= width {
            // Sem espaço para o contexto: trunca à direita para caber.
            let avail = width.saturating_sub(1);
            line2_spans.push(Span::styled(
                truncate_right(&context_text, avail),
                theme.dim(),
            ));
        } else {
            let pad = width.saturating_sub(left_len + context_text.chars().count());
            line2_spans.push(Span::styled(" ".repeat(pad), Style::default()));
            // Trunca o contexto se ainda assim exceder a largura restante.
            let avail = width.saturating_sub(left_len + pad);
            line2_spans.push(Span::styled(
                truncate_right(&context_text, avail),
                theme.dim(),
            ));
        }
    }
    let line2 = Line::from(line2_spans);
    let p2 = Paragraph::new(line2).block(Block::default().borders(Borders::NONE));
    frame.render_widget(p2, chunks[1]);
}

fn truncate_left(s: &str, max_chars: usize) -> String {
    let w = s.chars().count();
    if w <= max_chars {
        s.to_string()
    } else {
        let keep = max_chars.saturating_sub(3);
        let start = s.chars().count() - keep;
        let tail: String = s.chars().skip(start).collect();
        format!("...{}", tail)
    }
}

/// Trunca a cauda de uma string para caber em `max_chars` caracteres.
fn truncate_right(s: &str, max_chars: usize) -> String {
    if s.chars().count() <= max_chars {
        s.to_string()
    } else {
        let head: String = s.chars().take(max_chars).collect();
        head
    }
}
