use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use ratatui::Frame;

/// Cor de borda do painel de boas-vindas (dodger_blue1 do original).
pub const KIMI_BLUE: Color = Color::Rgb(28, 151, 234);

/// Arte ASCII do tucano (transcrita de `_TUCAO_LOGO` do Carcara CLI).
///
/// Cores do original: orange3, grey35, white.
pub fn tucano_logo() -> Vec<Line<'static>> {
    let orange = Style::default().fg(Color::Rgb(205, 145, 60));
    let grey = Style::default().fg(Color::Rgb(84, 84, 84));
    let white = Style::default().fg(Color::White);

    let lines: Vec<Vec<(String, Style)>> = vec![
        vec![("            ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄".into(), grey)],
        vec![
            ("   ▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄".into(), orange),
            ("▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀".into(), grey),
        ],
        vec![
            ("  ████████████████████".into(), orange),
            ("██".into(), grey),
            ("o".into(), white),
            ("███████".into(), grey),
        ],
        vec![
            (" █████████████████████".into(), orange),
            ("██▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄".into(), grey),
        ],
        vec![
            (" █████████████████████".into(), orange),
            ("████████████████████".into(), grey),
        ],
        vec![
            ("  ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀".into(), orange),
            ("▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄".into(), grey),
        ],
        vec![("             ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀".into(), grey)],
    ];

    lines
        .into_iter()
        .map(|spans| {
            Line::from(
                spans
                    .into_iter()
                    .map(|(s, st)| Span::styled(s, st))
                    .collect::<Vec<_>>(),
            )
        })
        .collect()
}

/// Renderiza o painel de boas-vindas (tucano + título + itens de info).
pub fn welcome_area<'a>(directory: &str, session: &str, model: &str) -> Paragraph<'a> {
    let grey = Style::default().fg(Color::Rgb(128, 128, 128));

    let mut lines = Vec::new();

    // Título
    lines.push(Line::from(Span::styled(
        "Welcome to Carcara Code CLI!",
        Style::default().fg(Color::White).add_modifier(Modifier::BOLD),
    )));
    lines.push(Line::from(Span::styled(
        "Send /help for help information.",
        grey,
    )));
    lines.push(Line::default());

    // Itens de info
    lines.push(Line::from(vec![
        Span::styled(format!("Directory: "), grey),
        Span::styled(directory.to_string(), grey),
    ]));
    lines.push(Line::from(vec![
        Span::styled(format!("Session: "), grey),
        Span::styled(session.to_string(), grey),
    ]));
    lines.push(Line::from(vec![
        Span::styled(format!("Model: "), grey),
        Span::styled(model.to_string(), grey),
    ]));

    Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(Style::default().fg(KIMI_BLUE))
                .padding(ratatui::widgets::Padding::horizontal(2)),
        )
}

/// Renderiza apenas a arte do tucano para exibição no banner.
pub fn render_tucano_paragraph() -> Paragraph<'static> {
    Paragraph::new(tucano_logo())
}

/// Função de conveniência para desenhar o banner completo num frame.
/// `show_logo` coloca o tucano à esquerda e as infos à direita.
pub fn draw(frame: &mut Frame, directory: &str, session: &str, model: &str) {
    let area = frame.area();
    let chunks = ratatui::layout::Layout::default()
        .direction(ratatui::layout::Direction::Horizontal)
        .constraints([
            ratatui::layout::Constraint::Length(30),
            ratatui::layout::Constraint::Min(0),
        ])
        .split(area);

    frame.render_widget(render_tucano_paragraph(), chunks[0]);
    frame.render_widget(welcome_area(directory, session, model), chunks[1]);
}
