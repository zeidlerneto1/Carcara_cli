use std::io;
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use crossterm::event::{Event as CEvent, KeyCode, KeyEvent, KeyModifiers};
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use crossterm::ExecutableCommand;
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;
use tokio::sync::mpsc;

use crate::app::App;
use crate::event::ApprovalDecision;
use crate::llm::LlmClient;
use crate::ui::blocks::NotificationBlock;
use crate::ui::blocks::NotificationSeverity;
use crate::ui::layout;
use crate::ui::panels;
use crate::ui::prompt;
use crate::ui::theme::{self, Theme};
use crate::ui::toolbar;

/// Lista de slash commands disponíveis (para autocomplete/ajuda).
pub const SLASH_COMMANDS: &[&str] = &[
    "/help", "/clear", "/model", "/plan", "/yolo", "/afk", "/theme", "/version",
    "/exit", "/quit", "/new",
];

/// Loop principal da TUI.
pub async fn run(llm: Arc<LlmClient>, model: &str, system_prompt: &str) -> Result<()> {
    let mut app = App::new(llm, model, system_prompt);
    let mut theme = theme::DARK;

    // Terminal raw + alternate screen.
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    stdout.execute(EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let result = run_loop(&mut terminal, &mut app, &mut theme).await;

    // Restaura o terminal.
    disable_raw_mode()?;
    terminal.backend_mut().execute(LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    result
}

/// Canal interno para notificar o loop sobre respostas de aprovação/question.
type CmdTx = mpsc::UnboundedSender<Cmd>;
enum Cmd {
    Approval(ApprovalDecision),
    Quit,
}

async fn run_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    app: &mut App,
    theme: &mut Theme,
) -> Result<()> {
    let (cmd_tx, mut cmd_rx) = mpsc::unbounded_channel::<Cmd>();
    let mut turn_task: Option<tokio::task::JoinHandle<()>> = None;

    loop {
        // Poll eventos do agente.
        app.poll_events();

        // Se há turnos enfileirados e nenhum rodando, inicia.
        if turn_task.is_none() && !app.input_queue.is_empty() {
            turn_task = app.spawn_turn();
        }

        // Se terminou o turno, limpa o handle e consome o input.
        if let Some(t) = &turn_task {
            if t.is_finished() {
                turn_task = None;
                app.dequeue_turn();
            }
        }

        // Render.
        terminal.draw(|f| draw(f, app, *theme))?;

        // Multiplexa eventos de tecla e comandos da UI.
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_millis(50)) => {}
            cmd = cmd_rx.recv() => {
                match cmd {
                    Some(Cmd::Approval(decision)) => {
                        app.resolve_approval(decision).await;
                    }
                    Some(Cmd::Quit) | None => {
                        app.state.running = false;
                    }
                }
            }
            ev = read_key_async() => {
                if let Some(ev) = ev {
                    let mut quit = false;
                    handle_key(ev, app, theme, cmd_tx.clone(), &mut quit);
                    if quit {
                        app.state.running = false;
                    }
                }
            }
        }

        if !app.state.running {
            break;
        }
    }

    Ok(())
}

/// Lê um evento de tecla de forma assíncrona (sem bloquear o loop).
async fn read_key_async() -> Option<CEvent> {
    tokio::task::spawn_blocking(|| {
        if crossterm::event::poll(Duration::from_millis(50)).unwrap_or(false) {
            crossterm::event::read().ok()
        } else {
            None
        }
    })
    .await
    .ok()
    .flatten()
}

