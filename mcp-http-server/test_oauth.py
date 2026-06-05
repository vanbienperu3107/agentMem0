"""Unit tests for oauth.py — OAuth 2.1 + DCR flow.

Uses FastAPI TestClient (no real HTTP). Tests cover:
- Discovery endpoints return proper metadata
- /register accepts metadata, returns client_id
- /authorize requires valid client + PKCE, redirects with code
- /token exchanges code for access_token (valid PKCE)
- /token rejects invalid PKCE / expired code / one-time use
- verify_token recognizes both internal and OAuth-issued tokens
"""
from __future__ import annotations
import base64
import hashlib
import os
import secrets
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Required env for module import
os.environ["MCP_BEARER_TOKEN"] = "test-internal-secret"
os.environ["OAUTH_ISSUER"] = "https://test.example.com/mcp"
# Required by mcp-http-server/app.py at import time
os.environ.setdefault("ARCHIVE_URL", "http://archive-api:8001")
os.environ.setdefault("ARCHIVE_AUTH_TOKEN", "test-archive")

# Allow importing from same dir
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient
import oauth


def _build_app():
    """Build a minimal FastAPI app mounting the oauth router for testing."""
    app = FastAPI()
    app.include_router(oauth.router)
    return TestClient(app)


def _pkce_pair():
    """Generate (code_verifier, code_challenge) PKCE pair."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class TestDiscovery(unittest.TestCase):
    """Discovery endpoints (no auth needed)."""

    def setUp(self):
        self.client = _build_app()

    def test_protected_resource_metadata(self):
        r = self.client.get("/.well-known/oauth-protected-resource")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["resource"], "https://test.example.com/mcp")
        self.assertIn("mcp", data["scopes_supported"])

    def test_auth_server_metadata_required_fields(self):
        r = self.client.get("/.well-known/oauth-authorization-server")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        # Required by RFC 8414
        for field in [
            "issuer", "authorization_endpoint", "token_endpoint",
            "registration_endpoint", "response_types_supported",
            "grant_types_supported", "code_challenge_methods_supported",
        ]:
            self.assertIn(field, data)
        # Specific values
        self.assertIn("S256", data["code_challenge_methods_supported"])
        self.assertIn("authorization_code", data["grant_types_supported"])


class TestRegistration(unittest.TestCase):
    """RFC 7591 Dynamic Client Registration."""

    def setUp(self):
        oauth.reset_state()
        self.client = _build_app()

    def test_register_returns_client_id(self):
        r = self.client.post("/register", json={
            "client_name": "Claude Desktop",
            "redirect_uris": ["http://localhost:6274/callback"],
        })
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertIn("client_id", data)
        self.assertTrue(len(data["client_id"]) >= 32)
        self.assertEqual(data["redirect_uris"], ["http://localhost:6274/callback"])
        self.assertEqual(data["token_endpoint_auth_method"], "none")

    def test_register_rejects_missing_redirect_uris(self):
        r = self.client.post("/register", json={"client_name": "X"})
        self.assertEqual(r.status_code, 400)

    def test_register_rejects_non_list_redirect_uris(self):
        r = self.client.post("/register", json={
            "client_name": "X",
            "redirect_uris": "http://localhost/cb",  # string not list
        })
        self.assertEqual(r.status_code, 400)


class TestAuthorize(unittest.TestCase):
    """Authorization endpoint."""

    def setUp(self):
        oauth.reset_state()
        self.client = _build_app()
        reg = self.client.post("/register", json={
            "client_name": "Claude Desktop",
            "redirect_uris": ["http://localhost:6274/callback"],
        }).json()
        self.client_id = reg["client_id"]

    def test_authorize_redirects_with_code(self):
        _, challenge = _pkce_pair()
        r = self.client.get("/authorize", params={
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": "http://localhost:6274/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "test-state",
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        loc = r.headers["location"]
        parsed = urlparse(loc)
        self.assertEqual(parsed.scheme, "http")
        self.assertEqual(parsed.netloc, "localhost:6274")
        params = parse_qs(parsed.query)
        self.assertIn("code", params)
        self.assertEqual(params["state"], ["test-state"])

    def test_authorize_rejects_invalid_client(self):
        _, challenge = _pkce_pair()
        r = self.client.get("/authorize", params={
            "response_type": "code",
            "client_id": "nonexistent",
            "redirect_uri": "http://localhost:6274/callback",
            "code_challenge": challenge,
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 400)

    def test_authorize_rejects_unregistered_redirect_uri(self):
        _, challenge = _pkce_pair()
        r = self.client.get("/authorize", params={
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": "http://evil.com/cb",
            "code_challenge": challenge,
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 400)

    def test_authorize_rejects_plain_challenge_method(self):
        _, challenge = _pkce_pair()
        r = self.client.get("/authorize", params={
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": "http://localhost:6274/callback",
            "code_challenge": challenge,
            "code_challenge_method": "plain",
        }, follow_redirects=False)
        self.assertEqual(r.status_code, 400)


class TestTokenExchange(unittest.TestCase):
    """Token endpoint — code + PKCE → access_token."""

    def setUp(self):
        oauth.reset_state()
        self.client = _build_app()
        reg = self.client.post("/register", json={
            "client_name": "Claude Desktop",
            "redirect_uris": ["http://localhost:6274/callback"],
        }).json()
        self.client_id = reg["client_id"]
        self.verifier, self.challenge = _pkce_pair()
        # Get an authorization code
        r = self.client.get("/authorize", params={
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": "http://localhost:6274/callback",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
        }, follow_redirects=False)
        self.code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

    def test_token_exchange_success(self):
        r = self.client.post("/token", data={
            "grant_type": "authorization_code",
            "code": self.code,
            "redirect_uri": "http://localhost:6274/callback",
            "client_id": self.client_id,
            "code_verifier": self.verifier,
        })
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "Bearer")
        self.assertEqual(data["expires_in"], 3600)

    def test_token_exchange_rejects_invalid_verifier(self):
        r = self.client.post("/token", data={
            "grant_type": "authorization_code",
            "code": self.code,
            "redirect_uri": "http://localhost:6274/callback",
            "client_id": self.client_id,
            "code_verifier": "wrong-verifier-" + secrets.token_urlsafe(32),
        })
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_token_exchange_one_time_use(self):
        # First exchange succeeds
        r1 = self.client.post("/token", data={
            "grant_type": "authorization_code",
            "code": self.code,
            "redirect_uri": "http://localhost:6274/callback",
            "client_id": self.client_id,
            "code_verifier": self.verifier,
        })
        self.assertEqual(r1.status_code, 200)
        # Second exchange with same code fails
        r2 = self.client.post("/token", data={
            "grant_type": "authorization_code",
            "code": self.code,
            "redirect_uri": "http://localhost:6274/callback",
            "client_id": self.client_id,
            "code_verifier": self.verifier,
        })
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.json()["error"], "invalid_grant")

    def test_token_exchange_rejects_unsupported_grant(self):
        r = self.client.post("/token", data={
            "grant_type": "refresh_token",
            "code": self.code,
            "redirect_uri": "http://localhost:6274/callback",
            "client_id": self.client_id,
            "code_verifier": self.verifier,
        })
        self.assertEqual(r.status_code, 400)

    def test_token_exchange_rejects_wrong_redirect_uri(self):
        r = self.client.post("/token", data={
            "grant_type": "authorization_code",
            "code": self.code,
            "redirect_uri": "http://evil.com/cb",
            "client_id": self.client_id,
            "code_verifier": self.verifier,
        })
        self.assertEqual(r.status_code, 400)


class TestVerifyToken(unittest.TestCase):
    """verify_token() — used by main app auth middleware."""

    def setUp(self):
        oauth.reset_state()

    def test_internal_token_always_valid(self):
        self.assertTrue(oauth.verify_token("test-internal-secret"))

    def test_unknown_token_invalid(self):
        self.assertFalse(oauth.verify_token("nonexistent-token"))

    def test_empty_token_invalid(self):
        self.assertFalse(oauth.verify_token(""))
        self.assertFalse(oauth.verify_token(None))

    def test_issued_token_valid_until_expiry(self):
        oauth.ACCESS_TOKENS["abc"] = {
            "client_id": "test",
            "expires_at": time.time() + 100,
        }
        self.assertTrue(oauth.verify_token("abc"))

    def test_expired_token_invalid_and_pruned(self):
        oauth.ACCESS_TOKENS["expired"] = {
            "client_id": "test",
            "expires_at": time.time() - 10,
        }
        self.assertFalse(oauth.verify_token("expired"))
        # Should be auto-pruned
        self.assertNotIn("expired", oauth.ACCESS_TOKENS)


class TestPKCEHelper(unittest.TestCase):
    """Test PKCE verification helper."""

    def test_pkce_matching_pair(self):
        verifier = "test-verifier-" + secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        self.assertTrue(oauth._pkce_verify(verifier, challenge))

    def test_pkce_mismatched_pair(self):
        self.assertFalse(oauth._pkce_verify("a", "wrong"))


class TestEndToEndFlow(unittest.TestCase):
    """Full OAuth flow end-to-end."""

    def test_full_flow_register_to_token(self):
        oauth.reset_state()
        client = _build_app()

        # 1. Discovery
        meta = client.get("/.well-known/oauth-authorization-server").json()
        self.assertIn("token_endpoint", meta)

        # 2. Register
        reg = client.post("/register", json={
            "client_name": "Test Client",
            "redirect_uris": ["http://localhost:9999/cb"],
        }).json()
        cid = reg["client_id"]

        # 3. PKCE
        verifier, challenge = _pkce_pair()

        # 4. Authorize
        r = client.get("/authorize", params={
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": "http://localhost:9999/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        }, follow_redirects=False)
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]

        # 5. Token
        tok = client.post("/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://localhost:9999/cb",
            "client_id": cid,
            "code_verifier": verifier,
        }).json()
        access_token = tok["access_token"]

        # 6. Verify token works
        self.assertTrue(oauth.verify_token(access_token))


if __name__ == "__main__":
    unittest.main(verbosity=2)
