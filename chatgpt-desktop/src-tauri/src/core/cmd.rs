use tauri::{command, AppHandle, LogicalPosition, Manager, PhysicalSize, State};

use crate::core::{
    conf::AppConf,
    constant::{ASK_HEIGHT, TITLEBAR_HEIGHT},
    history::{self, HistoryState, LoggedMessage},
};

#[command]
pub fn view_reload(app: AppHandle) {
    app.get_window("core")
        .unwrap()
        .get_webview("main")
        .unwrap()
        .eval("window.location.reload()")
        .unwrap();
}

#[command]
pub fn view_url(app: AppHandle) -> tauri::Url {
    app.get_window("core")
        .unwrap()
        .get_webview("main")
        .unwrap()
        .url()
        .unwrap()
}

#[command]
pub fn view_go_forward(app: AppHandle) {
    app.get_window("core")
        .unwrap()
        .get_webview("main")
        .unwrap()
        .eval("window.history.forward()")
        .unwrap();
}

#[command]
pub fn view_go_back(app: AppHandle) {
    app.get_window("core")
        .unwrap()
        .get_webview("main")
        .unwrap()
        .eval("window.history.back()")
        .unwrap();
}

#[command]
pub fn window_pin(app: AppHandle, pin: bool) {
    let conf = AppConf::load(&app).unwrap();
    conf.amend(serde_json::json!({"stay_on_top": pin}))
        .unwrap()
        .save(&app)
        .unwrap();

    app.get_window("core")
        .unwrap()
        .set_always_on_top(pin)
        .unwrap();
}

#[command]
pub fn ask_sync(app: AppHandle, message: String) {
    app.get_window("core")
        .unwrap()
        .get_webview("main")
        .unwrap()
        .eval(&format!("ChatAsk.sync({})", message))
        .unwrap();
}

#[command]
pub fn ask_send(app: AppHandle) {
    let win = app.get_window("core").unwrap();

    win.get_webview("main")
        .unwrap()
        .eval(
            r#"
        ChatAsk.submit();
        setTimeout(() => {
            __TAURI__.webview.Webview.getByLabel('ask')?.setFocus();
        }, 500);
        "#,
        )
        .unwrap();
}

#[command]
pub fn set_theme(app: AppHandle, theme: String) {
    let conf = AppConf::load(&app).unwrap();
    conf.amend(serde_json::json!({"theme": theme}))
        .unwrap()
        .save(&app)
        .unwrap();

    app.restart();
}

#[command]
pub fn get_app_conf(app: AppHandle) -> AppConf {
    AppConf::load(&app).unwrap()
}

// ---------- Chat history commands ----------

#[command]
pub fn log_message(
    app: AppHandle,
    state: State<HistoryState>,
    id: String,
    conversation_id: String,
    role: String,
    content: String,
) -> Result<(), String> {
    let captured_at = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    history::log_message(
        &app,
        &state,
        LoggedMessage {
            id,
            conversation_id,
            role,
            content,
            captured_at,
        },
    )
}

#[command]
pub fn compact_session(
    app: AppHandle,
    state: State<HistoryState>,
) -> Result<Option<String>, String> {
    history::compact_session(&app, &state, "compact")
        .map(|opt| opt.map(|p| p.to_string_lossy().to_string()))
}

