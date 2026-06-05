#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod core;
use core::{cmd, history::{HistoryState, LoggedMessage}, setup, window};
use simplelog::{ColorChoice, CombinedLogger, ConfigBuilder, LevelFilter, TermLogger, TerminalMode, WriteLogger};
use std::fs::OpenOptions;
use tauri::{Emitter, Listener, Manager};

fn init_logger() {
    let log_dir = {
        let exe = std::env::current_exe().ok();
        let exe_dir = exe.as_ref().and_then(|p| p.parent()).map(|p| p.to_path_buf());

        let portable_data = exe_dir
            .as_ref()
            .map(|d| d.join("data").join("com.nofwl.chatgpt").join("logs"));
        let portable_works = portable_data
            .as_ref()
            .map(|d| std::fs::create_dir_all(d).is_ok())
            .unwrap_or(false);

        if portable_works {
            portable_data.unwrap()
        } else {
            let base = std::env::var("APPDATA")
                .ok()
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| std::path::PathBuf::from("."));
            let p = base.join("com.nofwl.chatgpt").join("logs");
            let _ = std::fs::create_dir_all(&p);
            p
        }
    };

    let log_file = log_dir.join("app.log");
    let cfg = ConfigBuilder::new()
        .set_time_format_rfc3339()
        .set_target_level(LevelFilter::Error)
        .build();

    let mut loggers: Vec<Box<dyn simplelog::SharedLogger>> = vec![
        TermLogger::new(LevelFilter::Info, cfg.clone(), TerminalMode::Mixed, ColorChoice::Auto),
    ];
    if let Ok(file) = OpenOptions::new().create(true).append(true).open(&log_file) {
        loggers.push(WriteLogger::new(LevelFilter::Debug, cfg, file));
    }
    let _ = CombinedLogger::init(loggers);
    log::info!("===== ChatGPT Desktop starting =====");
    log::info!("[logger] file = {}", log_file.display());
}

