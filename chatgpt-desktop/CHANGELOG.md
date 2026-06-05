# Changelog

All notable changes to this project will be documented in this file.

> Xem thêm FLOW.md để biết sequence/flow tổng hợp (cập nhật mỗi phiên).

## [v0.7.1] - 2026-05-31
### Added
- Tự sinh `summarize.json` + `sync.json` vào data dir khi chạy lần đầu (nhúng trong .exe qua `include_str!`); không đè config người dùng đã chỉnh.

## [v0.7.0] - 2026-05-31
### Added
- Xem lịch sử: keyword `/lichsu` (và "xem lịch sử") → Rust GET `/sessions` → chèn 5 phiên gần nhất vào ô chat (CSP-safe).
- `memory-rest-api/openapi-archive-for-chatgpt.yaml`: Action archive cho Custom GPT (listSessions/getSession/searchSessions).

## [v0.6.1] - 2026-05-31
### Added
- Phản hồi nút: backend emit `chat-logger://result {action, ok, msg}`; nút đổi xanh ✓ / đỏ ✗ + tooltip lý do + toast; timeout 12s.

## [v0.6.0] - 2026-05-31
### Fixed
- Fix lag nặng: MutationObserver bỏ `characterData` (fire mỗi token khi stream), chuyển `childList`+`subtree` + debounce 500ms (500 scan/câu → 1).
### Added
- 2 nút nổi: "📝 Lưu summary" (summarize_current) + "💾 Lưu full session" (compact).
- test-chat-logger.mjs (jsdom) cho chat-logger.


## [v0.2.1] - 2026-05-29

### Added
- Hook main ChatGPT textarea: gõ `compact`/`lưu`/`luu` rồi Enter trong ô chính cũng trigger export (không chỉ ô Ask riêng)
- Capture-phase keydown listener trên cả `<textarea>` và `[contenteditable]`
- `scan()` skip message có content match keyword (không log "compact" thành user message gửi lên OpenAI)

### Changed
- chat-logger.js bumped to v0.3.0 (script version độc lập với app version)
- MutationObserver giờ re-hook keyword trigger mỗi DOM change (cover SPA navigation)

### Fixed
- Trước đây user chỉ có thể trigger qua ô Ask riêng của app (mặc định ẩn) → confusing UX
- Keyword gõ vào textarea ChatGPT bị gửi lên OpenAI như message thường

## [v0.2.0] - 2026-05-29

### Added
- **Auto-portable mode**: detect exe location → data lưu cạnh exe nếu KHÔNG trong Program Files
- **Full logging system** via simplelog: file `logs/app.log` + stdout, level Info/Debug
- Override flags `portable.flag` + `use-appdata.flag`
- Validate-release CI job: 15 acceptance criteria check
- `run-portable.bat` wrapper (cho user không upgrade lên v0.2.0+)

### Changed
- **CSP bypass**: chat-logger.js v0.2.0 dùng `__TAURI__.event.emit()` (postMessage) thay vì `invoke()` (HTTP IPC bị chatgpt.com CSP block)
- `main.rs` thêm `app.listen_any("chat-logger://log-message", ...)` + compact event listeners
- Version aligned: Cargo + package.json + git tag = 0.2.0

### Fixed
- tauri-plugin-log API mismatch với Tauri beta.22 → chuyển sang simplelog
- TermLogger return type Box not Option
- template.rs truncate bug (mount filesystem)
- Upload path workspace target (không phải src-tauri/target)
- Tauri build silent fail (--bundles flag, explicit targets)
- WiX preinstalled check (không force install)
- Dead code warning `Template` struct
- Python UTF-8 stdout on Windows runner
- Cargo + package.json version mismatch

## [v0.1.0] - 2026-05-28

### Added
- Feature gốc: compact JSON theo keyword
- 8 Rust unit tests + 17 Python harness tests
- Tauri command `log_message`, `compact_session`
- chat-logger.js scrape DOM chatgpt.com qua MutationObserver
- Ask.tsx detect keyword `compact`/`lưu`/`luu`
- Build script `build-windows.ps1` (1 lệnh cài Rust + WebView2 + build)
- GitHub Actions workflow (test → build → smoke-test → release)
- WAL (Write-Ahead Log) crash recovery
- Auto-flush on app exit
- README song ngữ VI/EN + DISCLAIMER + acceptance test
- Buy Me a Coffee info (0869649888)

[v0.2.1]: https://github.com/thanhiont423/mem0custom/releases/tag/v0.2.1
[v0.2.0]: https://github.com/thanhiont423/mem0custom/releases/tag/v0.2.0
[v0.1.0]: https://github.com/thanhiont423/mem0custom/releases/tag/v0.1.0