fn handle_key(
    ev: crossterm::event::Event,
    app: &mut App,
    theme: &mut Theme,
    cmd_tx: CmdTx,
    quit: &mut bool,
) {
    match ev {
        CEvent::Key(KeyEvent { code, modifiers, .. }) => {
            if code == KeyCode::Char('c') && modifiers.contains(KeyModifiers::CONTROL) {
                *quit = true;
                return;
            }
            if code == KeyCode::Char('d') && modifiers.contains(KeyModifiers::CONTROL) {
                *quit = true;
                return;
            }

            // Se há painel de aprovação, teclas são para ele.
            if app.state.approval.is_some() {
                handle_approval_key(app, code, cmd_tx);
                return;
            }
            // Se há painel de question, teclas são para ele.
            if app.state.question.is_some() {
                handle_question_key(app, code);
                return;
            }

            match code {
                KeyCode::Char('x') if modifiers.contains(KeyModifiers::CONTROL) => {
                    app.state.prompt.agent_mode = !app.state.prompt.agent_mode;
                }
                KeyCode::BackTab => {
                    app.state.prompt.plan = !app.state.prompt.plan;
                    app.state.toolbar.plan = app.state.prompt.plan;
                }
                KeyCode::Enter => {
                    let input = app.state.prompt.buffer.trim().to_string();
                    if input.is_empty() {
                        return;
                    }
                    if input.starts_with('/') {
                        handle_slash_command(app, &input, theme);
                        app.state.prompt.buffer.clear();
                        app.state.prompt.cursor = 0;
                    } else if app.state.prompt.agent_mode {
                        app.queue_input(input);
                        app.state.prompt.buffer.clear();
                        app.state.prompt.cursor = 0;
                    } else {
                        execute_shell_command(app, &input);
                        app.state.prompt.buffer.clear();
                        app.state.prompt.cursor = 0;
                    }
                }
                KeyCode::Char('s') if modifiers.contains(KeyModifiers::CONTROL) => {
                    let input = app.state.prompt.buffer.trim().to_string();
                    if !input.is_empty() {
                        app.steer(input);
                        app.state.prompt.buffer.clear();
                        app.state.prompt.cursor = 0;
                    }
                }
                KeyCode::Char(c) => {
                    app.state.prompt.buffer.insert(app.state.prompt.cursor, c);
                    app.state.prompt.cursor += 1;
                }
                KeyCode::Backspace => {
                    if app.state.prompt.cursor > 0 {
                        app.state.prompt.cursor -= 1;
                        app.state.prompt.buffer.remove(app.state.prompt.cursor);
                    }
                }
                KeyCode::Left => {
                    if app.state.prompt.cursor > 0 {
                        app.state.prompt.cursor -= 1;
                    }
                }
                KeyCode::Right => {
                    if app.state.prompt.cursor < app.state.prompt.buffer.chars().count() {
                        app.state.prompt.cursor += 1;
                    }
                }
                KeyCode::Home => app.state.prompt.cursor = 0,
                KeyCode::End => app.state.prompt.cursor = app.state.prompt.buffer.chars().count(),
                KeyCode::Tab => {
                    if let Some(completed) =
                        prompt::complete_slash_command(&app.state.prompt.buffer, SLASH_COMMANDS)
                    {
                        app.state.prompt.buffer = completed;
                        app.state.prompt.cursor = app.state.prompt.buffer.chars().count();
                    }
                }
                KeyCode::Esc => *quit = true,
                _ => {}
            }
        }
        _ => {}
    }
}

fn handle_approval_key(_app: &mut App, code: KeyCode, cmd_tx: CmdTx) {
    let decision = match code {
        KeyCode::Char('y') | KeyCode::Char('Y') | KeyCode::Enter => Some(ApprovalDecision::Approve),
        KeyCode::Char('a') | KeyCode::Char('A') => Some(ApprovalDecision::ApproveForSession),
        KeyCode::Char('n') | KeyCode::Char('N') | KeyCode::Esc => Some(ApprovalDecision::Reject),
        _ => None,
    };
    if let Some(d) = decision {
        let _ = cmd_tx.send(Cmd::Approval(d));
    }
}

fn handle_question_key(app: &mut App, code: KeyCode) {
    let Some(panel) = &mut app.state.question else {
        return;
    };
    match code {
        KeyCode::Up | KeyCode::Char('k') => {
            let opts = panel.questions[panel.current].options.len() + 1;
            if panel.selected[panel.current] > 0 {
                panel.selected[panel.current] -= 1;
            } else {
                panel.selected[panel.current] = opts - 1;
            }
        }
        KeyCode::Down | KeyCode::Char('j') => {
            let opts = panel.questions[panel.current].options.len() + 1;
            panel.selected[panel.current] = (panel.selected[panel.current] + 1) % opts;
        }
        KeyCode::Enter => {
            let q = panel.questions[panel.current].clone();
            let sel = panel.selected[panel.current];
            let answer = if sel < q.options.len() {
                q.options[sel].label.clone()
            } else {
                "Other".to_string()
            };
            if panel.current + 1 < panel.questions.len() {
                panel.current += 1;
            } else {
                let id = panel.id.clone();
                let answer = crate::event::QuestionAnswer {
                    answers: vec![vec![answer]],
                };
                // Resolve via resolver compartilhado de forma síncrona é bloqueante;
                // usamos spawn para resolver sem bloquear o loop.
                let resolver = app.resolver();
                tokio::spawn(async move {
                    resolver.resolve_question(&id, answer).await;
                });
                app.state.question = None;
            }
        }
        KeyCode::Esc => {
            let id = panel.id.clone();
            let resolver = app.resolver();
            tokio::spawn(async move {
                resolver.resolve_question(&id, crate::event::QuestionAnswer { answers: Vec::new() })
                    .await;
            });
            app.state.question = None;
        }
        _ => {}
    }
}

