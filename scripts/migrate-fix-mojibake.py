#!/usr/bin/env python3
"""Migration: fix mojibake trong chat_sessions do archive-upload.py thiếu encoding=utf-8.

ARCHIVE_DB_URL=postgresql://... python3 migrate-fix-mojibake.py --dry-run
ARCHIVE_DB_URL=postgresql://... python3 migrate-fix-mojibake.py --execute --confirm I_HAVE_BACKUP
"""
import argparse, json, os, re, sys

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor, Json
except ImportError:
    print("Cần: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

VN_RE = re.compile(r"[ăâêôơưđáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]", re.IGNORECASE)


def try_fix(s):
    if not isinstance(s, str) or not s:
        return None
    if all(ord(c) < 128 for c in s):
        return None
    def hints(t):
        return t.count("Ã") + t.count("Â") + t.count("Ä") + t.count("á»") + t.count("áº")
    orig_hints = hints(s)
    if orig_hints == 0:
        return None
    candidates = []
    for enc in ("cp1252", "latin-1"):
        try:
            bytes_ = s.encode(enc, errors="replace")
            decoded = bytes_.decode("utf-8", errors="replace")
            new_hints = hints(decoded)
            vn = len(VN_RE.findall(decoded))
            repl = decoded.count("�")
            if new_hints < orig_hints and vn > 0:
                score = (orig_hints - new_hints) * 3 + vn - 2 * repl
                candidates.append((score, enc, decoded))
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def fix_transcript(transcript):
    if not isinstance(transcript, list):
        return transcript, 0
    changed = 0
    for msg in transcript:
        if not isinstance(msg, dict):
            continue
        for key in ("content", "text"):
            if key in msg:
                f = try_fix(msg[key])
                if f is not None:
                    msg[key] = f
                    changed += 1
    return transcript, changed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--user-id", default="")
    args = ap.parse_args()

    if args.execute and args.confirm != "I_HAVE_BACKUP":
        print("ERROR: --execute yêu cầu --confirm I_HAVE_BACKUP", file=sys.stderr)
        sys.exit(2)

    db_url = os.environ.get("ARCHIVE_DB_URL", "")
    if not db_url:
        print("ERROR: thiếu env ARCHIVE_DB_URL=postgresql://...", file=sys.stderr)
        sys.exit(2)

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"=== Migration mojibake fix — mode: {mode} ===")

    sql = "SELECT id, user_id, summary, llm_summary, transcript FROM chat_sessions"
    cond, params = [], []
    if args.user_id:
        cond.append("user_id = %s")
        params.append(args.user_id)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY started_at DESC"
    if args.limit:
        sql += f" LIMIT {args.limit}"

    total_scanned, total_changed, total_updated = 0, 0, 0

    with psycopg2.connect(db_url, cursor_factory=RealDictCursor) as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            print(f"Scan {len(rows)} phiên...")
            for r in rows:
                total_scanned += 1
                sid = r["id"]
                changes = []
                new_summary = try_fix(r.get("summary") or "")
                if new_summary:
                    changes.append(("summary", r["summary"][:60], new_summary[:60]))
                new_llm = try_fix(r.get("llm_summary") or "")
                if new_llm:
                    changes.append(("llm_summary", (r["llm_summary"] or "")[:60], new_llm[:60]))
                tr = r.get("transcript")
                if isinstance(tr, str):
                    try: tr = json.loads(tr)
                    except: tr = None
                tr_fixed, tr_changes = fix_transcript(tr) if tr else (None, 0)
                if tr_changes > 0:
                    changes.append(("transcript", f"{tr_changes} messages", "(fixed)"))
                if not changes:
                    continue
                total_changed += 1
                print(f"\n[{total_changed}] session {sid}:")
                for field, old, new in changes:
                    print(f"  {field}: {old!r:65} → {new!r}")
                if args.execute:
                    upd_sql = "UPDATE chat_sessions SET "
                    upd_params = []
                    sets = []
                    if new_summary:
                        sets.append("summary = %s"); upd_params.append(new_summary)
                    if new_llm:
                        sets.append("llm_summary = %s"); upd_params.append(new_llm)
                    if tr_changes > 0:
                        sets.append("transcript = %s"); upd_params.append(Json(tr_fixed))
                    upd_sql += ", ".join(sets) + " WHERE id = %s"
                    upd_params.append(sid)
                    with c.cursor() as cu:
                        cu.execute(upd_sql, upd_params)
                    total_updated += 1
            if args.execute:
                c.commit()

    print(f"\n=== Kết quả ===")
    print(f"Đã scan:    {total_scanned} phiên")
    print(f"Có mojibake: {total_changed} phiên")
    if args.execute:
        print(f"Đã UPDATE:  {total_updated} phiên")


if __name__ == "__main__":
    main()
