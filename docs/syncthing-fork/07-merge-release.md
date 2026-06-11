# Merge & Release — Integration Test & Quy trình phát hành

## Tổng quan

Sau khi hoàn thành 5 feature branches (F1–F5), merge tất cả vào branch `develop`, chạy integration test toàn diện, rồi tạo release branch.

---

## Phase 1 — Plan: Thứ tự merge

### 1.1 Merge order (quan trọng — có dependency)

```
Bước 1: feature/wss-transport     → develop   (F1, nền tảng)
Bước 2: feature/bearer-auth       → develop   (F2, phụ thuộc F1)
Bước 3: feature/incremental-scan  → develop   (F3, independent)
Bước 4: feature/rsync-delta       → develop   (F4, phụ thuộc F1+F3)
Bước 5: feature/lww-ancestor      → develop   (F5, phụ thuộc F4)
```

### 1.2 Pre-merge checklist cho MỖI feature

Trước khi merge mỗi feature vào develop, verify:

- [ ] Feature branch rebase lên develop mới nhất
- [ ] `go build ./...` pass (compile OK)
- [ ] `go vet ./...` pass
- [ ] `go test ./... -count=1` pass (tất cả tests)
- [ ] `go test ./... -race -count=1` pass (race detector)
- [ ] `golangci-lint run ./...` pass
- [ ] Không có TODO/FIXME chưa xử lý trong code mới
- [ ] Commit messages follow convention

### 1.3 Merge commands

```bash
# Bước 1: F1
git checkout develop
git merge feature/wss-transport --no-ff
go test ./... -count=1 -race
# Nếu pass → tiếp, nếu fail → fix trên feature branch rồi merge lại

# Bước 2: F2
git checkout feature/bearer-auth
git rebase develop              # rebase lên develop (đã có F1)
# Fix conflicts nếu có (chủ yếu ở lib/connections/service.go)
go test ./... -count=1
git checkout develop
git merge feature/bearer-auth --no-ff
go test ./... -count=1 -race

# Bước 3: F3
git checkout feature/incremental-scan
git rebase develop
go test ./... -count=1
git checkout develop
git merge feature/incremental-scan --no-ff
go test ./... -count=1 -race

# Bước 4: F4
git checkout feature/rsync-delta
git rebase develop              # rebase lên develop (có F1+F2+F3)
go test ./... -count=1
git checkout develop
git merge feature/rsync-delta --no-ff
go test ./... -count=1 -race

# Bước 5: F5
git checkout feature/lww-ancestor
git rebase develop
go test ./... -count=1
git checkout develop
git merge feature/lww-ancestor --no-ff
go test ./... -count=1 -race
```

### 1.4 Conflict hotspots dự kiến

| File | Conflict giữa | Cách resolve |
|------|---------------|-------------|
| `lib/connections/service.go` | F1 + F2 | F2 build trên F1, merge F1 trước |
| `lib/model/folder_sendrecv.go` | F4 + F5 | F5 build trên F4, merge F4 trước |
| `lib/model/folder.go` | F3 + F4 | F3 sửa scan, F4 sửa transfer — ít overlap |
| `lib/config/config.go` | F1–F5 tất cả thêm fields | Merge additive — giữ tất cả fields |
| `go.mod` | F1 (gorilla/ws) + F2 (jwt) | `go mod tidy` sau merge |

---

## Phase 2 — Review toàn bộ code sau merge

### 2.1 Code review checklist

- [ ] **Consistency:** Tất cả features dùng cùng error handling pattern
- [ ] **Config:** Tất cả config fields mới có default values hợp lý
- [ ] **Logging:** Log levels nhất quán (Info cho lifecycle, Debug cho detail)
- [ ] **Naming:** Conventions nhất quán (camelCase cho exported, snake cho internal)
- [ ] **Dependencies:** `go mod tidy` — không có unused deps
- [ ] **Dead code:** Không còn code cũ bị comment out hoặc unreachable
- [ ] **Backward compat:** Config migration từ syncthing gốc hoạt động

### 2.2 Security review

- [ ] Bearer token không bị log
- [ ] Hub secret không hardcode trong source
- [ ] WSS enforce TLS 1.3 minimum
- [ ] Registration endpoint có protection (secret hoặc rate limit)
- [ ] Digest cache không leak file content
- [ ] rsync operations validate block hashes
- [ ] Ancestor store dùng parameterized SQL (no injection)

### 2.3 Performance review

- [ ] Incremental scan skip non-dirty subtrees đúng
- [ ] rsync delta nhỏ hơn full transfer cho typical changes
- [ ] WebSocket connection pooling/reuse hoạt động
- [ ] Digest cache có eviction policy (không grow vô hạn)
- [ ] Ancestor table có index trên (folder, path)

