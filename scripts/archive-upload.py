#!/usr/bin/env python3
"""Upload Claude Code session transcripts to archive API.

NEW on branch new-features:
- After upload, calls /sessions/{id}/summarize to generate LLM summary + embedding
  (only if SUMMARIZE_ON_UPLOAD=1).

Required env vars:
    ARCHIVE_URL          e.g. https://claude.hangocthanh.io.vn/archive
    ARCHIVE_AUTH_TOKEN   bearer token (matches VPS .env)
    USER_ID              default "thanh"

Optional:
    SUMMARIZE_ON_UPLOAD  set to "1" to trigger LLM summary after each upload
"""
import json
import os
import sys
import hashlib
from pathlib import Path
import urllib.request

ARCHIVE_URL = os.environ["ARCHIVE_URL"]
ARCHIVE_TOKEN = os.environ["ARCHIVE_AUTH_TOKEN"]
USER_ID = os.environ.get("USER_ID", "thanh")
SUMMARIZE = os.environ.get("SUMMARIZE_ON_UPLOAD") == "1"
STATE_FILE = Path.home() / ".cache" / "claude-archive-state.json"
STATE_FILE.parent.mkdir(exist_ok=True)


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"uploaded": []}


def save_state(s):
    STATE_FILE.write_text(json.dumps(s))


def file_id(p: Path):
    h = hashlib.sha256()
    h.update(str(p).encode())
    h.update(str(p.stat().st_mtime).encode())
    return h.hexdigest()[:16]


def parse_session(jsonl_path: Path):
    messages, workspace, times = [], None, []
    for line in jsonl_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        if "cwd" in m and not workspace:
            workspace = m["cwd"]
        if m.get("type") in ("user", "assistant"):
            content = m.get("message", {}).get("content")
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            messages.append({
                "role": m.get("type"),
                "content": content,
                "timestamp": m.get("timestamp"),
            })
            if m.get("timestamp"):
                times.append(m["timestamp"])
    if not messages or not times:
        return None
    project_tag = Path(workspace).name if workspace else None
    first_user = next((m["content"] for m in messages if m["role"] == "user"), "")
    return {
        "user_id": USER_ID,
        "project_tag": project_tag,
        "workspace_path": workspace,
        "started_at": min(times),
        "ended_at": max(times),
        "message_count": len(messages),
        "transcript": messages,
        "summary": (first_user or "")[:200],  # fallback; LLM summary added later
        "metadata": {"source_file": jsonl_path.name},
    }


def upload(data):
    req = urllib.request.Request(
        f"{ARCHIVE_URL}/sessions",
        data=json.dumps(data, default=str).encode(),
        headers={
            "Authorization": f"Bearer {ARCHIVE_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def trigger_summary(session_id: str):
    """Tell archive-api to generate LLM summary + embedding for this session."""
    req = urllib.request.Request(
        f"{ARCHIVE_URL}/sessions/{session_id}/summarize",
        data=b"",  # empty POST body
        headers={"Authorization": f"Bearer {ARCHIVE_TOKEN}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main():
    state = load_state()
    uploaded = set(state["uploaded"])
    sessions_dir = Path.home() / ".claude" / "projects"
    if not sessions_dir.exists():
        print("No Claude Code sessions found", file=sys.stderr)
        return
    new = 0
    for jsonl in sessions_dir.rglob("*.jsonl"):
        fid = file_id(jsonl)
        if fid in uploaded:
            continue
        data = parse_session(jsonl)
        if not data:
            continue
        try:
            result = upload(data)
            uploaded.add(fid)
            new += 1
            print(f"Uploaded {jsonl.name} -> project={data['project_tag']} id={result['id']}")

            if SUMMARIZE:
                try:
                    s = trigger_summary(result["id"])
                    print(f"  + LLM summary: {s['summary'][:80]}...")
                except Exception as e:
                    print(f"  ! summarize failed: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Failed {jsonl.name}: {e}", file=sys.stderr)
    state["uploaded"] = list(uploaded)
    save_state(state)
    print(f"Done. {new} new sessions uploaded.")


if __name__ == "__main__":
    main()
