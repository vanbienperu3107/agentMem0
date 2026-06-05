# Hướng dẫn CI/CD với GitHub Actions (cho người mới)

Tài liệu này hướng dẫn từ A → Z cách thiết lập **build + deploy tự động** từ GitHub về VPS, không cần SSH thủ công.

## 1. CI/CD là gì? Vì sao cần?

### 1.1 Định nghĩa

- **CI (Continuous Integration)**: mỗi lần push code, hệ thống tự test + build → đảm bảo code không vỡ
- **CD (Continuous Deployment)**: sau build OK, tự deploy lên server → user dùng code mới ngay

→ **CI/CD** = **không cần SSH thủ công** mỗi lần update code.

### 1.2 Vì sao quan trọng?

So sánh:

| | Không có CI/CD | Có CI/CD |
|---|---|---|
| Thời gian deploy | 15-30 phút (SSH, build, copy, restart) | 3-5 phút (tự động) |
| Lỗi quên bước | Cao (quên restart container, quên migrate DB) | Thấp (workflow định nghĩa cố định) |
| Rollback | Phải nhớ commit cũ + manual revert | Re-run workflow với SHA cũ |
| Verify production | Manual curl hoặc mở app | Smoke test tự chạy + báo fail ngay |
| Test trước deploy | Chạy local, dễ quên | Bắt buộc pass mới deploy |

### 1.3 Khi nào KHÔNG cần CI/CD?

- Project 1 người, chạy local thôi → không cần
- Side project demo 1 lần → overkill
- Static HTML không có build step → dùng GitHub Pages thay vì Actions

CI/CD cần khi: **có server**, **có nhiều người**, **deploy thường xuyên**.

## 2. GitHub Actions căn bản

### 2.1 Anatomy

```
Repository
└── .github/
    └── workflows/
        ├── tests.yml          ← workflow 1
        └── build-deploy.yml   ← workflow 2

Workflow file (.yml)
├── name: "Display name on GitHub UI"
├── on: when to trigger
│   ├── push: branches: [test]      ← run when push to test branch
│   ├── pull_request: ...
│   └── workflow_dispatch: ...      ← manual trigger button
├── permissions: what tokens can access
├── env: variables shared across jobs
└── jobs:
    └── build_job_name:
        ├── runs-on: ubuntu-latest  ← VM type (free)
        ├── strategy: matrix (run multiple variants)
        └── steps:
            ├── - uses: actions/checkout@v4  ← marketplace action
            └── - run: |                     ← shell command
                    echo "Hello"
                    docker build .
```

### 2.2 Các khái niệm

- **Workflow**: 1 file YAML định nghĩa toàn bộ pipeline
- **Trigger**: điều kiện kích hoạt (`on:` block) — push, PR, schedule, manual
- **Job**: tập hợp các bước chạy trên cùng 1 VM (runner)
- **Step**: 1 đơn vị thực thi — chạy shell command hoặc gọi action
- **Action**: code reusable từ marketplace (vd `actions/checkout@v4`)
- **Runner**: máy ảo chạy workflow (GitHub-hosted Ubuntu free 2000 phút/tháng)
- **Secret**: biến nhạy cảm (token, password) — encrypted, không hiện trong logs
- **Artifact**: file output từ build, có thể download

### 2.3 YAML syntax gotchas

```yaml
# Indentation 2 spaces, KHÔNG tab
jobs:
  my_job:        # ← 2 spaces
    runs-on: ...  # ← 4 spaces

# String multi-line dùng |
run: |
  echo "Line 1"
  echo "Line 2"

# Quote khi có special chars
run: 'echo "Hello: World"'

# Expression ${{ ... }} eval lúc runtime
run: echo "Branch is ${{ github.ref_name }}"

# Conditional với if
- if: github.event_name == 'push'
  run: echo "This is a push"
```

## 3. Setup GitHub Actions cho project mem0custom

### 3.1 Tổng quan pipeline mong muốn

