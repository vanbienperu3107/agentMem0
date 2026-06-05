# Hướng dẫn: lệnh `/sum` + tool `add_memory` (Claude Code, Claude App, ChatGPT App)

Tính năng: tóm tắt hội thoại và lưu lên mem0. Dùng **Claude Max OAT** nên **miễn phí token**.

## 1. Solution — các cách dùng

| Cách | Kích hoạt | Phí | Khi nào dùng |
|---|---|---|---|
| **A. Slash command `/sum`** (Claude Code) | Gõ tay `/sum` | Miễn phí (OAT phiên hiện tại) | Đóng dấu một phiên quan trọng |
| **B. Hook tự động cuối phiên** (Claude Code) | Tự chạy khi phiên kết thúc | Miễn phí (OAT qua `claude -p`) | Auto-lưu mọi phiên |
| **C. Tool `add_memory` trên MCP HTTP** (Claude App + ChatGPT App) | Claude/GPT tự gọi tool | Miễn phí*/theo backend | Lưu/đọc memory ngoài Claude Code |

(*) MCP HTTP gọi `memory-rest-api`, vốn dùng OpenAI embeddings + gpt-4o-mini — chi phí theo cấu hình hiện tại của API đó, không phải OAT.

## 2. Phương án A — slash command `/sum`

1. File `.claude/commands/sum.md` đã có sẵn trong repo → mở Claude Code tại thư mục repo là có `/sum`. Muốn dùng mọi nơi: chép vào `~/.claude/commands/sum.md`.
2. Gõ `/mcp` để xác nhận tên server. File dùng tiền tố `mcp__mem0__`; nếu đặt tên khác khi `claude mcp add`, sửa cho khớp.
3. Dùng: `/sum` hoặc `/sum đang ở Giai đoạn 2 deploy docker`.
4. Claude tóm tắt → gọi `add_memory(text=..., user_id="thanh")` → báo lại fact đã lưu.

> Tham số tool là `text` (chuỗi, bắt buộc) + `user_id` — khớp `mem0-mcp-selfhosted` (server.py) và `memory-rest-api` (AddBody.text). `messages` chỉ là optional.

## 3. Phương án B — hook tự động

1. Chép `.claude/settings.json.example` → `.claude/settings.json`. Hook `Stop` chạy `python scripts/sum_hook.py` khi phiên kết thúc.
2. Cơ chế: Claude Code gửi `{"transcript_path": "..."}` qua STDIN → script đọc transcript → `claude -p` (Haiku) tóm tắt + gọi `add_memory` qua OAT.
3. Tuỳ biến qua env: `SUM_HOOK_MODEL`, `SUM_HOOK_MCP_SERVER`, `MEM0_USER_ID`, `SUM_HOOK_MAX_CHARS`.
4. Test khô: `echo '{"transcript_path":"<path>.jsonl"}' | python scripts/sum_hook.py --dry-run`

### Tránh trùng với auto-upload v0.4.2
v0.4.2 đã auto-upload transcript theo giờ + khi `/compact`. Bật thêm hook `Stop` sẽ lưu hai lần (mem0 fact + archive transcript) — khác mục đích nên không lỗi, nhưng muốn gọn thì chỉ dùng Phương án A, hoặc tắt phần trùng.

## 4. Phương án C — tool `add_memory` / `search_memories` trên MCP HTTP server

MCP HTTP server (`mcp-http-server/app.py`) nay expose thêm 2 tool, có ở **cả Claude App lẫn ChatGPT App** (cùng lấy từ `tools/list`):

| Tool | Tham số | Việc làm |
|---|---|---|
| `add_memory` | `text` (bắt buộc), `metadata` (tuỳ chọn) | Lưu fact/summary vào mem0 |
| `search_memories` | `query` (bắt buộc), `limit` (mặc định 10) | Semantic search memory |

### Cơ chế
MCP HTTP server → `memory-rest-api` (`POST /memories`, `POST /memories/search`) → mem0 (Qdrant + OpenAI). Vì cùng `tools/list`, **không cần cấu hình riêng cho ChatGPT** — thêm vào `TOOLS` là tự có ở cả hai. (ChatGPT deep-research vẫn dùng `search`/`fetch`; Custom GPT / MCP connector thấy đủ `add_memory`, `search_memories`.)

### Cấu hình triển khai
1. `docker-compose.yml` đã wire service `memory-rest-api` (port 8002, đúng Dockerfile) và `mcp-http-server depends_on` nó.
2. Thêm vào `.env` (xem `.env.example`):
   ```bash
   CHATGPT_AUTH_TOKEN=<openssl rand -hex 32>
   MEMORY_API_URL=http://memory-rest-api:8002
   MEMORY_API_TOKEN=${CHATGPT_AUTH_TOKEN}   # MCP server gửi đúng Bearer này tới memory-rest-api
   ```
3. Rebuild: `docker compose up -d --build mcp-http-server memory-rest-api`.

### Why
- Dùng lại `memory-rest-api` (đã là backend của ChatGPT Custom GPT) thay vì nhúng mem0 vào MCP server → giữ MCP server nhẹ, một backend duy nhất, share chung Qdrant collection.
- `MEMORY_API_TOKEN` = `CHATGPT_AUTH_TOKEN` vì `memory-rest-api` yêu cầu đúng Bearer đó (`check()` trong app.py).
- `add_memory` chỉ nhận `text` cho schema đơn giản, LLM dễ gọi đúng; map thẳng vào `AddBody.text`.

## 5. Verification
```bash
python scripts/test_sum_hook.py                 # 13 passed (hook)
python mcp-http-server/test_memory_tools.py     # 8 passed (MCP tools)

# Sau deploy: liệt kê tool (cần Bearer)
curl -s -X POST https://claude.hangocthanh.io.vn/mcp \
  -H "Authorization: Bearer <MCP_BEARER_TOKEN>" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m json.tool
# Phải thấy add_memory + search_memories
```

## 6. Phương án trả phí (tách khỏi OAT)
Nếu không muốn tóm tắt ăn rate-limit OAT (Opus hay 429), thay `claude -p` trong `sum_hook.py` bằng Gemini 2.5 Flash-Lite (~5$/năm) hoặc DeepSeek V4 Flash (~6$/năm). Chi tiết: `nghien-cuu-luu-tru-hoi-thoai-tom-tat.md`.
