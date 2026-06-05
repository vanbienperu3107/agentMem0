# ChatGPT Desktop — Sequence / Flow tổng hợp

> Tài liệu SỐNG: mô tả toàn bộ luồng hoạt động của app ChatGPT Desktop.
> **Quy ước: mỗi phiên sửa tính năng → cập nhật file này + bump mục "Phiên bản".**
> Cập nhật lần cuối: 2026-05-31 · App v0.8.0 · chat-logger.js v0.8.1

---

## 0. Tổng quan kiến trúc

```
[chatgpt.com trong webview]
   │  chat-logger.js (inject lúc load trang)
   │  - quan sát DOM (childList + debounce) -> thu tin nhắn
   │  - hook Enter bắt keyword
   │  - 2 nút nổi + /lichsu
   │
   │  event.emit("chat-logger://...")   (postMessage, CSP-safe)
   ▼
[Rust backend (main.rs listen_any)]
   │  - log-message  -> history.rs (ghi WAL buffer)
   │  - compact      -> history.rs compact_session + sync.rs upload_session_file
   │  - summarize_current -> summarize.rs + sync.rs upload_summary
   │  - fetch-history -> sync.rs fetch_recent_sessions
   │  emit kết quả ngược: chat-logger://result, chat-logger://history-result
   ▼
[archive-api trên VPS]  POST /sessions, POST /compact-summaries, GET /sessions
   -> Postgres / R2 / Qdrant
```

Config tự sinh khi chạy lần đầu (nhúng trong .exe, ghi vào data dir nếu chưa có):
`summarize.json` (enabled) + `sync.json` (enabled). Không cần trỏ đường dẫn ngoài.

---

## 1. Luồng THU tin nhắn (logging)

1. `chat-logger.js` gắn MutationObserver lên `<main>` (chỉ `childList`+`subtree`, **debounce 500ms** — KHÔNG `characterData` để tránh lag khi stream).
2. `scan()` quét `[data-message-id]`: tin user gửi ngay; tin assistant chỉ gửi khi **đã xong** (có nút Copy). Dedup bằng `loggedIds`.
3. Mỗi tin → `event.emit("chat-logger://log-message", {id, conversationId, role, content})`.
4. Rust `log_message()` → ghi vào **WAL buffer** (file tạm trong data dir).

## 2. Luồng LƯU FULL SESSION (nút 💾 / keyword compact·/lưu / thoát app)

1. Trigger → `event.emit("chat-logger://compact")`.
2. Rust `compact_session()`: gom WAL → 1 file session JSON (session_id, messages, message_count, started_at, instruction).
3. `upload_session_file()` → **POST `/sessions`** với payload: user_id, project_tag, started_at/ended_at, message_count, **transcript (nguyên văn)**, summary:null, metadata.
4. Rust emit `chat-logger://result {action:"compact", ok, msg}` → frontend đổi màu nút + toast.
5. Độ bền: ghi marker trước khi gửi → retry backoff → app crash thì lần sau `recover_pending_uploads` retry.

## 3. Luồng LƯU SUMMARY (nút 📝)

1. Trigger → `event.emit("chat-logger://summarize_current")`.
2. Rust `summarize_current_impl()`: lấy buffer → dựng transcript → gọi **LLM** theo `summarize.json` (provider `active_provider`).
   - `claude_oat`: đọc OAT từ `~/.claude/.credentials.json` (miễn phí, cần login Claude Code).
   - `anthropic`/`openai`: dùng API key (trả phí).
3. Nhận bản tóm tắt → (tùy chọn) ghi `.md` riêng → `upload_summary()` **POST `/compact-summaries`** với payload: summary_text, messages_before, metadata.
4. Rust emit `chat-logger://result {action:"summarize", ok, msg}` → nút đổi màu + toast.

## 4. Luồng XEM LỊCH SỬ (keyword `lichsu` — KHÔNG dấu /)

