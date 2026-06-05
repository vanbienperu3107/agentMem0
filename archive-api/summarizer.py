"""LLM-based summarizer for chat transcripts.

Replaces the `first_user[:200]` fallback with a real LLM-generated summary.

Two backends supported:
- Anthropic Claude Haiku (via API key — when not using OAT Max)
- OpenAI GPT-4o-mini (fallback)

Both produce ~200-400 word Vietnamese summary listing:
- main topics discussed
- conclusions reached
- action items

For OAT Max usage (no API charge), this module is called by the
archive-upload.py client where Claude Code's auth context is available.
"""
from __future__ import annotations
import os
import json
import httpx
from typing import Iterable, Optional

# ----- Anthropic backend -----

ANTHROPIC_MODEL = os.environ.get("SUMMARIZER_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

# ----- OpenAI backend -----

OPENAI_MODEL = os.environ.get("SUMMARIZER_OPENAI_MODEL", "gpt-4o-mini")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

PROMPT = """Tóm tắt cuộc hội thoại sau bằng tiếng Việt trong 200-400 từ.
Trình bày 3 phần:
1. Chủ đề chính: 1-2 câu mô tả nội dung
2. Kết luận / quyết định đã chốt: liệt kê gạch đầu dòng
3. Action items / việc cần làm tiếp: liệt kê gạch đầu dòng (nếu có)

Chỉ dùng thông tin trong transcript, không bịa.

Transcript:
{transcript}
"""

MAX_TRANSCRIPT_CHARS = 50_000  # truncate long sessions


def _format_messages(messages: Iterable[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        lines.append(f"[{role}]: {content}")
    text = "\n\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS] + "\n\n[...truncated...]"
    return text


def summarize_anthropic(transcript: str, timeout: float = 60) -> str:
    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": PROMPT.format(transcript=transcript)}],
    }
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    r = httpx.post(ANTHROPIC_URL, json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def summarize_openai(transcript: str, timeout: float = 60) -> str:
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    payload = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": PROMPT.format(transcript=transcript)}],
        "temperature": 0.3,
    }
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }
    r = httpx.post(OPENAI_URL, json=payload, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def summarize(messages: Iterable[dict], backend: Optional[str] = None) -> str:
    """Summarize a list of message dicts.

    backend: 'anthropic' | 'openai' | None (auto: anthropic if key set, else openai).
    """
    transcript = _format_messages(messages)
    backend = backend or os.environ.get("SUMMARIZER_BACKEND")
    if backend == "anthropic" or (backend is None and ANTHROPIC_KEY):
        return summarize_anthropic(transcript)
    return summarize_openai(transcript)


if __name__ == "__main__":
    # Quick test: pipe a JSON list of messages on stdin
    import sys
    msgs = json.load(sys.stdin)
    print(summarize(msgs))
