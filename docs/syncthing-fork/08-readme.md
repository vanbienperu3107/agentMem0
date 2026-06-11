# Syncthing Fork — Proxy-Friendly Continuous File Synchronization

Fork từ [syncthing/syncthing](https://github.com/syncthing/syncthing) — tối ưu cho môi trường proxy chỉ cho phép port 443, sync 2 chiều real-time giữa VPS hub và nhiều máy cá nhân.

## Khác biệt so với Syncthing gốc

| | Syncthing gốc | Bản fork |
|---|---|---|
| **Transport** | TLS + BEP, port 22000 + relay 22067 | WebSocket Secure, port 443 duy nhất |
| **Auth** | TLS client certs (DeviceID = cert hash) | Bearer token (JWT) |
| **Scanning** | Periodic full scan + fsnotify | Incremental: dirty paths + digest cache |
| **Transfer** | Block exchange (fixed-size blocks) | rsync delta (rolling hash, variable blocks) |
| **Conflict** | .sync-conflict files | Last-writer-wins + ancestor tracking |

## Kiến trúc

```
              ┌──────────────────────┐
              │   VPS Hub (:443)     │
              │   HTTPS + WebSocket  │
              │   JWT auth           │
              └──┬───────┬───────┬───┘
                 │       │       │
            WSS  │  WSS  │  WSS  │
                 │       │       │
           ┌─────┴┐  ┌──┴───┐  ┌┴─────┐
           │ PC A │  │ PC B │  │ PC C │
           └──────┘  └──────┘  └──────┘
```

Mọi traffic đi qua port 443 (HTTPS) — hoạt động qua mọi proxy, firewall, corporate network.

## Quick Start

### 1. Setup Hub (VPS)

```bash
# Build
git clone https://github.com/vanbienperu3107/syncthing.git
cd syncthing
go run build.go -no-upgrade

# Config
cat > config.yaml <<EOF
hubMode: true
listenAddress: ":443"
hubSecret: "$(openssl rand -hex 32)"
registrationSecret: "$(openssl rand -hex 16)"
tlsCert: "/path/to/cert.pem"
tlsKey: "/path/to/key.pem"
EOF

# Start
./bin/syncthing --hub-mode --config=config.yaml
```

### 2. Register Device

```bash
# Từ máy client
curl -X POST https://your-vps:443/api/register \
  -H "X-Registration-Secret: <registration-secret>" \
  -H "Content-Type: application/json" \
  -d '{"device_name": "My Laptop"}'

# Lưu token từ response
```

### 3. Setup Client

```bash
# Build (hoặc download binary)
go run build.go -no-upgrade

# Config
cat > config.yaml <<EOF
hubURL: "wss://your-vps:443/ws"
deviceToken: "<token-from-register>"
syncFolders:
  - path: "/home/user/Sync"
    id: "default"
EOF

# Start
./bin/syncthing --config=config.yaml
```

### 4. Thêm máy khác

Lặp lại bước 2 + 3 cho mỗi máy. Tất cả máy share cùng folder ID sẽ tự động sync qua hub.

## Docker

```bash
# Hub
docker run -d --name syncthing-hub \
  -p 443:443 \
  -v /data:/data \
  -e HUB_SECRET="your-secret" \
  syncthing-fork:latest --hub-mode

# Client
docker run -d --name syncthing-client \
  -v /sync:/sync \
  -e HUB_URL="wss://your-vps:443/ws" \
  -e DEVICE_TOKEN="your-token" \
  syncthing-fork:latest
```

## Proxy Configuration

Bản fork hoạt động qua mọi HTTP/HTTPS proxy mà không cần cấu hình đặc biệt:

```bash
# Standard proxy env vars (gorilla/websocket tự đọc)
export HTTP_PROXY=http://proxy.company.com:8080
export HTTPS_PROXY=http://proxy.company.com:8080

./bin/syncthing --config=config.yaml
```

Không cần `all_proxy`, không cần SOCKS5, không cần proxy đặc biệt. WebSocket upgrade đi qua HTTPS proxy bình thường.

## Features chi tiết

### Incremental Scanner

Thay vì scan toàn bộ folder mỗi interval, bản fork chỉ scan files thay đổi:
- fsnotify detect file changes → đánh dấu dirty paths
- Chỉ rescan dirty paths, skip phần còn lại
- Digest cache: files không đổi mtime/size → skip re-hash
- Kết quả: scan 5 files thay đổi trong folder 100K files chỉ mất < 1 giây

### rsync Delta Transfer

Thay vì transfer toàn bộ blocks khi file thay đổi:
- Rolling hash detect matching blocks tại bất kỳ offset
- Chỉ gửi delta (phần thay đổi thực sự)
- Insert 1 byte ở đầu file 100MB → chỉ gửi ~1KB delta

### LWW + Ancestor

Khi 2 máy sửa cùng file đồng thời:
- Bên sửa cuối cùng (mtime mới nhất) wins
- Không tạo .sync-conflict files
- Ancestor tracking đảm bảo detect đúng "1 bên sửa" vs "2 bên sửa"

## Build từ source

```bash
# Yêu cầu: Go 1.22+
git clone https://github.com/vanbienperu3107/syncthing.git
cd syncthing
go run build.go -no-upgrade

# Cross-compile
GOOS=linux GOARCH=amd64 go run build.go -no-upgrade
GOOS=linux GOARCH=arm64 go run build.go -no-upgrade

# Tests
go test ./... -count=1 -race
```

## Tài liệu triển khai

| File | Nội dung |
|------|----------|
| `00-architecture-summary.md` | Tổng quan kiến trúc và tính năng |
| `01-setup-guide.md` | Hướng dẫn clone, setup, workflow |
| `02-feature-wss-transport.md` | Chi tiết F1: WSS Transport |
| `03-feature-bearer-auth.md` | Chi tiết F2: Bearer Token Auth |
| `04-feature-incremental-scanner.md` | Chi tiết F3: Incremental Scanner |
| `05-feature-rsync-delta.md` | Chi tiết F4: rsync Delta Transfer |
| `06-feature-lww-ancestor.md` | Chi tiết F5: LWW + Ancestor |
| `07-merge-release.md` | Quy trình merge, test, release |

## License

[MPL-2.0](LICENSE) (giữ nguyên từ Syncthing gốc)

## Credits

Fork từ [Syncthing](https://syncthing.net/) bởi [vanbienperu3107](https://github.com/vanbienperu3107).
rsync algorithm và incremental scan lấy cảm hứng từ [Mutagen](https://github.com/mutagen-io/mutagen).