- **Tìm** `lichsu <điều kiện>`: semantic-first (theo ý nghĩa) → fallback keyword ILIKE trên **summary** → list (ngày+tóm tắt+id). `lichsu` trống = 5 gần nhất.
- **Xem chi tiết** `xemphien <id>`: lấy **full transcript** phiên đó (GET /sessions/{id}) → chèn vào chat.
- Nguyên tắc: TÌM dựa summary (nhẹ); FULL chỉ tải khi xem 1 phiên cụ thể.

### (chi tiết kỹ thuật cũ)

1. Gõ `/lichsu` (hoặc "xem lịch sử") + Enter → `event.emit("chat-logger://fetch-history")`.
2. Rust `fetch_recent_sessions()` → **GET `/sessions?user_id=..&limit=5`** (qua Rust vì CSP chặn fetch trực tiếp từ JS).
3. Rust emit `chat-logger://history-result {ok, sessions}`.
4. Frontend `renderHistory()` → `insertIntoChat()` chèn danh sách 5 phiên (thời gian + tóm tắt + id) vào ô chat.

## 5. Phản hồi thành công/thất bại (UI)

- Nút lúc bấm: "⏳ Đang lưu..." (khóa nút).
- Backend emit `chat-logger://result` → nút **xanh ✓** / **đỏ ✗ + tooltip lý do** + **toast** góc phải.
- Timeout 12s không phản hồi → nút báo timeout (không kẹt).

## 6. Phân biệt với hook Claude Code (TRÁNH NHẦM)

- 2 file `scripts/archive-upload.py` + `scripts/sum_hook.py` là **hook của Claude Code**, KHÔNG phải app này.
- App ChatGPT tự làm mọi thứ bằng Rust (`sync.rs` + `summarize.rs`), không gọi Python.
- Log dạng `SessionEnd hook [python ...]` là của Claude Code, không liên quan app ChatGPT.

---

## 7. Lịch sử phiên bản (cập nhật mỗi lần sửa)

| Version | Thay đổi |
|---|---|
| v0.8.2 | Thông báo lưu summary kèm **AI nào** (model + provider, vd 'gpt-4o-mini (qua api.openai.com)'); sửa 401: nút Gia hạn nay CHẠY `claude` CLI (CLI tự refresh OAT, không tự đoán OAuth endpoint dễ sai/bị proxy chặn); summarize FALLBACK-ON-ERROR sang OpenAI/DeepSeek khi Claude 401 |
| v0.8.1 | Tìm lịch sử THÔNG MINH: `lichsu <điều kiện>` (bỏ dấu /, khớp prefix) → semantic-first + fallback keyword trên summary → list tóm tắt+id; `xemphien <id>` → chèn full transcript. Tìm dựa summary, full chỉ khi xem chi tiết |
| v0.8.0 | Kiểm tra OAT lúc start; hết hạn → nút summary đỏ + nút '🔄 Gia hạn' (tự refresh qua OAuth endpoint); fallback OpenAI khi thiếu credentials.json |
| v0.7.1 | Tự sinh `summarize.json` + `sync.json` (nhúng `include_str!`) vào data dir lúc chạy đầu; không đè config user |
| v0.7.0 | Xem lịch sử: keyword `/lichsu` → chèn 5 phiên gần nhất vào chat; OpenAPI archive cho Custom GPT |
| v0.6.1 | Phản hồi nút thật: emit `chat-logger://result`, nút đổi xanh/đỏ + toast + timeout |
| v0.6.0 | Fix lag: bỏ `characterData`, dùng `childList`+debounce 500ms; thêm 2 nút nổi (Lưu summary / Lưu full session) |
| ≤v0.5.0 | (xem CHANGELOG.md) chat-logger CSP-safe, auto-portable, hook keyword, summarize providers |

> Khi sửa tính năng: thêm 1 dòng vào bảng này + cập nhật mục liên quan ở trên.
