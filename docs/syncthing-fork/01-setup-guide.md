# Hướng dẫn Setup & Workflow sửa Syncthing Fork

## Yêu cầu hệ thống

- Go 1.22+ (kiểm tra: `go version`)
- Git 2.30+
- Make (optional, dùng `go run build.go`)
- IDE: VS Code hoặc GoLand (khuyến nghị GoLand cho Go project lớn)
- Protobuf compiler: `protoc` + `protoc-gen-go` (cho sửa proto files)
- SQLite3 CLI (debug database)

## Bước 1 — Clone & setup repo

```bash
# Clone fork
git clone https://github.com/vanbienperu3107/syncthing.git
cd syncthing

# Sync với upstream (lấy commits mới nhất)
git remote add upstream https://github.com/syncthing/syncthing.git
git fetch upstream
git merge upstream/main

# Verify build gốc chạy được
go run build.go
./bin/syncthing --version
```

## Bước 2 — Tạo branch strategy

```bash
# Branch chính cho từng tính năng
git checkout -b feature/wss-transport    # F1
git checkout -b feature/bearer-auth      # F2
git checkout -b feature/incremental-scan # F3
git checkout -b feature/rsync-delta      # F4
git checkout -b feature/lww-ancestor     # F5

# Branch integration
git checkout -b develop                  # merge tất cả features vào đây
git checkout -b release/v1.0.0           # branch release cuối cùng
```

## Bước 3 — Hiểu cấu trúc thư mục

```
syncthing/
├── cmd/                    # Entry points (binaries)
│   ├── syncthing/          # Main binary ← sửa main.go
│   ├── strelaysrv/         # Relay server ← XÓA
│   ├── strelaypoolsrv/     # Relay pool ← XÓA
│   └── stdiscosrv/         # Discovery server ← XÓA
├── lib/                    # Core libraries
│   ├── api/                # REST API ← thêm register endpoint
│   ├── auth/               # ← THÊ MỚI: JWT token logic
│   ├── config/             # Configuration ← thêm hub/token fields
│   ├── connections/        # Transport layer ← SỬA NẶNG
│   ├── db/                 # Database ← thêm ancestor table
│   ├── fs/                 # Filesystem abstraction (giữ nguyên)
│   ├── model/              # Sync model ← sửa conflict + transfer
│   ├── protocol/           # BEP protocol ← sửa framing + device ID
│   ├── rsync/              # ← THÊM MỚI: rsync engine
│   ├── scanner/            # File scanner ← sửa incremental
│   ├── sync/               # ← THÊM MỚI: reconcile logic
│   ├── tlsutil/            # TLS utils ← XÓA
│   └── versioner/          # File versioning (giữ nguyên)
├── proto/                  # Protobuf definitions ← thêm messages
├── gui/                    # Web GUI (giữ nguyên phần lớn)
├── test/                   # Integration tests
└── internal/               # Internal packages (DB impl)
```

## Bước 4 — Workflow sửa từng tính năng

Mỗi tính năng follow quy trình:

### 4.1. Checkout feature branch

```bash
git checkout feature/<tên-tính-năng>
git rebase main  # đảm bảo up-to-date
```

### 4.2. Đọc code hiện tại trước khi sửa

**Quy tắc bắt buộc:** Đọc hết export, caller, và util của module trước khi viết code mới.

```bash
# Ví dụ: trước khi sửa lib/connections/
grep -r "func " lib/connections/*.go | head -30     # list public functions
grep -r "connections\." lib/model/ lib/protocol/     # ai gọi module này?
grep -r "import.*connections" lib/                   # ai import module này?
```

### 4.3. Viết code theo plan trong feature doc

Xem file `02-feature-*.md` tương ứng. Mỗi file có:
- Plan: mô tả thay đổi từng file
- Code: code mẫu và giải thích
- Review checklist
- Test cases
- Deploy steps

### 4.4. Chạy tests

```bash
# Unit tests cho module đang sửa
go test ./lib/connections/... -v -count=1
go test ./lib/auth/... -v -count=1

# Tất cả tests
go test ./... -count=1

# Race detector
go test ./... -race -count=1

# Build kiểm tra compile
go run build.go
```

### 4.5. Commit theo convention

```bash
git add -A
git commit -m "feat(connections): replace TCP with WSS transport

- Remove tcp_dial.go, tcp_listen.go
- Add ws_dial.go, ws_listen.go using gorilla/websocket
- Update rawConnection to use WS binary frames
- Remove relay server dependencies

Refs: F1-WSS-Transport"
```

### 4.6. Push & tạo PR (nếu team)

```bash
git push origin feature/<tên>
# Tạo PR vào branch develop
```

## Bước 5 — Thứ tự sửa các tính năng

```
Bước 5.1: F1 — WSS Transport
    Xem: 02-feature-wss-transport.md
    Branch: feature/wss-transport
    Merge vào: develop

Bước 5.2: F2 — Bearer Auth
    Xem: 03-feature-bearer-auth.md
    Branch: feature/bearer-auth
    Merge vào: develop (sau F1)

Bước 5.3: F3 — Incremental Scanner
    Xem: 04-feature-incremental-scanner.md
    Branch: feature/incremental-scan
    Merge vào: develop (song song với F2)

Bước 5.4: F4 — rsync Delta
    Xem: 05-feature-rsync-delta.md
    Branch: feature/rsync-delta
    Merge vào: develop (sau F1 + F3)

Bước 5.5: F5 — LWW + Ancestor
    Xem: 06-feature-lww-ancestor.md
    Branch: feature/lww-ancestor
    Merge vào: develop (cuối cùng)

Bước 5.6: Integration Test & Release
    Xem: 07-merge-release.md
    Branch: release/v1.0.0
```

## Bước 6 — Cài dependencies mới

```bash
# WebSocket library
go get github.com/gorilla/websocket@latest

# JWT library
go get github.com/golang-jwt/jwt/v5@latest

# Tidy go.mod
go mod tidy
```

## Bước 7 — Protobuf workflow

Khi sửa file `.proto`:

```bash
# Cài tools (1 lần)
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest

# Generate Go code từ proto
go run build.go proto

# Hoặc thủ công
protoc --go_out=. --go_opt=paths=source_relative proto/bep.proto
```

## Bước 8 — Debug & troubleshoot

```bash
# Chạy với verbose logging
./bin/syncthing -verbose

# Chạy 2 instances local để test sync
STHOME=~/.config/syncthing-A ./bin/syncthing -gui-address=127.0.0.1:8384
STHOME=~/.config/syncthing-B ./bin/syncthing -gui-address=127.0.0.1:8385

# Check database
sqlite3 ~/.config/syncthing/index-v0.14.0.db ".tables"

# Profile CPU
go test ./lib/scanner/... -cpuprofile=cpu.prof -bench=.
go tool pprof cpu.prof
```

## Bước 9 — Build binary cuối cùng

```bash
# Build cho platform hiện tại
go run build.go -no-upgrade

# Cross-compile
GOOS=linux GOARCH=amd64 go run build.go -no-upgrade
GOOS=windows GOARCH=amd64 go run build.go -no-upgrade
GOOS=darwin GOARCH=arm64 go run build.go -no-upgrade

# Docker
docker build -t syncthing-fork:latest .
```
