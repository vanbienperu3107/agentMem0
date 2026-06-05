#!/usr/bin/env python3
"""R2 budget check — daily cron job that alerts via Telegram.

Defense layer 4 of the R2 cost protection stack (the others are: virtual card
hard limit, Cloudflare bucket lifecycle rules, Cloudflare billing alerts).

What it does
------------
1. Connects to R2 via boto3
2. Lists all objects, sums total size
3. Compares against thresholds (warn/critical)
4. Sends Telegram message if level changed since last check
   - OR if level=ok and 7 days since last alert (weekly summary)

State is tracked in ~/.cache/r2-budget-check-state.json to avoid spam.

Required env vars
-----------------
    R2_ENDPOINT_URL          e.g. https://<account-id>.r2.cloudflarestorage.com
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
    R2_BUCKET                default 'mem0-transcripts'
    TELEGRAM_BOT_TOKEN       from @BotFather
    TELEGRAM_CHAT_ID         your user ID (from @userinfobot)

Optional env vars (with defaults)
---------------------------------
    R2_WARN_GB               default 7.0   (70% of 10GB free tier)
    R2_CRITICAL_GB           default 9.0   (90% of free tier)
    R2_WEEKLY_SUMMARY_DAYS   default 7     (send OK summary every N days)

Install
-------
    pip install --user boto3

Cron (run daily at 7:00 AM VPS time)
------------------------------------
    0 7 * * * /usr/bin/env -i HOME=$HOME PATH=/usr/local/bin:/usr/bin:/bin \\
        $HOME/scripts/r2-budget-env.sh \\
        python3 $HOME/scripts/r2-budget-check.py \\
        >> $HOME/scripts/r2-budget-check.log 2>&1
"""
from __future__ import annotations
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import boto3
    from botocore.client import Config
except ImportError:
    print("ERROR: boto3 not installed. Run: pip install --user boto3", file=sys.stderr)
    sys.exit(1)


# ============================================================
# Configuration
# ============================================================

R2_ENDPOINT_URL = os.environ["R2_ENDPOINT_URL"]
R2_ACCESS_KEY_ID = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET = os.environ.get("R2_BUCKET", "mem0-transcripts")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

WARN_GB = float(os.environ.get("R2_WARN_GB", "7.0"))
CRITICAL_GB = float(os.environ.get("R2_CRITICAL_GB", "9.0"))
WEEKLY_SUMMARY_DAYS = int(os.environ.get("R2_WEEKLY_SUMMARY_DAYS", "7"))

STATE_FILE = Path.home() / ".cache" / "r2-budget-check-state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)


# ============================================================
# Helpers
# ============================================================

def r2_client():
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
    )


def get_bucket_stats() -> tuple[int, int]:
    """Return (total_bytes, object_count) for the bucket.

    Uses pagination — safe for buckets with thousands of objects.
    """
    c = r2_client()
    paginator = c.get_paginator("list_objects_v2")
    total_bytes = 0
    count = 0
    for page in paginator.paginate(Bucket=R2_BUCKET):
        for obj in page.get("Contents", []):
            total_bytes += obj["Size"]
            count += 1
    return total_bytes, count


def send_telegram(text: str) -> dict:
    """Send a Markdown message to the configured Telegram chat."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_alert_level": "ok", "last_check": None}


def save_state(s: dict) -> None:
    STATE_FILE.write_text(json.dumps(s, default=str))


# ============================================================
# Main
# ============================================================

def classify_level(gb: float) -> str:
    if gb >= CRITICAL_GB:
        return "critical"
    if gb >= WARN_GB:
        return "warning"
    return "ok"


def build_message(level: str, gb: float, pct_free: float, count: int) -> str:
    if level == "critical":
        return (
            f"🔴 *R2 CRITICAL*\n\n"
            f"Storage: *{gb:.2f} GB* ({pct_free:.1f}% of 10GB free)\n"
            f"Objects: `{count}`\n"
            f"Bucket: `{R2_BUCKET}`\n\n"
            f"⚠️ Vượt critical threshold ({CRITICAL_GB:.1f} GB).\n"
            f"_Action gấp:_ migrate dữ liệu, hoặc giảm lifecycle rule xuống <365 ngày."
        )
    if level == "warning":
        return (
            f"🟡 *R2 Warning*\n\n"
            f"Storage: *{gb:.2f} GB* ({pct_free:.1f}% of 10GB free)\n"
            f"Objects: `{count}`\n"
            f"Bucket: `{R2_BUCKET}`\n\n"
            f"Vượt warning threshold ({WARN_GB:.1f} GB).\n"
            f"_Lên kế hoạch:_ review session cũ, tăng lifecycle aggressive hơn."
        )
    return (
        f"✅ *R2 OK (weekly check)*\n\n"
        f"Storage: {gb:.2f} GB ({pct_free:.1f}% of 10GB free)\n"
        f"Objects: `{count}`\n"
        f"Bucket: `{R2_BUCKET}`\n\n"
        f"Tất cả bình thường."
    )


def days_since(iso_str) -> float:
    if not iso_str:
        return float("inf")
    if isinstance(iso_str, datetime):
        last = iso_str
    else:
        last = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() / 86400.0


def main() -> int:
    state = load_state()
    now = datetime.now(timezone.utc)

    # Fetch bucket stats
    try:
        total_bytes, count = get_bucket_stats()
    except Exception as e:
        try:
            send_telegram(
                f"🔴 *R2 Budget Check FAILED*\n\n"
                f"Bucket: `{R2_BUCKET}`\n"
                f"Error: `{type(e).__name__}: {e}`\n"
                f"Time: {now.isoformat()}"
            )
        except Exception:
            pass
        print(f"Failed to get bucket stats: {e}", file=sys.stderr)
        return 1

    gb = total_bytes / (1024 ** 3)
    pct_free = (gb / 10.0) * 100
    level = classify_level(gb)

    print(f"[{now.isoformat()}] R2 usage: {gb:.3f} GB "
          f"({pct_free:.1f}% of 10GB free), {count} objects, level={level}")

    # Decide if we should send alert
    last_level = state.get("last_alert_level", "ok")
    last_check = state.get("last_check")
    elapsed = days_since(last_check)

    should_alert = False
    if level != last_level:
        should_alert = True  # level changed, always notify
    elif level == "ok" and elapsed >= WEEKLY_SUMMARY_DAYS:
        should_alert = True  # weekly heartbeat
    elif level in ("warning", "critical") and elapsed >= 1:
        should_alert = True  # re-alert daily if still in warning/critical

    if should_alert:
        try:
            send_telegram(build_message(level, gb, pct_free, count))
            print(f"Alert sent (level={level})")
        except Exception as e:
            print(f"Failed to send Telegram alert: {e}", file=sys.stderr)

    state["last_alert_level"] = level
    state["last_check"] = now.isoformat()
    state["last_gb"] = round(gb, 3)
    state["last_count"] = count
    save_state(state)

    return 0


if __name__ == "__main__":
    sys.exit(main())
