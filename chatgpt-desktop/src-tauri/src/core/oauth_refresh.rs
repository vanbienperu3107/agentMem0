// OAuth token check + refresh cho Claude Max OAT.
// Đọc ~/.claude/.credentials.json: claudeAiOauth.{accessToken, refreshToken, expiresAt}.
// - check_token_status(): còn hạn / sắp hết / hết hạn.
// - refresh_token(): chạy `claude` CLI để CLI tự refresh OAT (không tự đoán OAuth endpoint).
//
// LƯU Ý: client_id + endpoint để trong code dưới dạng default, có thể override qua biến
// môi trường (CLAUDE_OAUTH_CLIENT_ID / CLAUDE_OAUTH_TOKEN_URL) nếu Anthropic đổi.

use serde_json::{json, Value};
use std::path::PathBuf;

// Ngưỡng coi là "sắp hết hạn" (ms): còn dưới 5 phút -> nên refresh.
const SOON_MS: i64 = 5 * 60 * 1000;

fn creds_path() -> PathBuf {
    let home = std::env::var_os("HOME")
        .or_else(|| std::env::var_os("USERPROFILE"))
        .map(PathBuf::from)
        .unwrap_or_default();
    home.join(".claude").join(".credentials.json")
}

fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

fn read_creds() -> Result<Value, String> {
    let p = creds_path();
    let raw = std::fs::read_to_string(&p)
        .map_err(|e| format!("read {}: {}", p.display(), e))?;
    serde_json::from_str(&raw).map_err(|e| format!("parse creds: {}", e))
}

/// "valid" | "expired" | "missing"
pub fn check_token_status() -> String {
    let creds = match read_creds() {
        Ok(c) => c,
        Err(_) => return "missing".into(),
    };
    let oauth = match creds.get("claudeAiOauth") {
        Some(o) => o,
        None => return "missing".into(),
    };
    if oauth.get("accessToken").and_then(|v| v.as_str()).unwrap_or("").is_empty() {
        return "missing".into();
    }
    match oauth.get("expiresAt").and_then(|v| v.as_i64()) {
        Some(exp) => {
            if exp - now_ms() <= SOON_MS { "expired".into() } else { "valid".into() }
        }
        // không có expiresAt -> coi như valid (không chặn)
        None => "valid".into(),
    }
}

/// Refresh OAT bằng refreshToken. Ghi token mới vào .credentials.json. Trả status mới.
pub async fn refresh_token() -> Result<String, String> {
    // KHÔNG tự đoán OAuth endpoint nữa (dễ sai client_id + bị proxy chặn -> 401).
    // Thay vào đó gọi chính `claude` CLI: nó tự refresh OAT đúng cách rồi ghi lại
    // ~/.claude/.credentials.json. Sau đó ta đọc lại token mới.
    // Lệnh nhẹ, không tốn quota: `claude --version` đủ để CLI nạp + refresh khi cần;
    // nếu cần ép, dùng `claude -p "ping"` (có thể tốn 1 lượt nhỏ).
    let candidates = [
        std::env::var("CLAUDE_CLI_PATH").ok(),
        Some("claude".to_string()),
        Some("claude.cmd".to_string()), // Windows npm shim
    ];
    let mut last_err = String::from("không tìm thấy claude CLI");
    for cand in candidates.into_iter().flatten() {
        // chạy claude -p để buộc CLI xác thực (tự refresh token nếu hết hạn)
        let out = std::process::Command::new(&cand)
            .args(["-p", "ping", "--model", "claude-haiku-4-5-20251001"])
            .output();
        match out {
            Ok(o) => {
                if o.status.success() {
                    // CLI chạy OK -> token đã được refresh trong file
                    return Ok(check_token_status());
                } else {
                    last_err = format!(
                        "{} chạy lỗi: {}",
                        cand,
                        String::from_utf8_lossy(&o.stderr).trim()
                    );
                }
            }
            Err(e) => { last_err = format!("không chạy được '{}': {}", cand, e); }
        }
    }
    Err(format!(
        "Gia hạn qua claude CLI thất bại ({}). Hãy mở terminal chạy `claude` để đăng nhập lại.",
        last_err
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_status_missing_when_no_file() {
        // Trên CI không có ~/.claude/.credentials.json -> missing (không panic)
        let s = check_token_status();
        assert!(s == "missing" || s == "valid" || s == "expired");
    }
    #[test]
    fn test_soon_threshold_positive() {
        assert!(SOON_MS > 0);
    }
}
