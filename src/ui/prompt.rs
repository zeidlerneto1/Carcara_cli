use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use ratatui::Frame;

use crate::ui::theme::Theme;

/// Símbolos de prompt (espelham o original).
pub const SYMBOL_AGENT: &str = "✨";
pub const SYMBOL_SHELL: &str = "$";
pub const SYMBOL_THINKING: &str = "💫";
pub const SYMBOL_PLAN: &str = "📋";

/// Estado da linha de input.
#[derive(Debug, Clone, Default)]
pub struct PromptState {
    pub buffer: String,
    pub cursor: usize,
    pub agent_mode: bool,
    pub thinking_on: bool,
    pub plan: bool,
    pub queued: usize,
}

impl PromptState {
    pub fn symbol(&self) -> &'static str {
        if self.plan {
            SYMBOL_PLAN
        } else if self.thinking_on {
            SYMBOL_THINKING
        } else if self.agent_mode {
            SYMBOL_AGENT
        } else {
            SYMBOL_SHELL
        }
    }
}

/// Desenha o cabeçalho do input + linha do prompt.
pub fn draw(frame: &mut Frame, area: ratatui::layout::Rect, state: &PromptState, theme: &Theme) {
    let chunks = ratatui::layout::Layout::default()
        .direction(ratatui::layout::Direction::Vertical)
        .constraints([
            ratatui::layout::Constraint::Length(1),
            ratatui::layout::Constraint::Length(1),
        ])
        .split(area);

    // Cabeçalho: `── input ──` ou `╌╌ input · plan ╌╌`
    let header_style = if state.plan {
        Style::default().fg(theme.input_header_plan)
    } else {
        theme.dim()
    };
    let header_text = if state.plan {
        format!("╌╌ input · plan ╌╌")
    } else {
        format!("── input ──")
    };
    let mut header_spans = vec![Span::styled(header_text, header_style)];
    if state.queued > 0 {
        header_spans.push(Span::styled(
            format!(" {} queued", state.queued),
            Style::default().fg(theme.info).add_modifier(Modifier::BOLD),
        ));
    }
    let h = Paragraph::new(Line::from(header_spans)).block(Block::default().borders(Borders::NONE));
    frame.render_widget(h, chunks[0]);

    // Linha do prompt
    let symbol = state.symbol();
    let sym_style = Style::default().fg(theme.prompt_symbol);
    let buffer_text = if state.buffer.is_empty() {
        " ".to_string()
    } else {
        state.buffer.clone()
    };
    let p = Paragraph::new(Line::from(vec![
        Span::styled(format!("{} ", symbol), sym_style),
        Span::styled(buffer_text, Style::default().fg(theme.assistant_text)),
    ]))
    .block(Block::default().borders(Borders::NONE));
    frame.render_widget(p, chunks[1]);
}

/// Autocomplete de slash commands (fuzzy básico por prefixo).
pub fn complete_slash_command(input: &str, commands: &[&str]) -> Option<String> {
    if !input.starts_with('/') {
        return None;
    }
    let prefix = input.to_lowercase();
    let matches: Vec<&str> = commands
        .iter()
        .filter(|c| c.starts_with(&prefix))
        .copied()
        .collect();
    if matches.len() == 1 {
        Some(matches[0].to_string())
    } else {
        None
    }
}
