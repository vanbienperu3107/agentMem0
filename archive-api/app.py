"""Archive API v2 - REST endpoints for storing/retrieving Claude Code transcripts."""
import os
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Literal

try:
    from . import summarizer, embeddings, qdrant_helper, r2_storage
except ImportError:
    import summarizer, embeddings, qdrant_helper, r2_storage

DB_URL = os.environ["DB_URL"]
AUTH = os.environ["ARCHIVE_AUTH_TOKEN"]
USE_R2 = bool(os.environ.get("R2_ENDPOINT_URL"))
USE_SEMANTIC = bool(os.environ.get("OPENAI_API_KEY"))

app = FastAPI(
    title="Chat Archive API",
    description="Archive of past conversations with semantic search and context loading.",
    version="2.0.0",
    servers=[{"url": "https://claude.hangocthanh.io.vn/archive"}],
)


def check(token):
    if not token or token != f"Bearer {AUTH}":
        raise HTTPException(401, "Unauthorized")


def conn():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)


class Session(BaseModel):
    user_id: str
    project_tag: Optional[str] = None
    started_at: str
    ended_at: Optional[str] = None
    message_count: int
    transcript: list
    summary: Optional[str] = None
    workspace_path: Optional[str] = None
    metadata: dict = {}


@app.post("/sessions")
def create(s: Session, authorization: str = Header(None)):
    check(authorization)
    r2_key, r2_size = None, None
    transcript_to_store = s.transcript
    if USE_R2:
        try:
            r2_key, r2_size = r2_storage.upload_transcript(s.user_id, s.transcript)
            transcript_to_store = []
        except Exception as e:
            print(f"R2 upload failed: {e}")
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO chat_sessions (user_id, project_tag, started_at, ended_at, message_count, transcript, summary, workspace_path, metadata, r2_key, r2_size_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (s.user_id, s.project_tag, s.started_at, s.ended_at, s.message_count, Json(transcript_to_store), s.summary, s.workspace_path, Json(s.metadata), r2_key, r2_size),
        )
        sid = str(cur.fetchone()["id"])
    return {"id": sid, "r2_key": r2_key}


@app.get("/sessions")
def list_sessions(user_id: str, project_tag: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None, q: Optional[str] = None, limit: int = 100, authorization: str = Header(None)):
    check(authorization)
    sql = "SELECT id, started_at, project_tag, message_count, COALESCE(llm_summary, summary) AS summary FROM chat_sessions WHERE user_id=%s"
    args = [user_id]
    if project_tag:
        sql += " AND project_tag=%s"
        args.append(project_tag)
    if date_from:
        sql += " AND started_at >= %s"
        args.append(date_from)
    if date_to:
        sql += " AND started_at <= %s"
        args.append(date_to)
    if q:
        sql += " AND (COALESCE(llm_summary, summary) ILIKE %s)"
        args.append(f"%{q}%")
    sql += " ORDER BY started_at DESC LIMIT %s"
    args.append(limit)
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


