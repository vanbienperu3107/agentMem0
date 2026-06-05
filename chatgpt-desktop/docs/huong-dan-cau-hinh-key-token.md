# Hướng dẫn cấu hình API key + Auth token (file thay env var)

> Áp dụng từ v0.8.3. App lưu 2 file cấu hình trong data dir:
> - `summarize.json` — API key cho LLM provider (OpenAI/DeepSeek/Anthropic)
> - `sync.json` — Auth token gọi archive-api (cho /lichsu, /xemphien, upload)

## File ở đâu

| Mode | Đường dẫn |
|---|---|
| Portable | `<thư mục exe>/data/com.nofwl.chatgpt/{summarize,sync}.json` |
| Installer | `%APPDATA%\com.nofwl.chatgpt\{summarize,sync}.json` |

## Phần 1 — Cấu hình `summarize.json` (fallback OpenAI/DeepSeek)

Thay `"${env:OPENAI_API_KEY}"` thành key thật `"sk-proj-..."`. Save → app đọc lại lần Sum kế tiếp.

## Phần 2 — Cấu hình `ARCHIVE_AUTH_TOKEN`

`sync.json`:
```json
{
  "enabled": true,
  "archive_url": "https://...",
  "auth_token": "cfc546b3...token-thật...",
  ...
}
```

Triệu chứng nếu thiếu: gõ `lichsu` → "⚠️ Không lấy được lịch sử".

## Verify

PowerShell: `.\scripts\test-provider-key.ps1 -Provider openai_4o` hoặc `-Provider archive`
Devtools console: `await window.__TAURI__.core.invoke('test_provider_key', { name: 'archive' })`
