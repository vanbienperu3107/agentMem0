// Sync module — đẩy session JSON + summary lên memory server (archive-api).
//
// Pattern: Pending Queue + Marker File.
//   1. Mỗi lần cần upload, ta GHI 1 file marker JSON vào sessions/pending/ TRƯỚC khi gọi HTTP.
//   2. Upload chạy async (tokio task) -> không block UI.
//   3. Upload thành công -> xoá marker.
//   4. Nếu app crash giữa chừng (mất điện, kill, OOM):
//        - marker còn nguyên trong pending/
//        - lần khởi động sau gọi recover_pending_uploads() để retry hết.
//
// Endpoints (xem plan-trien-khai-memory-server-mac-windows.md Bước 7.2 và 7.8):
//   POST {archive_url}/sessions
//   POST {archive_url}/compact-summaries
// Auth: header "Authorization: Bearer {token}".

use log::{debug, error, info, warn};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    path::{Path, PathBuf},
    time::Duration,
};

// ---------- Config ----------

#[derive(Deserialize, Clone, Debug)]
pub struct SyncConfig {
    #[serde(default)]
    pub enabled: bool,

    pub archive_url: String,
    pub auth_token: String,

    #[serde(default = "default_user_id")]
    pub user_id: String,

    #[serde(default)]
    pub project_tag: Option<String>,

    #[serde(default = "default_true")]
    pub upload_session_on_compact: bool,

    #[serde(default = "default_true")]
    pub upload_summary_on_sum: bool,

    #[serde(default = "default_timeout")]
    pub timeout_seconds: u64,

    #[serde(default = "default_retry_max")]
    pub retry_max: u32,

    #[serde(default = "default_backoff")]
    pub retry_backoff_seconds: u64,
}

fn default_user_id() -> String { "default".into() }
fn default_true() -> bool { true }
fn default_timeout() -> u64 { 30 }
fn default_retry_max() -> u32 { 3 }
fn default_backoff() -> u64 { 5 }

/// v0.8.6: Inspect chi tiết — trả (status_code, detail_msg).
pub fn inspect_config(root_dir: &Path) -> (String, String) {
    let path = root_dir.join("sync.json");
    if !path.exists() {
        return ("no_file".into(), format!("sync.json không tồn tại tại {}", path.display()));
    }
    let raw = match fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) => return ("read_error".into(), format!("Không đọc được sync.json: {}", e)),
    };
    let cfg: SyncConfig = match serde_json::from_str(&raw) {
        Ok(c) => c,
        Err(e) => return ("parse_error".into(), format!("sync.json JSON sai: {}", e)),
    };
    if !cfg.enabled {
        return ("disabled".into(), "sync.json: enabled=false (sửa thành true)".into());
    }
    let token = resolve_env(&cfg.auth_token);
    if token.is_empty() {
        let hint = if cfg.auth_token.starts_with("${env:") {
            format!("auth_token vẫn là placeholder \"{}\" — edit sync.json thay bằng token thật", cfg.auth_token)
        } else {
            "auth_token rỗng".into()
        };
        return ("empty_token".into(), hint);
    }
    ("ready".into(), format!("OK · {} · user_id={}", cfg.archive_url, cfg.user_id))
}

/// Load + resolve ${env:VAR}. Trả None nếu file không có hoặc enabled=false.
pub fn load_config(root_dir: &Path) -> Option<SyncConfig> {
    let path = root_dir.join("sync.json");
    if !path.exists() {
        debug!("[sync] no sync.json -> disabled");
        return None;
    }
    let raw = match fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) => { error!("[sync] read {}: {}", path.display(), e); return None; }
    };
    let mut cfg: SyncConfig = match serde_json::from_str(&raw) {
        Ok(c) => c,
        Err(e) => { error!("[sync] parse sync.json: {}", e); return None; }
    };
    if !cfg.enabled {
        debug!("[sync] enabled=false");
        return None;
    }
    cfg.auth_token = resolve_env(&cfg.auth_token);
    cfg.archive_url = cfg.archive_url.trim_end_matches('/').to_string();
    if cfg.auth_token.is_empty() {
        warn!("[sync] auth_token rỗng (env var chưa set?) -> skip");
        return None;
    }
    info!("[sync] config loaded: archive_url={}, user_id={}, project_tag={:?}",
          cfg.archive_url, cfg.user_id, cfg.project_tag);
    Some(cfg)
}

