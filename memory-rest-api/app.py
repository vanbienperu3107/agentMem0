"""REST wrapper for mem0 — used by ChatGPT Custom GPT and VS Code Continue.dev.

Shares the same Qdrant collection with Claude Code MCP so memories sync bidirectionally.

Endpoints:
- POST   /memories          — add a new memory (mem0 extracts facts via LLM)
- POST   /memories/search   — semantic search facts
- GET    /memories          — list all memories
- DELETE /memories/{id}     — remove a memory
- GET    /health            — liveness probe
- GET    /openapi.json      — OpenAPI 3.x spec (for ChatGPT Custom GPT import)

Auth: all endpoints except /health require `Authorization: Bearer <CHATGPT_AUTH_TOKEN>`.
"""
import os
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from mem0 import Memory

AUTH = os.environ["CHATGPT_AUTH_TOKEN"]
QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
DEFAULT_USER = os.environ.get("DEFAULT_USER_ID", "thanh")
COLLECTION = os.environ.get("COLLECTION_NAME", "mem0_mcp_selfhosted")
PUBLIC_URL = os.environ.get(
    "PUBLIC_URL",
    "https://claude.hangocthanh.io.vn/memory",
)

config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "url": QDRANT_URL,
            "api_key": QDRANT_API_KEY,
            "collection_name": COLLECTION,
            "embedding_model_dims": 1536,
        },
    },
    "embedder": {
        "provider": "openai",
        "config": {"api_key": OPENAI_API_KEY, "model": "text-embedding-3-small"},
    },
    "llm": {
        "provider": "openai",
        "config": {"api_key": OPENAI_API_KEY, "model": "gpt-4o-mini"},
    },
    "version": "v1.1",
}
mem = Memory.from_config(config)

# Explicit servers field so OpenAPI spec has correct base URL when imported
# by ChatGPT Custom GPT / external tools.
app = FastAPI(
    title="Mem0 REST Wrapper",
    description="Self-hosted mem0 wrapper. Shared collection with Claude Code MCP.",
    version="1.0",
    servers=[{"url": PUBLIC_URL}],
)


def check(token: Optional[str]):
    if not token or token != f"Bearer {AUTH}":
        raise HTTPException(401, "Unauthorized")


class AddBody(BaseModel):
    text: str
    user_id: Optional[str] = None
    metadata: Optional[dict] = None


class SearchBody(BaseModel):
    query: str
    user_id: Optional[str] = None
    limit: int = 10


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/memories", operation_id="addMemory")
def add_memory(body: AddBody, authorization: Optional[str] = Header(None)):
    """Add a new memory. ChatGPT calls this when user asks to remember something."""
    check(authorization)
    uid = body.user_id or DEFAULT_USER
    result = mem.add(body.text, user_id=uid, metadata=body.metadata or {})
    return {"ok": True, "result": result}


@app.post("/memories/search", operation_id="searchMemory")
def search_memory(body: SearchBody, authorization: Optional[str] = Header(None)):
    """Search memories by semantic similarity. ChatGPT calls this BEFORE answering."""
    check(authorization)
    uid = body.user_id or DEFAULT_USER
    results = mem.search(body.query, user_id=uid, limit=body.limit)
    items = results.get("results", results) if isinstance(results, dict) else results
    return {"results": items}


@app.get("/memories", operation_id="listMemories")
def list_memories(
    user_id: Optional[str] = None,
    limit: int = 50,
    authorization: Optional[str] = Header(None),
):
    """List all memories for a user (for browsing in Custom GPT UI)."""
    check(authorization)
    uid = user_id or DEFAULT_USER
    all_mem = mem.get_all(user_id=uid)
    items = all_mem.get("results", all_mem) if isinstance(all_mem, dict) else all_mem
    return {"results": items[:limit]}


@app.delete("/memories/{memory_id}", operation_id="deleteMemory")
def delete_memory(memory_id: str, authorization: Optional[str] = Header(None)):
    """Delete a specific memory by ID."""
    check(authorization)
    mem.delete(memory_id=memory_id)
    return {"ok": True, "deleted_id": memory_id}
