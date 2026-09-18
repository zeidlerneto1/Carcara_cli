use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, List, ListItem, ListState, Paragraph};
use ratatui::Frame;

use crate::event::{QuestionAnswer, QuestionItem};
use crate::ui::theme::Theme;

/// Estado de um pedido de aprovação pendente.
#[derive(Debug, Clone)]
pub struct ApprovalPanelState {
    pub id: String,
    pub sender: String,
    pub action: String,
    pub description: String,
}

impl ApprovalPanelState {
    pub fn render(&self, theme: &Theme) -> Vec<Line> {
        let border_style = Style::default().fg(theme.warn);
        let title = Line::from(vec![
            Span::styled("Approval Required", theme.bold(theme.warn)),
        ]);
        let lines = vec![
            Line::from(vec![
                Span::styled("Action: ", theme.bold(theme.warn)),
                Span::styled(&self.action, Style::default().fg(theme.assistant_text)),
            ]),
            Line::from(vec![
                Span::styled("Description: ", theme.dim()),
                Span::styled(&self.description, theme.dim()),
            ]),
            Line::default(),
            Line::from(Span::styled(
                "y/n approve or reject · a approve for session · Esc cancel",
                theme.dim(),
            )),
        ];
        let _ = border_style;
        let _ = title;
        lines
    }
}

/// Estado de um pedido de question pendente.
#[derive(Debug, Clone)]
pub struct QuestionPanelState {
    pub id: String,
    pub questions: Vec<QuestionItem>,
    /// Índice da pergunta atualmente selecionada.
    pub current: usize,
    /// Índice da opção selecionada na pergunta atual.
    pub selected: Vec<usize>,
    /// Modo de input custom ("Other").
    pub custom_input: Option<String>,
    pub custom_mode: bool,
}

impl QuestionPanelState {
    pub fn new(id: String, questions: Vec<QuestionItem>) -> Self {
        let selected = vec![0usize; questions.len()];
        QuestionPanelState {
            id,
            questions,
            current: 0,
            selected,
            custom_input: None,
            custom_mode: false,
        }
    }

    pub fn render(&self, theme: &Theme) -> Vec<Line> {
        let mut lines = Vec::new();
        let q = &self.questions[self.current];
        let header = if q.header.is_empty() {
            "Question".to_string()
        } else {
            q.header.clone()
        };
        lines.push(Line::from(Span::styled(
            format!("{} · {}", header, q.question),
            theme.bold(theme.info),
        )));

        for (i, opt) in q.options.iter().enumerate() {
            let marker = if i == self.selected[self.current] {
                "›"
            } else {
                " "
            };
            let style = if i == self.selected[self.current] {
                theme.bold(theme.info)
            } else {
                theme.dim()
            };
            lines.push(Line::from(Span::styled(
                format!("{} {}. {}", marker, i + 1, opt.label),
                style,
            )));
        }
        // Opção "Other"
        let other_marker = if self.selected[self.current] == q.options.len() {
            "›"
        } else {
            " "
        };
        let other_style = if self.selected[self.current] == q.options.len() {
            theme.bold(theme.info)
        } else {
            theme.dim()
        };
        lines.push(Line::from(Span::styled(
            format!("{} {}. Other", other_marker, q.options.len() + 1),
            other_style,
        )));

        lines.push(Line::default());
        lines.push(Line::from(Span::styled(
            "↑/↓ navigate · Enter confirm · Esc cancel",
            theme.dim(),
        )));
        lines
    }
}

/// Painel de plano (borda ciano).
#[derive(Debug, Clone)]
pub struct PlanPanelState {
    pub content: String,
    pub file_path: String,
}

impl PlanPanelState {
    pub fn render(&self, theme: &Theme) -> Vec<Line> {
        let mut lines = Vec::new();
        lines.push(Line::from(Span::styled(
            format!("Plan · {}", self.file_path),
            theme.bold(theme.plan_border),
        )));
        for l in self.content.lines() {
            lines.push(Line::from(Span::styled(
                l,
                Style::default().fg(theme.assistant_text),
            )));
        }
        lines
    }
}

/// Desenha um painel de overlay centralizado.
pub fn draw_panel(
    frame: &mut Frame,
    area: ratatui::layout::Rect,
    title: &str,
    lines: Vec<Line>,
    theme: &Theme,
) {
    // Terminal muito pequeno: não desenha o painel para evitar panic do ratatui
    // (um Block com bordas precisa de pelo menos 2x2).
    if area.width < 4 || area.height < 4 {
        return;
    }
    let height = (lines.len() + 2).min(area.height.saturating_sub(2) as usize) as u16;
    let width = 70u16.min(area.width.saturating_sub(2)).max(1);
    let x = area.x + (area.width.saturating_sub(width)) / 2;
    // Ancorado à parte inferior da área (junto ao prompt), em vez de centralizado.
    let y = area.y + area.height.saturating_sub(height);

    let panel_area = ratatui::layout::Rect::new(x, y, width, height);
    let block = Block::default()
        .borders(Borders::ALL)
        .title(title)
        .border_style(Style::default().fg(theme.plan_border));
    let inner = block.inner(panel_area);
    frame.render_widget(block, panel_area);
    let p = Paragraph::new(lines);
    frame.render_widget(p, inner);
}

// Re-export de helpers para não quebrar imports.
pub type _List = List<'static>;
pub type _ListItem = ListItem<'static>;
pub type _ListState = ListState;
pub type _QuestionAnswer = QuestionAnswer;