```
[Developer]
   │ git push origin test
   ▼
[GitHub repo: branch test]
   │ trigger
   ▼
[GitHub Actions]
   ├─ Job 1: Tests (58 unit tests, syntax check)
   │      ✓ Pass
   └─ Job 2: Build & Deploy
          ├─ Build Docker image cho 3 service
          ├─ Push lên GHCR (ghcr.io/<user>/mem0custom-<svc>:test)
          └─ SSH vào VPS
                 ├─ docker compose pull
                 ├─ docker compose up -d --force-recreate
                 ├─ Smoke test /health
                 ├─ Smoke test OAuth discovery
                 ├─ Smoke test DCR /register
                 ├─ Smoke test tools/list
                 ├─ Smoke test tools/call
                 └─ Check no RuntimeError in logs
                     ✓ All pass = production verified
```

### 3.2 Bước 1: Tạo Personal Access Token (PAT) trên GitHub

PAT để GitHub Actions push image lên GHCR + để VPS pull image.

1. Mở https://github.com/settings/tokens/new
2. Note: `mem0custom-cicd`
3. Expiration: 90 days (hoặc No expiration)
4. Scopes tick:
   - `repo` (toàn bộ)
   - `workflow`
   - `write:packages`
   - `read:packages`
   - `delete:packages` (optional)
5. Generate → **copy ngay** (chỉ hiện 1 lần)

→ Lưu PAT vào password manager.

### 3.3 Bước 2: Tạo SSH key dedicated cho GitHub Actions

KHÔNG dùng key SSH local của Thanh (lẫn lộn, khó revoke).

**Trên Windows local:**

```powershell
ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\github_deploy -N '""' -C "github-actions-deploy"
```

→ Tạo 2 file:
- `github_deploy` — private key (paste vào GitHub Secrets)
- `github_deploy.pub` — public key (paste vào VPS authorized_keys)

**Trên VPS, add public key:**

```bash
cat >> ~/.ssh/authorized_keys << 'EOF'
ssh-ed25519 AAAA... github-actions-deploy
EOF
chmod 600 ~/.ssh/authorized_keys
```

→ Paste nội dung file `.pub` vào EOF.

**Test SSH từ Windows:**

```powershell
ssh -i $env:USERPROFILE\.ssh\github_deploy thanh@<VPS_IP> "echo OK"
```

→ Phải in `OK`.

### 3.4 Bước 3: Add SSH alias trong ~/.ssh/config (optional nhưng nên)

```
Host vps-deploy
    HostName 45.119.87.220
    User thanh
    Port 443
    IdentityFile C:\Users\thanhhn5\.ssh\github_deploy
    ProxyCommand "E:\Tool\PortableGit\mingw64\bin\connect.exe" -H 10.121.127.204:3128 %h %p
```

→ Sau này SSH bằng `ssh vps-deploy` thay vì paste full command.

### 3.5 Bước 4: Add Secrets vào GitHub repo

Mở https://github.com/<user>/<repo>/settings/secrets/actions

Click `New repository secret` cho từng cái:

| Name | Value | Lấy từ đâu |
|---|---|---|
| `VPS_HOST` | `45.119.87.220` | Hardcode IP VPS |
| `VPS_USER` | `thanh` | Hardcode user trên VPS |
| `VPS_PORT` | `443` | Port SSH (sslh multiplex) |
| `VPS_SSH_KEY` | content private key | `Get-Content $env:USERPROFILE\.ssh\github_deploy -Raw \| Set-Clipboard` |
| `MCP_BEARER_TOKEN` | token hiện tại | `ssh vps-deploy "grep '^MCP_BEARER_TOKEN=' ~/memory-stack/.env"` |
| `ARCHIVE_AUTH_TOKEN` | token hiện tại | Tương tự MCP_BEARER_TOKEN |
| `GHCR_TOKEN` | PAT từ Bước 1 | Đã có |

**Lưu ý**:
- `VPS_SSH_KEY` paste **toàn bộ** content, kể cả `-----BEGIN` và `-----END`
- KHÔNG wrap trong dấu ngoặc kép
- Secrets không thể view lại sau khi save, chỉ update được

### 3.6 Bước 5: Tạo workflow file tests.yml

Tạo file `.github/workflows/tests.yml`:

```yaml
name: Tests

on:
  push:
    branches: [main, new-features, test]
  pull_request:
    branches: [main, new-features]
  workflow_dispatch:

jobs:
  syntax-check:
    name: Validate code & config syntax
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }

      - name: Install validation tools
        run: |
          pip install pyyaml openapi-spec-validator boto3 mcp httpx fastapi "uvicorn[standard]"

      - name: Python syntax check
        run: |
          for f in $(find . -name "*.py" -not -path "./.git/*"); do
            python -c "import ast; ast.parse(open('$f').read())"
          done

      - name: YAML syntax check
        run: |
          for f in $(find . \( -name "*.yaml" -o -name "*.yml" \) -not -path "./.git/*"); do
            python -c "import yaml; yaml.safe_load(open('$f'))"
          done

      - name: Validate docker-compose
        run: docker compose -f docker-compose.yml config --quiet

      - name: Run OAuth unit tests
        run: |
          cd mcp-http-server
          python -m unittest test_oauth -v
```

→ Mỗi lần push lên `main`, `new-features`, `test` → tự chạy tests.

### 3.7 Bước 6: Tạo workflow build-and-deploy.yml

Tạo file `.github/workflows/build-and-deploy.yml`:

```yaml
name: Build & Deploy

on:
  push:
    branches: [test]    # Chỉ deploy khi push lên test
    paths:
      - 'mcp-http-server/**'
      - 'archive-api/**'
      - 'memory-rest-api/**'
      - '.github/workflows/build-and-deploy.yml'

  workflow_dispatch:    # Manual trigger từ UI
    inputs:
      service:
        type: choice
        options: [all, mcp-http-server, archive-api, memory-rest-api]
        default: 'mcp-http-server'

permissions:
  contents: read
  packages: write    # Cần để push GHCR

env:
  REGISTRY: ghcr.io
  IMAGE_OWNER: ${{ github.repository_owner }}

jobs:
  build:
    name: Build ${{ matrix.service }}
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        service: [mcp-http-server, archive-api, memory-rest-api]

    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Lowercase owner
        id: lc
        run: echo "owner=$(echo '${{ env.IMAGE_OWNER }}' | tr '[:upper:]' '[:lower:]')" >> $GITHUB_OUTPUT

      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: ./${{ matrix.service }}
          push: true
          tags: |
            ${{ env.REGISTRY }}/${{ steps.lc.outputs.owner }}/mem0custom-${{ matrix.service }}:test
            ${{ env.REGISTRY }}/${{ steps.lc.outputs.owner }}/mem0custom-${{ matrix.service }}:${{ github.sha }}
          cache-from: type=gha,scope=${{ matrix.service }}
          cache-to: type=gha,mode=max,scope=${{ matrix.service }}

  deploy:
    name: Deploy to VPS
    needs: build
    runs-on: ubuntu-latest
    steps:
      - name: Pull + restart on VPS
        uses: appleboy/ssh-action@v1.2.0
        with:
          host: ${{ secrets.VPS_HOST }}
          username: ${{ secrets.VPS_USER }}
          port: ${{ secrets.VPS_PORT }}
          key: ${{ secrets.VPS_SSH_KEY }}
          command_timeout: 10m
          script: |
            cd ~/memory-stack
            echo "$GHCR_TOKEN" | docker login ghcr.io -u thanhiont423 --password-stdin
            docker compose pull
            docker compose up -d --force-recreate
            docker compose ps

      - name: Smoke test /health
        run: |
          sleep 8
          code=$(curl -s -o /dev/null -w "%{http_code}" https://claude.hangocthanh.io.vn/health)
          test "$code" = "200" && echo "/health OK" || exit 1

      - name: Smoke test OAuth discovery
        run: |
          for path in oauth-authorization-server oauth-protected-resource openid-configuration; do
            code=$(curl -s -o /dev/null -w "%{http_code}" "https://claude.hangocthanh.io.vn/mcp/.well-known/$path")
            test "$code" = "200" || (echo "Path $path got $code" && exit 1)
          done

      - name: Smoke test MCP tools/list
        env:
          TOKEN: ${{ secrets.MCP_BEARER_TOKEN }}
        run: |
          resp=$(curl -s -X POST https://claude.hangocthanh.io.vn/mcp \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}')
          count=$(echo "$resp" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['result']['tools']))")
          test "$count" -ge "6" || (echo "Expected >=6 tools, got $count" && exit 1)
```

### 3.8 Bước 7: Sửa docker-compose.yml dùng GHCR image

Trên VPS, sửa `~/memory-stack/docker-compose.yml`:

```yaml
# Thay thế block build: bằng image:
mcp-http-server:
  # BEFORE:
  # build: ./mcp-http-server
  # AFTER:
  image: ghcr.io/thanhiont423/mem0custom-mcp-http-server:test
  container_name: memory-mcp-http
  ...
```

Tương tự cho `archive-api`, `memory-rest-api`.

### 3.9 Bước 8: Login GHCR trên VPS lần đầu

```bash
# SSH vào VPS
ssh vps-deploy

# Login GHCR
echo "<GHCR_TOKEN>" | docker login ghcr.io -u <github_username> --password-stdin

# → Phải in: Login Succeeded
```

→ Credentials lưu vào `~/.docker/config.json`, dùng lại được nhiều lần.

### 3.10 Bước 9: Test pipeline

**Push commit test:**

```powershell
cd "E:\Thanhhn5\41. Khoa hoc\Claude\mem0custom"
git checkout test
echo "# Test CI/CD" >> README.md
git add README.md
git commit -m "test: trigger CI/CD pipeline"
git push origin test
```

**Monitor:**

Mở https://github.com/<user>/<repo>/actions

→ Phải thấy 2 workflows chạy:
- "Tests" — pass trong 1-2 phút
- "Build & Deploy" — pass trong 3-5 phút

**Verify production:**

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" https://claude.hangocthanh.io.vn/health
```

→ `200` = pipeline hoạt động.

## 4. Smoke tests — phát hiện bug ngay sau deploy

### 4.1 Triết lý

Build pass ≠ Production work.

Code có thể compile OK nhưng:
- Container startup fail
- Service không thấy nhau (DNS Docker)
- DB connection broken
- Endpoint trả 500 do logic bug

→ Cần **smoke test** sau deploy: gọi endpoint thực tế, verify status code + content.

### 4.2 Tầng smoke test

```
Layer 1: TCP connect      → /health (server còn sống)
Layer 2: HTTP routing     → /mcp/.well-known/* (Caddy + FastAPI mounting OK)
Layer 3: Business logic   → tools/list (FastAPI router + auth OK)
Layer 4: Backend deps     → tools/call (archive-api + DB + R2 OK)
Layer 5: No regression    → grep logs cho RuntimeError
```

→ Test sâu nhất layer 4 catch nhiều bug nhất.

### 4.3 Ví dụ smoke test phát hiện bug thực tế

Workflow chạy step "Smoke test MCP tools/call list_old_sessions":

```yaml
- name: Smoke test MCP tools/call list_old_sessions
  env:
    TOKEN: ${{ secrets.MCP_BEARER_TOKEN }}
  run: |
    resp=$(curl -s -X POST https://claude.hangocthanh.io.vn/mcp \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"list_old_sessions","arguments":{"limit":3}}}')
    echo "=== RAW RESPONSE ==="
    echo "$resp"
    echo "$resp" | python3 -c "
    import sys, json
    r = json.load(sys.stdin)
    result = r.get('result', {})
    if result.get('isError'):
        print('FAIL:', result['content'][0]['text']); sys.exit(1)
    print('OK')
    "
```

Khi bug archive-api thiếu httpx module, workflow output:

```
=== RAW RESPONSE ===
{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"Error: [Errno -2] Name or service not known"}],"isError":true}}
FAIL: Error: [Errno -2] Name or service not known
```

→ Thanh thấy ngay bug **archive-api không reachable**. Add debug step để dump archive-api logs:

```yaml
- name: Debug archive-api logs (only if smoke test failed)
  if: failure()
  uses: appleboy/ssh-action@v1.2.0
  with:
    host: ${{ secrets.VPS_HOST }}
    ...
    script: |
      docker logs --tail 50 memory-archive-api 2>&1 | tail -50
```

→ Logs hiện trên GitHub Actions UI:

```
ModuleNotFoundError: No module named 'httpx'
```

→ Pinpoint exact bug: `requirements.txt` thiếu httpx.

### 4.4 Pattern self-documenting failure

`if: failure()` chỉ chạy khi step trước fail. Dùng để:
- Dump logs container
- Print env variables (cẩn thận với secrets)
- Báo Slack/Telegram

→ Mỗi lần fail = tự động có context để debug, không cần SSH manual.

## 5. Best practices

### 5.1 Branch strategy

```
main          ← stable, production-ready
  ↑ merge after test
test          ← deploy to production VPS (auto-deploy on push)
  ↑ merge after dev
new-features  ← in-progress development
```

→ Chỉ branch `test` trigger deploy. `main` chỉ chứa stable code (audit trail).

### 5.2 Secrets management

- KHÔNG hardcode token trong code, dù là comment
- KHÔNG paste secret vào chat AI (chatGPT, Claude) — luôn rotate sau khi accidental leak
- Dùng GitHub Secrets cho CI, **không** dùng env file commit lên Git
- Rotate token định kỳ (90 days)
- Mỗi env (dev/staging/prod) có secrets riêng

### 5.3 Cache để giảm thời gian build

```yaml
- uses: docker/build-push-action@v5
  with:
    cache-from: type=gha,scope=${{ matrix.service }}
    cache-to: type=gha,mode=max,scope=${{ matrix.service }}
```

→ Layer Docker không đổi (vd pip install requirements.txt) được cache → build từ 3 phút xuống 30 giây.

### 5.4 Matrix strategy build song song

```yaml
strategy:
  matrix:
    service: [mcp-http-server, archive-api, memory-rest-api]
```

→ Build 3 service song song trên 3 runner thay vì tuần tự. Tổng thời gian = thời gian build service chậm nhất.

### 5.5 fail-fast: false

```yaml
strategy:
  fail-fast: false
```

→ Nếu 1 service build fail, 2 service kia vẫn chạy. Để debug nhiều bug cùng lúc.

### 5.6 Workflow dispatch để manual trigger

```yaml
on:
  workflow_dispatch:
    inputs:
      service:
        type: choice
        options: [all, mcp-http-server, archive-api, memory-rest-api]
```

→ Trên UI có nút "Run workflow" → chọn service deploy → không cần push commit.

## 6. Troubleshooting workflow thường gặp

### 6.1 Error: "missing server host"

**Cause**: secrets `VPS_HOST` rỗng.

**Fix**: Add lại secret, đảm bảo paste đúng IP, không có space.

### 6.2 Error: "Permission denied (publickey)"

**Cause**: Public key chưa add vào `~/.ssh/authorized_keys` trên VPS.

**Fix**:
```bash
ssh thanh@VPS_IP "cat ~/.ssh/authorized_keys"
# Verify ed25519 key có trong list
```

### 6.3 Error: "unauthorized: authentication required" khi pull GHCR

**Cause**: GHCR_TOKEN thiếu scope `read:packages` hoặc đã expire.

**Fix**: Regenerate PAT với đủ scope.

### 6.4 Workflow chạy nhưng deploy không update code

**Cause**: Docker compose cache image cũ.

**Fix**: Thêm `--force-recreate`:
```bash
docker compose up -d --force-recreate
```

### 6.5 Smoke test pass trên dev nhưng fail trên CI

**Cause**: Locale, timezone, env vars khác nhau.

**Fix**: Print env trong workflow:
```yaml
- run: env | grep -v -i "token\|key\|secret"
```

## 7. Kết luận

CI/CD với GitHub Actions giải quyết 3 vấn đề:

1. **Reproducibility**: deploy giống nhau mỗi lần, không phụ thuộc human memory
2. **Speed**: build song song + cache → 3-5 phút từ push tới production
3. **Confidence**: smoke test verify production thực sự work, không chỉ "build OK"

Đầu tư setup 1-2 giờ ban đầu, tiết kiệm hàng giờ debug + manual deploy hàng tháng.

## 8. Tài liệu tham khảo

- [GitHub Actions docs](https://docs.github.com/en/actions)
- [appleboy/ssh-action](https://github.com/appleboy/ssh-action) — SSH deploy action
- [docker/build-push-action](https://github.com/docker/build-push-action) — Build + push Docker image
- [GHCR docs](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [MCP Specification](https://spec.modelcontextprotocol.io/) — Model Context Protocol
- [RFC 7591](https://datatracker.ietf.org/doc/html/rfc7591) — Dynamic Client Registration
- [RFC 8414](https://datatracker.ietf.org/doc/html/rfc8414) — Authorization Server Metadata
- [RFC 9728](https://datatracker.ietf.org/doc/html/rfc9728) — Protected Resource Metadata
