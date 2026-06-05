"""MCP HTTP server — exposes archive tools to Claude App + ChatGPT App.

Transport: Streamable HTTP (MCP spec 2025-03-26).
Backend: proxies to archive-api on the same Docker network.

Auth: clients must send `Authorization: Bearer <MCP_BEARER_TOKEN>`.

Tools exposed (same as archive-mcp.py stdio version, plus continuation):
- list_old_sessions
- search_old_sessions             (keyword/ILIKE)
- search_old_sessions_semantic    (Qdrant semantic)
- get_session_summary
- get_old_session
- load_context_for_continuation   (use case 3c — RAG/compressed/full)
- search                          (alias for ChatGPT deep-research compatibility)
- fetch                           (alias for ChatGPT deep-research compatibility)
- add_memory                      (save fact/summary to mem0 long-term memory)
- search_memories                 (semantic recall from mem0)
- save_full_session               (save FULL transcript to archive)
"""
from __future__ import annotations
import os
import json
import asyncio
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

try:
    from . import oauth
except ImportError:
    import oauth  # type: ignore

ARCHIVE_URL = os.environ["ARCHIVE_URL"]           # http://archive-api:8001
ARCHIVE_TOKEN = os.environ["ARCHIVE_AUTH_TOKEN"]
USER_ID = os.environ.get("USER_ID", "thanh")
EXPECTED_BEARER = os.environ["MCP_BEARER_TOKEN"]
HEADERS = {"Authorization": f"Bearer {ARCHIVE_TOKEN}"}

# Memory REST API (mem0 wrapper) — powers add_memory / search_memories.
# Cung backend voi ChatGPT Custom GPT (memory-rest-api), share Qdrant collection.
MEMORY_API_URL = os.environ.get("MEMORY_API_URL", "http://memory-rest-api:8002")
MEMORY_API_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")
MEM_HEADERS = {"Authorization": f"Bearer {MEMORY_API_TOKEN}"} if MEMORY_API_TOKEN else {}

app = FastAPI(
    title="mem0custom MCP HTTP",
    description="Remote MCP server for Claude App + ChatGPT App with OAuth 2.1 + DCR",
    version="1.2.0",
)

# CORS: Claude Desktop App + ChatGPT App fetch OAuth endpoints from browser
# context (claude.ai / chatgpt.com). Without CORS, preflight OPTIONS returns
# 405 and DCR fails with "Couldn't register".
# Wildcard origin since DCR/OAuth flow uses public PKCE (no credentials).
# Claude Desktop may use various Origin headers (claude.ai, app://, null, etc).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["WWW-Authenticate", "Content-Type"],
    max_age=3600,
)

# Mount OAuth router for /.well-known/* and /register, /authorize, /token
# Prefix "/mcp" because Caddy forwards /mcp/* without strip_prefix.
# Internal routes become /mcp/.well-known/..., /mcp/register, etc.
app.include_router(oauth.router, prefix="/mcp")


# ============================================================
# Auth helper (inline check used by /mcp endpoint only)
# ============================================================
#
# NOTE: We avoid @app.middleware("http") because Starlette BaseHTTPMiddleware
# has a known bug where Content-Length mismatches for responses with multi-byte
# UTF-8 content (Vietnamese, etc), causing uvicorn RuntimeError and truncated
# response. See https://github.com/encode/starlette/issues/919
#
# OAuth discovery + DCR endpoints (/.well-known/*, /register, /authorize, /token)
# are already public (no auth check). Only POST /mcp requires Bearer token,
# enforced inline in mcp_endpoint() below.


def _build_401_response() -> JSONResponse:
    """Build 401 with WWW-Authenticate per RFC 6750 + RFC 9728."""
    return JSONResponse(
        {"error": "unauthorized"},
        status_code=401,
        headers={
            "WWW-Authenticate": (
                f'Bearer realm="mcp", '
                f'resource_metadata="{oauth.ISSUER}/.well-known/oauth-protected-resource"'
            )
        },
    )


def _check_auth(request: Request) -> Optional[JSONResponse]:
    """Return 401 response if auth invalid, None if OK."""
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not oauth.verify_token(token):
        return _build_401_response()
    return None


# ============================================================
# Tool definitions (MCP-compatible JSON Schema)
# ============================================================

