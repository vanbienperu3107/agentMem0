# wikiAgent — Wiki Knowledge Layer

> Phase 1 of **Roadmap 3.0 — Personal AI Knowledge System**
> Turn every AI conversation (and later Markdown files & WhatsApp) into
> structured, searchable knowledge that Claude & ChatGPT can recall from any
> client via MCP/REST.

`wikiAgent` distills reusable technical facts from your conversations and stores
them in a single Qdrant collection (`wiki_knowledge`). It is designed to sit
next to [`agentMem0`](https://github.com/vanbienperu3107/agentMem0) and **reuse
its Qdrant instance and API keys — no new datastore, no new service required.**

---

## Why

`agentMem0` archives whole transcripts and manages loose mem0 facts. `wikiAgent`
adds the missing layer: **structured, deduplicated, multi-source knowledge** with
an explicit schema (`topic`, `content`, `source`, `tags`, `confidence`,
timestamps) that any AI client can query.

```
AI conversation ends
   ↓ summarizer.py (existing)
   ↓ knowledge_extractor.py   ← Haiku extracts structured facts
   ↓ wiki_knowledge (Qdrant)  ← source: "conversation"
   ↓ search_wiki / list_wiki_topics (MCP tools)
```

## Design principles (from Roadmap 3.0)

- **No new service** — runs on the Qdrant you already have.
- **LLM only for judgment** — deciding *what is a reusable fact*. Routing,
  filtering, and dedup are deterministic code.
- **Privacy-first** — a keyword filter blocks sensitive messages *before* any
  LLM call or storage.
- **Idempotent** — deterministic `uuid5(content)` ids mean re-ingesting the same
  fact overwrites in place instead of duplicating.

## Architecture

```
wiki_agent/
├── config.py             # all env vars in one place
├── embeddings.py         # OpenAI text-embedding-3-small (1536 dims)
├── qdrant_helper.py      # wiki_knowledge collection: ensure / upsert / search / scroll
├── knowledge_extractor.py# privacy filter → Haiku extract → embed + store
├── wiki_search.py        # search_wiki() + list_wiki_topics()
├── app.py                # REST API (FastAPI): ingest + query
└── mcp_server.py         # MCP HTTP server (Streamable HTTP, JSON-RPC)
```

### `wiki_knowledge` payload schema

| field        | type       | example                          |
|--------------|------------|----------------------------------|
| `topic`      | str        | `OCS/charging`                   |
| `content`    | str        | `MK201=50MB và MK311=50MB`       |
| `source`     | str        | `conversation` \| `file` \| `whatsapp` |
| `tags`       | list[str]  | `["OCS", "MK201", "Bitel"]`      |
| `confidence` | float      | `0.91`                           |
| `created_at` / `updated_at` | ISO 8601 | `2026-07-05T…Z`        |
| `ref`        | str \| null| session id / file path / thread  |

## API

### REST (`wiki_agent.app:app`, port 8010)

| Method | Path                     | Purpose                                     |
|--------|--------------------------|---------------------------------------------|
| POST   | `/ingest/conversation`   | Hướng B — extract facts from a transcript   |
| POST   | `/ingest/file`           | Hướng A — index a Markdown file (conf=1.0)  |
| GET    | `/wiki/search`           | semantic search (`q`, `topic?`, `source?`, `limit`) |
| GET    | `/wiki/topics`           | topic list with counts + sources            |
| GET    | `/health`                | liveness                                    |

All non-health endpoints require `Authorization: Bearer $WIKI_AUTH_TOKEN`.

### MCP tools (`wiki_agent.mcp_server:app`, port 8011)

- `search_wiki(query, topic?, source?, limit=5)`
- `list_wiki_topics()`

Streamable HTTP transport (MCP 2025-03-26), same shape as the agentMem0
`mcp-http-server`, so it can sit behind the same Caddy/OAuth front door.

## Quick start

```bash
cp .env.example .env        # fill in OPENAI_API_KEY, ANTHROPIC_API_KEY, tokens
docker compose up --build   # brings up qdrant + wiki-api + wiki-mcp
```

Ingest a conversation:

```bash
curl -s localhost:8010/ingest/conversation \
  -H "Authorization: Bearer $WIKI_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"transcript":[{"role":"user","content":"MK201 charge 50MB"}],"session_id":"s1"}'
```

Search it back:

```bash
curl -s "localhost:8010/wiki/search?q=OCS%20charge&limit=3" \
  -H "Authorization: Bearer $WIKI_AUTH_TOKEN"
```

## Integrating with agentMem0

Add three lines after the summarizer in `archive-api/app.py`:

```python
from wiki_agent import knowledge_extractor
n_facts = knowledge_extractor.extract_and_store(transcript, session_id=session_id)
```

Point `QDRANT_INTERNAL_URL` at the shared Qdrant and drop the bundled `qdrant`
service from `docker-compose.yml`.

## Development

```bash
pip install -r requirements-dev.txt
pytest -q
```

Tests mock the LLM, embeddings, and Qdrant, so they run offline. CI lives in
`.github/workflows/ci.yml` (active once this project is extracted to its own
repository).

## Roadmap

This repo implements **Phase 1**. The endpoints and schema are already shaped
for later phases:

| Phase | What                              | Status (in this repo)               |
|-------|-----------------------------------|-------------------------------------|
| 1     | Wiki Knowledge Layer              | ✅ implemented                       |
| 2     | File Sync (`/ingest/file`)        | ✅ endpoint ready, awaits syncthingMem0 WSS |
| 3     | WhatsApp pipeline                 | 🔜 `source: "whatsapp"` reserved    |
| 4     | RAG 2.0 (hybrid + reranker)       | 🔒 after 50 real queries            |
| 5     | Multi-source consolidation        | 🔒 nightly job                      |

Full plan: [`docs/ROADMAP-3.0.html`](docs/ROADMAP-3.0.html).

## License

Apache-2.0 — see [LICENSE](LICENSE).
Part of the Personal AI Knowledge System · Hà Ngọc Thanh · vanbienperu3107
