# Hướng dẫn triển khai MCP Server (mem0custom)

Tài liệu này mô tả chi tiết kiến trúc, các thành phần và cách triển khai MCP Server `mem0custom` — một remote MCP server tích hợp với Claude Desktop App + ChatGPT App, hỗ trợ OAuth 2.1 + Dynamic Client Registration.

## 1. Tổng quan kiến trúc

### 1.1 Mục đích

`mem0custom` là một stack tự host gồm 3 lớp:

1. **Layer Memory (mem0 + Qdrant)**: lưu memory dạng vector + embedding để tìm kiếm ngữ nghĩa
2. **Layer Archive (Postgres + R2)**: lưu transcript chat đầy đủ, summary do LLM tạo
3. **Layer MCP**: cung cấp 8 tool cho Claude Desktop/ChatGPT App qua giao thức MCP

### 1.2 Sơ đồ kiến trúc

```
┌─────────────────┐        ┌─────────────────┐
│ Claude Desktop  │        │   ChatGPT App   │
│      App        │        │  (Custom GPT)   │
└────────┬────────┘        └────────┬────────┘
         │ HTTPS                    │ HTTPS
         │ OAuth 2.1                │ Bearer token
         └────────────┬─────────────┘
                      │
              ┌───────▼────────┐
              │  Cloudflare    │  DDoS protection, DNS
              │      DNS       │
              └───────┬────────┘
                      │
                      │ port 443 (sslh multiplex)
                      │
              ┌───────▼────────┐
              │     Caddy      │  Reverse proxy + auto HTTPS
              │  (sslh frontend)│  Multiplex SSH + HTTPS trên cùng port
              └───────┬────────┘
                      │
       ┌──────────────┼──────────────┐
       │              │              │
   ┌───▼───┐     ┌───▼────────┐  ┌──▼──────────┐
   │ /mcp/*│     │ /archive/* │  │ /memory/*   │
   └───┬───┘     └─────┬──────┘  └──────┬──────┘
       │               │                 │
   ┌───▼──────────┐  ┌─▼──────────┐ ┌───▼────────────┐
   │ mcp-http-    │  │ archive-api│ │ memory-rest-api│
   │   server     │  │            │ │                │
   │  (port 8000) │  │ (port 8001)│ │  (port 8002)   │
   └──┬───────────┘  └──┬─────────┘ └───┬────────────┘
      │                 │               │
      │ proxy           │               │ mem0ai SDK
      │ archive call    │               │
      ├─────────────────┤               │
      │                 │               │
      ▼                 ▼               ▼
   ┌─────────────────────────────────────────┐
   │           Backend storage                │
   ├──────────────┬─────────────┬────────────┤
   │   Qdrant     │    Neon     │ Cloudflare │
   │   (vector)   │  Postgres   │     R2     │
   │              │  (sessions) │ (transcripts)│
   └──────────────┴─────────────┴────────────┘
```

### 1.3 Stack công nghệ

| Layer | Technology | Mục đích |
|---|---|---|
| Reverse proxy | Caddy 2.8 | Auto HTTPS, route theo path |
| Transport multiplex | sslh | SSH + HTTPS chung port 443 (xuyên corp proxy) |
| MCP HTTP server | FastAPI + httpx | Streamable HTTP transport, OAuth, 8 tools |
| Archive API | FastAPI + psycopg2 | REST endpoints cho transcript |
| Memory REST API | FastAPI + mem0ai<2.0 | REST cho ChatGPT Custom GPT |
| Vector DB | Qdrant 1.12 | Lưu embedding (1536 dim, cosine) |
| Postgres | Neon Cloud (ap-southeast-1) | Session metadata + summary |
| Object storage | Cloudflare R2 | Transcript JSON gzipped |
| Containerization | Docker Compose v2 | Orchestrate 5 services |
| CI/CD | GitHub Actions | Build images → GHCR → SSH deploy VPS |

## 2. Các service Docker

### 2.1 mcp-http-server (port 8000)

**Chức năng**: Remote MCP server theo spec MCP 2025-03-26 với Streamable HTTP transport.

**Endpoints công khai (không cần auth)**:
- `GET /health` — health check
- `GET /mcp/.well-known/oauth-authorization-server` — RFC 8414 metadata
- `GET /mcp/.well-known/oauth-protected-resource` — RFC 9728 metadata
- `GET /mcp/.well-known/openid-configuration` — OIDC compat (fallback cho clients probe OIDC)
- `POST /mcp/register` — RFC 7591 Dynamic Client Registration
- `GET /mcp/authorize` — OAuth 2.1 authorize endpoint (PKCE S256, auto-approve)
- `POST /mcp/token` — token exchange với PKCE

