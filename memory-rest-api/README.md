# memory-rest-api

REST wrapper for [mem0](https://github.com/mem0ai/mem0) — exposes `/memories` CRUD endpoints for ChatGPT Custom GPT Actions and VS Code Continue.dev.

Shares the same Qdrant collection (`mem0_mcp_selfhosted`) with Claude Code MCP, so memories sync bidirectionally across all clients.

## Architecture

```
ChatGPT (web/desktop)         VS Code Continue.dev
        │                              │
        │ HTTPS                        │ HTTPS
        ▼                              ▼
Caddy → memory-rest-api (FastAPI, port 8002)
                │
                │ mem0 library
                ▼
        ┌───────┴──────────┐
        │                  │
   OpenAI gpt-4o-mini  Qdrant (same collection
   (fact extraction)    as Claude Code MCP)
```

## Endpoints

| Method | Path | OperationId | Description |
|---|---|---|---|
| GET | `/health` | `health` | Liveness probe (no auth) |
| POST | `/memories` | **`addMemory`** | Add a memory — mem0 extracts atomic facts via LLM |
| POST | `/memories/search` | **`searchMemory`** | Semantic search facts by query |
| GET | `/memories` | **`listMemories`** | List all memories for a user |
| DELETE | `/memories/{id}` | **`deleteMemory`** | Remove a memory |
| GET | `/openapi.json` | — | OpenAPI 3.x spec (auto-generated) |

Auth: `Authorization: Bearer <CHATGPT_AUTH_TOKEN>`.

## Required env vars

```
CHATGPT_AUTH_TOKEN     openssl rand -hex 32
QDRANT_URL             http://qdrant:6333 (internal Docker network)
QDRANT_API_KEY         shared with mem0 MCP layer
OPENAI_API_KEY         platform.openai.com (used for embedding + LLM)
DEFAULT_USER_ID        thanh (default user when client omits user_id)
COLLECTION_NAME        mem0_mcp_selfhosted (MUST match Claude Code MCP collection)
PUBLIC_URL             https://claude.hangocthanh.io.vn/memory (for OpenAPI servers field)
```

## Setup ChatGPT Custom GPT

1. Run `test-actions.sh` (Linux/Mac) or `test-actions.ps1` (Windows) to verify endpoints work.
2. Go to https://chatgpt.com/gpts/editor → Configure
3. Paste content of `openapi-for-chatgpt.yaml` into Actions → Schema
4. Authentication: API Key → Bearer → paste `CHATGPT_AUTH_TOKEN`
5. Privacy URL: `https://anthropic.com/legal/privacy` (placeholder)
6. Save → "Only me" (never share — token is embedded in GPT config)

See `docs/section-8-chatgpt-customgpt.md` for full setup walkthrough.

## Cost

| Operation | Cost per call |
|---|---|
| addMemory (LLM fact extraction) | ~$0.001 |
| searchMemory (embed only) | ~$0.00001 |
| listMemories (no LLM/embed) | $0 |

Total: ~$1.50/month for 50 add operations/day.

## Cross-platform memory sync

Because `COLLECTION_NAME=mem0_mcp_selfhosted` matches Claude Code MCP's collection:

- ChatGPT adds memory → Claude Code search returns it
- Claude Code adds memory → ChatGPT search returns it
- Continue.dev shares the same collection too

This is verified by Test 3 in plan Section 8.4.

## Troubleshooting

- **HTTP 401 Unauthorized** → token mismatch. Re-paste in GPT Authentication.
- **HTTP 500 on POST /memories** → check OPENAI_API_KEY validity, and ensure no non-ASCII chars in env (lesson learned: VN chars in placeholder).
- **Claude Code not seeing memories** → collection name mismatch. Verify with `claude mcp logs mem0`.
- **OpenAPI import fail in ChatGPT** → must include `components.schemas` (ChatGPT validator strict).

See plan Section 8 Troubleshooting (T8.1–T8.7) for full debug guide.
