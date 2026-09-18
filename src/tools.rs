use anyhow::Result;
use serde_json::{json, Value};

use crate::message::ToolDef;

/// Registro das ferramentas disponíveis, com seus schemas JSON.
pub fn tool_defs() -> Vec<ToolDef> {
    vec![
        ToolDef {
            name: "shell".to_string(),
            description: "Executa um comando shell e retorna sua saída. Use para rodar comandos "
                .to_string(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "O comando shell a ser executado."
                    }
                },
                "required": ["command"]
            }),
        },
        ToolDef {
            name: "read_file".to_string(),
            description: "Lê o conteúdo de um arquivo de texto e retorna seu conteúdo.".to_string(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Caminho absoluto ou relativo do arquivo."
                    }
                },
                "required": ["path"]
            }),
        },
        ToolDef {
            name: "write_file".to_string(),
            description: "Grava conteúdo em um arquivo (sobrescreve se existir).".to_string(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Caminho absoluto ou relativo do arquivo."
                    },
                    "content": {
                        "type": "string",
                        "description": "Conteúdo a ser gravado no arquivo."
                    }
                },
                "required": ["path", "content"]
            }),
        },
        ToolDef {
            name: "list_dir".to_string(),
            description: "Lista o conteúdo de um diretório.".to_string(),
            parameters: json!({
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Caminho absoluto ou relativo do diretório."
                    }
                },
                "required": ["path"]
            }),
        },
        ToolDef {
            name: "id".to_string(),
            description: "Retorna a identidade do usuário atual (UID, GID e grupos). Equivalente ao comando \"id\" do Linux.".to_string(),
            parameters: json!({
                "type": "object",
                "properties": {}
            }),
        },
    ]
}

/// Executa uma ferramenta pelo nome e argumentos JSON.
/// Retorna uma string com o resultado (que vai ser enviada ao modelo como `tool` message).
pub async fn run_tool(name: &str, args_json: &str) -> Result<(String, bool)> {
    let args: Value = serde_json::from_str(args_json)
        .unwrap_or_else(|_| serde_json::from_str("{}").unwrap());
    match name {
        "shell" => {
            let cmd = args
                .get("command")
                .and_then(Value::as_str)
                .ok_or_else(|| anyhow::anyhow!("campo 'command' ausente"))?;
            shell(cmd).await
        }
        "read_file" => {
            let path = args
                .get("path")
                .and_then(Value::as_str)
                .ok_or_else(|| anyhow::anyhow!("campo 'path' ausente"))?;
            read_file(path)
        }
        "write_file" => {
            let path = args
                .get("path")
                .and_then(Value::as_str)
                .ok_or_else(|| anyhow::anyhow!("campo 'path' ausente"))?;
            let content = args
                .get("content")
                .and_then(Value::as_str)
                .ok_or_else(|| anyhow::anyhow!("campo 'content' ausente"))?;
            write_file(path, content)
        }
        "list_dir" => {
            let path = args
                .get("path")
                .and_then(Value::as_str)
                .ok_or_else(|| anyhow::anyhow!("campo 'path' ausente"))?;
            list_dir(path)
        }
        "id" => id().await,
        _ => Err(anyhow::anyhow!("ferramenta desconhecida: {name}")),
    }
}

/// Executa um comando shell via `sh -c`, capturando stdout/stderr e exit code.
async fn shell(command: &str) -> Result<(String, bool)> {
    let output = tokio::process::Command::new("sh")
        .arg("-c")
        .arg(command)
        .output()
        .await?;

    let mut out = String::from_utf8_lossy(&output.stdout).to_string();
    let err = String::from_utf8_lossy(&output.stderr).to_string();
    if !err.is_empty() {
        if !out.is_empty() {
            out.push('\n');
        }
        out.push_str(&err);
    }
    let exit_code = output.status.code().unwrap_or(-1);
    if !output.status.success() {
        out = format!("(exit code {})\n{}", exit_code, out);
        Ok((out, true))
    } else {
        Ok((out, false))
    }
}

fn read_file(path: &str) -> Result<(String, bool)> {
    match std::fs::read_to_string(path) {
        Ok(content) => Ok((content, false)),
        Err(e) => Ok((format!("Erro ao ler '{}': {}", path, e), true)),
    }
}

fn write_file(path: &str, content: &str) -> Result<(String, bool)> {
    match std::fs::write(path, content) {
        Ok(_) => Ok((format!("Arquivo '{}' gravado ({} bytes).", path, content.len()), false)),
        Err(e) => Ok((format!("Erro ao gravar '{}': {}", path, e), true)),
    }
}

fn list_dir(path: &str) -> Result<(String, bool)> {
    let entries = std::fs::read_dir(path);
    match entries {
        Ok(rd) => {
            let mut lines: Vec<String> = Vec::new();
            for entry in rd {
                match entry {
                    Ok(e) => {
                        let name = e.file_name().to_string_lossy().to_string();
                        let kind = e.file_type().map(|t| t.is_dir()).unwrap_or(false);
                        let marker = if kind { "/" } else { "" };
                        lines.push(format!("{}{}", name, marker));
                    }
                    Err(err) => lines.push(format!("<erro: {}>", err)),
                }
            }
            lines.sort();
            let joined = lines.join("\n");
            if joined.is_empty() {
                Ok(("(diretório vazio)".to_string(), false))
            } else {
                Ok((joined, false))
            }
        }
        Err(e) => Ok((format!("Erro ao listar '{}': {}", path, e), true)),
    }
}

/// Retorna a identidade do usuário atual (UID, GID e grupos).
async fn id() -> Result<(String, bool)> {
    let output = tokio::process::Command::new("id")
        .output()
        .await
        .map_err(|e| anyhow::anyhow!("falha ao executar 'id': {}", e))?;
    let out = String::from_utf8_lossy(&output.stdout).to_string();
    if output.status.success() {
        Ok((out, false))
    } else {
        Ok((format!("[erro] {}", out), true))
    }
}
