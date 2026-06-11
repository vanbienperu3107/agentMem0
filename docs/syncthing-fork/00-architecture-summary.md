# Syncthing Fork — Architecture Summary

## 1. Tổng quan dự án

Fork từ [syncthing/syncthing](https://github.com/syncthing/syncthing) (Go, MPL-2.0) với mục tiêu tạo bản sync 2 chiều, real-time, hoạt động qua proxy chỉ cho phép port 443, sync giữa VPS hub và nhiều máy cá nhân.

**Repo:** `github.com/vanbienperu3107/syncthing`

## 2. Các thay đổi so với upstream

| # | Tính năng | Syncthing gốc | Bản fork |
|---|-----------|---------------|----------|
| F1 | Transport | TLS 1.3 + BEP custom, port 22000 + relay 22067 | WebSocket Secure (WSS) over HTTPS, port 443 duy nhất |
| F2 | Auth | TLS client certs, DeviceID = SHA256(cert) | Bearer token (JWT signed HMAC-SHA256) |
| F3 | Scanner | Periodic full scan + fsnotify từng subdirs | Incremental scan: dirty paths + baseline snapshot + digest cache |
| F4 | Transfer | Block exchange (fixed-size blocks, SHA-256) | rsync delta (rolling hash + strong hash, variable block size) |
| F5 | Conflict | Last-writer-wins, tạo `.sync-conflict-*` file | LWW + ancestor tracking — mtime mới nhất wins, không tạo conflict file |

## 3. Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────┐
│                    VPS Hub (port 443)                    │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐ │
│  │ HTTPS/WSS   │  │ Auth         │  │ Sync Engine    │ │
│  │ Server      │──│ Middleware   │──│                │ │
│  │ (gorilla/ws)│  │ (JWT verify) │  │ ┌────────────┐ │ │
│  └─────────────┘  └──────────────┘  │ │ Reconciler │ │ │
│                                      │ │ (LWW +     │ │ │
│                                      │ │  ancestor) │ │ │
│                                      │ └────────────┘ │ │
│                                      │ ┌────────────┐ │ │
│                                      │ │ rsync      │ │ │
│                                      │ │ engine     │ │ │
│                                      │ └────────────┘ │ │
│                                      │ ┌────────────┐ │ │
│                                      │ │ Incremental│ │ │
│                                      │ │ scanner    │ │ │
│                                      │ └────────────┘ │ │
│                                      └────────────────┘ │
│  ┌──────────────────────────────────────────────────┐   │
│  │              SQLite DB                            │   │
│  │  file_infos | ancestor_entries | digest_cache     │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
        ▲ WSS :443          ▲ WSS :443          ▲ WSS :443
        │ Bearer token      │ Bearer token      │ Bearer token
   ┌────┴────┐         ┌────┴────┐         ┌────┴────┐
   │ Máy A   │         │ Máy B   │         │ Máy C   │
   │ (client)│         │ (client)│         │ (client)│
   └─────────┘         └─────────┘         └─────────┘
```

## 4. Mô tả chi tiết từng tính năng

### F1 — WSS Transport (thay TLS + BEP)

**Vấn đề giải quyết:** BEP protocol chạy trên custom TLS connection, dùng port 22000 (sync) và 22067 (relay). Hầu hết proxy chỉ cho phép port 443 HTTPS, block các port khác.

**Giải pháp:** Chuyển toàn bộ transport sang WebSocket Secure (WSS) chạy trên HTTPS port 443. WebSocket là upgrade từ HTTP, nên mọi HTTP proxy đều forward được. Giữ nguyên BEP protobuf messages bên trong — chỉ thay lớp framing bên ngoài.

**Thay đổi chính:**
- Bỏ raw TCP dialer/listener → thay bằng WSS dialer/listener
- Bỏ custom framing (2B header + 4B message length) → dùng WebSocket binary frames
- Bỏ relay server infrastructure → VPS hub đóng vai trò relay tự nhiên
- VPS chạy HTTPS server với WebSocket upgrade endpoint `/ws`

**Files ảnh hưởng:**
- Xóa: `lib/connections/tcp_dial.go`, `tcp_listen.go`
- Thêm: `lib/connections/ws_dial.go`, `ws_listen.go`
- Sửa: `lib/protocol/protocol.go` (rawConnection read/write)
- Sửa: `lib/connections/service.go` (connection setup flow)
- Xóa: `cmd/strelaysrv/`, `cmd/strelaypoolsrv/`

---

### F2 — Bearer Token Auth (thay TLS client certs)

**Vấn đề giải quyết:** TLS client cert auth yêu cầu mỗi device tự sinh cert, pair thủ công qua device ID (32 bytes base32). Phức tạp cho user, khó quản lý nhiều device, và cert-based TLS passthrough khó đi qua proxy.

**Giải pháp:** Mỗi device register với hub qua API, nhận JWT token. Token chứa `device_id`, `exp`, signed bằng HMAC-SHA256 với shared secret trên hub. Mọi kết nối WSS gửi token trong header.

**Flow:**
1. Admin tạo shared secret trên hub (`config.yaml`)
2. Device gọi `POST /api/register` với device name → nhận JWT
3. Device kết nối `wss://hub:443/ws` với header `Authorization: Bearer <jwt>`
4. Hub verify JWT signature + expiry → extract device_id → cho phép kết nối
5. Token refresh: device gọi `POST /api/token/refresh` trước khi hết hạn

**Files ảnh hưởng:**
- Xóa: `lib/tlsutil/` (toàn bộ)
- Sửa: `lib/protocol/deviceid.go` (DeviceID derive từ token, không phải cert)
- Thêm: `lib/auth/token.go` (JWT generate/verify)
- Thêm: `lib/auth/middleware.go` (HTTP middleware)
- Sửa: `lib/connections/service.go` (validateIdentity, handleConns)
- Thêm: `lib/api/register.go` (device registration endpoint)

---

### F3 — Incremental Scanner (thay periodic full scan)

**Vấn đề giải quyết:** Syncthing periodic scan quét toàn bộ folder mỗi interval — compare mtime/size từng file vs DB. Với folder 100K+ files, mỗi lần scan tốn CPU spike đáng kể, dù chỉ 1-2 file thay đổi.

**Giải pháp:** Port incremental scan từ Mutagen:
- **Baseline snapshot:** giữ snapshot của lần scan cuối trong memory
- **Dirty paths:** fsnotify chỉ đánh dấu paths thay đổi vào set `dirtyPaths`
- **Selective rescan:** khi scan, chỉ traverse dirty paths. Subtrees không dirty → reuse baseline, zero disk I/O
- **Digest cache:** map `(path → mtime, size, digest)`. File matching cache → skip re-hash

**Kết quả:** Scan cost tỷ lệ với số file thay đổi, không phải tổng file count.

**Files ảnh hưởng:**
- Thêm: `lib/scanner/snapshot.go` (baseline snapshot + Entry tree)
- Thêm: `lib/scanner/cache.go` (digest cache)
- Sửa: `lib/scanner/walk.go` (walkRegular check dirty paths)
- Sửa: `lib/model/folder.go` (scanSubdirs truyền dirty paths)

---

### F4 — rsync Delta Transfer (thay block exchange)

**Vấn đề giải quyết:** Syncthing dùng fixed-size blocks (128KB–16MB). Nếu insert 1 byte ở đầu file, tất cả block boundaries shift → toàn bộ file phải re-transfer dù chỉ 1 byte thay đổi.

**Giải pháp:** Port rsync algorithm từ Mutagen:
- **Rolling hash** (weak hash, Adler32-variant) scan qua file tìm matching blocks tại **bất kỳ offset nào**
- **Strong hash** (SHA-1) verify matched blocks
- **Variable block size:** `sqrt(24 * fileLength)`, clamp 1KB–64KB — nhỏ hơn → detect delta chính xác hơn
- **Delta flow:** receiver gửi Signature (block hashes) → transmitter chạy Deltify (matching + literal data) → receiver Patch (reconstruct file)

**Kết quả:** Insert 1 byte ở đầu file 100MB → chỉ gửi ~1KB delta thay vì re-transfer 100MB.

**Files ảnh hưởng:**
- Thêm: `lib/rsync/engine.go` (Deltify, Patch, Signature)
- Thêm: `lib/rsync/rolling.go` (rolling hash)
- Thêm: `lib/rsync/operation.go` (Operation types)
- Sửa: `lib/model/folder_sendrecv.go` (thay copier/puller pipeline)
- Sửa: `lib/protocol/protocol.go` (thêm message types cho rsync)

---

### F5 — LWW + Ancestor Tracking (thay conflict copy)

**Vấn đề giải quyết:** Syncthing tạo `.sync-conflict-*` files khi 2 device sửa cùng file. File conflict tích tụ, user phải tự dọn. Muốn đơn giản hơn: bên sửa cuối cùng luôn thắng, không tạo file rác.

**Giải pháp:** Giữ LWW nhưng thêm ancestor tracking để detect chính xác:
- **Ancestor table** trong DB: lưu state cuối cùng mà tất cả devices đồng ý
- **Reconcile logic:**
  - So sánh alpha/beta với ancestor
  - 1 bên thay đổi → propagate bên thay đổi (giống hiện tại)
  - 2 bên thay đổi → **mtime mới nhất wins**, overwrite bên kia
  - 1 xóa + 1 sửa → bên sửa wins (giữ data)
- **Không tạo conflict file** — clean, đơn giản

**Files ảnh hưởng:**
- Thêm: `lib/sync/reconcile.go` (reconcile logic với ancestor)
- Sửa: `lib/db/` (thêm ancestor_entries table)
- Sửa: `lib/model/folder_sendrecv.go` (thay handleConflict)
- Sửa: `proto/` (thêm protobuf cho ancestor entry)

## 5. Thứ tự triển khai

```
F1 (WSS Transport)
    │
    ▼
F2 (Bearer Auth)     F3 (Incremental Scanner)
    │                     │
    ▼                     ▼
F4 (rsync Delta) ◄───────┘
    │
    ▼
F5 (LWW + Ancestor)
    │
    ▼
Integration Test + Release
```

F1 → F2: auth phụ thuộc transport mới
F3: independent, làm song song với F2
F4: cần transport + scanner hoàn thành
F5: cuối cùng, cần tất cả layer bên dưới ổn định

## 6. Những gì giữ nguyên từ upstream

- **Web GUI** (`gui/`) — giữ nguyên, thêm bearer token auth cho API calls
- **Config system** (`lib/config/`) — mở rộng thêm fields mới (hub URL, token, etc.)
- **Database engine** (`lib/db/`) — giữ SQLite, thêm tables mới
- **Filesystem abstraction** (`lib/fs/`) — không đổi
- **Folder management** (`lib/model/folder.go`) — sửa scanner integration, giữ logic khác
- **Ignore patterns** (`.stignore`) — không đổi
- **Versioning** (`lib/versioner/`) — không đổi

## 7. Những gì xóa bỏ

- `lib/tlsutil/` — toàn bộ TLS cert generation/management
- `cmd/strelaysrv/` — relay server (hub thay thế)
- `cmd/strelaypoolsrv/` — relay pool server
- `cmd/stdiscosrv/` — discovery server (không cần, devices kết nối trực tiếp hub)
- `lib/connections/tcp_dial.go`, `tcp_listen.go` — raw TCP transport
- `lib/connections/quic_*.go` — QUIC transport (nếu có)
- Global discovery, local discovery — thay bằng hub registry
