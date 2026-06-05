# mem0custom

**Self-hosted memory server** kết hợp **mem0** (vector memory) + **transcript archive** (lưu full chat) trên VPS duy nhất. Tương thích Claude Desktop App, Claude Code, ChatGPT App, và ChatGPT Custom GPT.

Production: [`claude.hangocthanh.io.vn`](https://claude.hangocthanh.io.vn)

[![Tests](https://github.com/thanhiont423/mem0custom/actions/workflows/tests.yml/badge.svg)](https://github.com/thanhiont423/mem0custom/actions/workflows/tests.yml)
[![Deploy](https://github.com/thanhiont423/mem0custom/actions/workflows/build-and-deploy.yml/badge.svg?branch=test)](https://github.com/thanhiont423/mem0custom/actions/workflows/build-and-deploy.yml)

---

## Tính năng

- **Lưu trữ memory dạng vector** (Qdrant) — AI nhớ về user qua nhiều phiên chat
- **Archive transcript đầy đủ** (Postgres + R2 hybrid) — review lại bất kỳ cuộc trò chuyện cũ
- **8 MCP tools** — list, search keyword, search semantic, summary, full, continuation, ChatGPT-compat alias
- **OAuth 2.1 + Dynamic Client Registration** — Claude Desktop App native support
- **REST API + OpenAPI** — ChatGPT Custom GPT integration
- **CI/CD tự động** — push code → GitHub Actions build + deploy + 6 smoke tests
- **R2 hybrid storage** — transcript gzipped trên Cloudflare R2, metadata trên Postgres

## Architecture

```
Client (Mac / Windows)              VPS (claude.hangocthanh.io.vn)
┌─────────────────────────┐         ┌──────────────────────────────────┐
│ Claude Code (stdio MCP) │────────▶│  sslh (port 443 multiplex)       │
│ Claude App (HTTP+OAuth) │────────▶│  ├── SSH → sshd                  │
│ ChatGPT App (HTTP MCP)  │────────▶│  └── HTTPS → Caddy               │
│ ChatGPT GPT Action      │────────▶│         ├── /mcp     mcp-http    │
└─────────────────────────┘         │         ├── /archive archive-api │
                                    │         ├── /memory  memory-rest │
                                    │         └── /qdrant  qdrant      │
                                    │                                  │
                                    │  Storage:                        │
                                    │  - Qdrant         (vector)       │
                                    │  - Neon Postgres  (metadata)     │
                                    │  - Cloudflare R2  (transcripts)  │
                                    └──────────────────────────────────┘
```

## Stack công nghệ

| Layer | Tech |
|---|---|
| Reverse proxy | Caddy 2.8 + sslh |
| MCP HTTP server | FastAPI + httpx (Streamable HTTP transport) |
| Archive API | FastAPI + psycopg2 + boto3 |
| Memory REST | FastAPI + mem0ai<2.0 |
| Vector DB | Qdrant 1.12 (collection `mem0_mcp_selfhosted`) |
| Postgres | Neon Cloud (ap-southeast-1) |
| Object storage | Cloudflare R2 (APAC) |
| Container | Docker Compose v2 |
| Registry | GHCR (`ghcr.io/thanhiont423/mem0custom-*`) |
| CI/CD | GitHub Actions |

## Quick start

### Trên VPS (Ubuntu 22.04+)

```bash
# Clone repo
git clone https://github.com/thanhiont423/mem0custom.git
cd mem0custom

# Setup environment
cp .env.example .env
# Edit .env: điền QDRANT_API_KEY, DB_URL, MCP_BEARER_TOKEN, ARCHIVE_AUTH_TOKEN,
# OPENAI_API_KEY, ANTHROPIC_API_KEY, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY

# Build + run
docker compose up -d --build

# Verify
curl https://claude.hangocthanh.io.vn/health
# → "ok"
```

### Trên client (Mac / Windows)

**Claude Desktop App**:

1. Settings → Connectors → Add custom connector
2. URL: `https://claude.hangocthanh.io.vn/mcp`
3. Click Connect → OAuth tự xong

**Claude Code (stdio MCP)**:

```bash
# Cài archive-mcp.py + config trong ~/.config/claude-code/mcp.json
# Chi tiết xem: docs/huong-dan-trien-khai-mcp-server.md
```

**ChatGPT Custom GPT**:

1. Tạo GPT mới trên https://chat.openai.com/gpts/editor
2. Actions → Import OpenAPI spec từ `memory-rest-api/openapi-for-chatgpt.yaml`
3. Auth: Bearer Token = `MCP_BEARER_TOKEN`

## Branches

- **`main`** — stable production code, có merge từ test branch sau khi verified
- **`test`** — deploy tự động qua GitHub Actions khi push (build + deploy + 6 smoke tests)
- **`new-features`** — development branch cho tính năng mới

## CI/CD

Mỗi `git push origin test` trigger workflow `Build & Deploy`:

```
Push → Build 3 Docker images → Push GHCR → SSH VPS → docker compose pull
     → Restart containers → Smoke tests (6 endpoints) → Pass/Fail badge
```

Total: ~4 phút từ push đến production verified.

Workflow gồm:
- Tests (58 unit tests, Python AST, YAML, OpenAPI, docker-compose validate)
- Build Docker images (matrix song song)
- Deploy to VPS via SSH
- 6 smoke tests: `/health`, OAuth discovery, DCR, tools/list, tools/call, no RuntimeError logs

## Tài liệu chi tiết

| File | Mô tả |
|---|---|
| [`docs/huong-dan-trien-khai-mcp-server.md`](docs/huong-dan-trien-khai-mcp-server.md) | Kiến trúc MCP, OAuth flow, 7 bugs gặp + fix |
| [`docs/huong-dan-ci-cd-github-actions.md`](docs/huong-dan-ci-cd-github-actions.md) | CI/CD GitHub Actions cho người mới |
| [`docs/huong-dan-trien-khai-r2-storage.md`](docs/huong-dan-trien-khai-r2-storage.md) | Setup Cloudflare R2 với 4-layer cost protection |
| [`docs/R2-PROTECTION-LAYERS.md`](docs/R2-PROTECTION-LAYERS.md) | Chi tiết về 4 lớp bảo vệ chi phí R2 |
| [`docs/NEW-FEATURES.md`](docs/NEW-FEATURES.md) | Mô tả 5 tính năng mới |
| [`docs/ROADMAP-RAG.md`](docs/ROADMAP-RAG.md) | Lộ trình nâng cấp RAG: reranker, hybrid search, time-aware, HyDE |
| [`docs/plan-trien-khai-memory-server-mac-windows.md`](docs/plan-trien-khai-memory-server-mac-windows.md) | Plan triển khai gốc (~4200 dòng) |

## Repo layout

```
mem0custom/
├── README.md
├── docker-compose.yml
├── Caddyfile
├── .env.example
├── .github/workflows/
│   ├── tests.yml
│   └── build-and-deploy.yml
├── mcp-http-server/         # MCP HTTP + OAuth 2.1 + DCR
│   ├── app.py
│   ├── oauth.py
│   ├── test_oauth.py
│   ├── requirements.txt
│   └── Dockerfile
├── archive-api/             # Transcript REST API
│   ├── app.py
│   ├── summarizer.py        # Claude Haiku → summary
│   ├── embeddings.py        # OpenAI text-embedding-3-small
│   ├── qdrant_helper.py
│   ├── r2_storage.py        # Cloudflare R2 upload/download
│   ├── requirements.txt
│   └── Dockerfile
├── memory-rest-api/         # mem0 REST cho ChatGPT
│   ├── app.py
│   ├── openapi-for-chatgpt.yaml
│   ├── requirements.txt
│   └── Dockerfile
├── scripts/                 # Client-side scripts
│   ├── archive-upload.py    # Upload ~/.claude/projects/*.jsonl
│   ├── archive-mcp.py       # stdio MCP cho Claude Code
│   ├── r2-budget-check.py   # Cron Telegram alert R2 cost
│   ├── test_archive_mcp.py
│   ├── test_r2_budget_check.py
│   └── archive-env.example.{sh,ps1}
└── docs/
    ├── huong-dan-trien-khai-mcp-server.md
    ├── huong-dan-ci-cd-github-actions.md
    ├── huong-dan-trien-khai-r2-storage.md
    ├── R2-PROTECTION-LAYERS.md
    ├── NEW-FEATURES.md
    └── plan-trien-khai-memory-server-mac-windows.md
```

## Auth model

- **OAuth 2.1 + DCR** cho Claude Desktop App (PKCE S256, auto-approve single-user)
- **Bearer token** cho ChatGPT App + Claude Code stdio + REST API
- `MCP_BEARER_TOKEN`, `ARCHIVE_AUTH_TOKEN` generate bằng `openssl rand -hex 32`

## Roadmap

- [x] mem0 + Qdrant vector layer
- [x] Archive transcript layer với Postgres
- [x] Caddy reverse proxy + sslh multiplex 443
- [x] R2 hybrid storage (gzipped transcripts)
- [x] LLM-generated summary (Claude Haiku)
- [x] Semantic search trên summary embeddings
- [x] `load_context_for_continuation` MCP tool
- [x] OAuth 2.1 + DCR cho Claude Desktop App
- [x] OpenAPI cho ChatGPT Custom GPT
- [x] GitHub Actions CI/CD (build + deploy + smoke tests)
- [ ] Branch protection rules cho main
- [ ] Scheduled daily health check
- [ ] Slack/Telegram notification on workflow failure
- [ ] Multi-user OAuth với consent UI
- [ ] Per-user token storage trong Postgres

### Nâng cấp RAG (xem chi tiết tại [`docs/ROADMAP-RAG.md`](docs/ROADMAP-RAG.md))

Hệ thống hiện tại là **RAG 1.0** (vector search thuần). Lộ trình nâng cấp lên **RAG 2.0**:

- [ ] **#1 — Reranker (cross-encoder)** — top-20 vector → rerank → top-5; Cohere API hoặc BGE local; kỳ vọng +20-35% precision@5
- [ ] **#2 — Hybrid search (BM25 + vector)** — fix miss với tên kỹ thuật / proper noun; fuse bằng RRF; kỳ vọng +15-25% recall
- [ ] **#3 — Embedding tốt hơn cho tiếng Việt** — đổi sang `text-embedding-3-large` hoặc `multilingual-e5-large`; cần re-index
- [ ] **#4 — Time-aware ranking** — score cuối = α × similarity + β × recency_decay; backup cho cơ chế UPDATE của mem0
- [ ] **#5 — Query rewriting / HyDE** — Haiku viết lại query mơ hồ trước khi embed; ưu tiên cho ChatGPT App

**Nguyên tắc**: KHÔNG làm trước khi đo. Giai đoạn 1 (06-07/2026) log 50 query thực tế để xác định cải tiến nào cần ưu tiên.

---

## ☕ Ủng hộ tác giả

Nếu project này hữu ích cho bạn, mời tác giả một ly cà phê để duy trì server + động lực phát triển tính năng mới:

### 🇻🇳 Vietnam (tất cả ngân hàng VN qua số điện thoại)

**Số tài khoản: `0869649888`**

Chủ tài khoản: **HÀ NGỌC THANH**

Áp dụng cho mọi ngân hàng Việt Nam:

- Vietcombank
- VietinBank
- BIDV
- Agribank
- Techcombank
- MB Bank
- VPBank
- TPBank
- ACB
- Sacombank
- HDBank
- SHB
- VIB
- OCB
- ... (mọi ngân hàng khác có hỗ trợ chuyển khoản qua số điện thoại / Napas 247)

### 📱 Ví điện tử

- **Momo**: `0869649888`
- **ZaloPay**: `0869649888`
- **ViettelPay**: `0869649888`

### 🌍 International

- **Buy Me a Coffee**: [buymeacoffee.com/thanhhang](https://buymeacoffee.com/thanhhang)
- **PayPal**: [paypal.me/thanhhang](https://paypal.me/thanhhang)

### ₿ Crypto

- **USDT (TRC20)**: liên hệ qua email để nhận địa chỉ
- **BTC**: liên hệ qua email để nhận địa chỉ

Mọi đóng góp dù 10K, 50K hay $1 đều được trân trọng và động viên rất nhiều. Cảm ơn các bạn! ❤️

---

## ⚠️ Tuyên bố miễn trừ trách nhiệm

**Đây là project cá nhân, không phải sản phẩm thương mại.**

### Phạm vi sử dụng

- Project này được phát triển cho **mục đích học tập, nghiên cứu, và sử dụng cá nhân**.
- Không được tài trợ hay liên kết bởi Anthropic, OpenAI, mem0.ai, Cloudflare, hoặc Neon.
- Tác giả KHÔNG khuyến khích sử dụng để lưu trữ thông tin nhạy cảm (mật khẩu, thông tin tài chính, thông tin sức khỏe, dữ liệu khách hàng).

### Không bảo hành

Project được cung cấp **"AS IS"** (như hiện trạng), không có bất kỳ bảo hành nào dù tường minh hay ngầm định, bao gồm nhưng không giới hạn:

- Bảo hành về tính phù hợp với mục đích cụ thể (fitness for a particular purpose)
- Bảo hành không vi phạm bản quyền (non-infringement)
- Bảo hành về tính khả dụng liên tục (continuous availability)
- Bảo hành về tính chính xác của kết quả

### Giới hạn trách nhiệm

Tác giả **KHÔNG chịu trách nhiệm** cho bất kỳ thiệt hại nào phát sinh từ việc sử dụng project, bao gồm:

- Mất mát dữ liệu (do bug, do server crash, do user lỗi thao tác)
- Lộ thông tin (do cấu hình sai, do token bị leak, do quên rotate)
- Chi phí phát sinh từ third-party services (OpenAI API, Anthropic API, Cloudflare, Neon, VPS bill)
- Vi phạm điều khoản dịch vụ của các bên thứ ba (nếu user dùng project trong mục đích cấm)
- Gián đoạn công việc, mất thời gian, mất uy tín

### Trách nhiệm của người dùng

Người dùng **TỰ CHỊU TRÁCH NHIỆM** về:

- **Bảo mật**: rotate token định kỳ, không commit secret vào Git, không paste secret vào AI chat
- **Backup**: thường xuyên backup Postgres + R2 transcripts (project KHÔNG có backup tự động)
- **Chi phí**: monitor billing Anthropic/OpenAI/Cloudflare/VPS để tránh vượt budget
- **Tuân thủ luật pháp**: nội dung lưu trữ tuân thủ luật Việt Nam + GDPR/CCPA nếu phục vụ user quốc tế
- **Audit**: review code trước khi deploy production, không chạy mù code lạ
- **Update**: theo dõi security advisory của FastAPI, Qdrant, mem0ai, các dependency

### Third-party services

Project tích hợp với các dịch vụ thứ ba có điều khoản riêng:

- [Anthropic Usage Policies](https://www.anthropic.com/policies)
- [OpenAI Usage Policies](https://openai.com/policies/usage-policies)
- [Cloudflare Terms](https://www.cloudflare.com/terms/)
- [Neon Terms](https://neon.tech/terms-of-service)
- [GitHub Terms](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service)

User phải đảm bảo việc sử dụng project tuân thủ các điều khoản trên. Tác giả không kiểm soát chính sách của bên thứ ba.

### Dữ liệu cá nhân

Project lưu trữ transcript chat — có thể chứa dữ liệu cá nhân (PII). Nếu user lưu chat của user khác (vd customer service), cần:

- Có sự đồng ý (consent) của các bên
- Tuân thủ Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân (Việt Nam)
- Tuân thủ GDPR nếu có user EU, CCPA nếu có user California

Tác giả KHÔNG cung cấp tư vấn pháp lý — vui lòng tham vấn luật sư nếu deploy production.

### Quyền hủy bỏ

Tác giả có quyền:

- Ngừng phát triển project bất kỳ lúc nào mà không thông báo trước
- Thay đổi license trong các version sau
- Xóa repository khỏi GitHub
- Tắt production server `claude.hangocthanh.io.vn`

### Đóng góp

Khi user gửi Pull Request hoặc Issue, user đồng ý:

- Code đóng góp dưới cùng license với project
- Tác giả có quyền chỉnh sửa hoặc từ chối contribution
- Không có nghĩa vụ phải ghi credit (nhưng sẽ cố gắng làm vậy)

---

## License

**Personal Use License**

- Sử dụng cá nhân, học tập, nghiên cứu: ✅ Miễn phí, không cần xin phép
- Fork, modify cho personal use: ✅ Free
- Đăng lại với attribution: ✅ Welcome
- Sử dụng thương mại (commercial use): ❌ Cần liên hệ tác giả
- Resell hoặc cung cấp dịch vụ SaaS dựa trên project: ❌ Cấm

Không liên kết với mem0.ai, Anthropic, OpenAI hay bất kỳ tổ chức nào khác.

---

## Liên hệ

- **GitHub Issues**: [github.com/thanhiont423/mem0custom/issues](https://github.com/thanhiont423/mem0custom/issues)
- **Email**: hangocthanhperu3107@gmail.com (chỉ cho câu hỏi technical, không hỗ trợ sử dụng cá nhân)

---

> "Built with ❤️ for personal AI memory. Not affiliated with mem0.ai, Anthropic, OpenAI, or any third party."
