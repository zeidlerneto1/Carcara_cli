use std::sync::Arc;

use anyhow::{anyhow, Result};
use futures_util::StreamExt;
use reqwest::header::{HeaderMap, HeaderValue};
use serde_json::{json, Value};

use crate::message::{LlmResponse, Message, ToolCall, ToolDef};

/// Cliente para uma API compatível com OpenAI `chat/completions`.
///
/// Foi calibrado para o endpoint Carcará
/// (`https://carcara.sinapad.lncc.br/service/v1`), que exige alguns headers
/// de browser (`Referer`, `Origin`, `Accept-Language`) para não retornar 403.
pub struct LlmClient {
    base_url: String,
    model: String,
    client: reqwest::Client,
    stream: bool,
}

#[derive(Debug, Clone)]
pub struct StreamedText {
    pub full: String,
    pub reasoning: String,
}

impl LlmClient {
    pub fn new(base_url: &str, model: &str) -> Result<Self> {
        let mut headers = HeaderMap::new();
        headers.insert("Accept", HeaderValue::from_static("*/*"));
        headers.insert(
            "Accept-Language",
            HeaderValue::from_static("en-US,en;q=0.9,pt;q=0.8"),
        );
        headers.insert(
            "Referer",
            HeaderValue::from_str(&format!("{}/", base_url))?,
        );
        headers.insert(
            "Origin",
            HeaderValue::from_str(base_url.rsplitn(3, '/').nth(1).unwrap_or(base_url))?,
        );
        headers.insert(
            "User-Agent",
            HeaderValue::from_static("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"),
        );
        headers.insert("Content-Type", HeaderValue::from_static("application/json"));

        let client = reqwest::Client::builder()
            .default_headers(headers)
            .timeout(std::time::Duration::from_secs(120))
            .build()?;

        Ok(LlmClient {
            base_url: base_url.trim_end_matches('/').to_string(),
            model: model.to_string(),
            client,
            stream: true,
        })
    }

    pub fn model(&self) -> &str {
        &self.model
    }

    fn build_body(
        &self,
        system_prompt: &str,
        tools: &[ToolDef],
        history: &[Message],
    ) -> Value {
        let mut messages: Vec<Value> = Vec::new();
        if !system_prompt.is_empty() {
            messages.push(json!({"role": "system", "content": system_prompt}));
        }
        for m in history {
            messages.push(serde_json::to_value(m).unwrap_or(Value::Null));
        }

        let mut body = json!({
            "model": self.model,
            "messages": messages,
            "stream": self.stream,
            "reasoning_format": "auto",
            "reasoning_control": true,
            "backend_sampling": false,
            "timings_per_token": true,
            "return_progress": true,
            "chat_template_kwargs": {"enable_thinking": false},
        });

        if !tools.is_empty() {
            let tool_schemas: Vec<Value> = tools
                .iter()
                .map(|t| {
                    json!({
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                    })
                })
                .collect();
            body["tools"] = Value::Array(tool_schemas);
            body["tool_choice"] = json!("auto");
        }

        if self.stream {
            body["stream_options"] = json!({"include_usage": true});
        }

        body
    }

