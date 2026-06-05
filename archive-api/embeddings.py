"""OpenAI embeddings helper for semantic search.

Uses text-embedding-3-small (1536 dims, $0.00002/1k tokens).
Same model already used by mem0 layer per .env config.
"""
from __future__ import annotations
import os
import httpx
from typing import List

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
EMBED_MODEL = os.environ.get("MEM0_EMBED_MODEL", "text-embedding-3-small")
EMBED_DIMS = int(os.environ.get("MEM0_EMBED_DIMS", "1536"))
OPENAI_URL = "https://api.openai.com/v1/embeddings"


def embed(text: str) -> List[float]:
    """Return embedding vector for a single text."""
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    payload = {"input": text, "model": EMBED_MODEL}
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }
    r = httpx.post(OPENAI_URL, json=payload, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def embed_batch(texts: List[str]) -> List[List[float]]:
    """Embed multiple texts in one API call (cheaper)."""
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    payload = {"input": texts, "model": EMBED_MODEL}
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }
    r = httpx.post(OPENAI_URL, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    return [d["embedding"] for d in r.json()["data"]]
