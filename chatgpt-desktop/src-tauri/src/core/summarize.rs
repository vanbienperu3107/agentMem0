// Multi-provider summarize module (v0.4.0)
// Supports: claude_oat, anthropic, openai (universal Chat Completions format)

use log::{debug, error, info, warn};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Deserialize, Clone, Debug)]
pub struct SummarizeConfig {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub auto_on_compact: bool,
    pub active_provider: String,
    #[serde(default)]
    pub output: OutputConfig,
    pub providers: HashMap<String, Provider>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct OutputConfig {
    #[serde(default = "default_true")]
    pub save_to_session_json: bool,
    #[serde(default)]
    pub save_separate_file: bool,
}

impl Default for OutputConfig {
    fn default() -> Self {
        Self { save_to_session_json: true, save_separate_file: false }
    }
}

fn default_true() -> bool { true }
fn default_max_tokens() -> u32 { 1024 }
fn default_anthropic_endpoint() -> String { "https://api.anthropic.com/v1/messages".into() }
fn default_openai_endpoint() -> String { "https://api.openai.com/v1/chat/completions".into() }
fn default_claude_creds() -> String { "~/.claude/.credentials.json".into() }

#[derive(Deserialize, Clone, Debug)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Provider {
    ClaudeOat {
        #[serde(default = "default_claude_creds")]
        credentials_path: String,
        model: String,
        #[serde(default = "default_max_tokens")]
        max_tokens: u32,
        #[serde(default)]
        system_prompt: String,
    },
    Anthropic {
        #[serde(default = "default_anthropic_endpoint")]
        endpoint: String,
        api_key: String,
        model: String,
        #[serde(default = "default_max_tokens")]
        max_tokens: u32,
        #[serde(default)]
        system_prompt: String,
    },
    Openai {
        #[serde(default = "default_openai_endpoint")]
        endpoint: String,
        api_key: String,
        model: String,
        #[serde(default)]
        max_tokens: Option<u32>,
        #[serde(default)]
        max_completion_tokens: Option<u32>,
        #[serde(default)]
        temperature: Option<f32>,
        #[serde(default)]
        system_prompt: String,
    },
}

fn expand_path(path: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE")) {
            return PathBuf::from(home).join(rest);
        }
    }
    PathBuf::from(path)
}

fn resolve_env(s: &str) -> String {
    if let Some(rest) = s.strip_prefix("${env:").and_then(|x| x.strip_suffix("}")) {
        std::env::var(rest).unwrap_or_default()
    } else {
        s.to_string()
    }
}

/// v0.8.6: Inspect summarize.json
impl Provider {
    pub fn model(&self) -> &str {
        match self {
            Provider::ClaudeOat { model, .. } => model,
            Provider::Anthropic { model, .. } => model,
            Provider::Openai { model, .. } => model,
        }
    }
}

pub fn inspect_config(root: &Path) -> (String, String) {
    let path = root.join("summarize.json");
    if !path.exists() {
        return ("no_file".into(), format!("summarize.json không tồn tại tại {}", path.display()));
    }
    let raw = match fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) => return ("read_error".into(), format!("Không đọc được summarize.json: {}", e)),
    };
    let cfg: SummarizeConfig = match serde_json::from_str(&raw) {
        Ok(c) => c,
        Err(e) => return ("parse_error".into(), format!("summarize.json JSON sai: {}", e)),
    };
    if !cfg.enabled {
        return ("disabled".into(), "summarize.json: enabled=false".into());
    }
    let active_ready = match cfg.providers.get(&cfg.active_provider) {
        Some(Provider::ClaudeOat { credentials_path, .. }) => expand_path(credentials_path).exists(),
        Some(Provider::Anthropic { api_key, .. }) => !resolve_env(api_key).is_empty(),
        Some(Provider::Openai { api_key, .. }) => !resolve_env(api_key).is_empty(),
        None => false,
    };
    let fb = find_paid_fallback(&cfg, &cfg.active_provider);
    if active_ready {
        ("ready".into(), format!("OK · active={}", cfg.active_provider))
    } else if fb.is_some() {
        ("fallback_only".into(), format!("Active '{}' thiếu key, có fallback '{}'", cfg.active_provider, fb.unwrap()))
    } else {
        ("no_key".into(), format!("Không provider nào có key (active='{}' thiếu)", cfg.active_provider))
    }
}

