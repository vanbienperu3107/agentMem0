# Changelog

## v1.0.0 — 2026-05-30

Bản phát hành ổn định đầu tiên: hệ thống AI memory self-hosted hoàn chỉnh cho Claude Code, Claude App và ChatGPT App.

### Tính năng chính
- **mem0 self-hosted**: lưu/tìm "fact" theo ngữ nghĩa qua Qdrant + Postgres, LLM trích xuất qua Claude Max OAT (miễn phí token) hoặc OpenAI.
- **MCP HTTP server** (Streamable HTTP + OAuth 2.1 + DCR) phục vụ đồng thời Claude App và ChatGPT App.
- **Lệnh `/sum`** (Claude Code): tóm tắt phiên chat và lưu vào mem0 bằng một lệnh.
- **Hook tự động cuối phiên**: tuỳ chọn tự tóm tắt + lưu, không bao giờ làm hỏng phiên.
- **Tool `add_memory` + `search_memories`** trên MCP HTTP server, dùng được ở cả Claude App lẫn ChatGPT App.
- **memory-rest-api**: REST wrapper mem0 cho ChatGPT Custom GPT (OpenAPI sẵn sàng import).
- **Archive**: lưu tóm tắt phiên + transcript (hybrid DB + R2/B2), tìm kiếm keyword/semantic, nạp lại ngữ cảnh để tiếp tục hội thoại.
- **ChatGPT Desktop app** (Tauri/Rust) kèm CI build Windows.

### CI/CD
- `test.yml`: kiểm tra cú pháp, YAML, OpenAPI, docker-compose, unit test.
- `build-and-deploy.yml`: build Docker image (GHCR) + deploy VPS qua SSH + smoke test; tự inject `MEMORY_API_TOKEN` để mcp-http-server gọi được memory-rest-api.
- `build-windows.yml`: build EXE portable.
- `release.yml` (mới): tạo GitHub Release tự động khi push tag `v*`.

### Sửa lỗi
- Sửa 401 khi mcp-http-server gọi memory-rest-api (thiếu `MEMORY_API_TOKEN` lúc deploy) — nay inject qua `docker-compose.override.yml`.
- Sửa `if-no-found` → `if-no-files-found` trong build-windows.yml.

## v0.2.0
- Bản phát triển trước (xem lịch sử git).

## v0.1.0
- Bản khởi tạo.