fn main() {
    init_logger();

    tauri::Builder::default()
        .plugin(tauri_plugin_os::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(HistoryState::default())
        .invoke_handler(tauri::generate_handler![
            cmd::view_reload,
            cmd::view_url,
            cmd::view_go_forward,
            cmd::view_go_back,
            cmd::set_view_ask,
            cmd::get_app_conf,
            cmd::window_pin,
            cmd::ask_sync,
            cmd::ask_send,
            cmd::set_theme,
            cmd::log_message,
            cmd::compact_session,
            cmd::get_instruction,
            cmd::get_keywords,
            cmd::summarize_current,
            cmd::check_oauth_status,
            cmd::refresh_oauth,
            cmd::test_provider_key,
            cmd::check_config_status,
            cmd::open_config_dir,
            window::open_settings,
        ])
        .setup(|app| {
            log::info!("[app] exe = {:?}", std::env::current_exe().ok());
            log::info!("[app] version = {}", env!("CARGO_PKG_VERSION"));

            let handle = app.handle().clone();
            let state = handle.state::<HistoryState>();
            match core::history::init_session(&handle, &state) {
                Ok(meta) => log::info!("[app] init_session OK: {}", meta.session_id),
                Err(e) => log::error!("[app] init_session failed: {}", e),
            }

            // ===== CRASH RECOVERY: retry các upload còn pending từ lần chạy trước =====
            if let Ok(root) = core::history::root_dir(&handle) {
                tauri::async_runtime::spawn(async move {
                    core::sync::recover_pending_uploads(root).await;
                });
            }

            let log_handle = app.handle().clone();
            app.listen_any("chat-logger://log-message", move |event| {
                #[derive(serde::Deserialize)]
                struct Payload {
                    id: String,
                    #[serde(rename = "conversationId")]
                    conversation_id: String,
                    role: String,
                    content: String,
                }
                match serde_json::from_str::<Payload>(event.payload()) {
                    Ok(p) => {
                        let captured_at = std::time::SystemTime::now()
                            .duration_since(std::time::UNIX_EPOCH)
                            .map(|d| d.as_secs())
                            .unwrap_or(0);
                        let msg = LoggedMessage {
                            id: p.id,
                            conversation_id: p.conversation_id,
                            role: p.role,
                            content: p.content,
                            captured_at,
                        };
                        let state = log_handle.state::<HistoryState>();
                        if let Err(e) = core::history::log_message(&log_handle, &state, msg) {
                            log::error!("[event] log_message failed: {}", e);
                        }
                    }
                    Err(e) => log::error!("[event] log-message payload parse failed: {}", e),
                }
            });

            let compact_handle = app.handle().clone();
            app.listen_any("chat-logger://compact", move |_event| {
                log::info!("[event] compact triggered from frontend");
                let state = compact_handle.state::<HistoryState>();
                match core::history::compact_session(&compact_handle, &state, "compact") {
                    Ok(Some(p)) => {
                        log::info!("[event] compact OK: {}", p.display());
                        let _ = compact_handle.emit("chat-logger://result",
                            serde_json::json!({"action":"compact","ok":true,"msg":"Đã lưu full session"}));
                        // ===== ASYNC SYNC: đẩy file session lên memory server =====
                        if let Ok(root) = core::history::root_dir(&compact_handle) {
                            let session_path = p.clone();
                            tauri::async_runtime::spawn(async move {
                                core::sync::upload_session_file(root, session_path).await;
                            });
                        }
                    }
                    Ok(None) => {
                        log::info!("[event] compact: empty buffer, only rotated");
                        let _ = compact_handle.emit("chat-logger://result",
                            serde_json::json!({"action":"compact","ok":true,"msg":"Không có nội dung mới để lưu"}));
                    }
                    Err(e) => {
                        log::error!("[event] compact failed: {}", e);
                        let _ = compact_handle.emit("chat-logger://result",
                            serde_json::json!({"action":"compact","ok":false,"msg":format!("Lỗi lưu: {}", e)}));
                    }
                }
            });

            // Summarize event từ frontend keyword
            let sum_handle = app.handle().clone();
            app.listen_any("chat-logger://summarize_current", move |_event| {
                log::info!("[event] summarize_current triggered");
                let h = sum_handle.clone();
                tauri::async_runtime::spawn(async move {
                    let state = h.state::<HistoryState>();
                    match crate::core::cmd::summarize_current_impl(&h, &state).await {
                        Ok((text, ai)) => {
                            log::info!("[event] summarize OK: {} chars (AI: {})", text.len(), ai);
                            let _ = h.emit("chat-logger://result",
                                serde_json::json!({"action":"summarize","ok":true,
                                    "msg":format!("Đã lưu summary vào mem0 (AI: {})", ai)}));
                        }
                        Err(e) => {
                            log::error!("[event] summarize failed: {}", e);
                            let _ = h.emit("chat-logger://result",
                                serde_json::json!({"action":"summarize","ok":false,"msg":format!("Lỗi tóm tắt: {}", e)}));
                        }
                    }
                });
            });
            // Đọc lịch sử: keyword /lichsu từ frontend -> Rust gọi archive-api (CSP-safe)
            let hist_handle = app.handle().clone();
            app.listen_any("chat-logger://fetch-history", move |event| {
                log::info!("[event] fetch-history triggered");
                let h = hist_handle.clone();
                // đọc query từ payload {"query": "..."} (rỗng nếu không có)
                let query = serde_json::from_str::<serde_json::Value>(event.payload())
                    .ok()
                    .and_then(|v| v.get("query").and_then(|q| q.as_str()).map(|s| s.to_string()))
                    .unwrap_or_default();
                tauri::async_runtime::spawn(async move {
                    let root = match core::history::root_dir(&h) {
                        Ok(r) => r,
                        Err(e) => {
                            let _ = h.emit("chat-logger://history-result",
                                serde_json::json!({"ok":false,"msg":format!("root_dir: {}", e)}));
                            return;
                        }
                    };
                    match core::sync::fetch_sessions(root, query, 5).await {
                        Ok(data) => {
                            let _ = h.emit("chat-logger://history-result",
                                serde_json::json!({"ok":true,"sessions":data}));
                        }
                        Err(e) => {
                            log::error!("[event] fetch-history failed: {}", e);
                            let _ = h.emit("chat-logger://history-result",
                                serde_json::json!({"ok":false,"msg":e}));
                        }
                    }
                });
            });

            // Xem full transcript 1 phiên theo id: keyword "xemphien <id>"
            let detail_handle = app.handle().clone();
            app.listen_any("chat-logger://fetch-session-detail", move |event| {
                let h = detail_handle.clone();
                let sid = serde_json::from_str::<serde_json::Value>(event.payload())
                    .ok()
                    .and_then(|v| v.get("id").and_then(|q| q.as_str()).map(|s| s.to_string()))
                    .unwrap_or_default();
                if sid.is_empty() {
                    let _ = h.emit("chat-logger://session-detail-result",
                        serde_json::json!({"ok":false,"msg":"thiếu id phiên"}));
                    return;
                }
                tauri::async_runtime::spawn(async move {
                    let root = match core::history::root_dir(&h) {
                        Ok(r) => r,
                        Err(e) => { let _ = h.emit("chat-logger://session-detail-result",
                            serde_json::json!({"ok":false,"msg":format!("root_dir: {}", e)})); return; }
                    };
                    match core::sync::fetch_session_detail(root, sid).await {
                        Ok(data) => { let _ = h.emit("chat-logger://session-detail-result",
                            serde_json::json!({"ok":true,"session":data})); }
                        Err(e) => { let _ = h.emit("chat-logger://session-detail-result",
                            serde_json::json!({"ok":false,"msg":e})); }
                    }
                });
            });

            // OAuth: kiểm tra trạng thái token (frontend gọi lúc start qua event)
            let oauth_check_handle = app.handle().clone();
            app.listen_any("chat-logger://check-oauth", move |_event| {
                let h = oauth_check_handle.clone();
                let status = core::oauth_refresh::check_token_status();
                log::info!("[event] check-oauth -> {}", status);
                let _ = h.emit("chat-logger://oauth-status",
                    serde_json::json!({"status": status}));
            });

            // OAuth: tự gia hạn token bằng refreshToken
            let oauth_refresh_handle = app.handle().clone();
            app.listen_any("chat-logger://refresh-oauth", move |_event| {
                let h = oauth_refresh_handle.clone();
                tauri::async_runtime::spawn(async move {
                    match core::oauth_refresh::refresh_token().await {
                        Ok(status) => {
                            log::info!("[event] refresh-oauth OK -> {}", status);
                            let _ = h.emit("chat-logger://oauth-status",
                                serde_json::json!({"status": status, "refreshed": true,
                                    "msg": "Đã gia hạn token thành công"}));
                        }
                        Err(e) => {
                            log::error!("[event] refresh-oauth failed: {}", e);
                            let _ = h.emit("chat-logger://oauth-status",
                                serde_json::json!({"status": "expired", "refreshed": false,
                                    "msg": format!("Gia hạn thất bại: {}", e)}));
                        }
                    }
                });
            });

            setup::init(app)
        })
        .build(tauri::generate_context!())
        .expect("error while building lencx/ChatGPT application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                log::info!("[app] exit requested, auto-compacting");
                let state = app_handle.state::<HistoryState>();
                match core::history::compact_session(app_handle, &state, "app_exit") {
                    Ok(Some(p)) => {
                        log::info!("[app] auto-compact OK: {}", p.display());
                        // SYNC enqueue (chỉ ghi marker, không HTTP để không trì hoãn exit).
                        // Recovery sẽ retry ở lần khởi động kế tiếp.
                        if let Ok(root) = core::history::root_dir(app_handle) {
                            core::sync::enqueue_session_for_upload(&root, &p);
                        }
                    }
                    Ok(None) => log::info!("[app] auto-compact: empty buffer"),
                    Err(e) => log::error!("[app] auto-compact failed: {}", e),
                }
            }
        });
}