/// Resolve ${env:NAME} -> std::env::var(NAME). Nếu không match pattern, trả nguyên.
fn resolve_env(input: &str) -> String {
    let t = input.trim();
    if let Some(name) = t.strip_prefix("${env:").and_then(|s| s.strip_suffix('}')) {
        std::env::var(name.trim()).unwrap_or_default()
    } else {
        t.to_string()
    }
}

// ---------- Pending marker ----------

#[derive(Serialize, Deserialize, Debug)]
struct PendingMarker {
    id: String,
    kind: String,      // "session" | "summary"
    endpoint: String,  // "/sessions" | "/compact-summaries"
    payload: Value,
    created_at: String,
    #[serde(default)]
    attempts: u32,
    #[serde(default)]
    last_error: Option<String>,
}

fn pending_dir(root_dir: &Path) -> PathBuf {
    root_dir.join("sessions").join("pending")
}

fn write_marker(root_dir: &Path, marker: &PendingMarker) -> Result<PathBuf, String> {
    let dir = pending_dir(root_dir);
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join(format!("pending_{}_{}.json", marker.kind, marker.id));
    let body = serde_json::to_string_pretty(marker).map_err(|e| e.to_string())?;
    fs::write(&path, body).map_err(|e| e.to_string())?;
    debug!("[sync] marker written: {}", path.display());
    Ok(path)
}

fn delete_marker(path: &Path) {
    if let Err(e) = fs::remove_file(path) {
        warn!("[sync] xoá marker {} fail: {}", path.display(), e);
    } else {
        debug!("[sync] marker removed: {}", path.display());
    }
}

fn update_marker_failure(path: &Path, attempts: u32, err: &str) {
    if let Ok(raw) = fs::read_to_string(path) {
        if let Ok(mut m) = serde_json::from_str::<PendingMarker>(&raw) {
            m.attempts = attempts;
            m.last_error = Some(err.to_string());
            if let Ok(s) = serde_json::to_string_pretty(&m) {
                let _ = fs::write(path, s);
            }
        }
    }
}

// ---------- HTTP ----------

async fn http_post(
    cfg: &SyncConfig,
    endpoint: &str,
    payload: &Value,
) -> Result<(), String> {
    let url = format!("{}{}", cfg.archive_url, endpoint);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(cfg.timeout_seconds))
        .build()
        .map_err(|e| format!("build client: {}", e))?;

    let resp = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", cfg.auth_token))
        .header("Content-Type", "application/json")
        .json(payload)
        .send()
        .await
        .map_err(|e| format!("POST {} fail: {}", url, e))?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("HTTP {} -> {}", status, body));
    }
    debug!("[sync] POST {} OK ({})", url, status);
    Ok(())
}


/// GET archive-api, trả JSON. Dùng cho đọc lịch sử (CSP của chatgpt.com chặn fetch
/// trực tiếp từ webview, nên đọc phải đi qua Rust).
async fn http_get(cfg: &SyncConfig, endpoint: &str) -> Result<Value, String> {
    let url = format!("{}{}", cfg.archive_url, endpoint);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(cfg.timeout_seconds))
        .build()
        .map_err(|e| format!("build client: {}", e))?;
    let resp = client
        .get(&url)
        .header("Authorization", format!("Bearer {}", cfg.auth_token))
        .send()
        .await
        .map_err(|e| format!("GET {} fail: {}", url, e))?;
    let status = resp.status();
    let body = resp.text().await.unwrap_or_default();
    if !status.is_success() {
        return Err(format!("HTTP {} -> {}", status, body));
    }
    serde_json::from_str(&body).map_err(|e| format!("parse JSON: {}", e))
}