#[command]
pub fn set_view_ask(app: AppHandle, enabled: bool) {
    let conf = AppConf::load(&app).unwrap();
    conf.amend(serde_json::json!({"ask_mode": enabled}))
        .unwrap()
        .save(&app)
        .unwrap();

    let core_window = app.get_window("core").unwrap();
    let ask_mode_height = if enabled { ASK_HEIGHT } else { 0.0 };
    let scale_factor = core_window.scale_factor().unwrap();
    let titlebar_height = (scale_factor * TITLEBAR_HEIGHT).round() as u32;
    let win_size = core_window.inner_size().unwrap();
    let ask_height = (scale_factor * ask_mode_height).round() as u32;

    let main_view = core_window.get_webview("main").unwrap();
    let titlebar_view = core_window.get_webview("titlebar").unwrap();
    let ask_view = core_window.get_webview("ask").unwrap();

    if enabled {
        ask_view.set_focus().unwrap();
    } else {
        main_view.set_focus().unwrap();
    }

    let set_view_properties =
        |view: &tauri::Webview, position: LogicalPosition<f64>, size: PhysicalSize<u32>| {
            if let Err(e) = view.set_position(position) {
                eprintln!("[cmd:view:position] Failed to set view position: {}", e);
            }
            if let Err(e) = view.set_size(size) {
                eprintln!("[cmd:view:size] Failed to set view size: {}", e);
            }
        };

    #[cfg(target_os = "macos")]
    {
        set_view_properties(
            &main_view,
            LogicalPosition::new(0.0, TITLEBAR_HEIGHT),
            PhysicalSize::new(
                win_size.width,
                win_size.height - (titlebar_height + ask_height),
            ),
        );
        set_view_properties(
            &titlebar_view,
            LogicalPosition::new(0.0, 0.0),
            PhysicalSize::new(win_size.width, titlebar_height),
        );
        set_view_properties(
            &ask_view,
            LogicalPosition::new(
                0.0,
                (win_size.height as f64 / scale_factor) - ask_mode_height,
            ),
            PhysicalSize::new(win_size.width, ask_height),
        );
    }

    #[cfg(not(target_os = "macos"))]
    {
        set_view_properties(
            &main_view,
            LogicalPosition::new(0.0, 0.0),
            PhysicalSize::new(
                win_size.width,
                win_size.height - (ask_height + titlebar_height),
            ),
        );
        set_view_properties(
            &titlebar_view,
            LogicalPosition::new(
                0.0,
                (win_size.height as f64 / scale_factor) - TITLEBAR_HEIGHT,
            ),
            PhysicalSize::new(win_size.width, titlebar_height),
        );
        set_view_properties(
            &ask_view,
            LogicalPosition::new(
                0.0,
                (win_size.height as f64 / scale_factor) - ask_mode_height - TITLEBAR_HEIGHT,
            ),
            PhysicalSize::new(win_size.width, ask_height),
        );
    }
}


// ============= v0.3.0 — instruction + keywords =============

#[command]
pub fn get_instruction(state: State<HistoryState>) -> Option<String> {
    state.session.lock().ok()
        .and_then(|s| s.clone())
        .and_then(|m| m.instruction)
}

#[command]
pub fn get_keywords(app: AppHandle) -> serde_json::Value {
    let path = match app.path().app_data_dir() {
        Ok(p) => p.join("com.nofwl.chatgpt").join("keywords.json"),
        Err(_) => return default_keywords(),
    };
    let alt = std::env::current_exe().ok()
        .and_then(|e| e.parent().map(|p| p.to_path_buf()))
        .map(|d| d.join("data").join("com.nofwl.chatgpt").join("keywords.json"));

    for p in [Some(path), alt].into_iter().flatten() {
        if p.exists() {
            if let Ok(content) = std::fs::read_to_string(&p) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&content) {
                    log::info!("[keywords] loaded from {}", p.display());
                    return v;
                }
            }
        }
    }
    log::debug!("[keywords] no file -> using defaults");
    default_keywords()
}

fn default_keywords() -> serde_json::Value {
    serde_json::json!({
        "compact": "compact_session",
        "lưu": "compact_session",
        "luu": "compact_session",
        "/compact": "compact_session",
        "/lưu": "compact_session"
    })
}


// ============= v0.4.0 — Summarize current session =============

use crate::core::summarize;

#[command]
pub async fn summarize_current(
    app: AppHandle,
    state: State<'_, HistoryState>,
) -> Result<String, String> {
    summarize_current_impl(&app, &state).await.map(|(s, _ai)| s)
}