    /// Envia o pedido e retorna a resposta completa (não-streaming).
    pub async fn chat(
        &self,
        system_prompt: &str,
        tools: &[ToolDef],
        history: &[Message],
    ) -> Result<LlmResponse> {
        let body = self.build_body(system_prompt, tools, history);
        let url = format!("{}/chat/completions", self.base_url);
        let resp = self.client.post(&url).json(&body).send().await?;

        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow!("LLM HTTP {}: {}", status, text));
        }
        let data: Value = resp.json().await?;
        parse_completion(data)
    }

    /// Envia o pedido em modo streaming, chamando `on_delta` a cada trecho.
    pub async fn chat_stream<F>(
        &self,
        system_prompt: &str,
        tools: &[ToolDef],
        history: &[Message],
        on_delta: F,
    ) -> Result<LlmResponse>
    where
        F: FnMut(&str) + Send,
    {
        let body = self.build_body(system_prompt, tools, history);
        let url = format!("{}/chat/completions", self.base_url);
        let resp = self.client.post(&url).json(&body).send().await?;

        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            return Err(anyhow!("LLM HTTP {}: {}", status, text));
        }

        let mut on_delta = on_delta;
        let mut content = String::new();
        let mut reasoning = String::new();
        let mut tool_calls: Vec<ToolCall> = Vec::new();
        let mut finish_reason = String::new();

        let mut stream = resp.bytes_stream();
        let mut buf = String::new();
        while let Some(chunk) = stream.next().await {
            let chunk = chunk?;
            buf.push_str(&String::from_utf8_lossy(&chunk));
            // Processa linhas completas
            while let Some(pos) = buf.find('\n') {
                let line = buf[..pos].to_string();
                buf = buf[pos + 1..].to_string();
                let line = line.trim();
                if !line.starts_with("data:") {
                    continue;
                }
                let data_str = line[5..].trim();
                if data_str.is_empty() || data_str == "[DONE]" {
                    continue;
                }
                let Ok(data) = serde_json::from_str::<Value>(data_str) else {
                    continue;
                };
                for choice in data["choices"].as_array().into_iter().flatten() {
                    if let Some(delta) = choice.get("delta") {
                        if let Some(r) = delta.get("reasoning_content").and_then(Value::as_str) {
                            reasoning.push_str(r);
                        }
                        if let Some(c) = delta.get("content").and_then(Value::as_str) {
                            content.push_str(c);
                            on_delta(c);
                        }
                        if let Some(tcs) = delta.get("tool_calls").and_then(Value::as_array) {
                            for tc in tcs {
                                let idx = tc.get("index").and_then(Value::as_u64).unwrap_or(0) as usize;
                                let func = tc.get("function").unwrap_or(&Value::Null);
                                let name = func.get("name").and_then(Value::as_str).unwrap_or("");
                                let args = func.get("arguments").and_then(Value::as_str).unwrap_or("");
                                let tc_id = tc.get("id").and_then(Value::as_str).unwrap_or("");

                                while tool_calls.len() <= idx {
                                    tool_calls.push(ToolCall::new("", "", ""));
                                }
                                if !name.is_empty() {
                                    tool_calls[idx].function.name.push_str(name);
                                }
                                if !args.is_empty() {
                                    tool_calls[idx].function.arguments.push_str(args);
                                }
                                if !tc_id.is_empty() {
                                    tool_calls[idx].id.push_str(tc_id);
                                }
                            }
                        }
                    }
                    if let Some(fr) = choice.get("finish_reason").and_then(Value::as_str) {
                        finish_reason = fr.to_string();
                    }
                }
            }
        }

        Ok(LlmResponse {
            content: if content.is_empty() { None } else { Some(content) },
            tool_calls,
            finish_reason,
        })
    }
}

/// Interpreta uma resposta não-streaming `chat.completion`.
fn parse_completion(data: Value) -> Result<LlmResponse> {
    let choice = data["choices"][0].clone();
    if choice.is_null() {
        return Err(anyhow!("LLM retornou resposta sem choices: {}", data));
    }
    let message = choice.get("message").unwrap_or(&Value::Null);
    let content = message.get("content").and_then(Value::as_str).map(String::from);
    let finish_reason = choice
        .get("finish_reason")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    let mut tool_calls = Vec::new();
    if let Some(tcs) = message.get("tool_calls").and_then(Value::as_array) {
        for tc in tcs {
            let func = tc.get("function").unwrap_or(&Value::Null);
            tool_calls.push(ToolCall::new(
                tc.get("id").and_then(Value::as_str).unwrap_or(""),
                func.get("name").and_then(Value::as_str).unwrap_or(""),
                func.get("arguments").and_then(Value::as_str).unwrap_or(""),
            ));
        }
    }

    Ok(LlmResponse {
        content,
        tool_calls,
        finish_reason,
    })
}

/// Alias de conveniência usado nos callers que querem manter um Arc compartilhado.
pub type SharedLlm = Arc<LlmClient>;
