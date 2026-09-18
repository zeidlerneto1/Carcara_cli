mod agent;
mod app;
mod banner;
mod event;
mod llm;
mod message;
mod tools;
mod ui;

use std::sync::Arc;

use llm::LlmClient;

const DEFAULT_BASE_URL: &str = "https://carcara.sinapad.lncc.br/service/v1";
const DEFAULT_MODEL: &str = "DeepSeek-v4-Flash-0731";

pub const SYSTEM_PROMPT: &str = r#"Você é um agente de software engineering chamado "poc-rust",
um experimento em Rust que replica a interface do Carcara CLI. Você ajuda o usuário
com tarefas de programação e operação de sistema.

Você tem acesso a ferramentas que pode chamar quando precisar executar comandos ou ler/gravar arquivos.
Regras:
- Se o usuário pedir para executar um comando, use a ferramenta `shell`.
- Se o usuário pedir para ler ou gravar arquivos, use `read_file`/`write_file`.
- Para listar um diretório, use `list_dir`.
- Para saber o usuário atual, use `id`.
- Sempre use o resultado da ferramenta para elaborar sua resposta final.
- Responda de forma concisa e direta em português.
"#;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let base_url = std::env::var("POC_LLM_BASE_URL").unwrap_or_else(|_| DEFAULT_BASE_URL.to_string());
    let model = std::env::var("POC_LLM_MODEL").unwrap_or_else(|_| DEFAULT_MODEL.to_string());

    let llm = Arc::new(LlmClient::new(&base_url, &model)?);

    // Modo não-interativo: `poc_rust "<input>"` executa um turno e sai.
    if let Some(input) = std::env::args().nth(1) {
        return run_print_mode(llm, &input).await;
    }

    // Modo interativo: detecta se stdin/stdout são TTY.
    if is_tty() {
        ui::tui::run(llm, &model, SYSTEM_PROMPT).await
    } else {
        run_print_mode(llm, "Digite /help para ajuda, /quit para sair.").await
    }
}

/// Executa um turno único e imprime o resultado (modo não-interativo).
async fn run_print_mode(llm: Arc<LlmClient>, input: &str) -> anyhow::Result<()> {
    use agent::Agent;
    // O receiver é dropado; os eventos são ignorados (try_send pode falhar,
    // mas o agente usa try_send e ignora erros, então não bloqueia).
    let (mut agent, _rx, _resolver) = Agent::with_events(llm, SYSTEM_PROMPT);
    match agent.run(input).await {
        Ok(text) => {
            println!("{}", text);
            Ok(())
        }
        Err(e) => {
            eprintln!("\n[erro] {}", e);
            std::process::exit(1);
        }
    }
}

/// Verifica se stdin e stdout são TTY (terminal interativo).
fn is_tty() -> bool {
    use std::io::IsTerminal;
    std::io::stdin().is_terminal() && std::io::stdout().is_terminal()
}