pub async fn summarize_current_impl(
    app: &AppHandle,
    state: &State<'_, HistoryState>,
) -> Result<(String, String), String> {
    // Dùng helper public từ history (đã expose ở v0.5.0)
    let root = crate::core::history::root_dir(app)?;

    let cfg = summarize::load_config(&root)
        .ok_or("summarize disabled or config missing")?;

    // FALLBACK: nếu provider đang chọn là claude_oat mà KHÔNG có credentials.json,
    // tự chuyển sang provider OpenAI khả dụng (có api_key) nếu cấu hình sẵn.
    let active = crate::core::summarize::resolve_active_provider(&cfg);
    let provider = cfg.providers.get(&active)
        .ok_or_else(|| format!("provider '{}' not found in config", active))?;

    let messages = state.buffer.lock().unwrap().clone();
    if messages.is_empty() {
        return Err("no messages in current buffer".into());
    }

    let transcript = summarize::build_transcript(&messages);
    log::info!("[summarize] transcript size: {} chars, {} messages",
               transcript.len(), messages.len());

    let mut used_name = active.clone();
    let summary = match summarize::summarize(provider, &transcript).await {
        Ok(sm) => sm,
        Err(e) => {
            // FALLBACK-ON-ERROR: nếu provider đang dùng lỗi (vd Claude OAT 401),
            // tự thử provider OpenAI/Anthropic khác có api_key.
            log::warn!("[summarize] provider '{}' lỗi: {} -> thử fallback", active, e);
            let fb = crate::core::summarize::find_paid_fallback(&cfg, &active);
            match fb.and_then(|name| cfg.providers.get(&name).map(|p| (name, p))) {
                Some((name, fbp)) => {
                    log::info!("[summarize] fallback sang '{}'", name);
                    let sm = summarize::summarize(fbp, &transcript).await
                        .map_err(|e2| format!("Claude lỗi ({}); fallback '{}' cũng lỗi: {}", e, name, e2))?;
                    used_name = name;
                    sm
                }
                None => return Err(format!("{} (không có provider fallback có api_key)", e)),
            }
        }
    };
    let ai_label = crate::core::summarize::provider_label(&cfg, &used_name);
    log::info!("[summarize] result: {} chars (AI: {})", summary.len(), ai_label);

    // Snapshot session_id + message count để dùng cho sync (sau khi đã clone)
    let session_snapshot = state.session.lock().unwrap().clone();
    let message_count = messages.len() as u32;

    // Save to file riêng nếu config bật
    if cfg.output.save_separate_file {
        if let Some(meta) = &session_snapshot {
            let summaries_dir = root.join("sessions").join("summaries");
            let _ = std::fs::create_dir_all(&summaries_dir);
            let fname = format!("summary_{}_{}.md",
                meta.session_id,
                chrono::Local::now().format("%Y%m%d-%H%M%S"));
            let path = summaries_dir.join(fname);
            let content = format!("# Summary — session {}\n\n{}\n", meta.session_id, summary);
            let _ = std::fs::write(&path, content);
            log::info!("[summarize] wrote separate file: {}", path.display());
        }
    }

    // ===== ASYNC SYNC: đẩy summary lên memory server (non-blocking) =====
    if let Some(meta) = session_snapshot {
        let root_clone = root.clone();
        let summary_clone = summary.clone();
        let sid = meta.session_id.clone();
        tauri::async_runtime::spawn(async move {
            crate::core::sync::upload_summary(root_clone, summary_clone, sid, message_count).await;
        });
    }

    Ok((summary, ai_label))
}


// ============= v0.8.0 — OAuth token check + refresh =============

#[command]
pub fn check_oauth_status() -> String {
    crate::core::oauth_refresh::check_token_status()
}

#[command]
pub async fn refresh_oauth() -> Result<String, String> {
    crate::core::oauth_refresh::refresh_token().await
}


// ============= v0.8.3+ — Test API key, check config, open data dir =============

#[command]
pub async fn test_provider_key(app: AppHandle, name: String) -> Result<String, String> {
    let root = crate::core::history::root_dir(&app)?;
    if name == "archive" {
        let r = crate::core::sync::fetch_sessions(root, String::new(), 1).await?;
        let cnt = r.as_array().map(|a| a.len()).unwrap_or(0);
        return Ok(format!("archive OK · {} phiên gần nhất", cnt));
    }
    let cfg = summarize::load_config(&root).ok_or("summarize disabled or config missing")?;
    let provider = cfg.providers.get(&name)
        .ok_or_else(|| format!("provider '{}' không tồn tại trong summarize.json", name))?;
    let _ = summarize::summarize(provider, "ping").await
        .map_err(|e| format!("provider '{}' lỗi: {}", name, e))?;
    Ok(summarize::provider_label(&cfg, &name))
}

#[derive(serde::Serialize)]
pub struct ConfigStatus {
    pub sync: String,
    pub sync_detail: String,
    pub summarize: String,
    pub summarize_detail: String,
    pub data_dir: String,
}

#[command]
pub fn check_config_status(app: AppHandle) -> Result<ConfigStatus, String> {
    let root = crate::core::history::root_dir(&app)?;
    let (sync_st, sync_dt) = crate::core::sync::inspect_config(&root);
    let (sum_st, sum_dt) = crate::core::summarize::inspect_config(&root);
    Ok(ConfigStatus {
        sync: sync_st, sync_detail: sync_dt,
        summarize: sum_st, summarize_detail: sum_dt,
        data_dir: root.to_string_lossy().to_string(),
    })
}

#[command]
pub fn open_config_dir(app: AppHandle) -> Result<String, String> {
    let root = crate::core::history::root_dir(&app)?;
    let path = root.to_string_lossy().to_string();
    #[cfg(target_os = "windows")]
    { std::process::Command::new("explorer").arg(&path).spawn()
        .map_err(|e| format!("explorer: {}", e))?; }
    #[cfg(target_os = "macos")]
    { std::process::Command::new("open").arg(&path).spawn()
        .map_err(|e| format!("open: {}", e))?; }
    #[cfg(target_os = "linux")]
    { std::process::Command::new("xdg-open").arg(&path).spawn()
        .map_err(|e| format!("xdg-open: {}", e))?; }
    Ok(path)
}