**Endpoint bảo mật (cần Bearer token)**:
- `POST /mcp` — MCP JSON-RPC over HTTP

**8 MCP Tools**:
1. `list_old_sessions` — list sessions theo project/date
2. `search_old_sessions` — keyword search (Postgres ILIKE)
3. `search_old_sessions_semantic` — vector search (Qdrant)
4. `get_session_summary` — metadata + first/last 5 messages
5. `get_old_session` — full transcript
6. `load_context_for_continuation` — load context để tiếp tục chat
7. `search` (ChatGPT compat alias) — same as semantic
8. `fetch` (ChatGPT compat alias) — same as get_session_summary

### 2.2 archive-api (port 8001)

**Chức năng**: REST API cho transcript storage.

**Endpoints**:
- `POST /sessions` — tạo session mới (auto upload transcript lên R2)
- `GET /sessions` — list sessions với filter
- `GET /sessions/search-semantic` — semantic search
- `GET /sessions/{id}` — chi tiết session (transcript auto download từ R2)
- `POST /sessions/{id}/summarize` — gọi Claude Haiku tạo summary + embedding
- `GET /sessions/{id}/context` — format context cho continuation (3 strategies: full/compressed/rag)

**Modules**:
- `app.py` — FastAPI app + endpoints
- `summarizer.py` — wrapper Claude API tạo summary
- `embeddings.py` — wrapper OpenAI text-embedding-3-small
- `qdrant_helper.py` — upsert/search Qdrant collection `mem0_mcp_selfhosted`
- `r2_storage.py` — boto3 upload/download Cloudflare R2

### 2.3 memory-rest-api (port 8002)

**Chức năng**: REST API cho mem0 memory layer (compatible với ChatGPT Custom GPT Actions).

**Endpoints**:
- `POST /memories` — add memory
- `GET /memories` — search memories
- `DELETE /memories/{id}` — delete
- `GET /openapi.json` — OpenAPI spec cho ChatGPT GPT import

### 2.4 qdrant + caddy

- **qdrant**: vector database, collection `mem0_mcp_selfhosted` shared cross-platform
- **caddy**: reverse proxy với auto HTTPS via Let's Encrypt

## 3. OAuth 2.1 + Dynamic Client Registration flow

### 3.1 Tại sao cần OAuth?

Claude Desktop App yêu cầu **OAuth 2.1 với PKCE** cho remote MCP servers. Không thể dùng API key tĩnh.

ChatGPT Custom GPT chỉ cần Bearer token đơn giản (set trong action settings).

→ Phải implement OAuth flow cho Claude, đồng thời giữ Bearer token internal cho ChatGPT + Claude Code stdio.

### 3.2 Flow chi tiết (Claude Desktop)

```
Step 1: User add custom connector URL "https://server/mcp"
        │
        ▼
Step 2: Claude POST /mcp without auth
        ◀── 401 Unauthorized
            WWW-Authenticate: Bearer realm="mcp",
              resource_metadata="https://server/mcp/.well-known/oauth-protected-resource"
        │
        ▼
Step 3: Claude GET .well-known/oauth-protected-resource
        ◀── 200 { "authorization_servers": ["https://server/mcp"] }
        │
        ▼
Step 4: Claude GET .well-known/oauth-authorization-server
        ◀── 200 { "authorization_endpoint": ".../authorize",
                  "token_endpoint": ".../token",
                  "registration_endpoint": ".../register",
                  "code_challenge_methods_supported": ["S256"] }
        │
        ▼
Step 5: Claude GET .well-known/openid-configuration  (probe OIDC)
        ◀── 200 (OAuth-compatible OIDC metadata, no id_token)
        │
        ▼
Step 6: Claude POST /mcp/register  (Dynamic Client Registration)
        Body: { "client_name": "Claude Desktop",
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"] }
        ◀── 201 Created
            { "client_id": "<uuid>",
              "client_id_issued_at": <timestamp>,
              "client_secret_expires_at": 0 }
        Cache-Control: no-store
        │
        ▼
Step 7: Claude open browser → /mcp/authorize?
            response_type=code&
            client_id=<uuid>&
            redirect_uri=https://claude.ai/api/mcp/auth_callback&
            code_challenge=<S256_hash>&
            code_challenge_method=S256&
            state=<random>
        ◀── 302 Redirect to claude.ai/api/mcp/auth_callback?
            code=<authorization_code>&state=<random>
        │ (Browser tự đóng, callback về Desktop App)
        ▼
Step 8: Claude POST /mcp/token
        Body: grant_type=authorization_code&
              code=<authorization_code>&
              client_id=<uuid>&
              code_verifier=<original_PKCE_verifier>&
              redirect_uri=https://claude.ai/api/mcp/auth_callback
        ◀── 200 { "access_token": "<token>",
                  "token_type": "Bearer",
                  "expires_in": 3600 }
        │
        ▼
Step 9: Claude POST /mcp với Authorization: Bearer <access_token>
        Body: { "jsonrpc": "2.0", "method": "tools/list", ... }
        ◀── 200 { "result": { "tools": [...8 tools...] } }
```