TOOLS = [
    {
        "name": "list_old_sessions",
        "description": (
            "List archived chat sessions. Use to browse history by date or project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_tag": {"type": "string", "description": "Filter by project name"},
                "date_from": {"type": "string", "description": "ISO date"},
                "date_to": {"type": "string", "description": "ISO date"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "search_old_sessions",
        "description": (
            "Keyword search across session summaries (ILIKE). Fast, exact match."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"q": {"type": "string"}},
            "required": ["q"],
        },
    },
    {
        "name": "search_old_sessions_semantic",
        "description": (
            "Semantic search via embeddings. Better for fuzzy 'I discussed something about X' queries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["q"],
        },
    },
    {
        "name": "get_session_summary",
        "description": (
            "Compact view: metadata + first/last 5 messages. "
            "PREFER this over get_old_session for overviews."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "get_old_session",
        "description": (
            "Fetch FULL transcript. Large response — only use when user explicitly wants full detail."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "load_context_for_continuation",
        "description": (
            "Load context from a past session so user can CONTINUE the conversation. "
            "Returns formatted block ready to use as context. "
            "Choose strategy: 'compressed' (default, summary + first/last), "
            "'full' (small sessions only), 'rag' (find relevant chunks for a query)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "strategy": {
                    "type": "string",
                    "enum": ["full", "compressed", "rag"],
                    "default": "compressed",
                },
                "query": {
                    "type": "string",
                    "description": "Required when strategy=rag — the new question being asked",
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "add_memory",
        "description": (
            "Save an important fact, preference, decision, or session summary to "
            "long-term memory (mem0). mem0 auto-extracts and de-duplicates facts. "
            "Use when the user says remember this, or to persist a /sum summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Fact(s)/summary to remember; one fact per line is fine."},
                "metadata": {"type": "object", "description": "Optional JSON metadata (e.g. project, date)."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "search_memories",
        "description": (
            "Semantic search over long-term memories (mem0). "
            "Use to recall what the user told you before about a topic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_full_session",
        "description": (
            "Save the FULL transcript of a conversation to the archive (Postgres/R2). "
            "Pass the complete list of messages. Use when the user wants to archive/save "
            "the entire current session verbatim, not just a summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "transcript": {
                    "type": "array",
                    "description": "List of message objects (role/content) — the full conversation.",
                    "items": {"type": "object"},
                },
                "summary": {"type": "string", "description": "Optional short summary."},
                "project_tag": {"type": "string", "description": "Optional project label."},
            },
            "required": ["transcript"],
        },
    },
    # ChatGPT deep-research compatibility — aliases
    {
        "name": "search",
        "description": (
            "[ChatGPT compat] Search across chat archive. Alias for search_old_sessions_semantic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "fetch",
        "description": (
            "[ChatGPT compat] Fetch a specific session by id. Alias for get_session_summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
]


# ============================================================
# Tool execution
# ============================================================

async def call_archive(method: str, path: str, **kwargs):
    async with httpx.AsyncClient(timeout=60, headers=HEADERS) as c:
        if method == "GET":
            r = await c.get(f"{ARCHIVE_URL}{path}", **kwargs)
        elif method == "POST":
            r = await c.post(f"{ARCHIVE_URL}{path}", **kwargs)
        else:
            raise ValueError(method)
        r.raise_for_status()
        return r.json()


async def call_memory(method: str, path: str, **kwargs):
    """Goi memory-rest-api (mem0 wrapper). Dung cho add_memory/search_memories."""
    async with httpx.AsyncClient(timeout=60, headers=MEM_HEADERS) as c:
        if method == "GET":
            r = await c.get(f"{MEMORY_API_URL}{path}", **kwargs)
        elif method == "POST":
            r = await c.post(f"{MEMORY_API_URL}{path}", **kwargs)
        else:
            raise ValueError(method)
        r.raise_for_status()
        return r.json()


async def exec_tool(name: str, args: dict):
    if name == "list_old_sessions":
        return await call_archive("GET", "/sessions", params={"user_id": USER_ID, **args})

    if name == "search_old_sessions":
        return await call_archive(
            "GET", "/sessions",
            params={"user_id": USER_ID, "q": args["q"], "limit": 20},
        )

    if name in ("search_old_sessions_semantic", "search"):
        q = args.get("q") or args.get("query")
        return await call_archive(
            "GET", "/sessions/search-semantic",
            params={"user_id": USER_ID, "q": q, "limit": args.get("limit", 10)},
        )

    if name in ("get_session_summary", "fetch"):
        sid = args.get("session_id") or args.get("id")
        data = await call_archive("GET", f"/sessions/{sid}")
        transcript = data.get("transcript") or []
        return {
            "id": data["id"],
            "started_at": data["started_at"],
            "ended_at": data.get("ended_at"),
            "project_tag": data.get("project_tag"),
            "summary": data.get("llm_summary") or data.get("summary"),
            "message_count": data["message_count"],
            "first_messages": transcript[:5],
            "last_messages": transcript[-5:] if len(transcript) > 5 else [],
        }

    if name == "get_old_session":
        return await call_archive("GET", f"/sessions/{args['session_id']}")

    if name == "load_context_for_continuation":
        params = {"strategy": args.get("strategy", "compressed")}
        if "query" in args:
            params["query"] = args["query"]
        return await call_archive(
            "GET", f"/sessions/{args['session_id']}/context", params=params,
        )

    if name == "add_memory":
        body = {"text": args["text"], "user_id": USER_ID}
        if args.get("metadata"):
            body["metadata"] = args["metadata"]
        return await call_memory("POST", "/memories", json=body)

    if name == "search_memories":
        q = args.get("query") or args.get("q")
        return await call_memory(
            "POST", "/memories/search",
            json={"query": q, "user_id": USER_ID, "limit": args.get("limit", 10)},
        )

    if name == "save_full_session":
        import datetime
        transcript = args.get("transcript") or []
        now = datetime.datetime.utcnow().isoformat()
        payload = {
            "user_id": USER_ID,
            "project_tag": args.get("project_tag", "mcp"),
            "started_at": now,
            "ended_at": now,
            "message_count": len(transcript),
            "transcript": transcript,
            "summary": args.get("summary", ""),
            "metadata": {"source": "save_full_session"},
        }
        return await call_archive("POST", "/sessions", json=payload)

    raise HTTPException(400, f"Unknown tool: {name}")


# ============================================================
# MCP JSON-RPC endpoint (Streamable HTTP)
# ============================================================

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    """Handle MCP JSON-RPC over HTTP (Streamable HTTP transport)."""
    # Inline auth check (avoid BaseHTTPMiddleware Content-Length bug)
    err = _check_auth(request)
    if err is not None:
        return err
    body = await request.json()
    method = body.get("method")
    req_id = body.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mem0custom-archive", "version": "1.2.0"},
            },
        }

    if method == "notifications/initialized":
        return JSONResponse(status_code=204, content=None)

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = body.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            result = await exec_tool(name, args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ],
                    "isError": False,
                },
            }
        except HTTPException as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": e.status_code, "message": e.detail},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
