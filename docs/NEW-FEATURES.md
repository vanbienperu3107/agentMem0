# New Features (branch `new-features`)

5 enhancements on top of `main`. Each is independent and can be enabled separately.

## 1. LLM-based summary

**Files:** `archive-api/summarizer.py`, `archive-api/app.py` (endpoint `/sessions/{id}/summarize`)

**What changes:** Instead of using `first_user[:200]` as the summary, generate a real 200-400 word Vietnamese summary listing topics, conclusions, and action items.

**Backend:** Claude Haiku via `ANTHROPIC_API_KEY` OR OpenAI GPT-4o-mini via `OPENAI_API_KEY` (auto-picks based on which key is set).

**Schema migration:** new column `llm_summary TEXT` (see `archive-api/schema-v2.sql`).

**Usage:**
```bash
# Generate summary for one session
curl -X POST -H "Authorization: Bearer $ARCHIVE_AUTH_TOKEN" \
  https://claude.hangocthanh.io.vn/archive/sessions/<id>/summarize

# Or set SUMMARIZE_ON_UPLOAD=1 in archive-env to do it automatically on upload
```

## 2. Semantic search

**Files:** `archive-api/embeddings.py`, `archive-api/qdrant_helper.py`, endpoint `/sessions/search-semantic`

**What changes:** When `/sessions/{id}/summarize` runs, it also embeds the summary (OpenAI `text-embedding-3-small`, 1536 dims) and stores in a new Qdrant collection `chat_summaries`. Semantic search returns top-K closest matches.

**Reuses existing Qdrant** (already running for mem0 layer) — zero extra infra.

**Usage:**
```bash
curl -H "Authorization: Bearer $ARCHIVE_AUTH_TOKEN" \
  "https://claude.hangocthanh.io.vn/archive/sessions/search-semantic?user_id=thanh&q=triển%20khai%20VPS"
```

## 3. Hybrid R2/B2 storage

**Files:** `archive-api/r2_storage.py`, `archive-api/schema-v2.sql` (columns `r2_key`, `r2_size_bytes`)

**What changes:** When `R2_ENDPOINT_URL` is set, full transcripts are gzipped and uploaded to object storage. Postgres only keeps metadata + `r2_key` pointer. `GET /sessions/{id}` transparently hydrates from R2 when needed.

**Why:** Neon free tier is 0.5GB. With ~50 sessions/day at ~200KB JSONB each, you'd hit the cap in ~1 month. R2/B2 free tier is 10GB — enough for years.

**Setup:**
1. Create R2 bucket (Cloudflare) or B2 bucket (Backblaze)
2. Get access key + secret
3. Add to VPS `.env`:
   ```
   R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
   R2_ACCESS_KEY_ID=...
   R2_SECRET_ACCESS_KEY=...
   R2_BUCKET=mem0-transcripts
   ```
4. `docker compose up -d --build archive-api`

**Migration of existing sessions:** see `scripts/migrate-to-r2.py` (TODO — write when ready to migrate).

## 4. `load_context_for_continuation` MCP tool

**Files:** `archive-api/app.py` (endpoint `/sessions/{id}/context`), `scripts/archive-mcp.py`, `mcp-http-server/app.py`

**What it does:** Loads a past session's context into the current chat so user can continue the conversation. This was the missing piece — `main` only supports browsing/viewing, not continuation.

**3 strategies:**
- `compressed` (default): summary + first 5 + last 5 messages — ~3-5k tokens
- `full`: entire transcript — only for short sessions
- `rag`: embed `query`, find top-10 relevant messages — best for long sessions

**Usage in Claude/ChatGPT:**
```
"Load context from session abc-123 and continue helping me debug the Docker issue we discussed"
→ tool call: load_context_for_continuation(session_id=abc-123, strategy=rag, query="Docker issue")
```

## 5. Multi-platform support (Claude App + ChatGPT App)

**Files:** `mcp-http-server/` directory (new container), `Caddyfile` (new `/mcp/*` route)

**What changes:** Adds a remote MCP server speaking Streamable HTTP at `https://claude.hangocthanh.io.vn/mcp`. Three integration points:

| Platform | How to connect |
|---|---|
| **Claude Code** (VS Code/CLI) | Keep using stdio `scripts/archive-mcp.py` (no change) |
| **Claude App** (desktop/web) | Settings → Connectors → Add custom: URL `/mcp`, Bearer = MCP_BEARER_TOKEN |
| **ChatGPT App** (Plus/Pro) | Settings → Connectors → Create MCP: URL `/mcp`, Bearer = MCP_BEARER_TOKEN |
| **ChatGPT Custom GPT** (any plan) | Builder → Actions → Import from URL `/archive/openapi.json`, Bearer = ARCHIVE_AUTH_TOKEN |

**Extra ChatGPT-compat tools:** `search` and `fetch` are exposed as aliases for `search_old_sessions_semantic` and `get_session_summary` to satisfy ChatGPT's deep-research mode requirements.

## Deployment order

To roll out all 5 on the VPS:

```bash
# 1. Pull new code
cd ~/memory-stack
git pull origin new-features

# 2. Apply schema migration
docker exec -i memory-postgres psql -U mem0 mem0 < archive-api/schema-v2.sql

# 3. Update .env with new optional vars (OPENAI/ANTHROPIC/R2)
vi .env

# 4. Rebuild + restart
docker compose up -d --build

# 5. Verify
curl https://claude.hangocthanh.io.vn/health                                  # ok
curl -H "Authorization: Bearer $MCP_BEARER_TOKEN" https://claude.hangocthanh.io.vn/mcp \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'                      # tools list
curl -H "Authorization: Bearer $ARCHIVE_AUTH_TOKEN" \
     "https://claude.hangocthanh.io.vn/archive/openapi.json" | head           # OpenAPI spec
```