### 3.3 PKCE S256 (chống intercept code)

```python
# Client tạo
verifier = secrets.token_urlsafe(64)   # random 64+ chars
challenge = base64url(sha256(verifier))  # S256 transform

# Gửi challenge ở /authorize, gửi verifier ở /token
# Server verify: base64url(sha256(verifier)) == challenge
```

→ Attacker chặn code không thể đổi token vì không có verifier.

### 3.4 Single-user auto-approve

Server skip consent UI vì là single-user setup. Bất kỳ ai reach `/authorize` (qua HTTPS authenticated từ Claude App) = chính chủ.

Production multi-user cần thêm:
- Login form
- Consent screen "App muốn truy cập memory của bạn"
- Per-user token storage

## 4. Caddy reverse proxy + sslh multiplex

### 4.1 Vì sao cần sslh?

Corp network của Thanh chặn outbound trừ port 80/443. SSH (port 22) bị block → không SSH được vào VPS.

Giải pháp: chạy **sslh** trên VPS port 443 → multiplex:
- Traffic SSH (binary protocol) → forward tới sshd port 22
- Traffic HTTPS (TLS handshake) → forward tới Caddy

Cùng port 443, sslh phân biệt protocol bằng cách inspect bytes đầu tiên.

### 4.2 Caddyfile chính

```
claude.hangocthanh.io.vn {
    encode gzip

    handle /qdrant/* {
        @authorized header api-key "{env.MCP_BEARER_TOKEN}"
        handle @authorized {
            uri strip_prefix /qdrant
            reverse_proxy qdrant:6333 {
                header_up api-key {env.QDRANT_API_KEY}
            }
        }
        respond "Unauthorized" 401
    }

    handle /archive/* {
        uri strip_prefix /archive
        reverse_proxy archive-api:8001
    }

    handle /memory/* {
        uri strip_prefix /memory
        reverse_proxy memory-rest-api:8002
    }

    handle /mcp/* {
        reverse_proxy mcp-http-server:8000 {
            flush_interval -1
        }
    }

    handle /health {
        respond "ok" 200
    }

    handle { respond "Not Found" 404 }
}
```

**Lưu ý quan trọng**:
- `flush_interval -1` cho /mcp → cho phép streaming (Streamable HTTP)
- /qdrant có auth swap (Caddy verify MCP token, forward Qdrant key) → tránh lộ Qdrant key cho client

## 5. Docker Compose stack

```yaml
services:
  qdrant:
    image: qdrant/qdrant:v1.12.0
    container_name: memory-qdrant
    volumes: [qdrant-data:/qdrant/storage]
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}

  archive-api:
    image: ghcr.io/thanhiont423/mem0custom-archive-api:test
    container_name: memory-archive-api
    env_file: .env

  memory-rest-api:
    image: ghcr.io/thanhiont423/mem0custom-memory-rest-api:test
    container_name: memory-rest-api
    env_file: .env

  mcp-http-server:
    image: ghcr.io/thanhiont423/mem0custom-mcp-http-server:test
    container_name: memory-mcp-http
    env_file: .env

  caddy:
    image: caddy:2.8
    container_name: memory-caddy
    ports: ["80:80"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy-data:/data
    env_file: .env

volumes:
  qdrant-data:
  caddy-data:

networks:
  default:
    name: memory-net
```

Caddy KHÔNG bind port 443 trực tiếp — sslh ở host listen 443 → forward tới Caddy 8443.

## 6. Environment variables (.env)

```bash
# Database
DB_URL=postgresql://user:pass@ep-xxx.ap-southeast-1.aws.neon.tech/neondb

# Qdrant
QDRANT_API_KEY=...

# Auth tokens
ARCHIVE_AUTH_TOKEN=<random-64-char>
MCP_BEARER_TOKEN=<random-64-char>

# LLM
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-proj-...

# R2
R2_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=mem0-transcripts

# MCP server
ARCHIVE_URL=http://archive-api:8001
USER_ID=thanh
OAUTH_ISSUER=https://claude.hangocthanh.io.vn/mcp

# CI/CD (cho VPS pull GHCR)
GHCR_TOKEN=<github-pat-with-read-packages>
```

## 7. Lessons learned (bugs gặp khi triển khai)

### 7.1 Dockerfile `COPY app.py .` thiếu modules

**Triệu chứng**: container crash với `ModuleNotFoundError: No module named 'summarizer'`.