pub fn load_config(root: &Path) -> Option<SummarizeConfig> {
    let path = root.join("summarize.json");
    if !path.exists() {
        return None;
    }
    match fs::read_to_string(&path) {
        Ok(content) => match serde_json::from_str::<SummarizeConfig>(&content) {
            Ok(cfg) => {
                if cfg.enabled {
                    info!("[summarize] loaded config, active_provider={}", cfg.active_provider);
                    Some(cfg)
                } else {
                    debug!("[summarize] disabled in config");
                    None
                }
            }
            Err(e) => {
                error!("[summarize] parse failed: {}", e);
                None
            }
        },
        Err(e) => {
            error!("[summarize] read failed: {}", e);
            None
        }
    }
}

/// Nhãn AI dễ đọc cho thông báo: "<tên model> (qua <provider>)".
pub fn provider_label(cfg: &SummarizeConfig, name: &str) -> String {
    match cfg.providers.get(name) {
        Some(Provider::ClaudeOat { model, .. }) => format!("{} (Claude Max)", model),
        Some(Provider::Anthropic { model, .. }) => format!("{} (Anthropic API)", model),
        Some(Provider::Openai { model, endpoint, .. }) => {
            let host = endpoint.split('/').nth(2).unwrap_or("openai");
            format!("{} (qua {})", model, host)
        }
        None => name.to_string(),
    }
}

/// Tìm provider OpenAI/Anthropic có api_key (khác provider hiện tại) để fallback khi lỗi.
pub fn find_paid_fallback(cfg: &SummarizeConfig, exclude: &str) -> Option<String> {
    for (name, prov) in &cfg.providers {
        if name == exclude { continue; }
        match prov {
            Provider::Openai { api_key, .. } | Provider::Anthropic { api_key, .. } => {
                if !resolve_env(api_key).is_empty() {
                    return Some(name.clone());
                }
            }
            _ => {}
        }
    }
    None
}

/// Quyết định provider thực sự dùng. Nếu active_provider là claude_oat mà KHÔNG có
/// file credentials.json, tự tìm provider OpenAI/Anthropic có api_key (đã resolve env)
/// để fallback. Nếu không có -> giữ nguyên active_provider (sẽ báo lỗi rõ khi gọi).
pub fn resolve_active_provider(cfg: &SummarizeConfig) -> String {
    let active = cfg.active_provider.clone();
    let needs_creds = match cfg.providers.get(&active) {
        Some(Provider::ClaudeOat { credentials_path, .. }) => {
            !expand_path(credentials_path).exists()
        }
        _ => false,
    };
    if !needs_creds {
        return active;
    }
    log::warn!("[summarize] credentials.json không tồn tại -> tìm provider OpenAI/Anthropic fallback");
    // ưu tiên provider OpenAI/Anthropic có api_key không rỗng (sau resolve env)
    for (name, prov) in &cfg.providers {
        match prov {
            Provider::Openai { api_key, .. } | Provider::Anthropic { api_key, .. } => {
                if !resolve_env(api_key).is_empty() {
                    log::info!("[summarize] fallback sang provider '{}'", name);
                    return name.clone();
                }
            }
            _ => {}
        }
    }
    log::warn!("[summarize] không có provider fallback hợp lệ (thiếu api_key)");
    active
}

pub async fn summarize(provider: &Provider, transcript: &str) -> Result<String, String> {
    match provider {
        Provider::ClaudeOat { credentials_path, model, max_tokens, system_prompt } => {
            call_claude_oat(credentials_path, model, *max_tokens, system_prompt, transcript).await
        }
        Provider::Anthropic { endpoint, api_key, model, max_tokens, system_prompt } => {
            call_anthropic(endpoint, api_key, model, *max_tokens, system_prompt, transcript).await
        }
        Provider::Openai { endpoint, api_key, model, max_tokens, max_completion_tokens, temperature, system_prompt } => {
            call_openai(endpoint, api_key, model, max_tokens, max_completion_tokens, temperature, system_prompt, transcript).await
        }
    }
}