/// Tìm session theo điều kiện (q). q rỗng -> 5 phiên gần nhất. Dùng /sessions?q=..
pub async fn fetch_sessions(root_dir: PathBuf, query: String, limit: u32) -> Result<Value, String> {
    let cfg = load_config(&root_dir).ok_or_else(|| "sync.json chưa bật/cấu hình".to_string())?;
    let q = query.trim();
    // Không có query -> 5 phiên gần nhất.
    if q.is_empty() {
        let ep = format!("/sessions?user_id={}&limit={}", cfg.user_id, limit);
        return http_get(&cfg, &ep).await;
    }
    let enc = q.replace(' ', "%20");
    // TÌM THÔNG MINH: semantic trước (theo ý nghĩa). Nếu lỗi (server chưa bật OpenAI)
    // hoặc rỗng -> fallback keyword ILIKE trên summary.
    let sem_ep = format!("/sessions/search-semantic?user_id={}&q={}&limit={}", cfg.user_id, enc, limit);
    if let Ok(v) = http_get(&cfg, &sem_ep).await {
        let empty = v.as_array().map(|a| a.is_empty()).unwrap_or(false);
        if !empty {
            return Ok(v);
        }
    }
    let kw_ep = format!("/sessions?user_id={}&q={}&limit={}", cfg.user_id, enc, limit);
    http_get(&cfg, &kw_ep).await
}

/// Lấy full transcript 1 session theo id (/sessions/{id}).
pub async fn fetch_session_detail(root_dir: PathBuf, session_id: String) -> Result<Value, String> {
    let cfg = load_config(&root_dir).ok_or_else(|| "sync.json chưa bật/cấu hình".to_string())?;
    http_get(&cfg, &format!("/sessions/{}", session_id)).await
}

/// Loop retry với backoff. Trả Err sau khi hết retry_max.
async fn http_post_with_retry(
    cfg: &SyncConfig,
    endpoint: &str,
    payload: &Value,
    marker_path: Option<&Path>,
) -> Result<(), String> {
    let mut last_err = String::new();
    for attempt in 1..=cfg.retry_max {
        match http_post(cfg, endpoint, payload).await {
            Ok(()) => return Ok(()),
            Err(e) => {
                warn!("[sync] {} attempt {}/{} fail: {}", endpoint, attempt, cfg.retry_max, e);
                last_err = e.clone();
                if let Some(p) = marker_path {
                    update_marker_failure(p, attempt, &e);
                }
                if attempt < cfg.retry_max {
                    tokio::time::sleep(Duration::from_secs(cfg.retry_backoff_seconds)).await;
                }
            }
        }
    }
    Err(last_err)
}

// ---------- Payload builders ----------

/// Build payload cho POST /sessions từ file session JSON đã ghi.
fn build_session_payload(cfg: &SyncConfig, session_file_path: &Path) -> Result<Value, String> {
    let raw = fs::read_to_string(session_file_path).map_err(|e| e.to_string())?;
    let session: Value = serde_json::from_str(&raw).map_err(|e| e.to_string())?;

    let started_at_iso = session.get("started_at_iso").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let exported_at_iso = session.get("exported_at_iso").and_then(|v| v.as_str()).map(|s| s.to_string());
    let message_count = session.get("message_count").and_then(|v| v.as_u64()).unwrap_or(0);
    let messages = session.get("messages").cloned().unwrap_or(json!([]));
    let instruction = session.get("instruction").and_then(|v| v.as_str()).map(|s| s.to_string());
    let session_id = session.get("session_id").and_then(|v| v.as_str()).unwrap_or("").to_string();

    let mut metadata = json!({
        "local_session_id": session_id,
        "source": "chatgpt-desktop",
        "filename": session_file_path.file_name().map(|f| f.to_string_lossy().to_string()).unwrap_or_default(),
    });
    if let Some(inst) = &instruction {
        metadata["instruction"] = json!(inst);
    }

    Ok(json!({
        "user_id": cfg.user_id,
        "project_tag": cfg.project_tag,
        "started_at": started_at_iso,
        "ended_at": exported_at_iso,
        "message_count": message_count,
        "transcript": messages,
        "summary": Value::Null,
        "workspace_path": session_file_path.parent().map(|p| p.display().to_string()),
        "metadata": metadata,
    }))
}

/// Build payload cho POST /compact-summaries từ summary text.
fn build_summary_payload(
    cfg: &SyncConfig,
    summary_text: &str,
    local_session_id: &str,
    messages_before: u32,
) -> Value {
    json!({
        "session_id": Value::Null,
        "user_id": cfg.user_id,
        "project_tag": cfg.project_tag,
        "workspace_path": Value::Null,
        "summary_text": summary_text,
        "messages_before": messages_before,
        "position_in_session": 0,
        "metadata": {
            "local_session_id": local_session_id,
            "source": "chatgpt-desktop-sum",
        }
    })
}

