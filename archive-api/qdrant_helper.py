"""Qdrant client wrapper for the chat_summaries collection.

Reuses the existing Qdrant instance (already running for mem0 layer).
Creates a separate collection `chat_summaries` with 1536-dim vectors
to keep mem0 facts and chat summaries cleanly separated.
"""
from __future__ import annotations
import os
import uuid
import httpx
from typing import List, Tuple, Optional

QDRANT_URL = os.environ.get("QDRANT_INTERNAL_URL", "http://qdrant:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY")
COLLECTION = "chat_summaries"
DIMS = int(os.environ.get("MEM0_EMBED_DIMS", "1536"))


def _headers():
    h = {"Content-Type": "application/json"}
    if QDRANT_API_KEY:
        h["api-key"] = QDRANT_API_KEY
    return h


def ensure_collection():
    """Create chat_summaries collection if not exists."""
    r = httpx.get(f"{QDRANT_URL}/collections/{COLLECTION}", headers=_headers())
    if r.status_code == 200:
        return
    body = {
        "vectors": {"size": DIMS, "distance": "Cosine"},
    }
    r = httpx.put(
        f"{QDRANT_URL}/collections/{COLLECTION}",
        json=body,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()


def upsert(point_id: str, vector: List[float], payload: dict) -> str:
    """Insert/update a single point. Returns the point_id."""
    body = {
        "points": [{
            "id": point_id,
            "vector": vector,
            "payload": payload,
        }]
    }
    r = httpx.put(
        f"{QDRANT_URL}/collections/{COLLECTION}/points?wait=true",
        json=body,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return point_id


def search(
    vector: List[float],
    limit: int = 10,
    user_id: Optional[str] = None,
) -> List[dict]:
    """Search top-K similar summaries. Returns list of {id, score, payload}."""
    body = {"vector": vector, "limit": limit, "with_payload": True}
    if user_id:
        body["filter"] = {
            "must": [{"key": "user_id", "match": {"value": user_id}}]
        }
    r = httpx.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
        json=body,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["result"]


def delete(point_id: str):
    body = {"points": [point_id]}
    r = httpx.post(
        f"{QDRANT_URL}/collections/{COLLECTION}/points/delete?wait=true",
        json=body,
        headers=_headers(),
        timeout=30,
    )
    r.raise_for_status()
