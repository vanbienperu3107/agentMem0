"""OAuth 2.1 + RFC 7591 Dynamic Client Registration for Claude Desktop App."""
from __future__ import annotations
import base64
import hashlib
import os
import secrets
import sys
import time
import uuid
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse


ISSUER = os.environ.get("OAUTH_ISSUER", "https://claude.hangocthanh.io.vn/mcp")
INTERNAL_TOKEN = os.environ.get("MCP_BEARER_TOKEN", "")

CLIENTS: dict[str, dict] = {}
AUTH_CODES: dict[str, dict] = {}
ACCESS_TOKENS: dict[str, dict] = {}

CODE_LIFETIME_SECONDS = 600
TOKEN_LIFETIME_SECONDS = 3600

router = APIRouter()


def _pkce_verify(verifier: str, challenge: str) -> bool:
    h = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(h).decode("ascii").rstrip("=")
    return secrets.compare_digest(computed, challenge)


def _now() -> float:
    return time.time()


def reset_state() -> None:
    CLIENTS.clear()
    AUTH_CODES.clear()
    ACCESS_TOKENS.clear()


@router.get("/.well-known/oauth-protected-resource")
def protected_resource_metadata():
    """RFC 9728 — Protected Resource Metadata."""
    return {
        "resource": ISSUER,
        "authorization_servers": [ISSUER],
        "scopes_supported": ["mcp"],
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-authorization-server")
def authorization_server_metadata():
    """RFC 8414 — Authorization Server Metadata."""
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "registration_endpoint": f"{ISSUER}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"],
    }


@router.get("/.well-known/openid-configuration")
def openid_configuration():
    """OIDC Discovery 1.0 — fallback for clients that probe OIDC first.

    We are NOT a real OIDC provider (no userinfo, no id_token), but Claude
    Desktop SDK falls back to this URL after fetching oauth-authorization-server.
    Return OAuth-compatible metadata so the client treats us as OAuth-only
    authorization server (no ID token claims).
    """
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "registration_endpoint": f"{ISSUER}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["none"],
    }


@router.post("/register", status_code=201)
async def register_client(request: Request):
    """RFC 7591 — Dynamic Client Registration. Returns HTTP 201."""
    # Log everything Claude sends for debugging
    raw_body = await request.body()
    headers = dict(request.headers)
    print(f"[DCR] Origin={headers.get('origin','-')} UA={headers.get('user-agent','-')[:80]}", flush=True)
    print(f"[DCR] Content-Type={headers.get('content-type','-')}", flush=True)
    print(f"[DCR] Raw body: {raw_body.decode('utf-8', errors='replace')[:500]}", flush=True)

    try:
        import json as _json
        body = _json.loads(raw_body) if raw_body else {}
    except Exception as e:
        print(f"[DCR] JSON parse error: {e}", flush=True)
        return JSONResponse(
            {"error": "invalid_client_metadata", "error_description": "body not JSON"},
            status_code=400,
        )

    redirect_uris = body.get("redirect_uris", [])
    if not redirect_uris or not isinstance(redirect_uris, list):
        print(f"[DCR] redirect_uris invalid: {redirect_uris}", flush=True)
        return JSONResponse(
            {"error": "invalid_redirect_uri", "error_description": "redirect_uris required (list of URIs)"},
            status_code=400,
        )

    client_id = str(uuid.uuid4())
    issued_at = int(_now())
    CLIENTS[client_id] = {
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "grant_types": body.get("grant_types", ["authorization_code"]),
        "response_types": body.get("response_types", ["code"]),
        "client_name": body.get("client_name", "Unknown"),
        "token_endpoint_auth_method": "none",
        "registered_at": _now(),
    }

    print(f"[DCR] Registered client_id={client_id[:8]} name={body.get('client_name','-')}", flush=True)

    return JSONResponse(
        status_code=201,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
        content={
            "client_id": client_id,
            "client_id_issued_at": issued_at,
            "client_secret_expires_at": 0,
            "redirect_uris": redirect_uris,
            "grant_types": CLIENTS[client_id]["grant_types"],
            "response_types": CLIENTS[client_id]["response_types"],
            "token_endpoint_auth_method": "none",
            "client_name": CLIENTS[client_id]["client_name"],
            "application_type": body.get("application_type", "web"),
            "scope": body.get("scope", "mcp"),
        },
    )


@router.get("/authorize")
def authorize(
    response_type: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    state: Optional[str] = None,
    scope: Optional[str] = None,
):
    """OAuth 2.1 authorization endpoint with PKCE."""
    print(f"[AUTH] client_id={client_id[:8] if client_id else '-'} redirect={redirect_uri}", flush=True)
    if response_type != "code":
        raise HTTPException(400, "unsupported_response_type")
    if code_challenge_method != "S256":
        raise HTTPException(400, "unsupported_code_challenge_method")
    if client_id not in CLIENTS:
        print(f"[AUTH] CLIENTS keys: {list(CLIENTS.keys())[:5]}", flush=True)
        raise HTTPException(400, "invalid_client")
    if redirect_uri not in CLIENTS[client_id]["redirect_uris"]:
        raise HTTPException(400, "invalid_redirect_uri")
    if not code_challenge or len(code_challenge) < 32:
        raise HTTPException(400, "invalid_request: code_challenge too short")

    code = secrets.token_urlsafe(32)
    AUTH_CODES[code] = {
        "client_id": client_id,
        "code_challenge": code_challenge,
        "redirect_uri": redirect_uri,
        "scope": scope or "mcp",
        "expires_at": _now() + CODE_LIFETIME_SECONDS,
    }

    params: dict[str, str] = {"code": code}
    if state:
        params["state"] = state
    redirect = f"{redirect_uri}?{urlencode(params)}"
    return RedirectResponse(url=redirect, status_code=302)


@router.post("/token")
async def token_exchange(
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(...),
    code_verifier: str = Form(...),
):
    """OAuth 2.1 token endpoint with PKCE."""
    print(f"[TOKEN] client_id={client_id[:8]} grant_type={grant_type}", flush=True)
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    auth = AUTH_CODES.get(code)
    if not auth:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    del AUTH_CODES[code]

    if auth["client_id"] != client_id:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if auth["redirect_uri"] != redirect_uri:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if _now() > auth["expires_at"]:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)
    if not _pkce_verify(code_verifier, auth["code_challenge"]):
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "PKCE verification failed"},
            status_code=400,
        )

    access_token = secrets.token_urlsafe(32)
    ACCESS_TOKENS[access_token] = {
        "client_id": client_id,
        "expires_at": _now() + TOKEN_LIFETIME_SECONDS,
        "scope": auth.get("scope", "mcp"),
    }

    return JSONResponse(
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        content={
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": TOKEN_LIFETIME_SECONDS,
            "scope": auth.get("scope", "mcp"),
        },
    )


def verify_token(token: str) -> bool:
    """Return True if token is valid (either internal or OAuth-issued)."""
    if not token:
        return False
    if INTERNAL_TOKEN and secrets.compare_digest(token, INTERNAL_TOKEN):
        return True
    info = ACCESS_TOKENS.get(token)
    if not info:
        return False
    if _now() > info["expires_at"]:
        del ACCESS_TOKENS[token]
        return False
    return True
