# ChatGPT Desktop ↔ mem0custom Integration

App ChatGPT Desktop này là **client** xuất chat history để feed vào hệ thống mem0custom server.

## Luồng dữ liệu

```
User chat trên chatgpt.com (qua app desktop)
         │
         │  chat-logger.js scrape DOM, gửi mỗi message về Rust
         ▼
Rust ghi vào WAL (current.wal)
         │
         │  User gõ "compact" hoặc "lưu" → flush
         ▼
File JSON: sessions/session_{id}_{time}.json
         │
         │  (Tiến trình ngoài) đọc file, gửi lên VPS
         ▼
archive-api (mem0custom) → Postgres + R2/B2
```

## File JSON output

Mỗi file đại diện 1 phiên chat, format:

```json
{
  "session_id": "s7a3f2b9",
  "started_at_iso": "2026-05-28T13:00:00+07:00",
  "exported_at_iso": "2026-05-28T14:30:22+07:00",
  "exported_via": "compact",
  "message_count": 12,
  "messages": [
    {"id": "...", "role": "user", "content": "...", "captured_at": 1748441100},
    {"id": "...", "role": "assistant", "content": "...", "captured_at": 1748441120}
  ]
}
```

## Vị trí file output trên máy user

- Windows: `%APPDATA%\com.nofwl.chatgpt\sessions\`
- macOS: `~/Library/Application Support/com.nofwl.chatgpt/sessions/`
- Linux: `~/.config/com.nofwl.chatgpt/sessions/`

## Build

Xem `CHAT-HISTORY-README.md` để biết chi tiết tính năng. Chạy `build-windows.ps1`
để build `.msi` trên Windows (1 lệnh).

## Trigger export

- Gõ `compact` hoặc `lưu` ở ô input "Ask" trong app → xuất file ngay
- Đóng app → auto-flush
- App crash → file recovery khi mở lại