// ---------- Public API ----------

/// Chỉ ghi marker pending cho session (KHÔNG gọi HTTP).
/// Dùng ở ExitRequested khi app sắp đóng — recovery ở lần khởi động sau retry.
/// Hàm SYNC, return ngay.
pub fn enqueue_session_for_upload(root_dir: &Path, session_path: &Path) {
    let cfg = match load_config(root_dir) {
        Some(c) => c,
        None => return,
    };
    if !cfg.upload_session_on_compact {
        return;
    }
    let payload = match build_session_payload(&cfg, session_path) {
        Ok(p) => p,
        Err(e) => { error!("[sync] enqueue build payload fail: {}", e); return; }
    };
    let id = session_path.file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| format!("exit_{}", chrono::Local::now().timestamp()));
    let marker = PendingMarker {
        id,
        kind: "session".into(),
        endpoint: "/sessions".into(),
        payload,
        created_at: chrono::Local::now().to_rfc3339(),
        attempts: 0,
        last_error: Some("enqueued at app exit, will retry on next start".into()),
    };
    match write_marker(root_dir, &marker) {
        Ok(p) => info!("[sync] enqueued (exit) -> {}", p.display()),
        Err(e) => error!("[sync] enqueue marker fail: {}", e),
    }
}

/// Upload 1 file session JSON. Marker được tạo TRƯỚC HTTP để crash-safe.
/// Chạy trong tokio task (caller dùng tauri::async_runtime::spawn).
pub async fn upload_session_file(root_dir: PathBuf, session_path: PathBuf) {
    let cfg = match load_config(&root_dir) {
        Some(c) => c,
        None => { debug!("[sync] disabled, skip upload_session_file"); return; }
    };
    if !cfg.upload_session_on_compact {
        debug!("[sync] upload_session_on_compact=false, skip");
        return;
    }

    let payload = match build_session_payload(&cfg, &session_path) {
        Ok(p) => p,
        Err(e) => { error!("[sync] build session payload fail: {}", e); return; }
    };

    let id = session_path.file_stem()
        .map(|s| s.to_string_lossy().to_string())
        .unwrap_or_else(|| format!("{}", chrono::Local::now().timestamp_nanos_opt().unwrap_or(0)));

    let marker = PendingMarker {
        id: id.clone(),
        kind: "session".into(),
        endpoint: "/sessions".into(),
        payload: payload.clone(),
        created_at: chrono::Local::now().to_rfc3339(),
        attempts: 0,
        last_error: None,
    };
    let marker_path = match write_marker(&root_dir, &marker) {
        Ok(p) => p,
        Err(e) => { error!("[sync] write marker fail: {}", e); return; }
    };

    info!("[sync] uploading session {} ({} bytes)...",
          session_path.display(),
          serde_json::to_string(&payload).map(|s| s.len()).unwrap_or(0));

    match http_post_with_retry(&cfg, "/sessions", &payload, Some(&marker_path)).await {
        Ok(()) => {
            info!("[sync] session upload OK: {}", session_path.display());
            delete_marker(&marker_path);
        }
        Err(e) => {
            error!("[sync] session upload FAIL sau {} retry: {} -> marker giữ lại {}",
                   cfg.retry_max, e, marker_path.display());
        }
    }
}