---

## Phase 3 — Integration Test toàn diện

### 3.1 Test environment setup

```bash
# Cần 3 machines (hoặc 3 containers)
# Hub: VPS với port 443
# Client A: máy 1
# Client B: máy 2

# Docker setup cho CI:
docker-compose up -d hub client-a client-b
```

```yaml
# docker-compose.test.yml
version: "3.8"
services:
  hub:
    build: .
    command: ["./syncthing", "--hub-mode", "--listen=:443"]
    ports: ["443:443"]
    volumes: ["./test-data/hub:/data"]
    environment:
      HUB_SECRET: "test-secret-for-ci-only-32bytes!!"

  client-a:
    build: .
    command: ["./syncthing", "--hub-url=wss://hub:443/ws"]
    volumes: ["./test-data/client-a:/data"]
    depends_on: [hub]

  client-b:
    build: .
    command: ["./syncthing", "--hub-url=wss://hub:443/ws"]
    volumes: ["./test-data/client-b:/data"]
    depends_on: [hub]
```

### 3.2 Test scenarios

#### T1 — Basic sync (smoke test)
```
1. Register client A và B với hub
2. Tạo shared folder trên cả 2
3. Client A tạo file test.txt (100 bytes)
4. Verify: Client B nhận file trong < 10s
5. Verify: file content identical (sha256sum)
```
**Pass criteria:** File sync thành công, content match

#### T2 — Bidirectional sync
```
1. Client A tạo file-a.txt
2. Client B tạo file-b.txt (đồng thời)
3. Đợi sync hoàn tất
4. Verify: cả 2 client có cả 2 files
```
**Pass criteria:** Cả 2 files tồn tại trên cả 2 clients

#### T3 — LWW conflict resolution
```
1. Disconnect client B (stop syncthing B)
2. Client A sửa shared.txt, mtime = T1
3. Client B sửa shared.txt offline, mtime = T2 (T2 > T1)
4. Reconnect client B
5. Verify: shared.txt content = phiên bản B (mtime mới hơn)
6. Verify: KHÔNG có .sync-conflict file
```
**Pass criteria:** Latest mtime wins, no conflict files

#### T4 — Delete + modify conflict
```
1. Disconnect client B
2. Client A xóa shared.txt
3. Client B sửa shared.txt offline
4. Reconnect client B
5. Verify: shared.txt tồn tại (modified version wins)
```
**Pass criteria:** Modified file preserved

#### T5 — rsync delta efficiency
```
1. Client A tạo large-file.bin (10MB random data)
2. Sync to client B
3. Client A sửa 100 bytes ở giữa file
4. Monitor network traffic
5. Verify: transfer size < 100KB (not full 10MB)
```
**Pass criteria:** Delta transfer < 1% of file size

#### T6 — Incremental scan performance
```
1. Tạo 100,000 files trong shared folder
2. Initial sync hoàn tất
3. Sửa 5 files
4. Measure scan time
5. Verify: scan time < 1s (not proportional to 100K files)
```
**Pass criteria:** Scan time proportional to changes, not total files

#### T7 — Proxy compatibility
```
1. Setup HTTP proxy (squid) chỉ cho phép port 443
2. Client A kết nối hub qua proxy
3. Tạo file, verify sync hoạt động
```
**Pass criteria:** Sync works through HTTP proxy on port 443

#### T8 — Token expiry & refresh
```
1. Set token TTL = 5 seconds (test config)
2. Client connect với token
3. Đợi token hết hạn
4. Verify: client tự refresh token
5. Verify: sync tiếp tục hoạt động
```
**Pass criteria:** Auto-refresh, no connection drop

#### T9 — Hub restart resilience
```
1. Client A + B connected, syncing
2. Kill hub process
3. Restart hub
4. Verify: clients auto-reconnect
5. Verify: sync resume không mất data
```
**Pass criteria:** Auto-reconnect, no data loss

#### T10 — Multi-client sync (3+ devices)
```
1. Register client A, B, C
2. All share folder F
3. Client A tạo file → verify B và C nhận
4. Client C sửa file → verify A và B nhận
```
**Pass criteria:** N-way sync through hub works

### 3.3 Chạy integration tests

```bash
# Automated
go test ./test/integration/... -v -count=1 -timeout 300s

# Manual với docker
docker-compose -f docker-compose.test.yml up --build
# Chạy test script
./test/run-integration.sh

# Xem logs
docker-compose -f docker-compose.test.yml logs -f
```

### 3.4 Performance benchmarks

```bash
# Scan performance
go test ./lib/scanner/... -bench=BenchmarkIncrementalScan -benchmem

# rsync delta
go test ./lib/rsync/... -bench=BenchmarkDeltify -benchmem

# End-to-end latency
go test ./test/... -bench=BenchmarkSyncLatency -benchmem
```