@app.get("/sessions/search-semantic")
def search_semantic(q: str, user_id: str, limit: int = 10, authorization: str = Header(None)):
    check(authorization)
    if not USE_SEMANTIC:
        raise HTTPException(503, "OPENAI_API_KEY missing")
    qvec = embeddings.embed(q)
    hits = qdrant_helper.search(qvec, limit=limit, user_id=user_id)
    if not hits:
        return []
    ids = [h["id"] for h in hits]
    score_map = {h["id"]: h["score"] for h in hits}
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, started_at, project_tag, message_count, COALESCE(llm_summary, summary) AS summary FROM chat_sessions WHERE id = ANY(%s::uuid[])",
            (ids,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["score"] = score_map.get(str(r["id"]))
    rows.sort(key=lambda r: r["score"] or 0, reverse=True)
    return rows


@app.get("/sessions/{session_id}")
def get_session(session_id: str, authorization: str = Header(None)):
    check(authorization)
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM chat_sessions WHERE id=%s", (session_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404)
        data = dict(r)
    if data.get("r2_key") and not data.get("transcript"):
        try:
            data["transcript"] = r2_storage.download_transcript(data["r2_key"])
        except Exception as e:
            data["transcript"] = []
            data["transcript_error"] = str(e)
    return data


@app.post("/sessions/{session_id}/summarize")
def summarize_session(session_id: str, authorization: str = Header(None)):
    check(authorization)
    sess = get_session(session_id, authorization)
    transcript = sess.get("transcript", [])
    if not transcript:
        raise HTTPException(400, "No transcript to summarize")
    summary_text = summarizer.summarize(transcript)
    with conn() as c, c.cursor() as cur:
        cur.execute("UPDATE chat_sessions SET llm_summary=%s WHERE id=%s", (summary_text, session_id))
    if USE_SEMANTIC:
        try:
            vec = embeddings.embed(summary_text)
            qdrant_helper.ensure_collection()
            qdrant_helper.upsert(point_id=session_id, vector=vec, payload={"user_id": sess["user_id"], "project_tag": sess.get("project_tag"), "started_at": str(sess["started_at"])})
            with conn() as c, c.cursor() as cur:
                cur.execute("UPDATE chat_sessions SET embedding_id=%s WHERE id=%s", (session_id, session_id))
        except Exception as e:
            print(f"Embedding/upsert failed (non-fatal): {e}")
    return {"id": session_id, "summary": summary_text}


@app.get("/sessions/{session_id}/context")
def load_context(session_id: str, strategy: Literal["full", "compressed", "rag"] = "compressed", query: Optional[str] = None, authorization: str = Header(None)):
    check(authorization)
    sess = get_session(session_id, authorization)
    transcript = sess.get("transcript", [])
    summary = sess.get("llm_summary") or sess.get("summary") or ""
    if strategy == "full":
        ctx = _format_full(sess, transcript, summary)
    elif strategy == "rag":
        if not query:
            raise HTTPException(400, "query parameter required for rag strategy")
        ctx = _format_rag(sess, transcript, summary, query)
    else:
        ctx = _format_compressed(sess, transcript, summary)
    return {"session_id": session_id, "strategy": strategy, "context": ctx, "instruction": "Hay doc context tren roi tiep tuc thao luan dua tren no."}


def _format_full(sess, transcript, summary):
    header = f"[CONTEXT tu session {sess['started_at']}, project={sess.get('project_tag', 'unknown')}]\nTom tat: {summary}\n\nToan bo transcript ({len(transcript)} messages):\n"
    body = "\n\n".join(_fmt_msg(i, m) for i, m in enumerate(transcript))
    return header + body


def _format_compressed(sess, transcript, summary):
    first = transcript[:5]
    last = transcript[-5:] if len(transcript) > 5 else []
    return f"[CONTEXT tu session {sess['started_at']}, project={sess.get('project_tag', 'unknown')}]\nTom tat: {summary}\n\n5 message dau:\n" + "\n\n".join(_fmt_msg(i, m) for i, m in enumerate(first)) + "\n\n5 message cuoi:\n" + "\n\n".join(_fmt_msg(i, m) for i, m in enumerate(last))


def _format_rag(sess, transcript, summary, query):
    if not USE_SEMANTIC or not transcript:
        return _format_compressed(sess, transcript, summary)
    qvec = embeddings.embed(query)
    msg_texts = []
    for i, m in enumerate(transcript):
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        if isinstance(content, str) and len(content) > 20:
            msg_texts.append((i, content))
    if not msg_texts:
        return _format_compressed(sess, transcript, summary)
    try:
        vecs = embeddings.embed_batch([t for _, t in msg_texts])
    except Exception:
        return _format_compressed(sess, transcript, summary)

    def cosine(a, b):
        from math import sqrt
        dot = sum(x * y for x, y in zip(a, b))
        na = sqrt(sum(x * x for x in a))
        nb = sqrt(sum(y * y for y in b))
        return dot / (na * nb + 1e-9)

    scored = [(cosine(qvec, v), i) for v, (i, _) in zip(vecs, msg_texts)]
    scored.sort(reverse=True)
    top_indices = sorted({i for _, i in scored[:10]})
    relevant = [transcript[i] for i in top_indices]
    return f"[CONTEXT tu session {sess['started_at']}, project={sess.get('project_tag', 'unknown')}]\nTom tat: {summary}\n\nCac doan lien quan toi cau hoi \"{query}\":\n" + "\n\n".join(_fmt_msg(top_indices[k], m) for k, m in enumerate(relevant))


def _fmt_msg(idx, m):
    role = m.get("role", "?")
    content = m.get("content", "")
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return f"[#{idx} {role}]: {content}"


class CompactSummary(BaseModel):
    user_id: str
    session_id: Optional[str] = None
    summary_text: str
    messages_before: int = 0
    position_in_session: Optional[int] = None
    metadata: dict = {}


@app.post("/compact-summaries")
def create_summary(s: CompactSummary, authorization: str = Header(None)):
    check(authorization)
    with conn() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO compact_summaries (user_id, session_id, summary_text, messages_before, position_in_session, metadata) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (s.user_id, s.session_id, s.summary_text, s.messages_before, s.position_in_session, Json(s.metadata)),
        )
        return {"id": str(cur.fetchone()["id"])}


@app.get("/compact-summaries")
def list_compact(user_id: str, q: Optional[str] = None, limit: int = 50, authorization: str = Header(None)):
    check(authorization)
    sql = "SELECT id, session_id, compacted_at, summary_text, messages_before FROM compact_summaries WHERE user_id=%s"
    args = [user_id]
    if q:
        sql += " AND summary_text ILIKE %s"
        args.append(f"%{q}%")
    sql += " ORDER BY compacted_at DESC LIMIT %s"
    args.append(limit)
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]


@app.get("/compact-summaries/{summary_id}")
def get_compact(summary_id: str, authorization: str = Header(None)):
    check(authorization)
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM compact_summaries WHERE id=%s", (summary_id,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404)
        return dict(r)


@app.get("/health")
def health():
    return "ok"
