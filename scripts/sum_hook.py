#!/usr/bin/env python3
"""
sum_hook.py - Hook tu dong tom tat phien Claude Code va luu len mem0.

Co che:
  - Dang ky lam Stop hook (chay khi phien Claude Code ket thuc).
  - Claude Code truyen JSON qua STDIN, trong do co `transcript_path`.
  - Script doc transcript .jsonl, trich text user/assistant, roi goi
    `claude -p` (headless) voi model Haiku de VUA tom tat VUA goi tool
    MCP `add_memory` - tat ca qua Claude Max OAT nen KHONG ton phi token.

Tham so MCP add_memory: `text` (chuoi, bat buoc) + `user_id` - khop voi
mem0-mcp-selfhosted (server.py) va memory-rest-api (AddBody.text).

Test thu cong:
    echo '{"transcript_path":"/path/to/session.jsonl"}' | python scripts/sum_hook.py --dry-run
"""
import argparse
import json
import os
import subprocess
import sys

DEFAULT_MODEL = os.environ.get("SUM_HOOK_MODEL", "claude-haiku-4-5-20251001")
MCP_SERVER = os.environ.get("SUM_HOOK_MCP_SERVER", "mem0")
DEFAULT_USER_ID = os.environ.get("MEM0_USER_ID", "thanh")
MAX_CHARS = int(os.environ.get("SUM_HOOK_MAX_CHARS", "12000"))


def parse_hook_input(raw):
    """Parse payload JSON Claude Code gui qua STDIN; tra {} neu hong."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def read_transcript(path):
    """Doc .jsonl, bo qua dong hong."""
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages


def extract_text(messages):
    """Trich text user/assistant, bo system/tool noise."""
    parts = []
    for msg in messages:
        role = msg.get("type") or msg.get("role") or ""
        if role not in ("user", "assistant", "human"):
            continue
        content = ""
        m = msg.get("message") or msg
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str):
            content = c
        elif isinstance(c, list):
            for block in c:
                if isinstance(block, dict) and block.get("type") == "text":
                    content += block.get("text", "")
        if content.strip():
            parts.append("[" + role + "] " + content.strip())
    return "\n".join(parts)


def build_prompt(text, user_id=DEFAULT_USER_ID, max_chars=MAX_CHARS):
    """Prompt yeu cau Claude tom tat + goi add_memory(text, user_id)."""
    snippet = text[:max_chars]
    return (
        "Ban la tro ly ghi nho. Duoi day la transcript mot phien lam viec.\n"
        "1) Tom tat thanh cac fact ngan gon TIENG VIET, moi y mot dong, uu tien: "
        "quyet dinh da chot, cau hinh/gia tri ky thuat, viec can lam tiep, "
        "loi da gap + cach khac phuc. Bo phan chao hoi/lan man.\n"
        "2) Goi tool MCP add_memory voi text = ban tom tat, user_id = \""
        + user_id + "\".\n"
        "3) Neu khong co gi dang luu, KHONG goi add_memory va noi ro ly do.\n\n"
        "Transcript:\n" + snippet
    )


def build_claude_cmd(prompt, model=DEFAULT_MODEL, mcp_server=MCP_SERVER):
    """Cau lenh `claude -p` headless, chi cho phep tool add_memory."""
    return [
        "claude", "-p", prompt,
        "--model", model,
        "--allowedTools", "mcp__" + mcp_server + "__add_memory",
    ]


def run_claude(cmd, timeout=120):
    """Chay claude headless. Tra (ok, output)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "Khong tim thay `claude` CLI trong PATH."
    except subprocess.TimeoutExpired:
        return False, "claude -p qua thoi gian (" + str(timeout) + "s)."
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "claude -p loi.").strip()
    return True, (result.stdout or "").strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stop hook: tom tat phien -> mem0")
    ap.add_argument("--transcript", help="Duong dan .jsonl (bo qua STDIN neu co)")
    ap.add_argument("--user-id", default=DEFAULT_USER_ID)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--dry-run", action="store_true", help="Chi in prompt, KHONG goi claude.")
    args = ap.parse_args(argv)

    transcript_path = args.transcript
    if not transcript_path:
        payload = parse_hook_input(sys.stdin.read())
        transcript_path = payload.get("transcript_path") or payload.get("transcript")

    if not transcript_path or not os.path.exists(transcript_path):
        print("sum_hook: khong co transcript hop le, bo qua.", file=sys.stderr)
        return 0

    text = extract_text(read_transcript(transcript_path))
    if not text.strip():
        print("sum_hook: transcript rong, bo qua.", file=sys.stderr)
        return 0

    prompt = build_prompt(text, user_id=args.user_id)
    if args.dry_run:
        print(prompt)
        return 0

    ok, out = run_claude(build_claude_cmd(prompt, model=args.model))
    if ok:
        print(out)
        return 0
    print("sum_hook: bo qua do loi claude -p: " + out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