fn handle_slash_command(app: &mut App, input: &str, theme: &mut Theme) {
    let cmd = input.split_whitespace().next().unwrap_or("");
    match cmd {
        "/exit" | "/quit" => {
            app.state.running = false;
        }
        "/clear" => {
            app.state.content = crate::ui::blocks::ContentBlock::new();
            app.state.tool_blocks.clear();
            app.state.notifications.clear();
        }
        "/help" => {
            app.state.notifications.push(NotificationBlock {
                severity: NotificationSeverity::Info,
                text: format!("Commands: {}", SLASH_COMMANDS.join(" ")),
            });
        }
        "/model" => {
            let parts: Vec<&str> = input.split_whitespace().collect();
            if parts.len() > 1 {
                app.state.toolbar.model = parts[1].to_string();
            }
        }
        "/plan" => {
            app.state.prompt.plan = true;
            app.state.toolbar.plan = true;
        }
        "/yolo" => {
            app.state.toolbar.yolo = !app.state.toolbar.yolo;
            app.set_yolo(app.state.toolbar.yolo);
        }
        "/afk" => {
            app.state.toolbar.afk = !app.state.toolbar.afk;
        }
        "/theme" => {
            let next = if theme.name == "dark" {
                theme::LIGHT
            } else {
                theme::DARK
            };
            *theme = next;
        }
        "/version" => {
            app.state.notifications.push(NotificationBlock {
                severity: NotificationSeverity::Info,
                text: "poc-rust 0.1.0 (Carcara CLI interface)".to_string(),
            });
        }
        _ => {
            app.state.notifications.push(NotificationBlock {
                severity: NotificationSeverity::Warn,
                text: format!("Unknown command: {} (use /help)", cmd),
            });
        }
    }
}

fn execute_shell_command(app: &mut App, input: &str) {
    let output = std::process::Command::new("sh").arg("-c").arg(input).output();
    match output {
        Ok(out) => {
            let stdout = String::from_utf8_lossy(&out.stdout).to_string();
            let stderr = String::from_utf8_lossy(&out.stderr).to_string();
            let mut text = stdout;
            if !stderr.is_empty() {
                text.push_str(&stderr);
            }
            app.state.notifications.push(NotificationBlock {
                severity: NotificationSeverity::Success,
                text,
            });
        }
        Err(e) => {
            app.state.notifications.push(NotificationBlock {
                severity: NotificationSeverity::Error,
                text: format!("Erro: {}", e),
            });
        }
    }
}

/// Desenha o frame completo.
fn draw(frame: &mut ratatui::Frame, app: &mut App, theme: Theme) {
    let areas = layout::compute(frame.area());

    if app.state.show_banner {
        // Banner de boas-vindas (tucano + welcome) na área de streaming.
        let directory = std::env::current_dir()
            .map(|p| p.display().to_string())
            .unwrap_or_else(|_| "?".to_string());
        let model = app.state.toolbar.model.clone();
        let session = "poc-rust".to_string();
        // Divide horizontalmente: tucano à esquerda, infos à direita.
        let chunks = ratatui::layout::Layout::default()
            .direction(ratatui::layout::Direction::Horizontal)
            .constraints([
                ratatui::layout::Constraint::Length(30),
                ratatui::layout::Constraint::Min(0),
            ])
            .split(areas.stream);
        frame.render_widget(crate::banner::render_tucano_paragraph(), chunks[0]);
        frame.render_widget(
            crate::banner::welcome_area(&directory, &session, &model),
            chunks[1],
        );
    } else {
        // Área de streaming
        crate::ui::blocks::draw_blocks(
            frame,
            areas.stream,
            &app.state.content,
            &app.state.tool_blocks,
            &app.state.notifications,
            &theme,
        );
    }

    // Prompt + toolbar
    let prompt_area = ratatui::layout::Rect::new(
        areas.input_header.x,
        areas.input_header.y,
        areas.input_header.width,
        areas.input_header.height + areas.prompt.height,
    );
    prompt::draw(frame, prompt_area, &app.state.prompt, &theme);
    toolbar::draw(frame, areas.toolbar, &app.state.toolbar, &theme);

    // Overlays (painéis)
    if let Some(approval) = &app.state.approval {
        panels::draw_panel(frame, areas.stream, "Approval", approval.render(&theme), &theme);
    }
    if let Some(question) = &app.state.question {
        panels::draw_panel(frame, areas.stream, "Question", question.render(&theme), &theme);
    }
    if let Some(plan) = &app.state.plan {
        panels::draw_panel(frame, areas.stream, "Plan", plan.render(&theme), &theme);
    }
}