/// Upload summary text sau /sum.
pub async fn upload_summary(
    root_dir: PathBuf,
    summary_text: String,
    local_session_id: String,
    messages_before: u32,
) {
    let cfg = match load_config(&root_dir) {
        Some(c) => c,
        None => { debug!("[sync] disabled, skip upload_summary"); return; }
    };
    if !cfg.upload_summary_on_sum {
        debug!("[sync] upload_summary_on_sum=false, skip");
        return;
    }

    let payload = build_summary_payload(&cfg, &summary_text, &local_session_id, messages_before);

    let id = format!("{}_{}",
        local_session_id,
        chrono::Local::now().format("%Y%m%d-%H%M%S"));

    let marker = PendingMarker {
        id: id.clone(),
        kind: "summary".into(),
        endpoint: "/compact-summaries".into(),
        payload: payload.clone(),
        created_at: chrono::Local::now().to_rfc3339(),
        attempts: 0,
        last_error: None,
    };
    let marker_path = match write_marker(&root_dir, &marker) {
        Ok(p) => p,
        Err(e) => { error!("[sync] write marker fail: {}", e); return; }
    };

    info!("[sync] uploading summary (session={}, {} chars)...",
          local_session_id, summary_text.len());

    match http_post_with_retry(&cfg, "/compact-summaries", &payload, Some(&marker_path)).await {
        Ok(()) => {
            info!("[sync] summary upload OK (session={})", local_session_id);
            delete_marker(&marker_path);
        }
        Err(e) => {
            error!("[sync] summary upload FAIL sau {} retry: {} -> marker giữ {}",
                   cfg.retry_max, e, marker_path.display());
        }
    }
}

/// Quét sessions/pending/ và retry hết các marker còn sót sau crash.
/// Gọi 1 lần ở startup (sau init_session).
pub async fn recover_pending_uploads(root_dir: PathBuf) {
    let cfg = match load_config(&root_dir) {
        Some(c) => c,
        None => { debug!("[sync] disabled, skip recovery"); return; }
    };

    let dir = pending_dir(&root_dir);
    if !dir.exists() {
        debug!("[sync] no pending dir, nothing to recover");
        return;
    }

    let entries = match fs::read_dir(&dir) {
        Ok(e) => e,
        Err(e) => { error!("[sync] read pending dir: {}", e); return; }
    };

    let mut pending_files: Vec<PathBuf> = entries
        .filter_map(|e| e.ok().map(|x| x.path()))
        .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("json"))
        .collect();
    pending_files.sort();

    if pending_files.is_empty() {
        debug!("[sync] no pending markers");
        return;
    }
    info!("[sync] RECOVERY: {} pending markers tìm thấy", pending_files.len());

    for path in pending_files {
        let raw = match fs::read_to_string(&path) {
            Ok(s) => s,
            Err(e) => { warn!("[sync] read marker {}: {}", path.display(), e); continue; }
        };
        let marker: PendingMarker = match serde_json::from_str(&raw) {
            Ok(m) => m,
            Err(e) => {
                warn!("[sync] marker {} corrupt ({}), xoá", path.display(), e);
                let _ = fs::remove_file(&path);
                continue;
            }
        };

        info!("[sync] retry {} (kind={}, attempts trước={})",
              path.display(), marker.kind, marker.attempts);

        match http_post_with_retry(&cfg, &marker.endpoint, &marker.payload, Some(&path)).await {
            Ok(()) => {
                info!("[sync] recovered upload OK: {}", path.display());
                delete_marker(&path);
            }
            Err(e) => {
                error!("[sync] recovered upload vẫn fail: {} -> giữ marker", e);
            }
        }
    }
}

// ---------- Tests ----------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resolve_env_literal() {
        assert_eq!(resolve_env("hello"), "hello");
    }

    #[test]
    fn test_resolve_env_var() {
        std::env::set_var("__SYNC_TEST_VAR__", "secret123");
        assert_eq!(resolve_env("${env:__SYNC_TEST_VAR__}"), "secret123");
    }

    #[test]
    fn test_resolve_env_missing() {
        std::env::remove_var("__SYNC_NOT_SET__");
        assert_eq!(resolve_env("${env:__SYNC_NOT_SET__}"), "");
    }

    #[test]
    fn test_build_summary_payload_shape() {
        let cfg = SyncConfig {
            enabled: true,
            archive_url: "http://x".into(),
            auth_token: "t".into(),
            user_id: "thanh".into(),
            project_tag: Some("pj".into()),
            upload_session_on_compact: true,
            upload_summary_on_sum: true,
            timeout_seconds: 30,
            retry_max: 3,
            retry_backoff_seconds: 5,
        };
        let p = build_summary_payload(&cfg, "hello", "s1234567", 10);
        assert_eq!(p["user_id"], "thanh");
        assert_eq!(p["project_tag"], "pj");
        assert_eq!(p["summary_text"], "hello");
        assert_eq!(p["messages_before"], 10);
        assert_eq!(p["metadata"]["local_session_id"], "s1234567");
    }
}