async fn call_claude_oat(
    credentials_path: &str,
    model: &str,
    max_tokens: u32,
    system_prompt: &str,
    transcript: &str,
) -> Result<String, String> {
    let creds_path = expand_path(credentials_path);
    let creds_content = fs::read_to_string(&creds_path)
        .map_err(|e| format!("read credentials {}: {}", creds_path.display(), e))?;
    let creds: Value = serde_json::from_str(&creds_content)
        .map_err(|e| format!("parse credentials: {}", e))?;
    let oat = creds.pointer("/claudeAiOauth/accessToken")
        .and_then(|v| v.as_str())
        .ok_or("missing claudeAiOauth.accessToken in credentials")?;

    let body = json!({
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": transcript}]
    });

    info!("[summarize] calling Claude OAT, model={}", model);
    let resp = reqwest::Client::new()
        .post("https://api.anthropic.com/v1/messages")
        .header("anthropic-version", "2023-06-01")
        .header("anthropic-beta", "oauth-2025-04-20")
        .header("Authorization", format!("Bearer {}", oat))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("request: {}", e))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Claude API error {}: {}", status, text));
    }

    let v: Value = resp.json().await.map_err(|e| format!("parse response: {}", e))?;
    v.pointer("/content/0/text")
        .and_then(|t| t.as_str())
        .map(String::from)
        .ok_or_else(|| "no text in Claude response".into())
}

async fn call_anthropic(
    endpoint: &str,
    api_key: &str,
    model: &str,
    max_tokens: u32,
    system_prompt: &str,
    transcript: &str,
) -> Result<String, String> {
    let key = resolve_env(api_key);
    if key.is_empty() {
        return Err("anthropic api_key is empty".into());
    }
    let body = json!({
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": transcript}]
    });
    info!("[summarize] calling Anthropic API, model={}", model);
    let resp = reqwest::Client::new()
        .post(endpoint)
        .header("anthropic-version", "2023-06-01")
        .header("x-api-key", key)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("request: {}", e))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Anthropic API error {}: {}", status, text));
    }
    let v: Value = resp.json().await.map_err(|e| format!("parse: {}", e))?;
    v.pointer("/content/0/text")
        .and_then(|t| t.as_str())
        .map(String::from)
        .ok_or_else(|| "no text".into())
}

#[allow(clippy::too_many_arguments)]
async fn call_openai(
    endpoint: &str,
    api_key: &str,
    model: &str,
    max_tokens: &Option<u32>,
    max_completion_tokens: &Option<u32>,
    temperature: &Option<f32>,
    system_prompt: &str,
    transcript: &str,
) -> Result<String, String> {
    let key = resolve_env(api_key);
    let mut messages = vec![];
    if !system_prompt.is_empty() {
        messages.push(json!({"role": "system", "content": system_prompt}));
    }
    messages.push(json!({"role": "user", "content": transcript}));

    let mut body = json!({
        "model": model,
        "messages": messages,
    });
    if let Some(t) = max_completion_tokens {
        body["max_completion_tokens"] = json!(t);
    } else if let Some(t) = max_tokens {
        body["max_tokens"] = json!(t);
    }
    if let Some(temp) = temperature {
        body["temperature"] = json!(temp);
    }

    info!("[summarize] calling OpenAI-compat, endpoint={}, model={}", endpoint, model);
    let resp = reqwest::Client::new()
        .post(endpoint)
        .header("Authorization", format!("Bearer {}", key))
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("request: {}", e))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("OpenAI-compat API error {}: {}", status, text));
    }
    let v: Value = resp.json().await.map_err(|e| format!("parse: {}", e))?;
    v.pointer("/choices/0/message/content")
        .and_then(|t| t.as_str())
        .map(String::from)
        .ok_or_else(|| "no content in response".into())
}

/// Build transcript text from session messages cho prompt.
pub fn build_transcript(messages: &[crate::core::history::LoggedMessage]) -> String {
    let mut out = String::with_capacity(messages.len() * 200);
    for m in messages {
        out.push_str(&format!("[{}]: {}\n\n", m.role, m.content));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_expand_path() {
        let p = expand_path("~/test");
        assert!(!p.to_string_lossy().starts_with("~/"));
    }

    #[test]
    fn test_resolve_env_no_var() {
        assert_eq!(resolve_env("plain-value"), "plain-value");
    }

    #[test]
    fn test_resolve_env_with_var() {
        std::env::set_var("TEST_VAR_X", "secret-value");
        assert_eq!(resolve_env("${env:TEST_VAR_X}"), "secret-value");
        std::env::remove_var("TEST_VAR_X");
    }

    #[test]
    fn test_parse_config_minimal() {
        let json = r#"{
            "enabled": true,
            "active_provider": "claude",
            "providers": {
                "claude": {
                    "type": "claude_oat",
                    "model": "claude-haiku-4-5"
                }
            }
        }"#;
        let cfg: SummarizeConfig = serde_json::from_str(json).unwrap();
        assert_eq!(cfg.active_provider, "claude");
    }
}