---

## Phase 4 — Release

### 4.1 Version tagging

```bash
git checkout develop
# Verify tất cả tests pass lần cuối
go test ./... -count=1 -race

# Tạo release branch
git checkout -b release/v1.0.0

# Tag
git tag -a v1.0.0 -m "v1.0.0: WSS transport, bearer auth, incremental scan, rsync delta, LWW ancestor"

# Push
git push origin release/v1.0.0
git push origin v1.0.0
```

### 4.2 Build release binaries

```bash
# Build cho các platforms
GOOS=linux   GOARCH=amd64 go run build.go -no-upgrade -version v1.0.0
GOOS=linux   GOARCH=arm64 go run build.go -no-upgrade -version v1.0.0
GOOS=darwin  GOARCH=arm64 go run build.go -no-upgrade -version v1.0.0
GOOS=windows GOARCH=amd64 go run build.go -no-upgrade -version v1.0.0

# Docker image
docker build -t syncthing-fork:v1.0.0 .
docker tag syncthing-fork:v1.0.0 syncthing-fork:latest
```

### 4.3 Release checklist

- [ ] Tất cả integration tests pass
- [ ] Performance benchmarks acceptable
- [ ] Binaries build cho linux/amd64, linux/arm64, darwin/arm64, windows/amd64
- [ ] Docker image build và chạy OK
- [ ] README.md cập nhật
- [ ] CHANGELOG.md viết (nếu cần)
- [ ] GitHub release tạo với binaries attached
- [ ] Config migration guide viết (nếu upgrade từ syncthing gốc)

### 4.4 Deploy lên VPS production

```bash
# Trên VPS
# 1. Stop service cũ
sudo systemctl stop syncthing

# 2. Backup data
cp -r ~/.config/syncthing ~/.config/syncthing.backup

# 3. Replace binary
sudo cp syncthing-fork /usr/local/bin/syncthing

# 4. Update config
# Thêm hubURL, hubSecret, etc. vào config.xml/config.yaml

# 5. Start
sudo systemctl start syncthing

# 6. Verify
sudo systemctl status syncthing
curl -s https://localhost:443/rest/system/status | jq .

# 7. Register devices
curl -X POST https://vps-ip:443/api/register \
  -H "X-Registration-Secret: ..." \
  -H "Content-Type: application/json" \
  -d '{"device_name": "Laptop"}'
```

### 4.5 Post-release monitoring

Theo dõi trong 48h đầu:
- [ ] Tất cả devices kết nối thành công
- [ ] Sync hoạt động 2 chiều
- [ ] Không có data loss
- [ ] Token refresh hoạt động
- [ ] Scan time hợp lý
- [ ] Disk usage ổn định (không có conflict files tích tụ)
- [ ] Memory usage ổn định (digest cache không grow vô hạn)

---

## Phase 5 — Rollback toàn bộ

Nếu release có vấn đề nghiêm trọng:

### 5.1 Quick rollback (< 5 phút)

```bash
# Trên VPS
sudo systemctl stop syncthing
sudo cp /usr/local/bin/syncthing.backup /usr/local/bin/syncthing
sudo systemctl start syncthing

# Trên clients — revert config, restart
```

### 5.2 Full rollback (revert develop branch)

```bash
git checkout develop
git revert --no-commit HEAD~5..HEAD  # revert tất cả 5 merges
go test ./... -count=1
git commit -m "revert: rollback all fork features"
```

### 5.3 Partial rollback (giữ 1 số features)

```bash
# Ví dụ: giữ F1 (WSS) + F2 (auth), revert F3–F5
git checkout develop
git revert <commit-hash-F5>
git revert <commit-hash-F4>
git revert <commit-hash-F3>
go test ./... -count=1
```

---

## Appendix — CI/CD Pipeline (GitHub Actions)

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'
      - run: go build ./...
      - run: go vet ./...
      - run: go test ./... -count=1 -race -timeout 120s
      - run: go test ./... -bench=. -benchmem -count=1

  integration:
    runs-on: ubuntu-latest
    needs: test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'
      - run: docker-compose -f docker-compose.test.yml up -d
      - run: sleep 10
      - run: go test ./test/integration/... -v -count=1 -timeout 300s
      - run: docker-compose -f docker-compose.test.yml down

  release:
    runs-on: ubuntu-latest
    needs: [test, integration]
    if: startsWith(github.ref, 'refs/tags/v')
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'
      - run: |
          GOOS=linux GOARCH=amd64 go run build.go -no-upgrade
          GOOS=linux GOARCH=arm64 go run build.go -no-upgrade
      - uses: softprops/action-gh-release@v2
        with:
          files: bin/*
```
