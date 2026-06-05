use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Mutex},
};
use tauri::{
    webview::DownloadEvent, App, LogicalPosition, Manager, PhysicalSize, WebviewBuilder,
    WebviewUrl, WindowBuilder, WindowEvent,
};
use tauri_plugin_shell::ShellExt;

#[cfg(target_os = "macos")]
use tauri::TitleBarStyle;

/// Build script inject window.__INJECTED_KEYWORDS__ với content keywords.json
/// để chat-logger.js đọc trực tiếp (không cần invoke -> CSP-safe).
fn build_keywords_inject_script(app: &tauri::AppHandle) -> String {
    use tauri::Manager;
    let default = r#"{"compact":"compact_session","/compact":"compact_session","lưu":"compact_session","/lưu":"compact_session","luu":"compact_session","save":"compact_session","xuat":"compact_session","/c":"compact_session","/save":"compact_session","/sum":"summarize_current","/summary":"summarize_current","tóm tắt":"summarize_current","/tóm tắt":"summarize_current","tomtat":"summarize_current"}"#;

    let mut content = default.to_string();

    // Tìm keywords.json (portable hoặc AppData)
    let candidates = vec![
        std::env::current_exe().ok()
            .and_then(|e| e.parent().map(|p| p.to_path_buf()))
            .map(|d| d.join("data").join("com.nofwl.chatgpt").join("keywords.json")),
        app.path().app_data_dir().ok()
            .map(|d| d.join("com.nofwl.chatgpt").join("keywords.json")),
    ];

    for p in candidates.into_iter().flatten() {
        if p.exists() {
            if let Ok(s) = std::fs::read_to_string(&p) {
                if serde_json::from_str::<serde_json::Value>(&s).is_ok() {
                    log::info!("[setup] inject keywords from {}", p.display());
                    content = s;
                    break;
                }
            }
        }
    }

    format!("window.__INJECTED_KEYWORDS__ = {};
", content)
}

use crate::core::{
    conf::AppConf,
    constant::{ASK_HEIGHT, INIT_SCRIPT, TITLEBAR_HEIGHT},
    template,
};

/// Tự sinh config mặc định (summarize.json + sync.json) vào data dir khi CHẠY LẦN ĐẦU.
/// Config được NHÚNG SẴN trong binary (include_str!), nên không cần file ngoài,
/// không phải trỏ đường dẫn đi đâu. Chỉ ghi nếu file CHƯA tồn tại (không đè cấu hình
/// người dùng đã chỉnh).
fn ensure_default_configs(app: &tauri::AppHandle) {
    let root = match crate::core::history::root_dir(app) {
        Ok(r) => r,
        Err(e) => { log::warn!("[setup] ensure_default_configs: root_dir lỗi: {}", e); return; }
    };
    let defaults: [(&str, &str); 2] = [
        ("summarize.json", include_str!("../../default-config/summarize.default.json")),
        ("sync.json",      include_str!("../../default-config/sync.default.json")),
    ];
    for (name, content) in defaults {
        let path = root.join(name);
        if path.exists() {
            log::debug!("[setup] {} đã tồn tại -> giữ nguyên", name);
            continue;
        }
        match fs::write(&path, content) {
            Ok(_) => log::info!("[setup] sinh config mặc định: {}", path.display()),
            Err(e) => log::warn!("[setup] không ghi được {}: {}", name, e),
        }
    }
}

pub fn init(app: &mut App) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle();

    // Tự sinh summarize.json + sync.json vào data dir nếu chưa có (config nhúng trong .exe).
    ensure_default_configs(handle);

    let conf = &AppConf::load(handle)?;
    let ask_mode_height = if conf.ask_mode { ASK_HEIGHT } else { 0.0 };

    template::Template::new(AppConf::get_scripts_path(handle)?);

    tauri::async_runtime::spawn({
        let handle = handle.clone();
        async move {
            let mut core_window = WindowBuilder::new(&handle, "core").title("ChatGPT");

            #[cfg(target_os = "macos")]
            {
                core_window = core_window
                    .title_bar_style(TitleBarStyle::Overlay)
                    .hidden_title(true);
            }

            core_window = core_window
                .resizable(true)
                .inner_size(800.0, 600.0)
                .min_inner_size(300.0, 200.0)
                .theme(Some(AppConf::get_theme(&handle)));

            let core_window = core_window
                .build()
                .expect("[core:window] Failed to build window");

            let win_size = core_window
                .inner_size()
                .expect("[core:window] Failed to get window size");
            // Wrap the window in Arc<Mutex<_>> to manage ownership across threads
            let window = Arc::new(Mutex::new(core_window));

            let main_view =
                WebviewBuilder::new("main", WebviewUrl::App("https://chatgpt.com".into()))
                    .auto_resize()
                    .on_download({
                        let app_handle = handle.clone();
                        let download_path = Arc::new(Mutex::new(PathBuf::new()));
                        move |_, event| {
                            match event {
                                DownloadEvent::Requested { destination, .. } => {
                                    let download_dir = app_handle
                                        .path()
                                        .download_dir()
                                        .expect("[view:download] Failed to get download directory");
                                    let mut locked_path = download_path
                                        .lock()
                                        .expect("[view:download] Failed to lock download path");
                                    *locked_path = download_dir.join(&destination);
                                    *destination = locked_path.clone();
                                }
                                DownloadEvent::Finished { success, .. } => {
                                    let final_path = download_path
                                        .lock()
                                        .expect("[view:download] Failed to lock download path")
                                        .clone();

                                    if success {
                                        app_handle
                                            .shell()
                                            .open(final_path.to_string_lossy(), None)
                                            .expect("[view:download] Failed to open file");
                                    }
                                }
                                _ => (),
                            }
                            true
                        }
                    })
                    .initialization_script(&AppConf::load_script(&handle, "ask.js"))
                    .initialization_script(&build_keywords_inject_script(&handle))
                    .initialization_script(&AppConf::load_script(&handle, "chat-logger.js"))
                    .initialization_script(INIT_SCRIPT);

            let titlebar_view = WebviewBuilder::new(
                "titlebar",
                WebviewUrl::App("index.html".into()),
            )
            .auto_resize();

            let ask_view =
                WebviewBuilder::new("ask", WebviewUrl::App("index.html".into()))
                    .auto_resize();

            let win = window.lock().unwrap();
            let scale_factor = win.scale_factor().unwrap();
            let titlebar_height = (scale_factor * TITLEBAR_HEIGHT).round() as u32;
            let ask_height = (scale_factor * ask_mode_height).round() as u32;

            #[cfg(target_os = "macos")]
            {
                let main_area_height = win_size.height - titlebar_height;

                win.add_child(
                    titlebar_view,
                    LogicalPosition::new(0, 0),
                    PhysicalSize::new(win_size.width, titlebar_height),
                )
                .unwrap();
                win.add_child(
                    ask_view,
                    LogicalPosition::new(
                        0.0,
                        (win_size.height as f64 / scale_factor) - ask_mode_height,
                    ),
                    PhysicalSize::new(win_size.width, ask_height),
                )
                .unwrap();
                win.add_child(
                    main_view,
                    LogicalPosition::new(0.0, TITLEBAR_HEIGHT),
                    PhysicalSize::new(win_size.width, main_area_height - ask_height),
                )
                .unwrap();
            }

            #[cfg(not(target_os = "macos"))]
            {
                win.add_child(
                    ask_view,
                    LogicalPosition::new(
                        0.0,
                        (win_size.height as f64 / scale_factor) - ask_mode_height,
                    ),
                    PhysicalSize::new(win_size.width, ask_height),
                )
                .unwrap();
                win.add_child(
                    titlebar_view,
                    LogicalPosition::new(
                        0.0,
                        (win_size.height as f64 / scale_factor) - ask_mode_height - TITLEBAR_HEIGHT,
                    ),
                    PhysicalSize::new(win_size.width, titlebar_height),
                )
                .unwrap();
                win.add_child(
                    main_view,
                    LogicalPosition::new(0.0, 0.0),
                    PhysicalSize::new(
                        win_size.width,
                        win_size.height - (ask_height + titlebar_height),
                    ),
                )
                .unwrap();
            }

            let window_clone = Arc::clone(&window);
            let set_view_properties =
                |view: &tauri::Webview, position: LogicalPosition<f64>, size: PhysicalSize<u32>| {
                    if let Err(e) = view.set_position(position) {
                        eprintln!("[view:position] Failed to set view position: {}", e);
                    }
                    if let Err(e) = view.set_size(size) {
                        eprintln!("[view:size] Failed to set view size: {}", e);
                    }
                };

            win.on_window_event(move |event| {
                let conf = &AppConf::load(&handle).unwrap();
                let ask_mode_height = if conf.ask_mode { ASK_HEIGHT } else { 0.0 };
                let ask_height = (scale_factor * ask_mode_height).round() as u32;

                if let WindowEvent::Resized(size) = event {
                    let win = window_clone.lock().unwrap();

                    let main_view = win
                        .get_webview("main")
                        .expect("[view:main] Failed to get webview window");
                    let titlebar_view = win
                        .get_webview("titlebar")
                        .expect("[view:titlebar] Failed to get webview window");
                    let ask_view = win
                        .get_webview("ask")
                        .expect("[view:ask] Failed to get webview window");

                    #[cfg(target_os = "macos")]
                    {
                        set_view_properties(
                            &main_view,
                            LogicalPosition::new(0.0, TITLEBAR_HEIGHT),
                            PhysicalSize::new(
                                size.width,
                                size.height - (titlebar_height + ask_height),
                            ),
                        );
                        set_view_properties(
                            &titlebar_view,
                            LogicalPosition::new(0.0, 0.0),
                            PhysicalSize::new(size.width, titlebar_height),
                        );
                        set_view_properties(
                            &ask_view,
                            LogicalPosition::new(
                                0.0,
                                (size.height as f64 / scale_factor) - ask_mode_height,
                            ),
                            PhysicalSize::new(size.width, ask_height),
                        );
                    }

                    #[cfg(not(target_os = "macos"))]
                    {
                        set_view_properties(
                            &main_view,
                            LogicalPosition::new(0.0, 0.0),
                            PhysicalSize::new(
                                size.width,
                                size.height - (ask_height + titlebar_height),
                            ),
                        );
                        set_view_properties(
                            &titlebar_view,
                            LogicalPosition::new(
                                0.0,
                                (size.height as f64 / scale_factor) - TITLEBAR_HEIGHT,
                            ),
                            PhysicalSize::new(size.width, titlebar_height),
                        );
                        set_view_properties(
                            &ask_view,
                            LogicalPosition::new(
                                0.0,
                                (size.height as f64 / scale_factor)
                                    - ask_mode_height
                                    - TITLEBAR_HEIGHT,
                            ),
                            PhysicalSize::new(size.width, ask_height),
                        );
                    }
                }
            });
        }
    });

    Ok(())
}
