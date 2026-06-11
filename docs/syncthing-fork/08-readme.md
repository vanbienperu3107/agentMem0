# Syncthing Fork - Proxy-Friendly Continuous File Synchronization

Fork từ [syncthing/syncthing](https://github.com/syncthing/syncthing), được thiết kế lại để chạy ổn qua proxy chỉ mở port 443, đồng bộ 2 chiều theo thời gian thực giữa một VPS hub và nhiều máy client.

## Tóm tắt thay đổi

| | Syncthing gốc | Bản fork |
|---|---|---|
| **Transport** | TLS + BEP, port 22000 + relay 22067 | WebSocket Secure, port 443 duy nhất |
| **Auth** | TLS client certs (DeviceID = cert hash) | Bearer token (JWT) |
| **Scanning** | Periodic full scan + fsnotify | Incremental: dirty paths + digest cache |
| **Transfer** | Block exchange (fixed-size blocks) | rsync delta (rolling hash, variable blocks) |
| **Conflict** | `.sync-conflict` files | Last-writer-wins + ancestor tracking |

## Kiến trúc

```
              ┌──────────────────────────────┐
              │   VPS Hub (:443)             │
              │   HTTPS + WebSocket          │
              │   JWT auth                   │
              └──────────┬──────────┬────────┘
                         │          │
                   WSS   │    WSS   │
                         │          │
                ┌────────┴┐  ┌──────┴──────┐
                │ Client A│  │ Client B    │
                └──────────┘  └─────────────┘
```

Mọi traffic đi qua port 443 (HTTPS), nên hoạt động qua proxy, firewall, và mạng doanh nghiệp dễ hơn bản gốc.

## Luồng hoạt động

1. Admin khởi tạo hub trên VPS, bật HTTPS/WSS tại `:443`, cấu hình `hubSecret`, `registrationSecret`, và đường dẫn chứng chỉ TLS.
2. Device client đăng ký với hub qua API, nhận JWT bearer token riêng cho từng máy.
3. Client mở kết nối `wss://.../ws`, gửi token trong `Authorization: Bearer <jwt>`, hub xác thực rồi cho phép vào phiên đồng bộ.
4. File watcher đánh dấu `dirty paths`; incremental scanner chỉ quét lại phần thay đổi, đồng thời dùng digest cache để bỏ qua file không đổi.
5. Khi cần truyền dữ liệu, receiver gửi signature, sender chạy rsync deltify, rồi patch phía nhận để chỉ gửi phần byte thực sự thay đổi.
6. Nếu hai phía cùng sửa một file, reconciler dùng ancestor tracking và last-writer-wins theo `mtime` mới nhất, không tạo file conflict.
7. Metadata và ancestor được ghi lại sau mỗi lần đồng bộ để lần sau so sánh nhanh hơn.

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
curl -X POST https://your-vps:443/api/register \
  -H "X-Registration-Secret: <registration-secret>" \
  -H "Content-Type: application/json" \
  -d '{"device_name": "My Laptop"}'
```

### 3. Setup Client

```bash
go run build.go -no-upgrade

cat > config.yaml <<EOF
hubURL: "wss://your-vps:443/ws"
deviceToken: "<token-from-register>"
syncFolders:
  - path: "/home/user/Sync"
    id: "default"
EOF

./bin/syncthing --config=config.yaml
```

### 4. Thêm máy khác

Lặp lại bước đăng ký và cấu hình client cho mỗi máy mới. Tất cả máy dùng cùng folder ID sẽ tự động sync qua hub.

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

```bash
export HTTP_PROXY=http://proxy.company.com:8080
export HTTPS_PROXY=http://proxy.company.com:8080

./bin/syncthing --config=config.yaml
```

Không cần `all_proxy`, không cần SOCKS5, không cần proxy đặc biệt. WebSocket upgrade đi qua HTTPS proxy bình thường.

## Chi tiết theo feature

### F1 - WSS Transport

Chuyển toàn bộ transport sang WebSocket Secure trên port 443. BEP protobuf messages được giữ nguyên, chỉ thay lớp framing bên ngoài.

### F2 - Bearer Token Auth

Mỗi device đăng ký với hub, nhận JWT token. Hub xác thực token trong header `Authorization` trước khi cho phép kết nối WSS.

### F3 - Incremental Scanner

Thay periodic full scan bằng baseline snapshot, dirty paths, selective rescan, và digest cache để tránh quét lại toàn bộ folder.

### F4 - rsync Delta Transfer

Thay block exchange cố định bằng rolling hash + strong hash để chỉ truyền phần byte thay đổi thật sự.

### F5 - LWW + Ancestor Tracking

Thay cơ chế tạo `.sync-conflict` bằng last-writer-wins kết hợp ancestor tracking để phân biệt rõ một phía sửa hay hai phía cùng sửa.

## Tài liệu triển khai

| File | Nội dung |
|---|---|
| `00-architecture-summary.md` | Tổng quan kiến trúc và thay đổi chính |
| `01-setup-guide.md` | Hướng dẫn clone, setup, workflow |
| `02-feature-wss-transport.md` | Chi tiết F1: WSS Transport |
| `03-feature-bearer-token-authentication.md` | Chi tiết F2: Bearer Token Auth |
| `04-feature-incremental-scanner.md` | Chi tiết F3: Incremental Scanner |
| `05-feature-rsync-delta-transfer.md` | Chi tiết F4: rsync Delta Transfer |
| `06-feature-lww-ancestor-tracking.md` | Chi tiết F5: LWW + Ancestor Tracking |
| `07-merge-release.md` | Quy trình merge, test, release |

## License

[MPL-2.0](LICENSE) (giữ nguyên từ Syncthing gốc)

## Credits

Fork từ [Syncthing](https://syncthing.net/) bởi [vanbienperu3107](https://github.com/vanbienperu3107).
Rsync algorithm và incremental scan lấy cảm hứng từ [Mutagen](https://github.com/mutagen-io/mutagen).

## Miễn trừ trách nhiệm

Tài liệu và mã nguồn trong bộ Syncthing fork này được cung cấp cho mục đích nghiên cứu, thử nghiệm và sử dụng cá nhân. Đây là một project độc lập, không có bất kỳ liên kết, chứng thực hay bảo trợ nào từ Syncthing, các tác giả gốc, hoặc bên thứ ba nào khác.

Phần mềm được cung cấp **nguyên trạng**, không có bảo hành dưới bất kỳ hình thức nào, dù rõ ràng hay ngầm định. Người dùng tự chịu trách nhiệm khi triển khai, cấu hình, vận hành, và kiểm tra lại trước khi dùng trong môi trường thực tế.

Tác giả không chịu trách nhiệm đối với:

- Mất mát dữ liệu, đồng bộ sai, hoặc hỏng dữ liệu do cấu hình lỗi hay lỗi vận hành
- Gián đoạn dịch vụ, downtime, hoặc lỗi tương thích khi Syncthing hoặc môi trường hạ tầng thay đổi
- Chi phí phát sinh từ VPS, proxy, chứng chỉ TLS, hoặc các dịch vụ bên thứ ba khác
- Việc sử dụng project không phù hợp với quy định nội bộ, điều khoản dịch vụ, hoặc pháp luật áp dụng

Khi dùng project này cho dữ liệu nhạy cảm, dữ liệu cá nhân, hoặc môi trường production, người dùng cần tự thực hiện backup, kiểm thử, rà soát bảo mật, và đánh giá tuân thủ trước khi triển khai.