**Nguyên nhân**: Dockerfile chỉ copy 1 file `app.py`, không copy các module khác.

**Fix**:
```dockerfile
# Sai
COPY app.py .

# Đúng
COPY *.py ./
```

→ **Bài học**: luôn dùng glob khi project có nhiều file Python.

### 7.2 Caddy forward `/mcp/*` không strip prefix → router không match

**Triệu chứng**: `/mcp/.well-known/oauth-authorization-server` → 404.

**Nguyên nhân**: FastAPI router định nghĩa routes `/.well-known/*` (không có /mcp prefix). Caddy forward nguyên xi `/mcp/.well-known/...` → FastAPI không tìm thấy route.

**Fix**: Mount router với prefix:
```python
app.include_router(oauth.router, prefix="/mcp")
```

### 7.3 CORS preflight OPTIONS → 405

**Triệu chứng**: Claude Desktop App báo "Couldn't register".

**Nguyên nhân**: Claude App browser context gửi preflight OPTIONS trước POST. FastAPI default không có OPTIONS handler → 405 → browser block POST.

**Fix**: Add CORSMiddleware với `allow_origins=["*"]`, `allow_methods=["*"]`.

### 7.4 DCR trả 200 thay vì 201

**Nguyên nhân**: RFC 7591 yêu cầu **201 Created**, ta trả 200. Một số OAuth SDK reject.

**Fix**:
```python
@router.post("/register", status_code=201)
async def register_client(request: Request):
    ...
    return JSONResponse(status_code=201, headers={"Cache-Control": "no-store"}, content={...})
```

### 7.5 OIDC discovery endpoint trả 401

**Triệu chứng**: Sau DCR success, Claude App "Couldn't register" lại.

**Nguyên nhân**: Auth middleware exact-match PUBLIC_PATHS. Claude probe `/mcp/.well-known/openid-configuration` để fallback OIDC. Path không có trong list → 401 → Claude SDK confused → bỏ flow.

**Fix**: Allow ALL `/.well-known/*`:
```python
if path in PUBLIC_PATHS or path.startswith("/.well-known/"):
    return await call_next(request)
```

Và thêm endpoint OIDC config:
```python
@router.get("/.well-known/openid-configuration")
def openid_configuration():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        ...
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["none"],
    }
```

### 7.6 RuntimeError: Response content longer than Content-Length (UTF-8 bug)

**Triệu chứng**: Tool call trả response bị truncate. Logs uvicorn báo:
```
RuntimeError: Response content longer than Content-Length
```

**Nguyên nhân**: Starlette `BaseHTTPMiddleware` (dùng bởi `@app.middleware("http")`) có bug khi response chứa **UTF-8 multi-byte** (tiếng Việt). Content-Length tính sai → uvicorn truncate → client parse JSON fail.

**Fix**: Bỏ middleware, dùng **inline auth check**:
```python
def _check_auth(request: Request) -> Optional[JSONResponse]:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    if not oauth.verify_token(token):
        return _build_401_response()
    return None

@app.post("/mcp")
async def mcp_endpoint(request: Request):
    err = _check_auth(request)
    if err is not None:
        return err
    # ... rest
```

CORSMiddleware OK vì là pure ASGI middleware (không kế thừa BaseHTTPMiddleware).

### 7.7 Dockerfile NULL byte sau khi edit từ Windows

**Triệu chứng**: `dockerfile parse error on line 7: unknown instruction:`

**Nguyên nhân**: Windows-mounted file system đôi khi append NULL byte (`\x00`) ở cuối file.

**Fix**: Rewrite bằng `printf` trên Linux:
```bash
printf 'FROM python:3.12-slim\nWORKDIR /app\n...' > Dockerfile
```

## 8. Tóm tắt deployment checklist

- [ ] VPS có Docker + Docker Compose v2
- [ ] sslh installed + config multiplex 443
- [ ] DNS A record trỏ về VPS
- [ ] `.env` file với 12 biến (DB, tokens, R2, LLM keys)
- [ ] Caddyfile với 6 handle blocks
- [ ] docker-compose.yml với 5 services
- [ ] GitHub repo có PAT với scope `repo, packages, workflow`
- [ ] GitHub Secrets: VPS_HOST, VPS_USER, VPS_PORT, VPS_SSH_KEY, MCP_BEARER_TOKEN, ARCHIVE_AUTH_TOKEN, GHCR_TOKEN
- [ ] Workflows: `tests.yml`, `build-and-deploy.yml`
- [ ] Push lên branch `test` → auto build + deploy
- [ ] Smoke test pass → connect Claude Desktop

→ Chi tiết CI/CD ở doc thứ 2: `huong-dan-ci-cd-github-actions.md`.
