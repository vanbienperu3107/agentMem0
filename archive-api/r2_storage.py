"""R2/B2/S3-compatible object storage for full transcripts.

Stores gzipped JSON transcripts at:
    sessions/{user_id}/{yyyy}/{mm}/{uuid}.json.gz

Uses boto3 — works with Cloudflare R2, Backblaze B2 (S3-compatible), AWS S3.
Configure via env:
    R2_ENDPOINT_URL      e.g. https://<account>.r2.cloudflarestorage.com
    R2_ACCESS_KEY_ID
    R2_SECRET_ACCESS_KEY
    R2_BUCKET            e.g. mem0-transcripts
"""
from __future__ import annotations
import os
import json
import gzip
import uuid
from datetime import datetime, timezone
from typing import Optional

try:
    import boto3
    from botocore.client import Config
except ImportError:
    boto3 = None
    Config = None

ENDPOINT = os.environ.get("R2_ENDPOINT_URL")
ACCESS_KEY = os.environ.get("R2_ACCESS_KEY_ID")
SECRET_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
BUCKET = os.environ.get("R2_BUCKET", "mem0-transcripts")


def _client():
    if boto3 is None:
        raise RuntimeError("boto3 not installed — pip install boto3")
    if not all([ENDPOINT, ACCESS_KEY, SECRET_KEY]):
        raise RuntimeError(
            "R2 env vars missing: R2_ENDPOINT_URL, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY"
        )
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        aws_access_key_id=ACCESS_KEY,
        aws_secret_access_key=SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


def make_key(user_id: str, session_id: Optional[str] = None) -> str:
    sid = session_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    return f"sessions/{user_id}/{now.year}/{now.month:02d}/{sid}.json.gz"


def upload_transcript(user_id: str, transcript: list, session_id: Optional[str] = None) -> tuple[str, int]:
    """Gzip + upload. Returns (r2_key, size_bytes)."""
    key = make_key(user_id, session_id)
    raw = json.dumps(transcript, ensure_ascii=False, default=str).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=6)
    c = _client()
    c.put_object(
        Bucket=BUCKET,
        Key=key,
        Body=gz,
        ContentType="application/json",
        ContentEncoding="gzip",
    )
    return key, len(gz)


def download_transcript(r2_key: str) -> list:
    """Fetch + ungzip + parse."""
    c = _client()
    obj = c.get_object(Bucket=BUCKET, Key=r2_key)
    gz = obj["Body"].read()
    raw = gzip.decompress(gz)
    return json.loads(raw)


def delete_transcript(r2_key: str):
    c = _client()
    c.delete_object(Bucket=BUCKET, Key=r2_key)
