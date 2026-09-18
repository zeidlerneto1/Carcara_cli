use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use ratatui::Frame;

use crate::ui::theme::Theme;

/// Um bloco de conteúdo streamado do assistant.
#[derive(Debug, Clone)]
pub struct ContentBlock {
    pub buffer: String,
    pub thinking: String,
    pub thinking_elapsed_s: f64,
    pub thinking_tokens: usize,
    pub started: std::time::Instant,
    pub composing: bool,
}

impl Default for ContentBlock {
    fn default() -> Self {
        ContentBlock {
            buffer: String::new(),
            thinking: String::new(),
            thinking_elapsed_s: 0.0,
            thinking_tokens: 0,
            started: std::time::Instant::now(),
            composing: false,
        }
    }
}

impl ContentBlock {
    pub fn new() -> Self {
        ContentBlock {
            started: std::time::Instant::now(),
            ..Default::default()
        }
    }

    fn elapsed_s(&self) -> f64 {
        self.started.elapsed().as_secs_f64()
    }

    fn token_estimate(&self) -> usize {
        self.buffer.len() / 4
    }

    pub fn render(&self, theme: &Theme) -> Vec<Line> {
        let mut lines = Vec::new();
        // Thinking line
        if !self.thinking.is_empty() {
            let elapsed = self.thinking_elapsed_s;
            let toks = self.thinking_tokens;
            let tok_s = if elapsed > 0.0 {
                (toks as f64 / elapsed) as usize
            } else {
                0
            };
            let meta = format!(
                "Thinking ...  {:.1}s · {} tokens · {} tok/s",
                elapsed, toks, tok_s
            );
            lines.push(Line::from(Span::styled(
                meta,
                Style::default().fg(theme.thinking).add_modifier(Modifier::ITALIC),
            )));
            // preview de raciocínio (última linha)
            if let Some(last) = self.thinking.lines().last() {
                let preview = truncate_right(last, 80);
                lines.push(Line::from(Span::styled(
                    format!("  {}", preview),
                    Style::default().fg(theme.thinking),
                )));
            }
        }

        // Composing line
        if self.composing && !self.buffer.is_empty() {
            let elapsed = self.elapsed_s();
            let toks = self.token_estimate();
            lines.push(Line::from(Span::styled(
                format!("Composing...  {:.1}s · {} tokens", elapsed, toks),
                theme.dim(),
            )));
        }

        // Conteúdo
        if !self.buffer.is_empty() {
            for l in self.buffer.lines() {
                lines.push(Line::from(Span::styled(
                    format!("  {}", l),
                    Style::default().fg(theme.assistant_text),
                )));
            }
        }
        lines
    }
}

/// Um bloco de chamada de tool (Using → Used).
#[derive(Debug, Clone)]
pub struct ToolCallBlock {
    pub tool_call_id: String,
    pub name: String,
    pub arguments: String,
    pub state: ToolCallState,
}

#[derive(Debug, Clone)]
pub enum ToolCallState {
    Using,
    Done { is_error: bool },
}

impl ToolCallBlock {
    pub fn new(tool_call_id: String, name: String, arguments: String) -> Self {
        ToolCallBlock {
            tool_call_id,
            name,
            arguments,
            state: ToolCallState::Using,
        }
    }

    pub fn render(&self, theme: &Theme) -> Vec<Line> {
        let mut lines = Vec::new();
        let (bullet, style) = match &self.state {
            ToolCallState::Using => ("◐", theme.dim()),
            ToolCallState::Done { is_error } => {
                if *is_error {
                    ("✗", Style::default().fg(theme.tool_err_bullet))
                } else {
                    ("✓", Style::default().fg(theme.tool_ok_bullet))
                }
            }
        };
        let name = Span::styled(&self.name, Style::default().fg(theme.tool_name));
        let args = Span::styled(
            &self.arguments,
            Style::default().fg(theme.tool_args),
        );
        let verb = match &self.state {
            ToolCallState::Using => "Using",
            ToolCallState::Done { .. } => "Used",
        };
        lines.push(Line::from(vec![
            Span::styled(format!("{} {}", bullet, verb), style),
            Span::styled(" ", Style::default()),
            name,
            Span::styled(format!(" ({})", args.to_string()), Style::default()),
        ]));
        lines
    }
}

/// Bloco de notificação (severidade colorida).
#[derive(Debug, Clone)]
pub struct NotificationBlock {
    pub severity: NotificationSeverity,
    pub text: String,
}

#[derive(Debug, Clone)]
pub enum NotificationSeverity {
    Info,
    Success,
    Warn,
    Error,
}

impl NotificationBlock {
    pub fn render(&self, theme: &Theme) -> Vec<Line> {
        let color = match self.severity {
            NotificationSeverity::Info => theme.info,
            NotificationSeverity::Success => theme.success,
            NotificationSeverity::Warn => theme.warn,
            NotificationSeverity::Error => theme.error,
        };
        let style = Style::default().fg(color).add_modifier(Modifier::BOLD);
        vec![Line::from(Span::styled(&self.text, style))]
    }
}

fn truncate_right(s: &str, max_chars: usize) -> String {
    let w = s.chars().count();
    if w <= max_chars {
        s.to_string()
    } else {
        let head: String = s.chars().take(max_chars).collect();
        format!("{}…", head)
    }
}

/// Desenha uma lista de blocos de conteúdo numa área.
pub fn draw_blocks(
    frame: &mut Frame,
    area: ratatui::layout::Rect,
    content: &ContentBlock,
    tools: &[ToolCallBlock],
    notifications: &[NotificationBlock],
    theme: &Theme,
) {
    let mut lines: Vec<Line> = Vec::new();
    lines.extend(content.render(theme));
    for t in tools {
        lines.extend(t.render(theme));
    }
    for n in notifications {
        lines.extend(n.render(theme));
    }
    let line_count = lines.len() as u16;
    let p = Paragraph::new(lines).block(Block::default().borders(Borders::NONE));
    if line_count <= area.height {
        // Conteúdo menor que a área: ancora na parte de baixo (junto ao prompt),
        // deixando o topo vazio (conversa cresce de baixo para cima).
        let chunks = ratatui::layout::Layout::default()
            .direction(ratatui::layout::Direction::Vertical)
            .constraints([
                ratatui::layout::Constraint::Min(0),
                ratatui::layout::Constraint::Length(line_count),
            ])
            .split(area);
        frame.render_widget(p, chunks[1]);
    } else {
        // Conteúdo maior que a área: rola até o fim, mantendo o final visível.
        let offset = line_count.saturating_sub(area.height);
        frame.render_widget(p.scroll((offset, 0)), area);
    }
}
