# Plan triển khai: Memory Server tự host với mem0 + Claude Max (Mac & Windows)

Tài liệu hướng dẫn triển khai hệ thống bộ nhớ AI tập trung, dùng tài khoản Claude Max cho LLM (miễn phí qua subscription), lưu dữ liệu trên VPS riêng để bảo mật và truy cập đa máy.

Hệ thống gồm **hai lớp lưu trữ chạy song song**: (1) **mem0** trích xuất facts ngắn gọn để Claude tự tra cứu khi chat — dùng cho "AI nhớ về tôi"; (2) **transcript archive** lưu nguyên văn cuộc chat để lướt lại theo ngày/dự án — dùng cho "tôi muốn xem lại tôi đã nói gì". Hai lớp dùng chung Postgres trên VPS.

**Thông số thực tế của bản triển khai này:**

| Hạng mục | Giá trị |
|---|---|
| Tên miền | `claude.hangocthanh.io.vn` |
| IP VPS | `45.119.87.220` |
| User quản trị trên VPS | `thanh` |
| Proxy công ty (máy Windows hiện tại) | `10.121.127.204:3128` |
| Công cụ tunnel SSH (máy Windows hiện tại) | `E:\Tool\PortableGit\mingw64\bin\connect.exe` |

> **Quy ước đọc tài liệu:**
>
> - **Lệnh chạy trên VPS** (Ubuntu) — giống nhau bất kể bạn dùng Mac hay Windows. Khối code ghi `bash`.
> - **Lệnh chạy trên máy client** — khác nhau giữa hai hệ điều hành. Mỗi bước loại này có **hai khối song song**: **Trên Mac — Terminal** (`bash`) và **Trên Windows — PowerShell** (`powershell`).
> - **Mọi lệnh tạo file đều ở dạng "copy — dán — chạy ngay"** (dùng heredoc `cat > ... << 'EOF'`). Không phải mở trình soạn thảo rồi gõ tay. Nếu bắt buộc phải sửa file thủ công, tài liệu dùng `vi` (không dùng `nano`).
> - Windows ở đây dùng **PowerShell + OpenSSH gốc** (không cài WSL). Yêu cầu Windows 10 phiên bản 1809+ hoặc Windows 11.
> - **Nếu mạng của bạn chặn SSH** (mạng công ty thường chặn cổng 22): xem **mục 3.2** — hướng dẫn kết nối qua proxy, có đầy đủ lệnh PowerShell.

---

## 1. Tổng quan kiến trúc

```
┌─────────────────────────────────────────┐
│  Máy client của bạn (Mac HOẶC Windows)  │
│                                         │
│  ┌─────────────────────────────────┐    │
│  │ VS Code + Claude Code           │    │
│  │ (đăng nhập tài khoản Max)       │    │
│  └────────────┬────────────────────┘    │
│               │ giao tiếp MCP (stdio)   │
│  ┌────────────▼────────────────────┐    │
│  │ mem0-mcp-selfhosted             │    │
│  │ - Phơi 11 tools MCP             │    │
│  │ - Dùng OAT của Max cho LLM      │    │
│  └────────────┬────────────────────┘    │
└───────────────┼─────────────────────────┘
                │ HTTPS + Bearer token
                │ (qua proxy công ty nếu có)
                ▼
┌─────────────────────────────────────────┐
│  VPS Ubuntu 24.04 — 45.119.87.220       │
│  claude.hangocthanh.io.vn               │
│                                         │
│  ┌─────────────────────────────────┐    │
│  │ Caddy (TLS + Auth + Rate limit) │    │
│  └────────────┬────────────────────┘    │
│               │                         │
│  ┌────────────▼─────┐  ┌─────────────┐  │
│  │ Qdrant           │  │ Postgres    │  │
│  │ (vector storage) │  │ (metadata)  │  │
│  └──────────────────┘  └─────────────┘  │
└─────────────────────────────────────────┘
```

**Luồng dữ liệu khi bạn chat:**

1. Bạn gõ câu hỏi trong VS Code Claude Code.
2. Claude Code (qua MCP) gọi `search_memory` của lớp MCP local.
3. Lớp MCP gọi mem0 library, biến query thành vector, gửi sang Qdrant trên VPS qua HTTPS tới `claude.hangocthanh.io.vn`.
4. Qdrant trả về top-K kết quả liên quan, đi ngược về Claude Code.
5. Claude Code nhét kết quả vào context, gọi Claude (qua OAT Max) sinh câu trả lời.
6. Cuối phiên, lớp MCP tự động `add_memory` cho cuộc chat mới.

**Điểm then chốt:** Mọi cuộc gọi Claude đều qua OAT Max, không sinh API charge.

---

## 2. Yêu cầu

### 2.1 Yêu cầu tối thiểu (không thể thiếu)

**Máy client (Mac hoặc Windows):**

- **Mac:** macOS 12+ — Terminal có sẵn `ssh`, `ssh-keygen`, `ssh-copy-id`.
- **Windows:** Windows 10 phiên bản 1809+ hoặc Windows 11 — phải có **OpenSSH Client** và dùng **PowerShell**. Windows **không** có sẵn `ssh-copy-id` và `openssl` — tài liệu này đã cung cấp lệnh thay thế.
- 8GB RAM, 10GB ổ trống
- Đã cài VS Code mới nhất + Claude Code extension, đăng nhập tài khoản Max
- Kết nối internet ổn định

**VPS (server lưu trữ):**

- Ubuntu 22.04 LTS hoặc 24.04 LTS (không dùng các bản khác)
- 2 vCPU, 4GB RAM, 40GB SSD
- IPv4 public, port 22/80/443 mở
- Quyền root hoặc sudo
- Hiểu rõ **đường vào VPS duy nhất** của bạn là SSH qua proxy tới cổng 443 — không có Web Console dự phòng. Bắt buộc đọc **mục 3.0** về quy tắc an toàn trước khi triển khai

**Domain & DNS:**

- Tên miền `claude.hangocthanh.io.vn`, DNS record A trỏ về `45.119.87.220`
- Khuyến nghị: dùng Cloudflare làm DNS provider (miễn phí, có DDoS protection)

**Tài khoản & API:**

- Tài khoản Anthropic có Claude Max plan ($100 hoặc $200/tháng)
- Tài khoản OpenAI Platform có credit ($5 nạp đủ dùng cả năm cho embeddings cá nhân)

### 2.2 Yêu cầu khuyến nghị (nên có)

- VPS 4 vCPU, 8GB RAM nếu định lưu >10 triệu memory
- Datacenter Singapore/Tokyo/Hồng Kông để latency thấp về VN
- Tài khoản Backblaze B2 hoặc S3 cho backup ($1–3/tháng)
- Better Stack hoặc UptimeRobot để monitor (có free tier)
- Telegram bot để nhận alert (miễn phí)

### 2.3 Chi phí ước tính / tháng

| Hạng mục | Min | Max | Ghi chú |
|---|---|---|---|
| Claude Max (đã có) | $100 | $200 | Không phát sinh thêm |
| VPS | $5 | $15 | Hetzner CX22 €4.5, Vultr $6, DO $6 |
| Domain | $1 | $1 | Chia theo năm |
| OpenAI embeddings | $0.5 | $3 | Cá nhân dùng đều |
| Backup storage (B2/S3) | $0 | $3 | 50GB free B2 |
| Monitoring | $0 | $0 | Free tier đủ |
| **Tổng phát sinh** | **~$7** | **~$22** | Trên đầu Max bạn đã trả |

### 2.4 Lựa chọn lưu trữ Postgres — Local VPS vs Neon Cloud

Plan này có **2 cách deploy Postgres** cho phần transcript archive (Giai đoạn 7). Chọn 1 trong 2 trước khi vào Giai đoạn 2:

| Tiêu chí | Postgres local trên VPS | Neon Cloud (free tier) |
|---|---|---|
| **RAM ngốn trên VPS** | ~1GB (image `pgvector/pgvector:pg16`) | **0** (chạy trên Neon Singapore) |
| **Chi phí** | $0 (đã trong VPS) | $0 (free tier 0.5GB storage) |
| **Region** | Theo VPS (thường Singapore) | AWS ap-southeast-1 Singapore — latency tương đương |
| **Setup** | Có sẵn trong `docker-compose.yml` | Cần signup Neon + lấy connection string |
| **Backup** | Tự script (xem Bước 6.1) | Neon có point-in-time recovery sẵn |
| **Khi nào nên** | VPS ≥8GB RAM, muốn fully self-host | VPS ≤4GB RAM chật RAM, hoặc muốn off-load Postgres |

**Khuyến nghị:**
- VPS **4GB RAM hoặc nhỏ hơn** → **Neon** (giải phóng 1GB RAM cho Qdrant + Ollama)
- VPS **≥8GB RAM** + muốn không phụ thuộc cloud → **Postgres local**

**Setup Neon (nếu chọn):**

1. Vào `https://neon.com` → Sign up với GitHub (free, không cần thẻ tín dụng)
2. **Create project:**
   - Name: `mem0-archive`
   - Postgres version: 17
   - Region: **AWS ap-southeast-1 (Singapore)** (BẮT BUỘC chọn đúng, không đổi được sau)
   - Neon Auth: **OFF** (không cần, đã có `ARCHIVE_AUTH_TOKEN`)
3. Copy connection string dạng:
   ```
   postgresql://neondb_owner:<password>@ep-<id>.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```
4. Trong Neon SQL Editor, chạy:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   CREATE EXTENSION IF NOT EXISTS pg_trgm;
   ```
5. Tạo table `chat_sessions` (xem Bước 7.1 — nội dung SQL giống hệt, chỉ chạy trên Neon SQL Editor thay vì `docker exec`)
6. Trong Bước 2.1, thêm dòng vào `.env`:
   ```bash
   echo 'NEON_DB_URL="<paste-connection-string>"' >> .env
   ```
7. Trong `docker-compose.yml` (Bước 2.2 / 7.3): **XÓA block `postgres:`**, sửa `archive-api` env `DB_URL: ${NEON_DB_URL}`
8. Khi `docker compose down`, BẮT BUỘC kèm `--remove-orphans` (xem **B27**)

> **Lưu ý plan free tier Neon:** 0.5GB storage + 100 compute hours/tháng + scale-to-zero sau 5 phút. Với use case archive cá nhân (~50 sessions/ngày), Thanh dùng ~2 compute hours/tháng (còn dư 98) và sau 3-4 năm mới chạm 0.5GB.

---

## 3. Pre-flight checklist

Hoàn thành 8 mục dưới đây trước khi bắt đầu Giai đoạn 1. Mỗi mục có **lệnh kiểm tra** để chắc chắn đã đúng và phần **nếu lỗi** để xử lý từng trường hợp.

- [ ] P1 — OpenAI API key dùng được
- [ ] P2 — VPS chạy, có IP và password root
- [ ] P3 — DNS đã trỏ đúng IP
- [ ] P4 — VS Code + Claude Code + tài khoản Max hoạt động
- [ ] P5 — SSH key pair đã tạo đúng (xem mục 3.1)
- [ ] P6 — Đã biết mạng có chặn SSH không (để chọn mục 3.2 hay 3.3)
- [ ] P7 — Đã đọc mục 3.0 và nắm quy tắc "chỉ có một đường vào VPS"
- [ ] P8 — (Chỉ Windows) Đã đổi PowerShell Execution Policy sang `RemoteSigned`

**P1 — OpenAI API key dùng được**

*Cách tạo key:* xem hướng dẫn chi tiết ở **Bước 3.3, mục b)**.

*Cách kiểm tra* (Windows sau proxy: đặt `HTTP_PROXY` trước — xem mục 3.2 Bước 3.2.2):

```powershell
# Windows
curl.exe https://api.openai.com/v1/models -H "Authorization: Bearer sk-<key-của-bạn>"
```

```bash
# Mac
curl https://api.openai.com/v1/models -H "Authorization: Bearer sk-<key-của-bạn>"
```

Trả về JSON danh sách model → OK.

*Nếu lỗi:*

- `401 ... invalid_api_key` → key sai hoặc copy thiếu ký tự. Lấy lại tại `platform.openai.com/api-keys`.
- `429 ... insufficient_quota` → chưa nạp tiền hoặc hết credit. Vào `platform.openai.com/account/billing` nạp tối thiểu $5.
- Treo rồi timeout (Windows) → chưa set biến proxy. Làm mục 3.2 Bước 3.2.2.

**P2 — VPS chạy, có IP và password root**

*Cách kiểm tra:*

```powershell
# Windows
Test-NetConnection 45.119.87.220 -Port 443
Test-NetConnection 45.119.87.220 -Port 22
```

```bash
# Mac
nc -vz 45.119.87.220 443
nc -vz 45.119.87.220 22
```

Ít nhất một cổng `True` / `succeeded` → VPS đang sống. (Ping `45.119.87.220` có thể fail do ICMP bị chặn — **đừng dựa vào ping** để kết luận.)

*Nếu lỗi:*

- Cả hai cổng đều fail → VPS chưa khởi động: vào trang quản lý nhà cung cấp, kiểm tra trạng thái, bấm Start/Reboot.
- Nghi sai IP → đối chiếu lại IP trong trang quản lý VPS.
- Quên password root → dùng chức năng "Reset root password" của nhà cung cấp.

**P3 — DNS đã trỏ đúng IP**

*Cách kiểm tra:*

```powershell
# Windows
Resolve-DnsName claude.hangocthanh.io.vn
```

```bash
# Mac
dig +short claude.hangocthanh.io.vn
```

Kết quả phải chứa `45.119.87.220`.

*Nếu lỗi:*

- Không ra gì → record A chưa tạo hoặc chưa propagate. Tạo record A: tên `claude` → giá trị `45.119.87.220`; đợi 5–30 phút.
- Ra một IP khác `45.119.87.220` → có thể bạn đang bật Cloudflare proxy (đám mây màu cam) nên ra IP của Cloudflare. Để Caddy lấy chứng chỉ dễ nhất: tạm để DNS ở chế độ **DNS only** (đám mây xám); bật proxy lại sau, kèm SSL mode **Full (strict)**.

**P4 — VS Code + Claude Code + tài khoản Max hoạt động**

*Cách kiểm tra:* Mở VS Code → mở panel Claude Code → gõ một câu bất kỳ ("xin chào"), thấy Claude trả lời → OK. Kiểm tra đã đăng nhập tài khoản Max.

*Nếu lỗi:*

- Không thấy Claude Code → cài extension Claude Code từ Marketplace của VS Code.
- Báo chưa đăng nhập → đăng nhập tài khoản Anthropic (gói Max).
- Sau proxy công ty, VS Code không tải/đăng nhập được → vào VS Code Settings, tìm `http.proxy`, đặt `http://10.121.127.204:3128`.

**P5 — SSH key pair đã tạo đúng**

*Cách kiểm tra:*

```powershell
# Windows
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

```bash
# Mac
cat ~/.ssh/id_ed25519.pub
```

Ra đúng một dòng `ssh-ed25519 AAAA... <nhãn>` → OK.

*Nếu lỗi:*

- `Cannot find path` / không có file → chưa tạo key, hoặc đã tạo nhưng lưu sai tên. Làm mục 3.1, **nhấn Enter** ở dòng hỏi tên file để lưu đúng `id_ed25519`.
- Trong `~/.ssh` có cặp file tên khác (vd `thanh-pccongty` + `.pub`) → key bị đặt sai tên, `ssh` sẽ không tự tìm thấy (`ssh -v` báo `type -1`). Tạo lại đúng tên mặc định — xem mục 5, lỗi A5.

**P6 — Đã biết mạng có chặn SSH không**

*Cách kiểm tra:*

```powershell
# Windows
Test-NetConnection 45.119.87.220 -Port 22
```

```bash
# Mac
nc -vz 45.119.87.220 22
```

- Cổng 22 = `True`/`succeeded` → mạng cho SSH thẳng → đi theo **mục 3.3**.
- Cổng 22 = `False` → mạng chặn cổng 22 → đi theo **mục 3.2** (kết nối qua proxy).

*Nếu lỗi:* nếu cả cổng 22 lẫn 443 đều `False` thì đây không phải chuyện "chặn SSH" mà là VPS không tới được — quay lại xử lý P2.

**P7 — Hiểu rõ rủi ro "chỉ có một đường vào VPS"**

Bạn không dùng được Web Console; đường vào VPS duy nhất là SSH qua proxy tới cổng 443. Đây là rủi ro lớn nhất của cả quá trình.

*Cách kiểm tra:* Đọc kỹ **mục 3.0** và nắm được 4 quy tắc an toàn — đặc biệt là mở sẵn phiên SSH thứ hai và đặt lệnh tự khôi phục bằng `systemd-run`.

*Nếu chưa nắm:* đừng bắt đầu Giai đoạn 1. Một thao tác SSH/`ufw` sai khi không có lưới an toàn sẽ khóa bạn ra khỏi VPS vĩnh viễn.

**P8 — (Chỉ Windows) PowerShell Execution Policy đã đổi sang `RemoteSigned`**

*Vì sao cần:* Windows mặc định set Execution Policy = `Restricted` → chặn HẾT file `.ps1`. Nếu không đổi, mọi script `.ps1` ở Giai đoạn 3, 4, 7 (như `archive-env.ps1`, `archive-upload-run.ps1`) sẽ báo `running scripts is disabled on this system` → env vars không load được → Python script crash với `KeyError`.

*Cách thực hiện:*

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
# Gõ Y khi hỏi xác nhận
```

*Cách kiểm tra:*

```powershell
Get-ExecutionPolicy -Scope CurrentUser
```

Phải trả `RemoteSigned` → OK. Nếu vẫn `Restricted` → chạy lại lệnh `Set-ExecutionPolicy`. Đây là policy ở scope `CurrentUser` (không cần quyền admin), chỉ áp dụng cho user Thanh, không ảnh hưởng máy khác. Chi tiết xem **B22**.

### 3.0 — Quy tắc an toàn: bạn chỉ có MỘT đường vào VPS

**Đọc kỹ mục này trước khi đụng tới bất kỳ cấu hình nào.**

Trong môi trường của bạn, đường vào VPS **duy nhất** là: SSH từ máy công ty → qua proxy → tới cổng 443. **Không** có Web Console, **không** có 4G/mạng nhà dự phòng. Hệ quả: nếu cổng 443 bị cấu hình sai và phiên SSH đang mở bị đóng, bạn **mất quyền vào VPS** — chỉ còn cách nhờ nhà cung cấp cài lại (mất sạch dữ liệu).

Vì vậy, trước **mọi** thao tác đụng tới SSH, `sslh`, `ufw`/firewall, hoặc cổng 443 — tuân thủ 4 quy tắc:

**Quy tắc 1 — Luôn mở sẵn một phiên SSH thứ hai.** Trước khi đổi cấu hình, mở thêm một cửa sổ PowerShell và chạy `ssh vps`. Một kết nối SSH **đã thiết lập vẫn sống** kể cả khi bạn restart sshd hay đổi cấu hình — đó là phao cứu sinh để hoàn tác. Tuyệt đối không đóng nó cho tới khi đã xác nhận một kết nối **mới** vào được.

**Quy tắc 2 — Sao lưu trước khi sửa.**

```bash
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.safe
```

**Quy tắc 3 — Đặt lệnh tự khôi phục cho thay đổi rủi ro cao.** Trước khi áp dụng thay đổi sshd/ufw, hẹn một lệnh tự khôi phục sau 5 phút. Nếu kết nối mới OK thì hủy; nếu bị khóa thì đợi 5 phút nó tự cứu:

```bash
# Hẹn rollback sau 5 phút
sudo systemd-run --on-active=5min --unit=ssh-rollback \
  /bin/bash -c 'cp /etc/ssh/sshd_config.safe /etc/ssh/sshd_config && systemctl restart ssh'

# ... áp dụng thay đổi, rồi mở cửa sổ PowerShell MỚI test: ssh vps "echo OK" ...

# Nếu test OK  → hủy rollback:
sudo systemctl stop ssh-rollback.timer
# Nếu bị khóa → đợi đủ 5 phút, rollback tự chạy, rồi ssh vps lại.
```

**Quy tắc 4 — `ufw`: luôn `allow` trước khi `enable`.** Không bao giờ `ufw enable` khi chưa `ufw allow` cổng SSH (22 và 443). Bật firewall mà quên mở cổng SSH = tự khóa ngay lập tức.

### 3.1 Tạo SSH key pair

**Vì sao cần SSH key:** Giai đoạn 1 sẽ **tắt đăng nhập password** (`PasswordAuthentication no`). Nếu chưa có SSH key trước đó, bạn sẽ bị **khóa ngoài VPS**. SSH key cũng chống brute-force tốt hơn password rất nhiều.

**Trên Mac — Terminal:**

```bash
ssh-keygen -t ed25519 -C "thanh-mac"
# Nhấn Enter ở dòng hỏi đường dẫn — để key lưu đúng tên mặc định ~/.ssh/id_ed25519
```

**Trên Windows — PowerShell:**

```powershell
# Kiểm tra OpenSSH Client (nếu lỗi thì cài dòng dưới)
ssh -V
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0

# Tạo key
ssh-keygen -t ed25519 -C "thanh-pccongty"
```

> **CỰC KỲ QUAN TRỌNG:** ở dòng `Enter file in which to save the key`, **chỉ nhấn Enter** — đừng gõ tên nào khác. Key phải nằm đúng `C:\Users\<tên-bạn>\.ssh\id_ed25519` thì `ssh` mới tự tìm thấy. Đây là lỗi hay gặp nhất: gõ nhầm tên file → sau này `ssh -v` báo `id_ed25519 type -1` (không tìm thấy key). Tham số `-C` chỉ là *nhãn ghi chú*, không phải tên file.

**Xác nhận key đã tạo đúng chỗ:**

```bash
# Mac
cat ~/.ssh/id_ed25519.pub
```

```powershell
# Windows
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

Phải in ra một dòng `ssh-ed25519 AAAA... <nhãn>`.

**Cách nạp key lên VPS** tùy tình huống:

- Mạng **không** chặn SSH → dùng `ssh-copy-id` (Mac) hoặc lệnh `Get-Content ... | ssh` (Windows) — xem mục 3.3.
- Mạng **có** chặn SSH → nạp key bằng `scp` qua proxy — xem mục 3.2, Bước 6.

> Nếu dùng cả Mac lẫn Windows: mỗi máy tạo **một cặp key riêng** (đừng copy file khóa riêng `id_ed25519` qua lại). File `~/.ssh/authorized_keys` trên VPS chứa được nhiều public key.

### 3.2 Kết nối VPS khi mạng công ty chặn SSH (Windows + proxy)

Phần này dành cho trường hợp `ssh root@45.119.87.220` **treo rồi timeout** — dấu hiệu mạng (thường là mạng công ty) chặn cổng ra 22. Đây là toàn bộ quy trình đã kiểm chứng, kèm đầy đủ lệnh PowerShell.

**Nguyên lý:** Proxy công ty chỉ cho mở "đường ống" (HTTP CONNECT) tới các cổng an toàn — thường là **443**, không cho cổng 22. Giải pháp: cho SSH trên VPS lắng nghe thêm **cổng 443**, rồi cho `ssh` chui qua proxy tới cổng 443 đó. Proxy thấy giống hệt một kết nối HTTPS bình thường nên cho qua; nó không soi bên trong là SSH hay TLS.

#### Bước 3.2.1 — Chẩn đoán: cổng nào bị chặn

```powershell
Test-NetConnection 45.119.87.220 -Port 22
Test-NetConnection 45.119.87.220 -Port 443
```

Nhìn dòng `TcpTestSucceeded`. Nếu **22 = False** và **443 = True** → đúng kiểu mạng chặn SSH, làm tiếp các bước dưới.

#### Bước 3.2.2 — Set proxy cho PowerShell (cho uv / pip / git / curl)

Lưu ý: các biến này giúp `uv`, `pip`, `git`, `curl.exe` và lưu lượng HTTPS của mem0 đi qua proxy. **`ssh` KHÔNG đọc các biến này** — `ssh` xử lý proxy riêng ở Bước 3.2.5.

```powershell
# Vĩnh viễn (áp dụng cho mọi phiên PowerShell sau này)
[Environment]::SetEnvironmentVariable("HTTP_PROXY",  "http://10.121.127.204:3128", "User")
[Environment]::SetEnvironmentVariable("HTTPS_PROXY", "http://10.121.127.204:3128", "User")
[Environment]::SetEnvironmentVariable("NO_PROXY",    "localhost,127.0.0.1", "User")
```

Đóng PowerShell, mở lại, kiểm tra:

```powershell
$env:HTTP_PROXY
curl.exe -I https://www.google.com
```

Thấy `HTTP/1.1 200 Connection established` rồi `HTTP/1.1 200 OK` → proxy hoạt động.

> Nếu proxy yêu cầu đăng nhập, dùng dạng `http://user:password@10.121.127.204:3128` (ký tự đặc biệt trong password phải mã hóa URL, ví dụ `@` → `%40`).

#### Bước 3.2.3 — Tìm công cụ tunnel cho SSH (`connect.exe`)

`ssh` cần một chương trình trung gian để nói chuyện với proxy. Bản PortableGit thường có sẵn `connect.exe`:

```powershell
Get-ChildItem "C:\Program Files\Git","E:\Tool\PortableGit" -Recurse -Filter connect.exe -ErrorAction SilentlyContinue | Select-Object FullName
```

Trong bản triển khai này, file ở: `E:\Tool\PortableGit\mingw64\bin\connect.exe`.

Nếu không tìm thấy: cài **Nmap** (`https://nmap.org/download.html`, giữ tùy chọn cài **Ncat**), rồi ở Bước 3.2.5 đổi dòng `ProxyCommand` thành: `ProxyCommand ncat --proxy 10.121.127.204:3128 --proxy-type http %h %p`.

#### Bước 3.2.4 — (Tùy chọn) Test xem proxy có cho cổng 22 không

```powershell
& "E:\Tool\PortableGit\mingw64\bin\connect.exe" -d -H 10.121.127.204:3128 45.119.87.220 22
```

- Thấy `HTTP/1.1 200 Connection established` rồi banner `SSH-2.0-...` → proxy cho cổng 22, bạn có thể dùng `Port 22` trong config và bỏ qua phần mở cổng 443.
- Thấy `HTTP/1.1 403 Forbidden` / `http proxy is not allowed` → proxy chặn cổng 22 (trường hợp phổ biến). Bắt buộc đi đường cổng 443 — làm Bước 3.2.5.

#### Bước 3.2.5 — Mở SSH cổng 443 trên VPS

Đây là bước **bootstrap khó nhất**: VPS mới chỉ mở SSH ở cổng 22, mà proxy công ty lại chặn cổng 22 — nên cần một lần truy cập VPS *không* qua proxy công ty để mở cổng 443. Dùng cách nào sẵn có:

1. **Web Console** của nhà cung cấp — nếu bạn dùng được.
2. **Một mạng cho phép cổng 22** — chỉ một lần: SSH `root@45.119.87.220` từ mạng đó (ví dụ 4G).
3. **Nhờ nhà cung cấp VPS** mở sẵn cổng 443 cho SSH ngay khi tạo VPS.

> Nếu bạn **chỉ có** SSH qua proxy công ty (không Web Console, không mạng nào khác), bước bootstrap này không thể tự làm — phải nhờ nhà cung cấp. Sau khi cổng 443 đã mở thì không bao giờ cần lại bước này.

Khi đã vào được VPS bằng một trong các cách trên, dán nguyên khối sau (tạo file cấu hình drop-in, không phải sửa tay):

```bash
sudo mkdir -p /etc/ssh/sshd_config.d
sudo tee /etc/ssh/sshd_config.d/10-port443.conf > /dev/null << 'EOF'
Port 22
Port 443
EOF
sudo ufw allow 22/tcp && sudo ufw allow 443/tcp
sudo systemctl restart ssh
ss -tlnp | grep -i ssh
```

Dòng cuối phải thấy sshd ở **cả `:22` và `:443`**.

**Nếu chỉ thấy `:22`** (Ubuntu 24.04 dùng *socket activation* nên bỏ qua dòng `Port`), chạy thêm để chuyển sang chạy sshd kiểu service:

```bash
sudo systemctl disable --now ssh.socket
sudo systemctl enable --now ssh
sudo systemctl restart ssh
ss -tlnp | grep -i ssh
```

> Nếu nhà cung cấp VPS có firewall riêng (cloud firewall / security group), nhớ mở cổng 443 ở đó nữa.

#### Bước 3.2.6 — Tạo file `~/.ssh/config` trên Windows

Không dùng `notepad` (hay lỗi nếu file/thư mục chưa tồn tại). Dán nguyên khối PowerShell sau — nó tạo thư mục và ghi file trong một lần:

```powershell
$cfg = @'
Host vps
    HostName 45.119.87.220
    User thanh
    Port 443
    ProxyCommand "E:\Tool\PortableGit\mingw64\bin\connect.exe" -H 10.121.127.204:3128 %h %p

Host vps-root
    HostName 45.119.87.220
    User root
    Port 443
    ProxyCommand "E:\Tool\PortableGit\mingw64\bin\connect.exe" -H 10.121.127.204:3128 %h %p
'@
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.ssh" | Out-Null
Set-Content -Path "$env:USERPROFILE\.ssh\config" -Value $cfg -Encoding ascii
Get-Content "$env:USERPROFILE\.ssh\config"
```

Từ đây: `ssh vps` = đăng nhập user `thanh`; `ssh vps-root` = đăng nhập `root`. Mỗi khi tài liệu ghi `ssh thanh@45.119.87.220` thì bạn gõ `ssh vps`; ghi `ssh root@45.119.87.220` thì gõ `ssh vps-root`.

> User `thanh` chưa tồn tại cho tới Giai đoạn 1 Bước 1.1. Trước đó chỉ dùng được `ssh vps-root`.

#### Bước 3.2.7 — Kết nối lần đầu (bằng password)

```powershell
ssh vps-root
```

Phải hiện `root@45.119.87.220's password:` → nhập password root VPS → vào được shell.

#### Bước 3.2.8 — Nạp public key bằng `scp`

Dán key thủ công vào trình soạn thảo rất hay bị lỗi xuống dòng giữa chừng. Dùng `scp` để chép nguyên file, không bao giờ sai:

```powershell
scp "$env:USERPROFILE\.ssh\id_ed25519.pub" vps-root:/root/.ssh/authorized_keys
```

`scp` đọc chung file `~/.ssh/config` nên cũng tự đi qua proxy. Nó vẫn hỏi password root (vì key chưa chạy) → nhập password.

Rồi trong phiên `ssh vps-root`, sửa quyền:

```bash
mkdir -p ~/.ssh
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
ls -la ~/.ssh
```

#### Bước 3.2.9 — Kiểm tra

```powershell
ssh vps-root
```

Vào thẳng, **không hỏi password** → SSH qua proxy đã hoàn chỉnh. Giờ bạn bắt đầu được Giai đoạn 1.

> **Lưu ý cho Giai đoạn 2:** vì SSH đang chiếm cổng 443, mà Caddy cũng cần cổng 443, nên Giai đoạn 2 có một bước phụ dùng `sslh` để chia sẻ cổng 443 cho cả SSH lẫn HTTPS — xem Bước 2.0.

### 3.3 Nạp SSH key khi mạng KHÔNG chặn SSH

Nếu Bước 3.2.1 cho thấy cổng 22 thông, bạn nạp key trực tiếp, đơn giản hơn nhiều:

**Trên Mac — Terminal:**

```bash
ssh-copy-id root@45.119.87.220
ssh root@45.119.87.220   # test, phải vào không hỏi password
```

**Trên Windows — PowerShell:**

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@45.119.87.220 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
ssh root@45.119.87.220   # test
```

Trường hợp này bạn dùng trực tiếp `ssh root@45.119.87.220` / `ssh thanh@45.119.87.220` trong toàn tài liệu (không cần file config alias, không cần proxy).

---

## 4. Triển khai chi tiết

> Toàn bộ lệnh tạo file dưới đây ở dạng **copy — dán — chạy ngay** (heredoc). Không mở trình soạn thảo. Nếu có lúc phải sửa tay, tài liệu chỉ định dùng `vi`.

### Giai đoạn 1: Chuẩn bị VPS (30 phút)

> Bước 1.1 chạy bằng **root** — dùng `ssh vps-root`. Từ Bước 1.2 trở đi, đăng nhập bằng **`ssh vps`** (user `thanh`). Theo Quy tắc 1 (mục 3.0): mở sẵn một phiên SSH thứ hai trước khi làm.

**Bước 1.1 — Tạo user non-root** (đang là root)

```bash
adduser --disabled-password --gecos "" thanh
usermod -aG sudo thanh
echo "thanh ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/thanh
chmod 440 /etc/sudoers.d/thanh
mkdir -p /home/thanh/.ssh
cp /root/.ssh/authorized_keys /home/thanh/.ssh/
chown -R thanh:thanh /home/thanh/.ssh
chmod 700 /home/thanh/.ssh
chmod 600 /home/thanh/.ssh/authorized_keys
```

> Lệnh trên tạo user `thanh` không cần password, cho `sudo` không hỏi password (`NOPASSWD`) — phù hợp VPS cá nhân một người quản trị, và giúp mọi lệnh sau đều copy-chạy-ngay. Dòng `cp ...authorized_keys` chép SSH key của root sang `thanh` nên cùng một key dùng được cho cả hai.

**Kiểm tra trước khi đi tiếp:** mở PowerShell/Terminal mới, chạy `ssh vps` — **phải vào được bằng user `thanh`, không hỏi password**. Chỉ khi OK mới làm Bước 1.2 (nếu không sẽ tự khóa mình).

**Bước 1.2 — Hardening SSH** (đăng nhập bằng `ssh vps`)

```bash
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf > /dev/null << 'EOF'
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
EOF
sudo systemctl restart ssh
```

> Đây là file cấu hình drop-in — không phải sửa file gốc. Sau bước này `ssh vps-root` sẽ ngừng tác dụng (đúng chủ đích); bạn dùng `ssh vps`.

**Bước 1.3 — Firewall**

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status
```

**Bước 1.4 — fail2ban**

```bash
sudo apt update
sudo apt install -y fail2ban
sudo systemctl enable --now fail2ban
```

**Bước 1.5 — Docker + Docker Compose**

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker thanh
```

Thoát (`exit`) và `ssh vps` lại để áp dụng quyền docker, rồi kiểm tra:

```bash
docker --version
docker compose version
```

**Verification Giai đoạn 1** — chạy lần lượt, mỗi lệnh phải đạt kết quả mô tả:

*V1.1 — SSH key-only hoạt động* (chạy trên máy client)

```
ssh vps "echo OK"
```

In ra `OK` mà không hỏi password → đạt. Nếu vẫn hỏi password → xem mục 5, lỗi A5. Thử `ssh vps-root` mà bị `Permission denied` là **đúng** (root đã bị khóa đăng nhập).

*V1.2 — Firewall đã bật đúng cổng* (chạy trên VPS qua `ssh vps`)

```bash
sudo ufw status
```

Phải thấy `Status: active` và các dòng `22/tcp ALLOW`, `80/tcp ALLOW`, `443/tcp ALLOW`. Nếu `Status: inactive` → `sudo ufw --force enable`.

*V1.3 — SSH hardening đã có hiệu lực*

```bash
sudo sshd -T | grep -E "permitrootlogin|passwordauthentication"
```

Phải ra `permitrootlogin no` và `passwordauthentication no`. Nếu chưa đúng → kiểm tra lại file `/etc/ssh/sshd_config.d/99-hardening.conf` (Bước 1.2), rồi `sudo systemctl restart ssh`.

*V1.4 — Docker chạy được*

```bash
docker run --rm hello-world > /dev/null 2>&1 && echo "DOCKER OK" || echo "DOCKER LỖI"
```

Hiện `DOCKER OK` → đạt (`--rm` để tự xóa container test). Nếu `DOCKER LỖI`, chạy `docker run --rm hello-world` lại để xem lỗi cụ thể:

- `permission denied ... docker.sock` → user `thanh` chưa áp quyền nhóm `docker`. Quyền nhóm chỉ ăn ở phiên đăng nhập mới: `exit` rồi `ssh vps` lại, chạy lại lệnh test.
- `Cannot connect to the Docker daemon` → dịch vụ chưa chạy: `sudo systemctl enable --now docker`.

*V1.5 — Docker Compose có sẵn*

```bash
docker compose version
```

In ra phiên bản (vd `Docker Compose version v2...`) → đạt.

---

### Giai đoạn 2: Deploy storage stack (45 phút)

**Bước 2.0 — (CHỈ cho người dùng proxy ở mục 3.2) Chia sẻ cổng 443 bằng sslh**

SSH của bạn đang chiếm cổng 443, mà Caddy cũng cần 443. Đặt `sslh` đứng trước cổng 443: nó tự nhận biết kết nối là SSH hay HTTPS rồi chuyển hướng — SSH về cổng 22, HTTPS về Caddy ở cổng 8443. **Đây là bước rủi ro cao nhất** — trước khi làm, áp dụng đầy đủ 4 quy tắc ở mục 3.0 (mở phiên SSH thứ hai, sao lưu, đặt lệnh tự khôi phục).

> **Vì sao bắt buộc dùng sslh:** proxy công ty chỉ cho CONNECT tới cổng 443 — đã kiểm chứng cổng 22 và 9999 đều bị trả `403 Forbidden`. Không thể dời SSH sang cổng khác để né sslh. Muốn SSH từ máy công ty thì SSH và Caddy buộc phải chia nhau cổng 443, và `sslh` chính là bộ chia đó.

```bash
sudo apt update
# Preseed lựa chọn "standalone" để cài sslh không bị hỏi tương tác.
# (Nếu vẫn hiện hộp thoại "from inetd / standalone" → chọn standalone: phím mũi tên chọn, Tab tới <Ok>, Enter.)
echo "sslh sslh/inetd_or_standalone select standalone" | sudo debconf-set-selections
sudo DEBIAN_FRONTEND=noninteractive apt install -y sslh

# Đưa SSH về đúng cổng 22 (sslh sẽ lo việc nhận ở 443)
sudo tee /etc/ssh/sshd_config.d/10-port443.conf > /dev/null << 'EOF'
Port 22
EOF
sudo systemctl restart ssh

# Tạo service sslh riêng, rõ ràng
sudo systemctl disable --now sslh 2>/dev/null || true
sudo tee /etc/systemd/system/sslh-mux.service > /dev/null << 'EOF'
[Unit]
Description=sslh 443 multiplexer (SSH + HTTPS)
After=network.target

[Service]
ExecStart=/usr/sbin/sslh --foreground --listen 0.0.0.0:443 --ssh 127.0.0.1:22 --tls 127.0.0.1:8443
Restart=always

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now sslh-mux
sudo systemctl status sslh-mux --no-pager
```

> Sau bước này, `docker-compose.yml` bên dưới map Caddy vào cổng `8443:443`. Nếu mạng bạn **không** chặn SSH (bỏ qua mục 3.2 và Bước 2.0 này), hãy đổi `8443:443` thành `443:443` trong file compose.

**Bước 2.1 — Tạo thư mục và sinh secrets thẳng vào `.env`**

```bash
mkdir -p ~/memory-stack/data/qdrant ~/memory-stack/data/pg ~/memory-stack/data/caddy
cd ~/memory-stack
cat > .env << EOF
QDRANT_API_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 32)
MCP_BEARER_TOKEN=$(openssl rand -hex 32)
EOF
chmod 600 .env
cat .env
```

> Heredoc này **không** đặt `EOF` trong nháy nên `$(openssl rand -hex 32)` được chạy ngay, sinh secret thẳng vào file. Lệnh `cat .env` cuối in ra 3 secret — lát nữa bạn cần `MCP_BEARER_TOKEN` cho cấu hình client (Giai đoạn 3). Khi cần xem lại: `grep MCP_BEARER_TOKEN ~/memory-stack/.env`.

**Bước 2.2 — Tạo `docker-compose.yml`**

```bash
cat > ~/memory-stack/docker-compose.yml << 'EOF'
services:
  qdrant:
    image: qdrant/qdrant:v1.12.0
    container_name: memory-qdrant
    volumes:
      - ./data/qdrant:/qdrant/storage
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}
    restart: unless-stopped
    networks: [memnet]

  postgres:
    image: pgvector/pgvector:pg16
    container_name: memory-postgres
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: mem0
      POSTGRES_USER: mem0
    volumes:
      - ./data/pg:/var/lib/postgresql/data
    restart: unless-stopped
    networks: [memnet]

  caddy:
    image: caddy:2.8
    container_name: memory-caddy
    ports:
      - "80:80"
      - "8443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./data/caddy:/data
    environment:
      MCP_BEARER_TOKEN: ${MCP_BEARER_TOKEN}
      QDRANT_API_KEY: ${QDRANT_API_KEY}
    restart: unless-stopped
    networks: [memnet]
    depends_on: [qdrant]

networks:
  memnet:
    driver: bridge
EOF
```

> Caddy map `8443:443` để nhường cổng 443 cho `sslh` (Bước 2.0). Không dùng proxy thì đổi thành `443:443`.

**Bước 2.3 — Tạo `Caddyfile`**

```bash
cat > ~/memory-stack/Caddyfile << 'EOF'
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

    handle /health {
        respond "ok" 200
    }

    handle {
        respond "Not Found" 404
    }

    log {
        output file /data/access.log
        format json
    }
}
EOF
```

> Caddy tự lấy chứng chỉ Let's Encrypt cho `claude.hangocthanh.io.vn`. Caddyfile cố ý không phơi Qdrant API key thật ra ngoài: client (mem0) gửi header `api-key: <MCP_BEARER_TOKEN>`, Caddy verify token đó rồi **thay** bằng Qdrant API key thật trước khi chuyển cho Qdrant. Phải dùng đúng header tên `api-key` vì thư viện `qdrant-client` của mem0 gửi token theo header này.

**Bước 2.4 — Khởi động stack**

```bash
cd ~/memory-stack
docker compose up -d
docker compose ps
```

Phải thấy đủ **3 container** trạng thái Up: `memory-qdrant`, `memory-postgres`, `memory-caddy`. Thiếu `memory-caddy` → xem Troubleshooting B7.

**Kiểm tra Caddy đã lấy được chứng chỉ TLS** — đợi ~30 giây rồi chạy:

```bash
docker compose logs caddy | grep -iE "certificate|error|acme"
```

Đọc kết quả:

- Thấy `certificate obtained successfully` cho `claude.hangocthanh.io.vn` → **OK**, sang Verification.
- Thấy `lookup ... on 127.0.0.53:53 ... connection refused` → container không phân giải được DNS → xem Troubleshooting **B8**.
- Thấy `lookup ... connect: network is unreachable` → container không ra được internet → xem Troubleshooting **B9**.
- Thấy `no such host` → bản ghi DNS của tên miền chưa trỏ đúng IP → xem Troubleshooting **B2**.
- Thấy `rate limited` / `429` → bị giới hạn của Let's Encrypt, đợi một lúc rồi thử lại.

**Verification Giai đoạn 2** — chạy từ máy client:

**Trên Mac — Terminal:**

```bash
curl https://claude.hangocthanh.io.vn/health
curl https://claude.hangocthanh.io.vn/qdrant/collections
curl https://claude.hangocthanh.io.vn/qdrant/collections -H "api-key: <MCP_BEARER_TOKEN>"
```

**Trên Windows — PowerShell** (nhớ `curl.exe`, không phải `curl`):

```powershell
curl.exe https://claude.hangocthanh.io.vn/health
curl.exe https://claude.hangocthanh.io.vn/qdrant/collections
curl.exe https://claude.hangocthanh.io.vn/qdrant/collections -H "api-key: <MCP_BEARER_TOKEN>"
```

Kết quả mong đợi: lệnh 1 trả `ok`; lệnh 2 trả `Unauthorized`; lệnh 3 trả JSON `{"result": {"collections": []}, ...}`.

---

### Giai đoạn 3: Cài MCP server trên máy client (20 phút)

**Bước 3.1 — Cài uv**

**Trên Mac — Terminal:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Trên Windows — PowerShell:**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Đóng/mở lại terminal, kiểm tra (giống nhau): `uv --version` và `uvx --version`.

**Bước 3.2 — Test chạy MCP server** (giống nhau cả hai)

```
uvx --from git+https://github.com/elvismdev/mem0-mcp-selfhosted.git mem0-mcp-selfhosted --help
```

Lần đầu tải dependencies (~2 phút), sau đó hiện help text → OK.

**Bước 3.3 — Chuẩn bị giá trị config**

Lệnh `claude mcp add` ở Giai đoạn 4 cần các giá trị dưới đây. **CỰC KỲ QUAN TRỌNG:** server `mem0-mcp-selfhosted` chỉ đọc biến môi trường có tiền tố **`MEM0_`**. Đặt sai tên (vd `QDRANT_URL` thay vì `MEM0_QDRANT_URL`) thì server lặng lẽ bỏ qua và dùng giá trị mặc định `localhost` — đây chính là lỗi khiến server treo (xem B13).

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `MEM0_USER_ID` | `thanh` | Cố định |
| `MEM0_QDRANT_URL` | `https://claude.hangocthanh.io.vn:443/qdrant` | **Phải có `:443`** (xem B12). Mặc định của server là `localhost:6333` |
| `MEM0_QDRANT_API_KEY` | (giá trị của `MCP_BEARER_TOKEN`) | Xem mục a) |
| `MEM0_EMBED_PROVIDER` | `openai` | Mặc định là `ollama`; phải đổi vì máy client không chạy Ollama |
| `MEM0_EMBED_MODEL` | `text-embedding-3-small` | Model embedding của OpenAI |
| `MEM0_EMBED_DIMS` | `1536` | Số chiều vector của `text-embedding-3-small` |
| `MEM0_LLM_MODEL` | `claude-haiku-4-5-20251001` | **Bắt buộc đổi** — mặc định là `claude-opus-4-6` sẽ dính 429 ngay trên token Max (xem B14) |
| `OPENAI_API_KEY` | `sk-...` | Dùng cho **embedder** (xem mục b) |

> **LLM** (trích xuất facts) dùng **Claude qua OAT của Claude Max** — server tự đọc token từ `~/.claude/.credentials.json`, **không cần khai báo key**. Tuy nhiên **bắt buộc override `MEM0_LLM_MODEL` sang Haiku 4.5** vì model mặc định `claude-opus-4-6` quá nặng so với hạn mức tốc độ của gói Max → dính `429 rate_limit_error` tức thì khi mem0 chạy song song phiên Claude Code đang dùng cùng tài khoản (chi tiết B14). Không cần OpenAI key cho LLM; OpenAI key chỉ phục vụ phần embedding.

**a) Lấy `MCP_BEARER_TOKEN`**

Đây **không phải** thứ tạo trên web — nó là chuỗi ngẫu nhiên đã được sinh sẵn ở Bước 2.1, nằm trong file `.env` trên VPS. SSH vào VPS rồi chạy:

```bash
grep MCP_BEARER_TOKEN ~/memory-stack/.env
```

Copy phần giá trị nằm sau dấu `=`.

> **Lưu ý dễ nhầm:** ở lệnh `claude mcp add`, bạn dán giá trị `MCP_BEARER_TOKEN` này vào biến **tên là `MEM0_QDRANT_API_KEY`**. Nghe ngược nhưng đúng: client gửi token này cho Caddy; Caddy xác thực rồi mới thay bằng Qdrant API key thật. (Đây cũng chính là token bạn đã dùng khi `curl` thử `/qdrant/collections`.)

**b) Tạo `OPENAI_API_KEY`**

1. Vào `platform.openai.com` → menu trái **Billing** → thêm phương thức thanh toán và nạp tối thiểu **$5**. *Bắt buộc làm trước* — chưa có credit thì key sẽ báo lỗi `429 insufficient_quota`.
2. Sang menu trái **API keys** → bấm nút **Create new secret key**.
3. **Name:** đặt tên gợi nhớ, ví dụ `mem0-memory-server`. **Project:** để mặc định. **Permissions:** để **All**.
4. Bấm **Create secret key**.
5. Key dạng `sk-...` hiện ra **một lần duy nhất** — bấm biểu tượng copy và lưu ngay vào nơi an toàn. Đóng hộp thoại là **không xem lại được**; lỡ mất thì phải tạo key mới.

Kiểm tra key dùng được: xem mục **P1** ở phần Pre-flight.

**Verification Giai đoạn 3:**

```
uv --version
uvx --version
```

Cả hai in ra số phiên bản → đạt. Nếu báo `command not found` / `not recognized` → đóng hẳn terminal/PowerShell rồi mở lại (uv vừa cài cần phiên mới để nhận PATH); vẫn lỗi thì cài lại Bước 3.1.

---

### Giai đoạn 4: Gắn MCP vào Claude Code (10 phút)

**Bước 4.1 — Thêm MCP server** (Mac nối dòng bằng `\`, Windows bằng backtick `` ` ``)

**Trên Mac — Terminal:**

```bash
claude mcp add --scope user --transport stdio mem0 \
  --env MEM0_USER_ID=thanh \
  --env MEM0_QDRANT_URL=https://claude.hangocthanh.io.vn:443/qdrant \
  --env MEM0_QDRANT_API_KEY=<MCP_BEARER_TOKEN> \
  --env MEM0_EMBED_PROVIDER=openai \
  --env MEM0_EMBED_MODEL=text-embedding-3-small \
  --env MEM0_EMBED_DIMS=1536 \
  --env MEM0_LLM_MODEL=claude-haiku-4-5-20251001 \
  --env OPENAI_API_KEY=<sk-...> \
  -- uvx --from git+https://github.com/elvismdev/mem0-mcp-selfhosted.git mem0-mcp-selfhosted
```

**Trên Windows — PowerShell:**

```powershell
claude mcp add --scope user --transport stdio mem0 `
  --env MEM0_USER_ID=thanh `
  --env MEM0_QDRANT_URL=https://claude.hangocthanh.io.vn:443/qdrant `
  --env MEM0_QDRANT_API_KEY=<MCP_BEARER_TOKEN> `
  --env MEM0_EMBED_PROVIDER=openai `
  --env MEM0_EMBED_MODEL=text-embedding-3-small `
  --env MEM0_EMBED_DIMS=1536 `
  --env MEM0_LLM_MODEL=claude-haiku-4-5-20251001 `
  --env OPENAI_API_KEY=<sk-...> `
  -- uvx --from git+https://github.com/elvismdev/mem0-mcp-selfhosted.git mem0-mcp-selfhosted
```

> **Nếu bạn vào mạng qua proxy công ty** (mục 3.2): tiến trình MCP server cần biết proxy để gọi được OpenAI và Qdrant trên VPS. Thiếu nó, lệnh `search_memories`/`add_memory` sẽ **treo** tới khi timeout (Claude Code "nghĩ" rất lâu). Thêm 2 dòng `--env` sau vào lệnh `claude mcp add` bên trên (đặt cùng nhóm các dòng `--env`, nhớ ký tự nối dòng `\` cho Mac hoặc `` ` `` cho Windows):
>
> `--env HTTP_PROXY=http://10.121.127.204:3128`
>
> `--env HTTPS_PROXY=http://10.121.127.204:3128`
>
> Server `archive` ở Bước 7.7 cũng cần thêm 2 dòng `--env` này.

**Bước 4.2 — Verify trong Claude Code**

Mở VS Code → Claude Code panel → gõ `/mcp`. Phải thấy `mem0` server ● connected và 11 tools (`add_memory`, `search_memory`, `list_memories`, ...). Nếu ● disconnected → `/mcp logs mem0` để xem lỗi (mục Troubleshooting).

---

### Giai đoạn 5: Test thực tế (15 phút)

Các test này chạy bên trong Claude Code.

**Test 1 — Ghi memory.** Trong session Claude Code mới:

```
Hãy nhớ rằng tôi tên Thanh, đang dùng Claude Max và vừa triển khai memory server tự host trên VPS.
```

Claude phải gọi tool `add_memory` (hiện permission prompt → Đồng ý).

**Test 2 — Tìm memory cùng session:**

```
Tôi đã nói gì về VPS?
```

Claude gọi `search_memory` và trả lời chính xác.

**Test 3 — Persistence qua session mới.** Đóng hẳn VS Code, mở lại, tạo session mới:

```
Bạn nhớ gì về tôi?
```

Claude gọi `search_memory` và liệt kê đúng facts đã lưu.

**Test 4 — Verify trên VPS.** `ssh vps` rồi chạy (image Qdrant **không** có sẵn `curl`, nên dùng một container `curl` tạm trên cùng mạng):

```bash
docker run --rm --network memory-stack_memnet curlimages/curl -s http://qdrant:6333/collections -H "api-key: $(grep QDRANT_API_KEY ~/memory-stack/.env | cut -d= -f2)"
```

Thấy danh sách `collections` có ít nhất một collection (mem0 tạo collection khi `add_memory` chạy lần đầu) → mem0 đã lưu memory thật. Nếu trả về `"collections":[]` rỗng → chưa có memory nào được lưu qua mem0 (kiểm tra lại Test 1: Claude phải gọi đúng tool `add_memory`, không phải bộ nhớ built-in của Claude Code). Cả 4 test pass → hệ thống hoạt động.

---

### Giai đoạn 6: Backup & Monitoring (45 phút)

**Bước 6.1 — Script backup hàng ngày** (chạy trên VPS)

```bash
sudo tee /usr/local/bin/memory-backup.sh > /dev/null << 'EOF'
#!/bin/bash
set -euo pipefail
BACKUP_DIR=/var/backups/memory
DATE=$(date +%F)
mkdir -p "$BACKUP_DIR"

# Backup Postgres
docker exec memory-postgres pg_dump -U mem0 mem0 | gzip > "$BACKUP_DIR/pg_$DATE.sql.gz"

# Backup Qdrant (snapshot) — image Qdrant không có curl, dùng container curl tạm
docker run --rm --network memory-stack_memnet curlimages/curl -s -X POST http://qdrant:6333/snapshots -H "api-key: $QDRANT_API_KEY"
docker cp memory-qdrant:/qdrant/storage/snapshots/. "$BACKUP_DIR/qdrant_$DATE/"
tar czf "$BACKUP_DIR/qdrant_$DATE.tar.gz" -C "$BACKUP_DIR" "qdrant_$DATE"
rm -rf "$BACKUP_DIR/qdrant_$DATE"

# Xóa backup > 14 ngày
find "$BACKUP_DIR" -name "*.gz" -mtime +14 -delete
EOF
sudo chmod +x /usr/local/bin/memory-backup.sh
```

**Bước 6.2 — Hẹn giờ chạy backup 03:00 mỗi ngày** (thêm dòng cron, không mở editor)

```bash
( sudo crontab -l 2>/dev/null; echo "0 3 * * * QDRANT_API_KEY=$(grep QDRANT_API_KEY ~/memory-stack/.env | cut -d= -f2) /usr/local/bin/memory-backup.sh >> /var/log/memory-backup.log 2>&1" ) | sudo crontab -
sudo crontab -l
```

**Bước 6.3 — Test backup ngay**

```bash
sudo QDRANT_API_KEY=$(grep QDRANT_API_KEY ~/memory-stack/.env | cut -d= -f2) /usr/local/bin/memory-backup.sh
ls -lh /var/backups/memory/
```

**Bước 6.4 — Monitoring**

Cách nhẹ nhất, không cần sửa file: đăng ký **Better Stack** (better-stack.com) hoặc **UptimeRobot**, tạo HTTP monitor cho `https://claude.hangocthanh.io.vn/health`, ping mỗi 5 phút, alert qua email/Telegram.

Muốn xem dashboard qua SSH tunnel (ví dụ Uptime Kuma self-host) thì lệnh tunnel giống nhau trên Mac/Windows:

```
ssh -L 3001:localhost:3001 vps
```

Giữ cửa sổ đó mở, mở `http://localhost:3001` trên trình duyệt máy client.

**Verification Giai đoạn 6** (chạy trên VPS):

```bash
ls -lh /var/backups/memory/
sudo crontab -l | grep memory-backup
```

Phải thấy có file `pg_*.sql.gz` và `qdrant_*.tar.gz` (do backup test ở Bước 6.3 tạo ra), và một dòng cron chứa `memory-backup.sh` → đạt. Nếu thư mục trống → chạy lại Bước 6.3 và xem log lỗi.

---

### Giai đoạn 7: Transcript archive (khuyến nghị · 90 phút)

Bổ sung khả năng **lưu nguyên văn cuộc chat** để lướt lại theo ngày/dự án. Dùng chung VPS, chung Postgres, thêm 1 table + 1 REST API + 1 uploader.

**Bước 7.1 — Tạo table `chat_sessions`** (chạy trên VPS — đẩy SQL qua heredoc, không vào psql tương tác)

```bash
docker exec -i memory-postgres psql -U mem0 mem0 << 'EOF'
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    project_tag TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    message_count INT,
    transcript JSONB NOT NULL,
    summary TEXT,
    workspace_path TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user_date ON chat_sessions (user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON chat_sessions (project_tag);
CREATE INDEX IF NOT EXISTS idx_sessions_summary_trgm ON chat_sessions USING gin (summary gin_trgm_ops);
EOF
```

**Bước 7.2 — Tạo archive REST API** (trên VPS)

```bash
mkdir -p ~/memory-stack/archive-api
cat > ~/memory-stack/archive-api/Dockerfile << 'EOF'
FROM python:3.12-slim
WORKDIR /app
RUN pip install fastapi uvicorn psycopg2-binary
COPY app.py .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
EOF
```

```bash
cat > ~/memory-stack/archive-api/app.py << 'EOF'
import os
import psycopg2
from psycopg2.extras import Json, RealDictCursor
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

DB_URL = os.environ["DB_URL"]
AUTH = os.environ["ARCHIVE_AUTH_TOKEN"]
app = FastAPI()

def check(token):
    if not token or token != f"Bearer {AUTH}":
        raise HTTPException(401, "Unauthorized")

def conn():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)

class Session(BaseModel):
    user_id: str
    project_tag: Optional[str] = None
    started_at: str
    ended_at: Optional[str] = None
    message_count: int
    transcript: list
    summary: Optional[str] = None
    workspace_path: Optional[str] = None
    metadata: dict = {}

@app.post("/sessions")
def create(s: Session, authorization: str = Header(None)):
    check(authorization)
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO chat_sessions
            (user_id, project_tag, started_at, ended_at, message_count,
             transcript, summary, workspace_path, metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (s.user_id, s.project_tag, s.started_at, s.ended_at,
              s.message_count, Json(s.transcript), s.summary,
              s.workspace_path, Json(s.metadata)))
        return {"id": str(cur.fetchone()["id"])}

@app.get("/sessions")
def list_sessions(
    user_id: str,
    project_tag: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    authorization: str = Header(None),
):
    check(authorization)
    sql = "SELECT id, started_at, project_tag, message_count, summary FROM chat_sessions WHERE user_id=%s"
    args = [user_id]
    if project_tag: sql += " AND project_tag=%s"; args.append(project_tag)
    if date_from:   sql += " AND started_at >= %s"; args.append(date_from)
    if date_to:     sql += " AND started_at <= %s"; args.append(date_to)
    if q:           sql += " AND summary ILIKE %s"; args.append(f"%{q}%")
    sql += " ORDER BY started_at DESC LIMIT %s"; args.append(limit)
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]

@app.get("/sessions/{session_id}")
def get_session(session_id: str, authorization: str = Header(None)):
    check(authorization)
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM chat_sessions WHERE id=%s", (session_id,))
        r = cur.fetchone()
        if not r: raise HTTPException(404)
        return dict(r)

@app.get("/health")
def health(): return "ok"
EOF
```

Thêm secret cho archive vào `.env` (append một dòng, không sửa tay):

```bash
echo "ARCHIVE_AUTH_TOKEN=$(openssl rand -hex 32)" >> ~/memory-stack/.env
grep ARCHIVE_AUTH_TOKEN ~/memory-stack/.env
```

**Bước 7.3 — Ghi đè `docker-compose.yml` (đã thêm archive-api)**

```bash
cat > ~/memory-stack/docker-compose.yml << 'EOF'
services:
  qdrant:
    image: qdrant/qdrant:v1.12.0
    container_name: memory-qdrant
    volumes:
      - ./data/qdrant:/qdrant/storage
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}
    restart: unless-stopped
    networks: [memnet]

  postgres:
    image: pgvector/pgvector:pg16
    container_name: memory-postgres
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: mem0
      POSTGRES_USER: mem0
    volumes:
      - ./data/pg:/var/lib/postgresql/data
    restart: unless-stopped
    networks: [memnet]

  archive-api:
    build: ./archive-api
    container_name: memory-archive-api
    environment:
      DB_URL: postgresql://mem0:${POSTGRES_PASSWORD}@postgres/mem0
      ARCHIVE_AUTH_TOKEN: ${ARCHIVE_AUTH_TOKEN}
    networks: [memnet]
    depends_on: [postgres]
    restart: unless-stopped

  caddy:
    image: caddy:2.8
    container_name: memory-caddy
    ports:
      - "80:80"
      - "8443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./data/caddy:/data
    environment:
      MCP_BEARER_TOKEN: ${MCP_BEARER_TOKEN}
      QDRANT_API_KEY: ${QDRANT_API_KEY}
    restart: unless-stopped
    networks: [memnet]
    depends_on: [qdrant]

networks:
  memnet:
    driver: bridge
EOF
```

**Bước 7.4 — Ghi đè `Caddyfile` (đã thêm route `/archive`)**

```bash
cat > ~/memory-stack/Caddyfile << 'EOF'
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

    handle /health {
        respond "ok" 200
    }

    handle {
        respond "Not Found" 404
    }

    log {
        output file /data/access.log
        format json
    }
}
EOF
```

Build và khởi động lại:

```bash
cd ~/memory-stack
docker compose up -d --build
```

Test từ máy client: `curl https://claude.hangocthanh.io.vn/archive/health` (Mac) hoặc `curl.exe https://claude.hangocthanh.io.vn/archive/health` (Windows) → `"ok"`.

**Bước 7.5 — Uploader script trên máy client**

Script đọc các file session Claude Code (`~/.claude/projects/...`), upload session mới lên VPS. Dùng `Path.home()` nên chạy được cả Mac lẫn Windows. Lưu vào `~/scripts/archive-upload.py` (Mac) hoặc `C:\Users\<tên-bạn>\scripts\archive-upload.py` (Windows).

**Trên Mac — Terminal:**

```bash
mkdir -p ~/scripts
cat > ~/scripts/archive-upload.py << 'EOF'
#!/usr/bin/env python3
"""Upload Claude Code session transcripts to archive API."""
import json, os, sys, hashlib
from pathlib import Path
import urllib.request

ARCHIVE_URL = os.environ["ARCHIVE_URL"]
ARCHIVE_TOKEN = os.environ["ARCHIVE_AUTH_TOKEN"]
USER_ID = os.environ.get("USER_ID", "thanh")
STATE_FILE = Path.home() / ".cache" / "claude-archive-state.json"
STATE_FILE.parent.mkdir(exist_ok=True)

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"uploaded": []}

def save_state(s):
    STATE_FILE.write_text(json.dumps(s))

def file_id(p: Path):
    h = hashlib.sha256()
    h.update(str(p).encode())
    h.update(str(p.stat().st_mtime).encode())
    return h.hexdigest()[:16]

def parse_session(jsonl_path: Path):
    messages, workspace, times = [], None, []
    for line in jsonl_path.read_text(errors="ignore").splitlines():
        if not line.strip(): continue
        try: m = json.loads(line)
        except: continue
        if "cwd" in m and not workspace: workspace = m["cwd"]
        if m.get("type") in ("user", "assistant"):
            content = m.get("message", {}).get("content")
            if isinstance(content, list):
                content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
            messages.append({"role": m.get("type"), "content": content,
                             "timestamp": m.get("timestamp")})
            if m.get("timestamp"): times.append(m["timestamp"])
    if not messages or not times: return None
    project_tag = Path(workspace).name if workspace else None
    first_user = next((m["content"] for m in messages if m["role"] == "user"), "")
    return {
        "user_id": USER_ID, "project_tag": project_tag,
        "workspace_path": workspace, "started_at": min(times),
        "ended_at": max(times), "message_count": len(messages),
        "transcript": messages, "summary": (first_user or "")[:200],
        "metadata": {"source_file": jsonl_path.name},
    }

def upload(data):
    req = urllib.request.Request(
        f"{ARCHIVE_URL}/sessions",
        data=json.dumps(data, default=str).encode(),
        headers={"Authorization": f"Bearer {ARCHIVE_TOKEN}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def main():
    state = load_state()
    uploaded = set(state["uploaded"])
    sessions_dir = Path.home() / ".claude" / "projects"
    if not sessions_dir.exists():
        print("No Claude Code sessions found", file=sys.stderr); return
    new = 0
    for jsonl in sessions_dir.rglob("*.jsonl"):
        fid = file_id(jsonl)
        if fid in uploaded: continue
        data = parse_session(jsonl)
        if not data: continue
        try:
            upload(data); uploaded.add(fid); new += 1
            print(f"Uploaded {jsonl.name} -> project={data['project_tag']}")
        except Exception as e:
            print(f"Failed {jsonl.name}: {e}", file=sys.stderr)
    state["uploaded"] = list(uploaded)
    save_state(state)
    print(f"Done. {new} new sessions uploaded.")

if __name__ == "__main__":
    main()
EOF
chmod +x ~/scripts/archive-upload.py
```

**Trên Windows — PowerShell:** dùng cùng nội dung script trên. Tạo nhanh bằng:

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\scripts" | Out-Null
# Mở file rỗng bằng vi (qua Git) rồi dán nội dung script ở khối Mac phía trên:
# & "E:\Tool\PortableGit\usr\bin\vi.exe" "$env:USERPROFILE\scripts\archive-upload.py"
```

> Trên Windows, cách chắc nhất để tạo file Python dài: tạo nó **trên VPS** bằng heredoc rồi `scp` về máy, hoặc dùng `vi` của Git Bash. Tránh notepad vì dễ thêm BOM/đổi xuống dòng.

**Đặt biến môi trường và chạy thử:**

**Trên Mac:**

```bash
cat > ~/.config/archive-env << 'EOF'
export ARCHIVE_URL=https://claude.hangocthanh.io.vn/archive
export ARCHIVE_AUTH_TOKEN=<token-từ-.env-VPS>
export USER_ID=thanh
EOF
source ~/.config/archive-env
~/scripts/archive-upload.py
```

**Trên Windows:**

```powershell
$envps = @'
$env:ARCHIVE_URL        = "https://claude.hangocthanh.io.vn/archive"
$env:ARCHIVE_AUTH_TOKEN = "<token-từ-.env-VPS>"
$env:USER_ID            = "thanh"
'@
Set-Content -Path "$env:USERPROFILE\scripts\archive-env.ps1" -Value $envps -Encoding ascii
. "$env:USERPROFILE\scripts\archive-env.ps1"
python "$env:USERPROFILE\scripts\archive-upload.py"
```

**Bước 7.6 — Tự động chạy mỗi giờ**

**Trên Mac — launchd:**

```bash
cat > ~/Library/LaunchAgents/com.thanh.archive-upload.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.thanh.archive-upload</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-c</string>
    <string>source $HOME/.config/archive-env && $HOME/scripts/archive-upload.py</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>StandardOutPath</key><string>/tmp/archive-upload.log</string>
  <key>StandardErrorPath</key><string>/tmp/archive-upload.err</string>
</dict>
</plist>
EOF
launchctl load ~/Library/LaunchAgents/com.thanh.archive-upload.plist
```

**Trên Windows — Task Scheduler:**

> **Lưu ý:** plan cũ dùng backtick `` ` `` để xuống dòng, **hay fail khi paste** (trailing space stripping). Plan đã update sang **ONE-LINE** an toàn hơn — xem **B28**. Kèm `-WindowStyle Hidden` + Settings `-Hidden` để không popup mỗi giờ — xem **B29**.

```powershell
# Bước 1: Tạo runner script (heredoc OK vì @'...'@ không phụ thuộc line continuation)
$runner = @'
. "$env:USERPROFILE\scripts\archive-env.ps1"
python "$env:USERPROFILE\scripts\archive-upload.py" *>> "$env:USERPROFILE\scripts\archive-upload.log"
'@
Set-Content -Path "$env:USERPROFILE\scripts\archive-upload-run.ps1" -Value $runner -Encoding ascii

# Bước 2: Cleanup task cũ (nếu add lại nhiều lần)
Unregister-ScheduledTask -TaskName "archive-upload" -Confirm:$false -ErrorAction SilentlyContinue

# Bước 3: Action — ONE LINE (KHÔNG backtick), có WindowStyle Hidden
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$env:USERPROFILE\scripts\archive-upload-run.ps1`""

# Bước 4: Trigger — ONE LINE
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1)

# Bước 5: Settings ẩn hoàn toàn (không popup)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -StartWhenAvailable

# Bước 6: Register — ONE LINE, Description ASCII (không dấu) để tránh encoding bug
Register-ScheduledTask -TaskName "archive-upload" -Action $action -Trigger $trigger -Settings $settings -Description "Upload Claude Code transcripts moi gio"

# Bước 7: Test chạy ngay
Start-ScheduledTask -TaskName "archive-upload"
Start-Sleep -Seconds 15
Get-Content "$env:USERPROFILE\scripts\archive-upload.log" -Tail 10
```

Verify: log có dòng `Done. X new sessions uploaded.` → OK. Nếu vẫn popup cửa sổ → check lại `-WindowStyle Hidden` trong `-Argument` (B29).

**Bước 7.7 — MCP tool để Claude truy vấn archive**

Cài SDK: `pip3 install --user mcp httpx` (Mac) / `pip install --user mcp httpx` (Windows). Tạo `archive-mcp.py` cạnh `archive-upload.py`:

> **Lưu ý:** plan này có **4 tools** (thay vì 3 ở bản gốc). Tool `get_session_summary` là **compact view** — trả về metadata + first/last 5 messages → tránh Claude Code lưu response ra file `.txt` (xem **B31**). LLM ưu tiên `get_session_summary` cho prompt "tóm tắt", chỉ gọi `get_old_session` khi cần xem toàn bộ.

```bash
cat > ~/scripts/archive-mcp.py << 'EOF'
#!/usr/bin/env python3
"""MCP server exposing archive read tools to Claude Code."""
import os, asyncio, json, httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

ARCHIVE_URL = os.environ["ARCHIVE_URL"]
TOKEN = os.environ["ARCHIVE_AUTH_TOKEN"]
USER_ID = os.environ.get("USER_ID", "thanh")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
server = Server("archive")

@server.list_tools()
async def list_tools():
    return [
        Tool(name="list_old_sessions",
             description="List archived chat sessions by project and date range.",
             inputSchema={"type": "object", "properties": {
                 "project_tag": {"type": "string"},
                 "date_from": {"type": "string"},
                 "date_to": {"type": "string"},
                 "limit": {"type": "integer", "default": 50}}}),
        Tool(name="get_session_summary",
             description="Get compact view: metadata + first/last 5 messages. USE THIS instead of get_old_session for most cases.",
             inputSchema={"type": "object",
                          "properties": {"session_id": {"type": "string"}},
                          "required": ["session_id"]}),
        Tool(name="get_old_session",
             description="Fetch FULL transcript. WARNING: very large response. Prefer get_session_summary for overview.",
             inputSchema={"type": "object",
                          "properties": {"session_id": {"type": "string"}},
                          "required": ["session_id"]}),
        Tool(name="search_old_sessions",
             description="Search archived sessions by keyword in their summary.",
             inputSchema={"type": "object",
                          "properties": {"q": {"type": "string"}},
                          "required": ["q"]}),
    ]

@server.call_tool()
async def call_tool(name, args):
    async with httpx.AsyncClient(timeout=30, headers=HEADERS) as c:
        if name == "list_old_sessions":
            r = await c.get(f"{ARCHIVE_URL}/sessions", params={"user_id": USER_ID, **args})
            return [TextContent(type="text", text=r.text)]
        elif name == "get_session_summary":
            r = await c.get(f"{ARCHIVE_URL}/sessions/{args['session_id']}")
            data = r.json()
            transcript = data.get("transcript", [])
            compact = {
                "id": data["id"],
                "started_at": data["started_at"],
                "ended_at": data["ended_at"],
                "project_tag": data.get("project_tag"),
                "summary": data.get("summary"),
                "message_count": data["message_count"],
                "first_messages": transcript[:5],
                "last_messages": transcript[-5:] if len(transcript) > 5 else [],
            }
            return [TextContent(type="text", text=json.dumps(compact, ensure_ascii=False, indent=2))]
        elif name == "get_old_session":
            r = await c.get(f"{ARCHIVE_URL}/sessions/{args['session_id']}")
            return [TextContent(type="text", text=r.text)]
        elif name == "search_old_sessions":
            r = await c.get(f"{ARCHIVE_URL}/sessions",
                            params={"user_id": USER_ID, "q": args["q"], "limit": 20})
            return [TextContent(type="text", text=r.text)]
        else:
            return [TextContent(type="text", text=f"unknown tool {name}")]

async def main():
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
EOF
```

> **Trên Windows**, tạo file qua Notepad (an toàn nhất, tránh paste lỗi B25):
> ```powershell
> notepad "$env:USERPROFILE\scripts\archive-mcp.py"
> ```
> Ctrl+A → Delete → paste nội dung Python (BỎ dòng `cat > ... << 'EOF'` và `EOF`) → Save As → encoding **UTF-8 (KHÔNG BOM)**.

Đăng ký với Claude Code:

**Trên Mac:**

```bash
claude mcp add --scope user --transport stdio archive \
  --env ARCHIVE_URL=https://claude.hangocthanh.io.vn/archive \
  --env ARCHIVE_AUTH_TOKEN=<token> \
  --env USER_ID=thanh \
  -- python3 ~/scripts/archive-mcp.py
```

**Trên Windows:**

> **Lưu ý:** dùng ONE-LINE thay backtick để tránh **B28** (backtick continuation fail). Phải có `--env HTTP_PROXY` + `--env HTTPS_PROXY` nếu đang qua proxy công ty (xem **B23** + lưu ý ở Bước 4.1).

```powershell
# Load token từ env file
. "$env:USERPROFILE\scripts\archive-env.ps1"

# Đăng ký ONE-LINE (paste cả dòng, KHÔNG cắt)
claude mcp add --scope user --transport stdio archive --env ARCHIVE_URL=https://claude.hangocthanh.io.vn/archive --env ARCHIVE_AUTH_TOKEN=$env:ARCHIVE_AUTH_TOKEN --env USER_ID=thanh --env HTTP_PROXY=http://10.121.127.204:3128 --env HTTPS_PROXY=http://10.121.127.204:3128 -- python "$env:USERPROFILE\scripts\archive-mcp.py"

# Verify
claude mcp list
```

Phải thấy `archive ... ☑ Connected`. Nếu `☒ Disconnected` → `claude mcp logs archive` xem lỗi cụ thể (B23, B24, hoặc thiếu `pip install --user mcp httpx`).

**Đóng HẲN VS Code** (File → Exit, không phải Reload Window — xem **B30**), mở lại, gõ `/mcp` — phải thấy 2 server `mem0` và `archive`.

**Test trong Claude Code — 5 scenarios theo thứ tự:**

**Test 1 — List sessions gần đây:**
```
Liệt kê 5 session chat gần đây nhất của tôi từ archive.
```
✅ Claude phải:
- Hiện permission prompt cho `list_old_sessions` → Đồng ý
- Trả về list session với `started_at`, `project_tag`, `summary`
- KHÔNG tạo file `.txt` trong `tool-results/` (response nhỏ)

**Test 2 — Search keyword:**
```
Tìm trong archive xem tôi đã thảo luận gì về "Neon" hoặc "Postgres".
```
✅ Claude gọi `search_old_sessions?q=Neon` → trả về sessions chứa keyword. Response nhỏ → không lưu file.

**Test 3 — Tóm tắt session (KHÔNG lưu file):**
```
Tóm tắt nhanh session ID <paste-id-từ-Test-1> — chỉ cần overview, không cần xem chi tiết.
```
✅ Claude phải gọi `get_session_summary` (KHÔNG phải `get_old_session`). Response ~3k tokens, hiển thị thẳng trong chat. Nếu Claude vẫn gọi `get_old_session` → check description tool có `USE THIS instead` đúng chưa.

**Test 4 — Đọc chi tiết FULL transcript (sẽ lưu file):**
```
Xem TOÀN BỘ nội dung session <id> để tôi đọc lại từng message.
```
✅ Claude gọi `get_old_session` → tạo file `tool-results/mcp-archive-get_old_session-<id>.txt` → Read file. Đây là hành vi đúng (B31) — chỉ kích hoạt khi user EXPLICITLY yêu cầu full.

**Test 5 — Combo (search + summary):**
```
Tìm session có chữ "VPS" trong archive, rồi tóm tắt session mới nhất tìm được.
```
✅ Claude gọi:
1. `search_old_sessions?q=VPS`
2. `get_session_summary` với ID của session mới nhất
Sequence này chỉ tốn ~5k tokens tổng → KHÔNG lưu file.

**Nếu Test fail:**
- Tất cả tools trả `[]` rỗng dù Neon có data → typo `USER_ID` 3 nơi (B26)
- Test 3 vẫn gọi `get_old_session` → Claude chưa thấy tool mới, restart VS Code (B30)
- `503 Bad Gateway` → archive-api container down trên VPS, `docker compose ps`
- `401 Unauthorized` → `ARCHIVE_AUTH_TOKEN` trong `claude mcp add` sai/cũ
- Timeout 30s → proxy không pass, thiếu `--env HTTPS_PROXY` (B23)

---

### Bước 7.8 — Compact Summary feature (mở rộng: lưu `/compact` history) · 25 phút

Thanh có thể `/compact` trong Claude Code để gom context khi đầy → Claude tạo summary tóm tắt conversation cũ. Bước 7.8 lưu các compact summary này thành **table riêng** trong Neon, có search nhanh — phù hợp use case "tôi đã /compact session nào về topic X".

**Khác biệt với `chat_sessions` (Bước 7.1-7.7):**
- `chat_sessions.transcript` JSONB chứa toàn bộ messages → search chậm
- `compact_summaries.summary_text` TEXT ngắn (~500-2000 ký tự) → search ILIKE nhanh, có trigram index

**Phụ thuộc:** đã hoàn thành Bước 7.1-7.7 (table `chat_sessions` đã tồn tại, MCP archive đã chạy).

#### Phase 1 — Neon Postgres (2 phút)

Vào Neon Dashboard → SQL Editor → paste và Run:

```sql
CREATE TABLE IF NOT EXISTS compact_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    project_tag TEXT,
    workspace_path TEXT,
    compacted_at TIMESTAMPTZ DEFAULT NOW(),
    summary_text TEXT NOT NULL,
    messages_before INT,
    position_in_session INT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_summaries_user_date
    ON compact_summaries (user_id, compacted_at DESC);

CREATE INDEX IF NOT EXISTS idx_summaries_project
    ON compact_summaries (project_tag);

CREATE INDEX IF NOT EXISTS idx_summaries_text_trgm
    ON compact_summaries USING gin (summary_text gin_trgm_ops);

-- Verify
SELECT tablename FROM pg_tables WHERE schemaname = 'public';
```

✅ Pass: thấy `compact_summaries` trong list. **Nếu trước đó đã tạo table với schema cũ** (thiếu `workspace_path`, `position_in_session`, `created_at`) → xem **B32** để fix bằng `ALTER TABLE`.

#### Phase 2 — Update `archive-api/app.py` trên VPS (5 phút)

```bash
ssh vps
cd ~/memory-stack/archive-api
cp app.py app.py.bak.$(date +%F-%H%M)

# Ghi đè app.py — thêm endpoint /compact-summaries
# (Code đầy đủ ~120 dòng — xem trong session lịch sử hoặc generate lại từ Claude)
# Cần thêm: class CompactSummary BaseModel + 3 endpoints POST/GET list/GET by id
```

Cấu trúc thêm vào `app.py`:

```python
# Sau class Session...
class CompactSummary(BaseModel):
    session_id: Optional[str] = None
    user_id: str
    project_tag: Optional[str] = None
    workspace_path: Optional[str] = None
    summary_text: str
    messages_before: int = 0
    position_in_session: int = 0
    metadata: dict = {}

@app.post("/compact-summaries")
def create_summary(s: CompactSummary, authorization: str = Header(None)):
    check(authorization)
    with conn() as c, c.cursor() as cur:
        cur.execute("""
            INSERT INTO compact_summaries
            (session_id, user_id, project_tag, workspace_path,
             summary_text, messages_before, position_in_session, metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (s.session_id, s.user_id, s.project_tag, s.workspace_path,
              s.summary_text, s.messages_before, s.position_in_session,
              Json(s.metadata)))
        return {"id": str(cur.fetchone()["id"])}

@app.get("/compact-summaries")
def list_summaries(user_id: str, project_tag: Optional[str] = None,
                   date_from: Optional[str] = None, date_to: Optional[str] = None,
                   q: Optional[str] = None, limit: int = 50,
                   authorization: str = Header(None)):
    check(authorization)
    sql = """SELECT id, session_id, project_tag, workspace_path,
                    compacted_at, summary_text, messages_before
             FROM compact_summaries WHERE user_id=%s"""
    args = [user_id]
    if project_tag: sql += " AND project_tag=%s"; args.append(project_tag)
    if date_from:   sql += " AND compacted_at >= %s"; args.append(date_from)
    if date_to:     sql += " AND compacted_at <= %s"; args.append(date_to)
    if q:           sql += " AND summary_text ILIKE %s"; args.append(f"%{q}%")
    sql += " ORDER BY compacted_at DESC LIMIT %s"; args.append(limit)
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]

@app.get("/compact-summaries/{summary_id}")
def get_summary(summary_id: str, authorization: str = Header(None)):
    check(authorization)
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM compact_summaries WHERE id=%s", (summary_id,))
        r = cur.fetchone()
        if not r: raise HTTPException(404)
        return dict(r)
```

Rebuild + restart:
```bash
cd ~/memory-stack
docker compose up -d --build archive-api
sleep 5
docker logs memory-archive-api --tail 20
```

Test endpoint:
```bash
TOKEN=$(grep ARCHIVE_AUTH_TOKEN ~/memory-stack/.env | cut -d= -f2)
curl -X POST "https://claude.hangocthanh.io.vn/archive/compact-summaries" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"user_id":"thanh","summary_text":"Test about Neon","messages_before":50}'
curl "https://claude.hangocthanh.io.vn/archive/compact-summaries?user_id=thanh&q=Neon" \
  -H "Authorization: Bearer $TOKEN"
```

Phải trả `{"id":"<uuid>"}` và array có 1 row.

#### Phase 3 — Update scripts trên Windows (10 phút)

**3.1. Update `archive-upload.py` (qua Notepad):** thêm function `extract_summaries()` detect compact summary trong JSONL bằng regex (`role=system` + chứa `previously discussed`/`conversation summary`), thêm `upload_summary()` POST tới `/compact-summaries`. State file thêm key `summaries_uploaded` (hash riêng cho mỗi summary).

**3.2. Reset state để re-scan summaries hồi tố:**
```powershell
. "$env:USERPROFILE\scripts\archive-env.ps1"
Remove-Item "$env:USERPROFILE\.cache\claude-archive-state.json" -ErrorAction SilentlyContinue
python "$env:USERPROFILE\scripts\archive-upload.py"
```

Output mong đợi: `Done. X new sessions, Y new compact summaries uploaded.`

**3.3. Update `archive-mcp.py` (qua Notepad):** thêm 3 tools mới vào `list_tools()` — `list_compact_summaries`, `search_compact_summaries`, `get_compact_summary`. Description nhấn mạnh: *"PREFERRED tool for 'what did I discuss about X' queries"*.

**3.4. Re-register MCP:**
```powershell
. "$env:USERPROFILE\scripts\archive-env.ps1"
claude mcp remove archive
claude mcp add --scope user --transport stdio archive --env ARCHIVE_URL=https://claude.hangocthanh.io.vn/archive --env ARCHIVE_AUTH_TOKEN=$env:ARCHIVE_AUTH_TOKEN --env USER_ID=thanh --env HTTP_PROXY=http://10.121.127.204:3128 --env HTTPS_PROXY=http://10.121.127.204:3128 -- python "$env:USERPROFILE\scripts\archive-mcp.py"
```

**Đóng HẲN VS Code** → mở lại → `/mcp` → archive phải có **7 tools** (4 cũ + 3 mới).

#### Test trong Claude Code (5 phút)

1. `Liệt kê các compact summary gần đây của tôi.` → gọi `list_compact_summaries`
2. `Tôi đã /compact những session nào về Postgres, Neon hoặc Task Scheduler?` → gọi `search_compact_summaries`
3. `Cho tôi xem chi tiết summary đầu tiên trong list trên.` → gọi `get_compact_summary`
4. `Trong các session cũ, tôi đã thảo luận gì về "VPS"? Ưu tiên search compact summary trước.` → Claude ưu tiên `search_compact_summaries` (không phải `search_old_sessions`)

#### Lưu ý quan trọng

- **Detect pattern compact** là heuristic (Claude Code chưa có API public xuất compact event riêng) — pattern check `role=system` + regex. Có thể false positive với message có chữ "summary" nhưng không phải compact thật. Filter `len > 100` giúp giảm noise.
- **State file mới có 2 keys** — `uploaded` (sessions) và `summaries_uploaded` (compact summaries). Reset cả 2 khi update logic extract.
- **Storage Neon thêm** — mỗi compact summary ~2KB → 1000 summaries chỉ tốn ~2MB, không sợ chạm 0.5GB free tier.
- **Search ILIKE + trigram** trên 1000 summaries chạy <50ms — nhanh hơn nhiều so với scan transcript JSONB.

---

## 5. Troubleshooting

### Nhóm A — Kết nối SSH và proxy

**A1. `ssh` treo rất lâu rồi báo `Connection timed out`**

Mạng (thường mạng công ty) chặn cổng ra 22. Chẩn đoán:

```powershell
Test-NetConnection 45.119.87.220 -Port 22
Test-NetConnection 45.119.87.220 -Port 443
```

Nếu `22 = False`, `443 = True` → làm theo **mục 3.2** (kết nối qua proxy, SSH trên cổng 443).

**A2. `connect.exe -d` báo `HTTP/1.1 403 Forbidden` / `http proxy is not allowed`**

Proxy từ chối mở đường ống tới cổng bạn yêu cầu (thường là cổng 22). Squid mặc định chỉ cho CONNECT tới 443. Cách xử lý: cho SSH chạy trên cổng 443 — mục 3.2, Bước 3.2.5.

**A3. Không tìm thấy `connect.exe`**

```powershell
Get-ChildItem "C:\Program Files\Git","E:\Tool\PortableGit" -Recurse -Filter connect.exe -ErrorAction SilentlyContinue | Select-Object FullName
```

Nếu trống: kiểm tra Git có cài không (`git --version`); hoặc cài Nmap (kèm `ncat`) rồi đổi `ProxyCommand` thành `ncat --proxy 10.121.127.204:3128 --proxy-type http %h %p`.

**A4. Mở `Port 443` trong `sshd_config` rồi nhưng `ss` vẫn chỉ thấy `:22`**

Ubuntu 24.04 dùng *socket activation* — `systemd` quản lý cổng SSH, các dòng `Port` trong config bị bỏ qua. Dấu hiệu: trong output `ss -tlnp` có `("systemd",pid=1,...)`. Khắc phục:

```bash
sudo systemctl disable --now ssh.socket
sudo systemctl enable --now ssh
sudo systemctl restart ssh
ss -tlnp | grep -i ssh
```

**A5. Đã nạp key nhưng `ssh` vẫn hỏi password**

Chạy `ssh -v vps` và đọc các dòng `identity file`:

- `id_ed25519 type -1` → **máy client không có private key** ở đường dẫn mặc định (thường do lúc `ssh-keygen` đã lưu sai tên). Tạo lại, nhấn Enter ở dòng hỏi tên file để lưu đúng `id_ed25519`:

  ```powershell
  ssh-keygen -t ed25519 -C "thanh-pccongty"
  Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
  ```

- `id_ed25519 type 3` (nạp được key) nhưng vẫn hỏi password → file `authorized_keys` trên VPS sai. Nguyên nhân hay gặp: dán key vào editor bị **xuống dòng giữa chừng**. Khắc phục bằng `scp` (chép nguyên file, không lỗi):

  ```powershell
  scp "$env:USERPROFILE\.ssh\id_ed25519.pub" vps:/root/.ssh/authorized_keys
  ```

  Rồi trên VPS: `chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys`. SSH cũng từ chối key nếu thư mục `.ssh` mở quyền quá rộng.

**A6. `notepad $env:USERPROFILE\.ssh\config` báo lỗi**

Thường do đường dẫn không bọc ngoặc kép, hoặc file/thư mục chưa tồn tại. Bỏ notepad, ghi thẳng bằng PowerShell (xem mục 3.2, Bước 3.2.6). Nếu vẫn muốn dùng editor: `notepad "$env:USERPROFILE\.ssh\config"` (có ngoặc kép, và file phải tồn tại trước).

**A7. `curl : ... Invoke-WebRequest` trên PowerShell**

Trong PowerShell, `curl` là alias của `Invoke-WebRequest`. Phải gõ đủ đuôi **`curl.exe`**.

**A8. `curl.exe http://45.119.87.220:80` trả `503` + `X-Squid-Error: ERR_CONNECT_FAIL 111`**

Đây **không phải lỗi**: proxy đã tới được VPS nhưng VPS chưa có service nào nghe ở cổng đó (lỗi 111 = connection refused). Bình thường khi VPS còn trống — sau khi deploy stack thì hết.

**A9. `ssh` / `ssh-keygen` không nhận lệnh trên Windows**

Chưa cài OpenSSH Client: `Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0`.

**A10. `sslh` không khởi động (`exit-code`, `Address already in use`)**

`sslh` cần là tiến trình **duy nhất** giữ cổng 443. Chẩn đoán: chạy tay `sudo /usr/sbin/sslh --foreground --listen 0.0.0.0:443 --ssh 127.0.0.1:22 --tls 127.0.0.1:8443` để thấy lỗi thật; `sudo ss -tlnp | grep ':443'` xem ai đang giữ cổng.

Nếu `sshd` vẫn giữ 443: `sshd` **cộng dồn** mọi dòng `Port` ở tất cả file cấu hình — tạo drop-in `Port 22` không xóa được `Port 443` đã lỡ thêm vào file chính. Tìm và xóa:

```bash
sudo grep -rnE "^Port " /etc/ssh/sshd_config /etc/ssh/sshd_config.d/
sudo sed -i '/^Port 443/d' /etc/ssh/sshd_config
sudo sshd -t && sudo systemctl restart ssh
sudo sshd -T | grep -i '^port '
```

Sau khi cổng 443 đã trống, khởi động lại `sslh` (phải `reset-failed` vì systemd đã khóa do restart quá nhanh):

```bash
sudo systemctl reset-failed sslh-mux
sudo systemctl start sslh-mux
sudo systemctl status sslh-mux --no-pager
```

**A11. Bị khóa ngoài VPS — không vào được bằng cách nào**

Vì bạn không có Web Console, đây là tình huống nguy hiểm nhất. Xử lý theo thứ tự ưu tiên:

- Còn **một phiên SSH đang mở** (Quy tắc 1, mục 3.0) → dùng ngay phiên đó để hoàn tác thay đổi vừa gây lỗi.
- Đã **đặt lệnh tự khôi phục** (`systemd-run`, Quy tắc 3) → đợi đủ thời gian hẹn (5 phút), nó tự revert, rồi `ssh vps` lại.
- **Không còn cả hai** → liên hệ nhà cung cấp VPS xin rescue mode; xấu nhất là cài lại VPS (mất dữ liệu — vì vậy backup ở Giai đoạn 6 rất quan trọng).

Phòng hơn chữa: luôn áp dụng đủ 4 quy tắc ở mục 3.0 trước mọi thay đổi SSH/`ufw`/`sslh`.

### Nhóm B — Stack, MCP, hệ thống

**B1. MCP server `disconnected` trong Claude Code**

`/mcp logs mem0`. Lỗi phổ biến:

- `Connection refused` / timeout: test lại `curl.exe https://claude.hangocthanh.io.vn/qdrant/collections -H "api-key: <token>"`.
- `401 Unauthorized`: biến `QDRANT_API_KEY` trong `claude mcp add` phải đúng bằng `MCP_BEARER_TOKEN` trên VPS.
- `OpenAI API key invalid`: kiểm tra key đúng dạng `sk-...` và còn credit.
- `Module not found`: chạy lại `uvx --from git+...` để cài lại dependencies.

**B2. Caddy không lấy được chứng chỉ TLS**

```bash
docker compose logs caddy
```

- `no such host`: DNS `claude.hangocthanh.io.vn` chưa propagate — đợi 5–10 phút, kiểm tra `dig +short claude.hangocthanh.io.vn`.
- `port 80 in use`: có service khác chiếm cổng 80 — `sudo ss -tlnp | grep :80`, tắt nó.
- Người dùng proxy: nhớ Caddy map `8443:443` và `sslh` đang chạy; cổng 80 vẫn phải để Caddy dùng cho ACME challenge.

**B3. Memory không được lưu**

Mở permission settings của Claude Code, bảo đảm `mem0` server được phép dùng tools. Vài phiên đầu có thể phải yêu cầu rõ "hãy nhớ rằng...".

**B4. Tốc độ chậm**

Qdrant query >500ms → VPS yếu, nâng RAM 8GB. Latency client→VPS >300ms → chọn datacenter gần VN hơn (Singapore).

**B5. `sudo` không chạy được với user `thanh`**

Hai triệu chứng khác nhau nhưng cùng một gốc — `thanh` chưa được cấp quyền sudo đầy đủ (hay gặp khi user `thanh` được tạo thiếu bước ở Giai đoạn 1 Bước 1.1):

- `thanh is not in the sudoers file` → `thanh` chưa thuộc nhóm `sudo`.
- `sudo` hỏi password rồi báo `incorrect password attempt` → `thanh` đã ở nhóm `sudo` nhưng thiếu file NOPASSWD, mà `thanh` lại không có password.

Cả hai phải sửa bằng quyền **root**: chạy `su -` (nhập password root), hoặc `ssh vps-root` (chỉ khi chưa hardening — root login vẫn còn). Khi đã là root:

```bash
usermod -aG sudo thanh
echo "thanh ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/thanh
chown root:root /etc/sudoers.d/thanh
chmod 440 /etc/sudoers.d/thanh
visudo -c
exit
```

`visudo -c` phải báo `parsed OK`. Kiểm tra (quay lại là `thanh`): `sudo -n true && echo "SUDO OK"`.

> Lưu ý: file trong `/etc/sudoers.d/` phải quyền **440**, chủ **root**, và tên **không có dấu chấm** — sai một trong số đó thì `sudo` bỏ qua file. File NOPASSWD cấp quyền theo tên user nên có hiệu lực ngay, không cần đăng nhập lại; còn `usermod -aG sudo` cấp theo nhóm, chỉ ăn ở lần đăng nhập mới.

**B6. `docker compose` báo `no configuration file provided: not found`**

Bạn đang chạy lệnh ở sai thư mục — `docker compose` tìm file `docker-compose.yml` trong thư mục hiện tại. Mọi lệnh `docker compose` phải chạy sau khi vào đúng thư mục stack:

```bash
cd ~/memory-stack
docker compose ps
```

**B7. Caddy không khởi động được**

`docker compose ps` thiếu `memory-caddy`, hoặc `docker compose up` báo lỗi. Hai nguyên nhân hay gặp:

*1) `Caddyfile` bị tạo thành thư mục.* Nếu `docker compose up` chạy khi file `Caddyfile` chưa tồn tại, Docker tự tạo một **thư mục** rỗng trùng tên → Caddy mở "file" thấy là thư mục → thoát ngay. Kiểm tra `ls -la ~/memory-stack/Caddyfile`; nếu là `drwx...`:

```bash
docker compose down
rm -rf ~/memory-stack/Caddyfile
# tạo lại Caddyfile bằng heredoc ở Bước 2.3, rồi:
docker compose up -d
```

*2) Cổng bị chiếm — `failed to bind host port ... address already in use`.* Caddy cần cổng 80 (chuyển hướng HTTP + ACME challenge) và 8443. Tìm tiến trình đang giữ:

```bash
sudo ss -tlnp | grep -E ':80 |:8443 '
```

- Thấy `apache2` / `nginx` → web server khác đang chạy: `sudo systemctl disable --now apache2` (hoặc `nginx`).
- Thấy `docker-proxy` → container cũ còn giữ cổng: `docker ps -a` tìm rồi `docker rm -f <tên>`.

Giải phóng cổng xong, chạy lại `docker compose up -d` trong `~/memory-stack`.

**B8. Caddy không lấy được chứng chỉ TLS — `lookup ... on 127.0.0.53:53 ... connection refused`**

Caddy chạy bình thường nhưng log lặp lại lỗi này khi `obtaining certificate`. Nguyên nhân: container Docker không phân giải được tên miền — nó nhận nameserver `127.0.0.53` (resolver cục bộ của host), nhưng địa chỉ đó không tồn tại bên trong container nên mọi truy vấn DNS bị `connection refused`. Caddy vì thế không gọi được máy chủ ACME của Let's Encrypt.

Khắc phục — đặt DNS cố định cho Docker daemon:

```bash
sudo tee /etc/docker/daemon.json > /dev/null << 'EOF'
{
  "dns": ["8.8.8.8", "1.1.1.1"]
}
EOF
sudo systemctl restart docker
cd ~/memory-stack && docker compose down && docker compose up -d
```

Đợi ~30 giây, kiểm tra lại:

```bash
docker compose logs caddy | grep -i certificate
```

Phải thấy `certificate obtained successfully`.

**B9. Caddy không lấy được chứng chỉ — `network is unreachable` (mạng Docker tùy chỉnh không ra internet)**

Triệu chứng: log Caddy lặp lại `lookup acme-v02.api.letsencrypt.org on 1.1.1.1:53 ... connect: network is unreachable` — thường xuất hiện *sau khi* đã sửa lỗi DNS ở B8. Container chạy nhưng không ra được internet.

Chẩn đoán — so sánh mạng bridge mặc định với mạng tùy chỉnh của stack:

```bash
docker run --rm alpine ping -c2 1.1.1.1
docker run --rm --network memory-stack_memnet alpine ping -c2 1.1.1.1
```

Nếu lệnh 1 OK còn lệnh 2 báo `network is unreachable` → đúng thủ phạm: mạng tùy chỉnh `memnet` (mạng của stack) bị chặn, không phải toàn bộ Docker. (Lệnh 1 OK cũng cho biết `ip_forward` đã bật — không cần đụng tới.)

Nguyên nhân: `ufw` bật với `DEFAULT_FORWARD_POLICY="DROP"` chặn lưu lượng đi-qua (FORWARD); vì thứ tự nạp quy tắc iptables, mạng `docker0` mặc định thường vẫn lọt, còn mạng Docker tạo sau (qua `docker compose`) bị chính sách DROP nuốt mất.

Khắc phục:

```bash
sudo sed -i 's/^DEFAULT_FORWARD_POLICY=.*/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw
sudo ufw reload
sudo systemctl restart docker
cd ~/memory-stack && docker compose down && docker compose up -d
```

Kiểm tra: `docker run --rm --network memory-stack_memnet alpine ping -c2 1.1.1.1` ping được, và `docker compose logs caddy | grep -i certificate` hiện `certificate obtained successfully`.

**B10. `/qdrant/*` trả về `Invalid API key or JWT`**

Triệu chứng: `curl` tới `/qdrant/collections` kèm header `api-key` đúng nhưng nhận `Invalid API key or JWT` thay vì JSON danh sách collection.

Đây là tín hiệu *gần xong*: HTTPS hoạt động, Caddy đã xác thực token thành công (nếu token sai, Caddy trả `Unauthorized` chứ không chuyển tiếp), request đã tới được Qdrant. Lỗi nằm ở chỗ Caddy gửi `api-key` **rỗng** cho Qdrant.

Nguyên nhân: service `caddy` trong `docker-compose.yml` thiếu biến `QDRANT_API_KEY`. Caddyfile dùng `{env.QDRANT_API_KEY}` để chèn api-key vào request gửi Qdrant, nhưng biến đó không có trong môi trường container Caddy → giá trị rỗng → Qdrant từ chối.

Khắc phục: trong `docker-compose.yml`, mục `environment` của service `caddy` phải có **cả hai** dòng:

```yaml
    environment:
      MCP_BEARER_TOKEN: ${MCP_BEARER_TOKEN}
      QDRANT_API_KEY: ${QDRANT_API_KEY}
```

Sửa xong chạy lại `docker compose up -d` (trong `~/memory-stack`) để Caddy được tạo lại với biến mới.

**B11. Qdrant trả `401 Unauthorized` — lệch header xác thực**

Triệu chứng: `curl ... /qdrant/collections -H "api-key: <token>"` trả `401 Unauthorized`; mem0 không lưu/đọc được memory nên Qdrant trống (`"collections":[]`). Query trong Claude Code có thể "treo" lâu vì `qdrant-client` thử lại nhiều lần trước khi bỏ cuộc.

Nguyên nhân: mem0 (qua thư viện `qdrant-client`) gửi token bằng header **`api-key`**. Nếu Caddyfile cấu hình kiểm tra header `Authorization: Bearer` thì request của mem0 không khớp matcher → Caddy trả 401.

Khắc phục: trong `Caddyfile`, dòng matcher phải là `@authorized header api-key "{env.MCP_BEARER_TOKEN}"` (đúng như tài liệu) — **không** dùng `Authorization "Bearer ..."`. Sửa xong: `docker compose restart caddy`.

> **Phân biệt `restart` và `up -d`:** sửa `Caddyfile` thì `docker compose restart caddy` là đủ (file được mount, container đọc lại khi start). Nhưng sửa **biến môi trường** trong `docker-compose.yml` thì `restart` **không** áp dụng — phải `docker compose up -d` để *tạo lại* container. Nếu Qdrant báo `Must provide an API key` dù Caddyfile đã đúng → caddy thiếu biến `QDRANT_API_KEY`, xem B10 và chạy `docker compose up -d`.

**B12. mem0 báo `httpx.ProxyError: 403 Forbidden` khi gọi Qdrant**

Triệu chứng: tool `add_memory` / `search_memories` treo lâu; chạy tay `qdrant-client` thấy traceback kết thúc bằng `httpx.ProxyError: 403 Forbidden` (hoặc `ResponseHandlingException: 403 Forbidden`).

Nguyên nhân: thư viện `qdrant-client` khi nhận `url` **không ghi rõ cổng** sẽ mặc định dùng cổng **6333** (cổng REST của Qdrant), không phải 443. Đi qua proxy công ty, mà proxy chỉ cho CONNECT tới cổng 443 → nó trả `403 Forbidden` cho cổng 6333 (đúng kiểu nó đã chặn cổng 22, 9999).

Khắc phục: biến `MEM0_QDRANT_URL` phải **ghi rõ `:443`** — `https://claude.hangocthanh.io.vn:443/qdrant` (không phải `https://claude.hangocthanh.io.vn/qdrant`). Sửa trong lệnh `claude mcp add`: `claude mcp remove mem0` rồi `add` lại, sau đó khởi động lại VS Code.

**B13. MCP server treo — luôn nói chuyện với `localhost` thay vì VPS**

Triệu chứng: `add_memory` / `search_memories` treo nhiều phút; mọi cấu hình trên VPS đều đúng (curl ra `HTTP 200`) nhưng MCP server vẫn không lưu/đọc được gì.

Nguyên nhân: lệnh `claude mcp add` đặt **sai tên biến môi trường**. Server `mem0-mcp-selfhosted` chỉ đọc biến có tiền tố **`MEM0_`**. Nếu đặt `QDRANT_URL` (thiếu tiền tố `MEM0_`), server **lặng lẽ bỏ qua** và dùng mặc định `MEM0_QDRANT_URL=http://localhost:6333` → nó cố kết nối Qdrant trên *chính máy client*, không phải VPS → treo vô tận.

Khắc phục: dùng đúng tên biến `MEM0_*` như bảng ở Bước 3.3 (`MEM0_QDRANT_URL`, `MEM0_QDRANT_API_KEY`, `MEM0_EMBED_PROVIDER`...). Kiểm tra bằng `claude mcp get mem0` — phải thấy đúng các biến `MEM0_*`. Đăng ký lại (`claude mcp remove mem0` + `add`) rồi khởi động lại VS Code.

Hai điểm dễ sai khác: server dùng **Claude (OAT của Claude Max)** làm LLM — tự động, không cần OpenAI key cho LLM. Embedder **mặc định là Ollama**; nếu máy không chạy Ollama thì bắt buộc đặt `MEM0_EMBED_PROVIDER=openai` (kèm `MEM0_EMBED_MODEL` và `MEM0_EMBED_DIMS`).

**B14. `add_memory` báo `LLM extraction failed: Error code: 429 - rate_limit_error`**

Triệu chứng: log mem0 ghi `Anthropic call ... 429 Too Many Requests` 2-3 lần liên tiếp rồi `LLM extraction failed`. Tool `add_memory` báo "xong" sau ~8 giây nhưng **không lưu được ghi nhớ nào** (đây là "xong trong thất bại").

Nguyên nhân: model mặc định của server là `claude-opus-4-6` — model nặng nhất của Claude. Mỗi `add_memory` cần 2 lần gọi LLM tuần tự (trích xuất fact + quyết định ADD/UPDATE), mỗi call Opus tiêu nhiều "budget" tốc độ của gói Max. Khi mem0 gọi nền song song với phiên Claude Code đang hoạt động trên **cùng tài khoản Max**, hai bên cạnh tranh hạn mức → 429 ngay. SDK Anthropic mặc định thử lại 2 lần trong ~2 giây — không đủ để vượt cửa sổ rate-limit của Anthropic, nên thất bại hoàn toàn.

Khắc phục: **bắt buộc đặt `MEM0_LLM_MODEL=claude-haiku-4-5-20251001`** (đã có sẵn trong lệnh ở Bước 4.1). Haiku 4.5 có hạn mức tốc độ cao hơn Opus 3-5 lần, mỗi call nhẹ hơn 5-10 lần, mà chất lượng trích xuất fact "Thanh tên là Thanh, dùng Claude Max" vẫn dư sức. Đổi xong cần `claude mcp remove mem0` + `add` lại + khởi động lại VS Code.

**B15. `add_memory` lần đầu mất 60-120 giây, các lần sau nhanh**

Triệu chứng: lần `add_memory` đầu tiên sau khi VS Code khởi động lâu bất thường (1-2 phút). Lần thứ 2 trở đi nhanh (vài giây). Log có dòng `[TIMING] _ensure_memory() mat 114.27s`.

Nguyên nhân: đây **không phải bug**. Đó là chi phí khởi tạo một lần của thư viện `mem0ai` với extras `[graph,llms]` — import langchain + langchain-neo4j + neo4j + qdrant-client + openai + anthropic (tổng ~80-100 nghìn dòng Python), kiểm tra/tạo collection Qdrant + 4 index. mem0ai dùng pattern *lazy init*: trì hoãn khởi tạo cho tới lần gọi tool đầu tiên thay vì khi server start.

Khắc phục: không cần. Nếu khó chịu, đừng restart VS Code thường xuyên — server MCP sống cùng phiên VS Code, chỉ phải trả 114s này một lần mỗi khi VS Code khởi động lại.

**B16. VS Code không thấy `mem0` dù `claude mcp list` báo `☑ Connected`**

Triệu chứng: chạy `claude mcp list` ở terminal thấy `mem0 ... ☑ Connected`, nhưng trong VS Code không gọi được tool memory; mở mục "MCP servers" trong Settings của VS Code cũng không thấy.

Nguyên nhân 1 — **nhầm vị trí kiểm tra**. Tính năng "MCP servers" của VS Code (trong Settings) là hệ thống MCP **riêng dành cho GitHub Copilot**, hoàn toàn tách biệt với Claude Code. `claude mcp add` ghi vào file cấu hình của *Claude Code* (`~/.claude.json` hoặc `C:\Users\<user>\.claude.json`), **không** ghi vào cấu hình MCP của VS Code. Xem MCP của Claude Code phải mở panel chat Claude Code → gõ `/mcp`.

Nguyên nhân 2 — **scope sai**. Nếu lệnh `claude mcp add` thiếu `--scope user`, Claude Code rơi vào scope mặc định `local`, gắn vào đúng thư mục bạn đang đứng khi chạy lệnh (ví dụ `C:\Users\thanhhn5`). VS Code mở project ở thư mục khác → không thấy. Bằng chứng nhận biết: output có dòng `Added ... to **local** config` và `File modified: .claude.json [project: C:\Users\thanhhn5]`.

Khắc phục: `claude mcp remove mem0`, rồi `add` lại với `--scope user` (đã có trong lệnh Bước 4.1). Đóng hẳn VS Code (thoát ứng dụng, không phải "Reload Window") rồi mở lại. Verify ở **panel Claude Code → gõ `/mcp`**, không phải Settings VS Code.

**B17. `add_memory` báo OK trong vài giây nhưng trả `{'results': []}`, log không có Anthropic call nào**

Triệu chứng: tool `add_memory` không lỗi, chạy nhanh (~8-10s), nhưng `mem.add()` trả `{'results': []}`. Trong log không thấy dòng `[TIMING] >>> Anthropic call` nào. Sau đó `search_memories` cũng lỗi `ValueError: Top-level entity parameters frozenset({'user_id'}) are not supported in search()`. Init nặng nề bất thường (5-15 phút lần đầu) kèm warning `mem0ai==2.0.2 does not have an extra named 'graph'`.

Nguyên nhân: **mem0ai bị uv auto-upgrade lên 2.x**. `pyproject.toml` của bản gốc chỉ ghi `"mem0ai[graph,llms]>=1.0.3"` không có upper bound → uv install bản mới nhất là 2.0.2. mem0ai 2.x phá API hoàn toàn so với 1.x: (1) bỏ extras `graph` và `llms`, (2) `add()` không tự gọi LLM cho extraction mặc định nữa → return `[]`, (3) `search()` đòi `user_id` trong `filters={}` chứ không nhận top-level, (4) kéo theo `google-cloud-aiplatform` nặng khiến init lần đầu 10+ phút. **3 triệu chứng tưởng riêng biệt thực ra cùng 1 root cause.**

Khắc phục: pin dependency trong `pyproject.toml` của bản build customize (xem mục 5.5):

```toml
dependencies = [
    "mcp[cli]>=1.23.0",
    "mem0ai[graph,llms]>=1.0.3,<2.0",   # ← thêm upper bound
    "anthropic>=0.77.0",
    ...
]
```

Sau đó **bump version** (xem B18), `uv cache prune`, rồi `claude mcp remove + add` lại. mem0ai 1.x sẽ được cài đặt, init giảm về <1 phút, `add()` gọi đầy đủ Anthropic LLM, `search/get_all` chấp nhận `user_id` top-level. **Lưu ý quan trọng:** mem0ai 1.x API ngược 2.x — `search(user_id=...)` top-level, KHÔNG nhét vào filters.

**B18. uvx vẫn chạy code cũ dù `--reinstall-package` đã có trong lệnh**

Triệu chứng: sửa source code mới, restart VS Code, log không có dòng log mới (vd dòng `[ACTION] Prewarm thread spawned` không xuất hiện dù file source có). Lệnh `claude mcp add` đã có `--reinstall-package mem0-mcp-selfhosted` nhưng vô tác dụng. Đôi khi uvx báo warning: `Tools cannot be reinstalled via uvx; use uv tool upgrade --all --reinstall ...`.

Nguyên nhân: `uvx` (= `uv tool run`) cache wheel theo **(package name + version)**. `--reinstall-package` chỉ buộc reinstall **từ wheel có sẵn trong cache**, không buộc rebuild wheel mới từ source. Khi source thay đổi nhưng version trong `pyproject.toml` không đổi → uvx nghĩ "cùng version 0.3.2, dùng cache cũ".

Khắc phục: **bump version mỗi lần thay đổi source nội bộ**. Sửa cả 2 nơi:

```toml
# pyproject.toml
version = "0.3.5"    # tăng từ 0.3.4 hoặc bất kỳ số nào lớn hơn
```

```python
# src/mem0_mcp_selfhosted/__init__.py
__version__ = "0.3.5"
```

Lưu ý PEP 440: dùng `0.3.5` (chuẩn) hoặc `0.3.4.dev1`, **không** dùng `0.3.4-debug.1` (dấu `-` giữa các phần không hợp PEP 440 → wheel metadata parse fail).

Nếu cache đã hỏng (vd lần trước build dở do version invalid), chạy `uv cache prune` một lần để dọn sạch, sau đó add MCP/run CLI bình thường.

**B19. MCP server treo ngay sau dòng log đầu, VS Code "Connecting..." vô tận**

Triệu chứng: trong panel `MCP servers` của VS Code thấy mem0 trạng thái `Connecting...` không bao giờ chuyển sang `Connected`. Log file dừng ngay sau dòng `[TIMING] ===== add_memory START =====` hoặc `[ACTION] register_providers: import LlmFactory START` mà không có dòng tiếp theo. Sau ~60 giây log file xuất hiện server start mới (PID khác) — Claude Code respawn nhưng vẫn lặp lại pattern. Vòng lặp này không bao giờ thoát.

Nguyên nhân: **stderr pipe đầy + import mem0ai chậm trên Windows có antivirus**. (1) `logging.basicConfig` mặc định ghi mọi log INFO ra stderr; Claude Code đọc stdout (cho MCP protocol) nhưng có thể đọc stderr chậm hơn → buffer stderr (~64KB) đầy → `write(stderr)` block → `logger.info` tiếp theo treo. (2) Import mem0ai lần đầu trên máy có antivirus công ty mất 2-5 phút (mỗi file Python phải scan); Claude Code timeout sớm hơn → kill server và spawn lại → lặp.

Khắc phục: cả 2 phần trong bản build customize (xem mục 5.5):

1. **stderr handler ở WARNING-only**: tách log INFO chỉ ghi vào file `MEM0_LOG_FILE`, stderr chỉ nhận WARNING+. Pipe stderr không bao giờ đầy.
2. **Prewarm thread**: spawn background thread gọi `_ensure_memory()` ngay khi server start, song song với MCP handshake. Main thread handshake nhanh → VS Code không timeout → không respawn. Khi tool call đến, init có thể đã xong (hoặc đang chạy) → tự nhiên đợi qua lock.

Sau khi 2 fix này được áp dụng + bump version + redeploy, lần đầu vẫn mất 1-5 phút prewarm (cost cố hữu), nhưng VS Code **không kill server giữa chừng**. Log có dòng `[CUSTOM-DEBUG] Prewarm thread spawned (daemon=True)` ngay khi server start là proof bản mới đang chạy.

**B20. `add_memory` trả `{'results': []}` nhưng KHÔNG phải lỗi**

Triệu chứng: gọi `add_memory` với một câu, log có đủ `[TIMING] >>> Anthropic call #1` (extract) + `#2` (decide), nhưng cuối cùng response là `{"results": []}`. Claude trả lời user kiểu "đã lưu nhưng không có results cụ thể".

Nguyên nhân: **deduplication của mem0**. Khi LLM (Haiku) so sánh fact mới trích với memory đã tồn tại và quyết định mọi fact đều **đã có sẵn**, mỗi fact được đánh dấu `event: NONE` → "NOOP for Memory". mem0 1.x lọc bỏ event=NONE khỏi response → trả `results: []`. Đây là tính năng **không lưu trùng**, không phải lỗi. Log có các dòng `NOOP for Memory` xác nhận điều này (ví dụ:
```
{'id': '0', 'text': 'Tên là Thanh', 'event': 'NONE'}
NOOP for Memory.
```
).

Khắc phục: không cần. Nếu muốn xác nhận memory đã lưu, gọi `get_memories` → sẽ thấy các fact trùng vẫn còn nguyên trong Qdrant với UUID. Hướng dẫn cho Claude diễn giải đúng response này: thêm vào CLAUDE.md (xem mục 5.7) đoạn *"`{"results": []}` from `add_memory` means the fact already exists (deduplication, event=NONE), not a failure"*.

**B21. Claude Code edit file `.md` thay vì gọi tool `add_memory`**

Triệu chứng: user nói *"lưu giúp tôi: tôi tên Thanh"*, Claude Code không gọi mem0 tool mà tự đi đọc/sửa file kiểu `C:\Users\<user>\.claude\projects\<project>\memory\user_profile.md`. Log mem0 không có `CallToolRequest` nào.

Nguyên nhân: **Claude Code có 2 lớp memory độc lập** và mặc định ưu tiên built-in cho personal facts:

| Lớp | Cơ chế | Default cho personal facts |
|---|---|---|
| Built-in auto-memory | Edit `.md` trong `.claude/projects/<project>/memory/` | ✅ **Ưu tiên** |
| mem0 MCP | Gọi tool `add_memory` qua MCP | ❌ Chỉ khi user explicit nêu tên tool, hoặc có CLAUDE.md override |

Khi user nói "lưu" mà không nêu tool name, Claude Code chọn cách dễ nhất (edit file local), không gọi mem0.

Khắc phục: tạo file `CLAUDE.md` ở scope user-global (`C:\Users\<user>\.claude\CLAUDE.md`) với instruction *"luôn dùng tool `mcp__mem0__add_memory` cho personal facts thay vì built-in memory"* — chi tiết ở mục 5.7. Sau khi tạo file, restart Claude Code panel, Claude sẽ tuân thủ ngay.

**B22. PowerShell báo `running scripts is disabled on this system` khi chạy `.ps1`**

Triệu chứng: gõ `. "$env:USERPROFILE\scripts\archive-env.ps1"` báo `File ... cannot be loaded because running scripts is disabled on this system. ... + FullyQualifiedErrorId : UnauthorizedAccess`. Sau đó các lệnh Python phụ thuộc env vars báo `KeyError: 'ARCHIVE_URL'`.

Nguyên nhân: Windows mặc định set Execution Policy là **`Restricted`** — chặn HẾT file `.ps1` để chống malware. Khi `archive-env.ps1` không load được, env vars (`ARCHIVE_URL`, `ARCHIVE_AUTH_TOKEN`, `HTTPS_PROXY`, ...) không được set → Python script đọc `os.environ["ARCHIVE_URL"]` crash với `KeyError`.

Khắc phục: đổi Execution Policy sang `RemoteSigned` cho user hiện tại (không cần admin, làm 1 lần dùng mãi):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
# Gõ Y khi hỏi xác nhận

# Verify
Get-ExecutionPolicy -Scope CurrentUser
# Phải trả: RemoteSigned
```

`RemoteSigned` cho phép script local Thanh tự tạo (như `archive-env.ps1`), nhưng vẫn chặn script tải từ Internet không có chữ ký số — cân bằng giữa tiện dụng và an toàn. Đây là policy mặc định mọi developer Windows dùng.

**B23. `archive-upload.py` treo timeout dù `$env:HTTPS_PROXY` đã set**

Triệu chứng: PowerShell đã `. archive-env.ps1` thành công, `echo $env:HTTPS_PROXY` ra đúng `http://10.121.127.204:3128`, nhưng chạy `python archive-upload.py` treo ~30-60s rồi báo `urllib.error.URLError: <urlopen error [Errno timed out]>`.

Nguyên nhân: thư viện `urllib` trong Python — trên **Linux/Mac** tự đọc biến `HTTPS_PROXY`/`HTTP_PROXY` từ env, nhưng **trên Windows KHÔNG tự đọc** (vì Windows dùng WinHTTP settings riêng). Script `archive-upload.py` của plan giả định Linux/Mac → fail trên Windows.

Khắc phục: thêm setup `ProxyHandler` ngay đầu script `archive-upload.py` (trước mọi `urllib.request` call):

```python
import os, urllib.request

# Setup proxy cho urllib (Windows không tự đọc HTTPS_PROXY env)
proxy_url = os.environ.get("HTTPS_PROXY")
if proxy_url:
    proxy = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(proxy)
    urllib.request.install_opener(opener)
```

Đoạn này tương thích cả Linux/Mac (env không set thì skip). Khi có set, mọi `urllib.request.urlopen()` sau đó tự động qua proxy.

**B24. `httpx.Client(proxies={...})` báo `TypeError: got an unexpected keyword argument 'proxies'`**

Triệu chứng: chạy test Python `httpx.Client(proxies={'http://': ..., 'https://': ...})` báo `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`. Thường gặp khi follow guide cũ hoặc tutorial.

Nguyên nhân: **httpx 0.28+ đã breaking change**: bỏ tham số `proxies` (số nhiều), đổi sang `proxy` (số ít) — cú pháp đơn giản hơn, đồng bộ với `requests` library. Plan gốc viết khi httpx ≤0.27.

Khắc phục: dùng API mới:

```python
# Sai (httpx ≤0.27):
httpx.Client(proxies={'http://': 'http://...', 'https://': 'http://...'})

# Đúng (httpx 0.28+):
# Option 1: pass proxy đơn lẻ
httpx.Client(proxy='http://10.121.127.204:3128')

# Option 2: để httpx tự đọc HTTPS_PROXY env (đơn giản nhất)
httpx.Client()
```

`archive-mcp.py` ở Bước 7.7 dùng pattern Option 2 — không pass `proxies`, dựa vào env. Đảm bảo lệnh `claude mcp add` có `--env HTTPS_PROXY=...` thì sẽ hoạt động.

**B25. Lỗi PowerShell `Missing '(' after 'if' in if statement` khi paste code Python**

Triệu chứng: paste nguyên đoạn Python (vd nội dung `archive-upload.py`) vào prompt PowerShell, báo hàng loạt lỗi `Missing '(' after 'if'`, `Missing opening '(' after keyword 'for'`, `The Data section is missing its statement block`, ...

Nguyên nhân: PowerShell prompt parse mọi input như **lệnh PowerShell**, không phải Python. Cú pháp Python (`if x: y`, `for a in b:`, ...) khác PowerShell (`if (x) { y }`, `foreach ($a in $b) { }`).

Khắc phục: KHÔNG paste code Python vào prompt. Tạo file `.py` bằng PowerShell **here-string** (cách clean nhất, không cần editor):

```powershell
$pyScript = @'
import os, sys
def main():
    print("hello")
if __name__ == "__main__":
    main()
'@
Set-Content -Path "$env:USERPROFILE\scripts\my-script.py" -Value $pyScript -Encoding utf8
```

Here-string `@'...'@` (single-quoted) **không interpolate** PowerShell variables → code Python giữ nguyên 100%. `-Encoding utf8` đảm bảo Python parse được.

Khi cần inject biến PowerShell vào (vd token từ `Read-Host`), dùng double-quoted `@"..."@` và escape các `$` không muốn interpolate bằng backtick: `` `$env:VAR ``.

**B26. Pipeline "chạy thành công" nhưng Claude Code thấy `[]` rỗng — Typo `USER_ID`**

Triệu chứng: `archive-upload.py` chạy báo `Done. 50 new sessions uploaded.`. Verify trên Neon SQL Editor: `SELECT * FROM chat_sessions` có 50 rows. Nhưng trong Claude Code gõ "Liệt kê 5 session gần đây", tool `list_old_sessions` trả `[]` rỗng.

Nguyên nhân: file `archive-env.ps1` có typo `USER_ID = "thann"` (4 chữ) thay vì `"thanh"` (5 chữ). Script upload lên với `user_id=thann`, nhưng MCP archive query với `user_id=thanh` (mặc định trong `archive-mcp.py`) → không match → rỗng.

Khắc phục: verify TỪNG ký tự `USER_ID` ở 3 nơi PHẢI khớp:

1. `archive-env.ps1` trên client: `$env:USER_ID = "thanh"`
2. Lệnh `claude mcp add archive` ở Bước 7.7: `--env USER_ID=thanh`
3. Default trong `archive-mcp.py`: `USER_ID = os.environ.get("USER_ID", "thanh")`

Nếu đã lỡ upload với `user_id` sai → SQL trên Neon để dọn:
```sql
DELETE FROM chat_sessions WHERE user_id = 'thann';
```
Rồi xóa state file local để upload lại:
```powershell
Remove-Item "$env:USERPROFILE\.cache\claude-archive-state.json"
python "$env:USERPROFILE\scripts\archive-upload.py"
```

**B27. `docker compose down` còn lại container "orphan" sau khi xóa service khỏi YAML**

Triệu chứng: sau khi xóa block `postgres:` khỏi `docker-compose.yml` và chạy `docker compose down`, output báo `! Network memory-stack_memnet Resource is still in use`. Chạy `docker compose ps` vẫn thấy `memory-postgres ... Up 22 hours` dù đã `up -d` lại. Output `WARN[0020] Found orphan containers ([memory-postgres])`.

Nguyên nhân: Docker Compose **không tự xóa** container của service đã bị xóa khỏi YAML — chỉ xóa các service hiện có. `memory-postgres` được tạo từ YAML cũ trở thành "orphan" (mồ côi) — vẫn chạy, vẫn ngốn RAM, vẫn giữ network reference. Đây là **feature**, không phải bug — Compose bảo thủ, tránh user lỡ xóa container quan trọng.

Khắc phục: dùng flag `--remove-orphans`:

```bash
cd ~/memory-stack

# Backup data của service sắp xóa (phòng hờ rollback)
tar czf ~/postgres-old-backup-$(date +%F).tar.gz -C ~/memory-stack data/pg

# Down kèm xóa orphan
docker compose down --remove-orphans

# Up lại stack mới (đã không còn postgres)
docker compose up -d
docker compose ps    # Phải KHÔNG còn memory-postgres
free -h              # Verify RAM giảm
```

Sau 1-2 tuần verify ổn → xóa data cũ:
```bash
sudo rm -rf ~/memory-stack/data/pg
```

**B28. `Register-ScheduledTask` báo `-Description is not recognized` / `Action is null` / `parameter Argument cannot be found`**

Triệu chứng: chạy block Bước 7.6 (Windows — Task Scheduler) báo hàng loạt lỗi:
- `The term '-Description' is not recognized as the name of a cmdlet`
- `Register-ScheduledTask : Cannot validate argument on parameter 'Action'. The argument is null or empty`
- `New-ScheduledTaskTrigger : A parameter cannot be found that matches parameter name 'Argument'`

Nguyên nhân: backtick `` ` `` cuối dòng (line continuation) bị **hỏng** khi paste — thường do **trailing space sau backtick** hoặc terminal strip ký tự. PowerShell coi mỗi dòng là 1 lệnh riêng → `$action` rỗng vì không có `-Argument`, `$trigger` không có `-RepetitionInterval`, `Register-ScheduledTask` nhận `$action = $null` + lệnh `-Description` lạc loài.

Khắc phục: viết mỗi cmdlet thành MỘT DÒNG, KHÔNG dùng backtick:

```powershell
Unregister-ScheduledTask -TaskName "archive-upload" -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$env:USERPROFILE\scripts\archive-upload-run.ps1`""

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1)

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -StartWhenAvailable

Register-ScheduledTask -TaskName "archive-upload" -Action $action -Trigger $trigger -Settings $settings -Description "Upload Claude Code transcripts moi gio"
```

Verify từng biến trước khi register: `$action`, `$trigger`, `$settings` phải in object (không null).

**B29. Task Scheduler chạy mỗi giờ hiện cửa sổ PowerShell đen ~2-5s gây giật mình**

Triệu chứng: task `archive-upload` chạy đúng giờ, nhưng mỗi lần trigger lại popup cửa sổ console đen 2-5 giây trong giờ làm việc → khó chịu.

Nguyên nhân: `Register-ScheduledTask` mặc định không ẩn cửa sổ. PowerShell launcher cần show console trước khi script load.

Khắc phục: kết hợp 2 thứ:
1. `-WindowStyle Hidden` trong `-Argument` của `New-ScheduledTaskAction`
2. `-Hidden` trong `New-ScheduledTaskSettingsSet`

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$env:USERPROFILE\scripts\archive-upload-run.ps1`""
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -StartWhenAvailable
Register-ScheduledTask -TaskName "archive-upload" -Action $action -Trigger $trigger -Settings $settings -Description "..."
```

Sau khi setup `-Hidden`, task không thấy trong Task Scheduler GUI mặc định nữa. Phải bật **View → Show Hidden Tasks** để thấy.

**Không khuyến nghị** dùng SYSTEM user (`-Principal` với `-UserId SYSTEM`) để hoàn toàn ẩn — vì SYSTEM không có `$env:USERPROFILE` hợp lý, không đọc được file user-level như `archive-env.ps1` và `~/.claude/projects/`.

**B30. Claude Code không thấy MCP server mới đăng ký dù `claude mcp list` báo Connected**

Triệu chứng: chạy `claude mcp add ... archive` thành công, `claude mcp list` báo `archive ☑ Connected`, nhưng mở Claude Code panel gõ `/mcp` không thấy `archive`. Restart VS Code (`Ctrl+Shift+P` → `Reload Window`) vẫn không có.

Nguyên nhân: Claude Code extension cache MCP config khi VS Code **process** start. `Reload Window` chỉ reload extension JS, không restart process → Claude Code dùng cache cũ.

Khắc phục: **đóng hẳn VS Code** (File → Exit, hoặc Alt+F4, hoặc kill process từ Task Manager), rồi mở lại. Lúc đó Claude Code đọc lại config file và thấy `archive` MCP mới.

Nếu vẫn không thấy:
1. Verify scope đúng: `claude mcp get archive` phải báo scope `user` (không phải `local`)
2. Verify config file: `notepad "$env:USERPROFILE\.claude.json"` phải có entry `archive` trong `mcpServers`
3. Nếu là `local` scope → `claude mcp remove archive` + add lại với `--scope user` (chi tiết B16)

**B31. Claude Code lưu MCP tool response ra file `.txt` rồi mới đọc — chậm + lỗi PowerShell parse**

Triệu chứng: gọi tool `get_old_session` (hoặc tool MCP nào trả về response lớn), Claude Code không hiển thị thẳng kết quả — thay vào đó tạo file `C:\Users\<user>\.claude\projects\<project>\tool-results\mcp-archive-get_old_session-<id>.txt` rồi tự `Get-Content` để đọc. Có khi báo `Exit code 1` ở lần PowerShell script đầu tiên do encoding/parse error.

Nguyên nhân: đây **không phải bug**, là **context window management** của Claude Code. Khi MCP tool response vượt threshold (~10k tokens), Claude Code tự lưu ra disk để tránh đốt token oan. Sau đó Claude phải đọc file qua `Read` + `Get-Content` với offset/limit. Tool `get_old_session` trả full transcript 42 messages có thể tới 40-60k tokens → kích hoạt cơ chế này.

Khắc phục — tạo tool MCP "compact" trả về **summary + first/last 5 messages** thay vì full transcript. Sửa `archive-mcp.py`, thêm tool `get_session_summary`:

```python
Tool(name="get_session_summary",
     description="Get compact view: metadata + first/last 5 messages. USE THIS instead of get_old_session for most cases.",
     inputSchema={"type": "object",
                  "properties": {"session_id": {"type": "string"}},
                  "required": ["session_id"]}),
```

Trong `call_tool`, thêm xử lý:
```python
elif name == "get_session_summary":
    r = await c.get(f"{ARCHIVE_URL}/sessions/{args['session_id']}")
    data = r.json()
    transcript = data.get("transcript", [])
    compact = {
        "id": data["id"],
        "started_at": data["started_at"],
        "ended_at": data["ended_at"],
        "project_tag": data.get("project_tag"),
        "summary": data.get("summary"),
        "message_count": data["message_count"],
        "first_messages": transcript[:5],
        "last_messages": transcript[-5:] if len(transcript) > 5 else [],
    }
    return [TextContent(type="text", text=json.dumps(compact, ensure_ascii=False, indent=2))]
```

Đổi `description` của `get_old_session` thành: `"Fetch FULL transcript. WARNING: very large. Prefer get_session_summary for overview."` để Claude biết ưu tiên dùng `get_session_summary`.

Sau khi sửa: restart Claude Code (Exit, không Reload) → `/mcp` thấy archive có **4 tools** (thêm `get_session_summary`). Prompt "Tóm tắt session X" sẽ gọi `get_session_summary` → response ~3k tokens → KHÔNG cần lưu file. Chỉ khi user chủ động yêu cầu "xem chi tiết toàn bộ" thì Claude mới gọi `get_old_session` (vẫn lưu file vì response lớn — đúng pattern).

> **Pattern chung:** khi viết MCP tool, luôn có **2 versions** — `_summary` (compact, default) và `_full` (verbose, on-demand). LLM ưu tiên tool có `description` rõ ràng và size nhỏ.

**B32. `CREATE TABLE IF NOT EXISTS` không update schema khi table đã tồn tại — phải dùng `ALTER TABLE`**

Triệu chứng: chạy `CREATE TABLE IF NOT EXISTS` với schema mới (thêm column `workspace_path`, `position_in_session`, `created_at`) nhưng table đã tồn tại từ trước với schema cũ. API endpoint mới fail với `psycopg2.errors.UndefinedColumn: column "workspace_path" does not exist`. Query `SELECT column_name FROM information_schema.columns WHERE table_name = 'X'` cho thấy schema vẫn cũ.

Nguyên nhân: `CREATE TABLE IF NOT EXISTS` logic là:
1. Check table tồn tại?
2. Nếu **có** → SKIP toàn bộ statement, không touch gì
3. Nếu **không** → tạo mới với schema trong statement

→ `IF NOT EXISTS` chỉ bảo vệ khỏi error "table already exists", **không đồng bộ schema**. Đây là behavior chuẩn của Postgres, không phải bug.

Khắc phục: dùng `ALTER TABLE ADD COLUMN IF NOT EXISTS` cho từng column thiếu (idempotent — chạy nhiều lần OK):

```sql
ALTER TABLE compact_summaries ADD COLUMN IF NOT EXISTS workspace_path TEXT;
ALTER TABLE compact_summaries ADD COLUMN IF NOT EXISTS position_in_session INT;
ALTER TABLE compact_summaries ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

-- Verify schema sau khi alter
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'compact_summaries'
ORDER BY ordinal_position;
```

Sau khi ALTER → restart container để clear cached connection pool:
```bash
docker compose restart archive-api
sleep 5
```

**Pattern best practice cho schema migration:**

| Tình huống | Cách làm |
|---|---|
| Table mới hoàn toàn | `CREATE TABLE IF NOT EXISTS ...` |
| Thêm column vào table có sẵn | `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...` |
| Đổi data type column | `ALTER TABLE ... ALTER COLUMN ... TYPE ...` (cẩn thận data loss) |
| Đổi tên column | `ALTER TABLE ... RENAME COLUMN ... TO ...` |
| Xóa column | `ALTER TABLE ... DROP COLUMN ... CASCADE` (mất data!) |
| Schema phức tạp, nhiều version | Dùng tool migration (Alembic, Flyway) |

Trước khi run `ALTER`, BACKUP table:
```sql
CREATE TABLE compact_summaries_bak AS SELECT * FROM compact_summaries;
-- Sau khi verify migration OK, drop backup:
-- DROP TABLE compact_summaries_bak;
```

> **Lesson learned:** mọi lúc update SQL schema trong plan này, sau khi chạy SQL phải **verify column** bằng `information_schema.columns`. Nếu mismatch với code Python → ALTER TABLE thêm.

---

**B33. mem0 MCP server timeout 30s khi prewarm — VS Code respawn loop infinite**

Triệu chứng: VS Code mở mem0 MCP, prewarm Python ~30-300s, VS Code default timeout 30s → kill process → respawn lại → vòng lặp vô tận. Log thấy nhiều `Server starting` mới mỗi ~30s với PID khác nhau, không bao giờ thấy `Prewarm: DONE`.

Nguyên nhân: import chain của mem0ai default rất nặng (langchain, neo4j, networkx, rank-bm25, ollama, gemini, groq, mistral, cohere). Trên Windows có corporate antivirus, mỗi `.py` file phải scan → import chain `mem0ai[graph,llms]` mất 60-200s lần đầu. VS Code MCP timeout cứng 30s → fail.

Khắc phục — combo 3 lớp giảm prewarm:

1. **Bỏ extras `[graph,llms]`** (Option B trong B22 → giảm dependencies từ ~150MB → 30MB)
2. **Lazy import optimization v0.3.9** trong fork (xem **Bước 7.10**):
   - `llm_anthropic.py`: lazy `import anthropic` (-5-15s Windows AV)
   - `config.py`: conditional Ollama provider registration (-3-8s)
   - `server.py`: lazy `graph_tools` + conditional patches (-2-5s)
3. **Pre-compile `.pyc`** bytecode trước (AV scan `.pyc` nhanh hơn `.py`)

Sau combo: prewarm 95-195s → 11-28s. Thêm `MCP_TIMEOUT=60000` (60s) làm safety margin.

Nếu sau optimization vẫn >25s thường xuyên → switch sang HTTP transport (**Bước 7.9**).

**B34. uvx vẫn dùng wheel cũ dù sửa source — không thấy thay đổi reflect**

Triệu chứng: Sửa source code mem0-mcp-selfhosted, restart VS Code, nhưng behavior không đổi. Log không có dòng log mới mà code đã thêm. `claude mcp add` với `--reinstall-package` không help.

Nguyên nhân: uvx cache wheel theo `(package_name, version)`. Source thay đổi nhưng version trong `pyproject.toml` không đổi → uvx coi là cùng package → dùng cache wheel cũ.

Khắc phục: **BUMP VERSION** trong cả 2 nơi:
```toml
# pyproject.toml
version = "0.3.10"   # tăng từ phiên bản cũ
```
```python
# src/mem0_mcp_selfhosted/__init__.py
__version__ = "0.3.10"
```

Sau đó:
```powershell
uv cache prune        # Xóa cache cũ
claude mcp remove mem0
# Add lại với cùng path -> uvx tự build wheel mới
```

Hoặc dùng `--reinstall-package mem0-mcp-selfhosted` trong lệnh uvx (force rebuild ngay cả khi version unchanged).

**Lưu ý PEP 440:** dùng `0.3.10` (chuẩn) hoặc `0.3.9.dev1`. KHÔNG dùng `0.3.9-debug.1` (dấu `-` không hợp PEP 440 → wheel metadata parse fail).

**B35. Prewarm chậm trên Windows nhưng nhanh trên Linux/Mac — Antivirus scanning**

Triệu chứng: Cùng source code mem0-mcp-selfhosted, cùng dependencies:
- Linux sandbox: prewarm 2-3s
- Mac (no AV): prewarm 5-15s
- Windows + corporate AV: prewarm 60-200s
- Windows + Windows Defender (default): prewarm 20-50s

Nguyên nhân: Antivirus scan từng file `.py` khi Python load. Corporate AV (TrendMicro, Symantec, McAfee) scan chậm hơn Windows Defender 5-10 lần. Import chain langchain + neo4j + qdrant-client + openai + anthropic = vài trăm files Python = vài chục đến vài trăm giây scan.

Khắc phục theo thứ tự ưu tiên:

1. **Pre-compile `.pyc`** giúp 30-50%: scan `.pyc` nhanh hơn `.py`. Chạy `python -m compileall <package_path>` cho mem0ai + anthropic + source fork. Mất 1-2 phút làm 1 lần.

2. **Add exclusion AV** giúp 70-90% nhưng cần admin/IT:
   - `%LOCALAPPDATA%\uv\cache` (uv wheel cache)
   - `%APPDATA%\Python` hoặc Python install dir
   - mem0-mcp-selfhosted source folder

3. **Combo Option B + lazy imports + precompile** (xem B33).

4. **Cuối cùng:** dùng HTTP transport (**Bước 7.9**) — server warm 24/7 chỉ chịu AV scan 1 lần khi login.

Trong môi trường công ty không thể request AV exclusion → Bước 7.9 là giải pháp duy nhất.

---

### Bước 7.9 — HTTP transport mode (server warm 24/7) · 10 phút

**Vấn đề giải quyết:** MCP stdio mode spawn Python mỗi lần VS Code mở → bị antivirus scan + import overhead 30-200s. VS Code timeout 30s → respawn loop (B33).

**Giải pháp:** Đổi từ stdio sang HTTP. Server chạy **1 lần** khi login Windows (Task Scheduler), stay warm 24/7. Claude Code chỉ HTTP request `localhost:8082` → connect <100ms, không bao giờ timeout.

**Khi nào dùng:**
- Prewarm sau optimization vẫn >25s thường xuyên
- Antivirus công ty quá chậm, không request exclusion được
- Muốn UX "Connected ngay" mỗi lần mở VS Code

**Khi nào KHÔNG cần:**
- Prewarm <25s ổn định (xem **Bước 7.10** + **B35**)
- Laptop yếu RAM (<8GB) — server tốn ~400MB
- Chỉ thỉnh thoảng dùng mem0

**Phase 1 — Tạo launcher script (chạy bởi Task Scheduler):**

Tạo `mem0-server-launcher.ps1` với env vars + chạy `uvx ... mem0-mcp-selfhosted` với `MEM0_TRANSPORT=streamable-http`, `MEM0_HOST=127.0.0.1`, `MEM0_PORT=8082`. Script tee log vào `mem0-server.log`.

**Phase 2 — Setup Task Scheduler auto-start:**

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File <path-to-launcher>"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "mem0-server-http" -Action $action -Trigger $trigger -Settings $settings
Start-ScheduledTask -TaskName "mem0-server-http"
```

**Phase 3 — Đăng ký Claude Code MCP HTTP:**

```powershell
claude mcp remove mem0
claude mcp add --scope user --transport http mem0 http://127.0.0.1:8082
```

**Test:**
```powershell
# Wait for server health
Invoke-WebRequest http://127.0.0.1:8082 -UseBasicParsing
# Restart VS Code -> /mcp phải thấy mem0 Connected NGAY (không đợi prewarm)
```

**Trade-offs:**

| | stdio (Bước 7.10) | http (Bước 7.9) |
|---|---|---|
| Cold start mỗi VS Code open | 12-28s | **0s** (server warm) |
| RAM Windows ngốn | 0 (spawn-die) | ~400MB (long-running) |
| Antivirus rescan mỗi lần | Yes | **No** (1 lần/login) |
| Setup effort | 5 phút | 10 phút |
| Multi-device (Mac+Windows) | Mỗi máy 1 instance | Future: deploy VPS |
| Khi update source | uvx tự rebuild | Phải Stop+Start Task |

→ Workspace có sẵn 2 scripts ready-to-run:
- `setup-http-mode.ps1` — automate Phase 1-3 + verify
- `mem0-server-launcher.ps1` — long-running server

---

### Bước 7.10 — Source code optimization v0.3.9 · 15 phút

**Mục tiêu:** Giảm prewarm time từ 95-195s xuống 11-28s thông qua lazy imports + conditional registration.

**Áp dụng cho:** fork `mem0-mcp-selfhosted` (folder `mem0-mcp-selfhosted-main` trên local).

**Phase 1 — Bump version (BẮT BUỘC, xem B34):**

```toml
# pyproject.toml
version = "0.3.9"
```
```python
# src/mem0_mcp_selfhosted/__init__.py
__version__ = "0.3.9"
```

**Phase 2 — Lazy import `anthropic` (lớn nhất, save ~5-15s Windows):**

Trong `src/mem0_mcp_selfhosted/llm_anthropic.py`, thay top-level `import anthropic` bằng:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic
else:
    anthropic = None  # lazy load in __init__
```

Trong `__init__` của `AnthropicOATLLM`:
```python
def __init__(self, config: AnthropicOATConfig):
    global anthropic
    if anthropic is None:
        import anthropic as _anthropic
        anthropic = _anthropic
    # ... existing code
    self.client = anthropic.Anthropic(**client_kwargs)
```

Type hints `anthropic.types.Message` trong method signatures vẫn OK vì `from __future__ import annotations` (PEP 563) làm type hints là strings, không evaluate.

**Phase 3 — Conditional Ollama provider registration (save ~3-8s):**

Trong `src/mem0_mcp_selfhosted/config.py`, thay block `providers_info = [{"name": "ollama", ...}]` luôn-có bằng:

```python
_needs_ollama = (
    llm_provider == "ollama"
    or (enable_graph and graph_llm_provider_raw == "ollama")
    or (enable_graph and graph_llm_provider_raw == "gemini_split"
        and contradiction_provider == "ollama")
)
providers_info: list[ProviderInfo] = []
if _needs_ollama:
    providers_info.append({
        "name": "ollama",
        "class_path": "mem0_mcp_selfhosted.llm_ollama.OllamaToolLLM",
    })
if _needs_anthropic:
    providers_info.append({...})
```

**Phase 4 — Lazy `graph_tools` import (save ~1-3s):**

Trong `src/mem0_mcp_selfhosted/server.py`, thay:
```python
from mem0_mcp_selfhosted.graph_tools import get_entity, search_graph
```
bằng comment, và move import vào trong tool handlers:
```python
def mcp_search_graph(query):
    from mem0_mcp_selfhosted.graph_tools import search_graph
    result = search_graph(query)
    # ...
```

**Phase 5 — Conditional patches (save ~1-2s):**

Trong `_init_memory()`, thay:
```python
patch_graph_sanitizer()
patch_gemini_parse_response()
```
bằng:
```python
_enable_graph = bool_env("MEM0_ENABLE_GRAPH")
if _enable_graph:
    patch_graph_sanitizer()
    graph_llm_provider = env("MEM0_GRAPH_LLM_PROVIDER", env("MEM0_PROVIDER", "anthropic"))
    if graph_llm_provider in ("gemini", "gemini_split"):
        patch_gemini_parse_response()
```

**Phase 6 — Defensive imports (safety net):**

- `llm_ollama.py`: wrap `from mem0.llms.ollama import OllamaLLM` trong try/except, define stub class nếu fail.
- `helpers.py`: wrap `import mem0.memory.utils` trong try/except, skip patch nếu mem0[graph] không có.

**Phase 7 — Pre-compile `.pyc` (giảm AV scan thêm 30-50%):**

```powershell
# Pre-compile source fork
python -m compileall -q "E:\Thanhhn5\41. Khoa hoc\Claude\mem0-mcp-selfhosted-main\mem0-mcp-selfhosted-main\src"

# Pre-compile mem0ai installed package
$mem0Path = python -c "import mem0; import os; print(os.path.dirname(mem0.__file__))"
python -m compileall -q $mem0Path

# Pre-compile anthropic
$anthropicPath = python -c "import anthropic; import os; print(os.path.dirname(anthropic.__file__))"
python -m compileall -q $anthropicPath
```

**Phase 8 — Test trên Windows:**

```powershell
. "$env:USERPROFILE\scripts\archive-env.ps1"
# ... set MEM0_* env vars ...
Measure-Command {
    uvx --from "<MEM0_SRC_PATH>" --reinstall-package mem0-mcp-selfhosted mem0-test-cli init
} | Select-Object TotalSeconds
```

Expected times (sau v0.3.9):
| Scenario | Time | Status |
|---|---|---|
| Cold start (sau `uv cache prune`) | 25-30s | ⚠️ Sát timeout |
| Warm cache | 12-15s | ✅ Tốt |
| Warm + precompile `.pyc` | 11-13s | ✅ EXCELLENT |

**Phase 9 — Re-register MCP với timeout 60s safety margin:**

```powershell
claude mcp add --scope user --transport stdio mem0 `
  --env MEM0_USER_ID=thanh `
  ... (full env như Bước 4.1) `
  --env MCP_TIMEOUT=60000 `
  -- uvx --from "<MEM0_SRC_PATH>" mem0-mcp-selfhosted
```

Patch `~/.claude.json` thêm `"timeout": 60000` cho service `mem0`.

**Files trong workspace ready-to-run:**
- `test-mem0-prewarm.ps1` — chạy 3 test scenarios, đo timing, recommend approach
- `deploy-mem0-stdio.ps1` — apply optimization + register MCP + setup timeout
- `precompile-mem0-pyc.ps1` — chỉ pre-compile (standalone)
- `setup-http-mode.ps1` — switch sang HTTP nếu stdio vẫn chậm

---

## 5.5. Phụ lục — Bản build customize có log chi tiết

Khi cần soi sâu hơn để tìm bottleneck (lần nào chậm, gọi Anthropic mấy lần, dính 429 ở đâu, embedder/Qdrant mất bao lâu), dùng bản build customize đã được instrument **log `[ACTION]` cho mọi tool call + `[TIMING]` cho mọi API call** thay vì bản gốc trên GitHub. Bản này ghi log ra một file riêng dễ đọc.

**Chuẩn bị:**

- Giải nén zip source vào thư mục cố định không xoá, ví dụ `E:\Thanhhn5\41. Khoa hoc\Claude\mem0-mcp-selfhosted-main\mem0-mcp-selfhosted-main\` (thư mục phải chứa `pyproject.toml`).
- 3 file đã instrument: `src/mem0_mcp_selfhosted/server.py`, `helpers.py`, `llm_anthropic.py`. Thay đổi gồm: (1) decorator `_log_tool` quanh mọi MCP tool; (2) `[ACTION]` log từng bước trong `_init_memory`; (3) wrap embedder + Qdrant methods bằng `[TIMING]`; (4) `[TIMING]` quanh mỗi lần gọi Anthropic với bộ đếm; (5) retry 429 với backoff 20/40/60s thay vì chỉ retry 5xx; (6) `max_retries=0` ở Anthropic SDK để retry dài của ta thực sự chạy.

**Đăng ký MCP trỏ về source local — Windows PowerShell (1 dòng):**

```powershell
claude mcp add --scope user mem0 -e "MEM0_LOG_FILE=C:\Users\thanhhn5\mem0-timing.log" -e "MEM0_LOG_LEVEL=INFO" -e "MEM0_USER_ID=thanh" -e "MEM0_QDRANT_URL=https://claude.hangocthanh.io.vn:443/qdrant" -e "MEM0_QDRANT_API_KEY=<MCP_BEARER_TOKEN>" -e "MEM0_EMBED_PROVIDER=openai" -e "MEM0_EMBED_MODEL=text-embedding-3-small" -e "MEM0_EMBED_DIMS=1536" -e "MEM0_LLM_MODEL=claude-haiku-4-5-20251001" -e "OPENAI_API_KEY=<sk-...>" -e "HTTP_PROXY=http://10.121.127.204:3128" -e "HTTPS_PROXY=http://10.121.127.204:3128" -- uvx --from "E:\Thanhhn5\41. Khoa hoc\Claude\mem0-mcp-selfhosted-main\mem0-mcp-selfhosted-main" --reinstall-package mem0-mcp-selfhosted mem0-mcp-selfhosted
```

Khác biệt so với Bước 4.1: **(a)** thêm `MEM0_LOG_FILE` (đường dẫn file ghi log) + `MEM0_LOG_LEVEL=INFO`; **(b)** đổi `--from git+https://github.com/...` thành `--from "<đường dẫn local>"`; **(c)** thêm `--reinstall-package mem0-mcp-selfhosted` để uvx buộc rebuild từ source local mỗi lần server khởi động (đảm bảo lấy đúng code đã sửa).

**Đọc log:**

Sau khi gọi `add_memory` một lần, mở `C:\Users\thanhhn5\mem0-timing.log`. Mỗi tool call có khối:

```
[ACTION] >>> tool 'add_memory' START args=[...]
[TIMING] ===== add_memory START (user_id=thanh) =====
[TIMING] _ensure_memory() mat 114.27s         <- chỉ in lần đầu
[ACTION] _init_memory: build_config() DONE in X
[ACTION] _init_memory: import mem0.Memory DONE in X
... (các bước con của khởi tạo)
[TIMING] embedder.embed mat X                 <- mỗi lần gọi OpenAI embedding
[TIMING] qdrant.search mat X                  <- mỗi lệnh Qdrant
[TIMING] >>> Anthropic call #N START model=claude-haiku-4-5-20251001
[ACTION] anthropic.messages.create attempt 1/4
[ACTION] anthropic.messages.create attempt 1/4 OK in X
[TIMING] <<< Anthropic call #N DONE sau X
[TIMING] mem.add() DONE sau X
[TIMING] ===== add_memory END tong cong X =====
[ACTION] <<< tool 'add_memory' DONE in X
```

Khi dính 429, thêm các dòng:

```
[ACTION] anthropic.messages.create attempt 1/4 FAIL status=429
[mem0] Anthropic 429, cho 20s roi thu lai (attempt 1/4)
[ACTION] anthropic.messages.create attempt 2/4
```

→ retry chậm rãi vượt cửa sổ rate-limit, lần thử thứ 2-3 thường thành công.

**Khi xong việc debug**, quay về bản gốc bằng `claude mcp remove mem0` + `add` lại theo lệnh Bước 4.1 (bỏ `--from "<local>"` + `--reinstall-package`, đổi về `--from git+https://github.com/elvismdev/mem0-mcp-selfhosted.git`). Bản gốc không có log [ACTION]/[TIMING] nên server start nhanh hơn (không phải rebuild local mỗi lần).

---

## 5.6. Phụ lục — CLI test harness (debug nhanh hơn MCP)

Khi cần thử nghiệm nhanh các thay đổi source code (ví dụ sửa bug `user_id` filter, đổi LLM model), test qua MCP rất chậm: phải `claude mcp remove + add`, đóng/mở VS Code, đợi Claude gọi tool, xem log... mỗi vòng 5-10 phút. CLI test harness bypass toàn bộ lớp MCP/Claude Code, gọi thẳng `mem.add()` / `mem.search()` / `mem.get_all()` từ PowerShell — log đổ ra stdout, phản hồi tức thì, mỗi iteration <30s.

**Cài đặt:** thêm 2 thứ vào bản build customize:

1. **File `src/mem0_mcp_selfhosted/test_cli.py`** — script đứng riêng, import server module, gọi hàm trực tiếp. Đầy đủ 5 subcommand:
   - `init` — chỉ gọi `_ensure_memory()`, đo thời gian khởi tạo (so sánh trước/sau khi pin mem0ai 1.x).
   - `list` — gọi `list_entities_facet()` → đếm users/agents/runs.
   - `get` — gọi `mem.get_all(user_id=...)` → list toàn bộ memory.
   - `add "<text>"` — gọi `mem.add()` với 1 câu, in kết quả 3 fact (hoặc `[]` nếu trùng — xem B20).
   - `search "<query>"` — gọi `mem.search()` với 1 từ khoá, in danh sách kết quả + score.

2. **Entry point trong `pyproject.toml`:**

```toml
[project.scripts]
mem0-mcp-selfhosted = "mem0_mcp_selfhosted:main"
mem0-test-cli = "mem0_mcp_selfhosted.test_cli:main"   # ← thêm dòng này
```

**Cách dùng (PowerShell):**

```powershell
# Set env vars một lần cho phiên terminal
$env:MEM0_USER_ID="thanh"
$env:MEM0_QDRANT_URL="https://claude.hangocthanh.io.vn:443/qdrant"
$env:MEM0_QDRANT_API_KEY="<bearer token>"
$env:MEM0_EMBED_PROVIDER="openai"
$env:MEM0_EMBED_MODEL="text-embedding-3-small"
$env:MEM0_EMBED_DIMS="1536"
$env:MEM0_LLM_MODEL="claude-haiku-4-5-20251001"
$env:OPENAI_API_KEY="<openai key>"
$env:HTTP_PROXY="http://10.121.127.204:3128"
$env:HTTPS_PROXY="http://10.121.127.204:3128"

# Test theo thứ tự dễ → khó
uvx --from "E:\Thanhhn5\41. Khoa hoc\Claude\mem0-mcp-selfhosted-main\mem0-mcp-selfhosted-main" mem0-test-cli init
uvx --from "...same path..." mem0-test-cli list
uvx --from "...same path..." mem0-test-cli add "Toi ten Thanh, lam o Ha Noi, dang trien khai memory server tu host"
uvx --from "...same path..." mem0-test-cli search "VPS"
uvx --from "...same path..." mem0-test-cli get
```

**Khi nào dùng CLI vs MCP:**

- **CLI**: lúc debug source code (sửa logic, thử dependency mới, đo timing). Loại trừ mọi lớp MCP/Claude Code/VS Code. Bug nếu có sẽ hiện ngay trên terminal.
- **MCP**: sau khi CLI verify code work, deploy vào Claude Code để dùng thực tế.

Đây là chiến lược **"verify ở tầng thấp nhất trước"** — tiết kiệm hàng giờ debug khi vấn đề thực ra nằm sâu dưới (vd mem0ai version, API mismatch, network proxy) chứ không phải plumbing MCP.

---

## 5.7. Phụ lục — Tạo `CLAUDE.md` để Claude Code ưu tiên mem0

Mặc định khi user nói *"lưu giúp tôi: tôi là X"*, Claude Code edit file `.md` trong `.claude/projects/<project>/memory/` (built-in auto-memory) thay vì gọi tool `add_memory` của mem0 (xem B21). Để buộc Claude Code dùng mem0 cho personal facts, tạo file `CLAUDE.md` ở scope user-global.

**Scope của `CLAUDE.md`:**

| Scope | Vị trí | Áp dụng |
|---|---|---|
| User-global *(khuyến nghị cho mem0)* | `C:\Users\<user>\.claude\CLAUDE.md` (Win), `~/.claude/CLAUDE.md` (Mac) | Mọi project trên máy |
| Workspace | `<workspace>\CLAUDE.md` | Khi mở VS Code tại folder này |
| Project | `<project>\CLAUDE.md` | Chỉ project đó |

User-global tốt nhất cho mem0 vì memory chia sẻ xuyên project.

**Tạo file (PowerShell, Windows):**

```powershell
New-Item -ItemType Directory -Force -Path "C:\Users\thanhhn5\.claude" | Out-Null

@'
# Memory management instructions

## Prefer mem0 MCP for long-term memory

When the user asks to save / remember / store personal information,
preferences, role, tools, projects (name, job, location, decisions, facts...),
ALWAYS call the `mcp__mem0__add_memory` tool from the mem0 MCP server.

Do NOT edit `.md` files under `C:\Users\<user>\.claude\projects\*\memory\`
(built-in auto-memory) for this kind of information. Built-in memory is
local to one machine and one project — it does not sync. mem0 is a
self-hosted centralized memory that works from every machine the user owns.

## Retrieving memories

For specific topic → `mcp__mem0__search_memories` with query.
For full list → `mcp__mem0__get_memories`.

## Interpret mem0 responses

- `{"results": []}` from `add_memory` means the fact already exists
  (deduplication, event=NONE). NOT a failure. Still tell the user it was
  saved (or already remembered).
- `{"results": [{"event": "ADD"}]}` → new fact stored.
- `{"results": [{"event": "UPDATE"}]}` → existing fact updated.

## When NOT to use mem0

- Project source code/files → use Read/Edit/Write as usual.
- Temporary conversation context → no need to store.
- Claude Code settings → keep built-in.

Rule: personal facts about the user → mem0; everything else → as before.

## Language

User prefers Vietnamese responses. Keep replying in Vietnamese even though
these instructions are in English.
'@ | Set-Content -Path "C:\Users\thanhhn5\.claude\CLAUDE.md" -Encoding UTF8
```

**Trên Mac (Terminal):**

```bash
mkdir -p ~/.claude
cat > ~/.claude/CLAUDE.md << 'EOF'
# (nội dung như trên, đổi C:\Users\... thành ~/.claude/projects/...)
EOF
```

**Kích hoạt:** đóng panel chat Claude Code hiện tại, mở session mới. Claude Code đọc `CLAUDE.md` **mỗi khi tạo session** và prepend nội dung vào system prompt.

**Test:** mở chat mới, gõ "Lưu giúp tôi: tôi đang dùng laptop Dell XPS 15" → Claude tự gọi `mcp__mem0__add_memory` (không edit file local). Block `Mem0 [add_memory]` xuất hiện trong UI là bằng chứng.

**Vì sao dùng tiếng Anh trong instructions:** Claude được train chủ yếu trên corpus tiếng Anh. Mệnh lệnh kiểu *"ALWAYS call X"*, *"Do NOT edit Y"* bằng tiếng Anh có độ tuân thủ cao hơn tiếng Việt. Phần `## Language` cuối file đảm bảo Claude vẫn **trả lời user** bằng tiếng Việt — tách lớp instruction (tiếng Anh) với lớp chat output (tiếng Việt).

---

## 6. Maintenance định kỳ

**Hàng tuần:** `sudo tail /var/log/memory-backup.log`; kiểm tra monitor không có alert sai.

**Hàng tháng:** `cd ~/memory-stack && docker compose pull && docker compose up -d`; `sudo apt update && sudo apt upgrade -y`; thử restore một backup vào container staging.

**Hàng quý:** rotate `MCP_BEARER_TOKEN` (sinh mới, cập nhật `.env` + `claude mcp add`); xem dung lượng memory đã lưu.

---

## 8. ChatGPT Desktop cùng đọc/ghi mem0 (60 phút)

> **Kế thừa hạ tầng đã có:** mục này tận dụng Qdrant, Postgres, Caddy, network `memnet` đã dựng ở Giai đoạn 2 — KHÔNG dựng infra mới. Trước khi bắt đầu phải hoàn thành Giai đoạn 1–7.

### 8.1 Tổng quan — vì sao chọn REST wrapper + Custom GPT Action

**ChatGPT (web + Desktop) chưa hỗ trợ MCP stdio đầy đủ** như Claude Code (tính tới 05/2026). Cách kết nối khả dụng và bền nhất hiện nay: **Custom GPT + OpenAPI Action** gọi REST API trên VPS. Custom GPT chạy được trên cả ChatGPT web và ChatGPT Desktop với cùng một cấu hình → tạo 1 lần, dùng được mọi nền tảng.

**Kiến trúc bổ sung (chỉ thêm 1 service):**

```
ChatGPT Desktop / Web (Custom GPT)
        │ HTTPS + Bearer token
        ▼
claude.hangocthanh.io.vn/memory/*
        │ Caddy route mới (kế thừa cert TLS đã có)
        ▼
memory-rest-api (FastAPI, port 8002)   ◄── service mới
        │ dùng cùng Qdrant + Postgres đã chạy
        ▼
Qdrant (vector) + Postgres (metadata)
```

**Vì sao KHÔNG dùng HTTP MCP mode từ Bước 7.9 cho ChatGPT:** Bước 7.9 server bind `localhost:8082` trên **máy Windows của bạn** → ChatGPT cloud (chạy ở Mỹ/EU) không truy cập tới được. ChatGPT chỉ gọi được URL public HTTPS → bắt buộc dựng endpoint trên VPS.

**Vì sao thêm service mới `memory-rest-api` thay vì sửa `archive-api`:** schema khác hoàn toàn (vector mem0 vs transcript JSON). Tách service giúp xoay token độc lập (rò rỉ Custom GPT token không ảnh hưởng archive-api). Vẫn dùng chung `docker-compose.yml`, chung network `memnet` → vẫn 1 lệnh `docker compose up -d`.

**Vì sao đồng bộ 2 chiều với Claude Code tự nhiên:** cả 2 client cùng `user_id=thanh` và cùng `collection_name` trong Qdrant → đây là 1 kho memory duy nhất, ChatGPT ghi → Claude Code đọc được ngay, và ngược lại (xem kịch bản test 8.4).

---

### 8.2 Tạo service `memory-rest-api` trên VPS

**Bước 8.2.1 — Sinh secret + thư mục** (đăng nhập `ssh vps`)

```bash
cd ~/memory-stack
echo "CHATGPT_AUTH_TOKEN=$(openssl rand -hex 32)" >> .env
grep CHATGPT_AUTH_TOKEN .env
mkdir -p ~/memory-stack/memory-rest-api
```

Copy giá trị `CHATGPT_AUTH_TOKEN` ra Notepad — lát nữa cần dán vào Custom GPT.

Nếu trước đó chưa thêm `OPENAI_API_KEY` vào `.env` (giai đoạn 4 dùng nó cho embedder, nhưng có thể bạn đặt trực tiếp trong lệnh `claude mcp add`), thêm vào:

```bash
# ⚠️ BẮT BUỘC thay YOUR_OPENAI_KEY_HERE bằng key thật (sk-proj-... hoặc sk-...) TRƯỚC khi chạy
grep -q '^OPENAI_API_KEY=' .env || echo 'OPENAI_API_KEY=YOUR_OPENAI_KEY_HERE' >> .env

# Verify: dòng OPENAI_API_KEY phải có giá trị thật, KHÔNG có ký tự non-ASCII
grep OPENAI_API_KEY .env
```

> **⚠️ CẢNH BÁO TỪ DEPLOYMENT THỰC TẾ (lesson learned):** Placeholder cũ của plan dùng tiếng Việt `<sk-...key-của-bạn>` — chữ "**của**" có ký tự `ủ` (Unicode `ủ`) → nếu user copy-paste mà QUÊN thay key thật → OpenAI SDK crash với `UnicodeEncodeError: 'ascii' codec can't encode character 'ủ'` vì HTTP headers phải ASCII. Symptom: `memory-rest-api` start được, `/health` OK, nhưng POST `/memories` trả 500. Xem **T8.6** để debug. Placeholder mới `YOUR_OPENAI_KEY_HERE` là ASCII thuần → nếu quên thay, OpenAI sẽ trả `401 invalid_api_key` rõ ràng hơn.

**Bước 8.2.2 — Tạo `Dockerfile`** (heredoc, copy-chạy-ngay)

```bash
cat > ~/memory-stack/memory-rest-api/Dockerfile << 'EOF'
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir \
    fastapi==0.115.* \
    uvicorn==0.32.* \
    "mem0ai<2.0" \
    qdrant-client==1.12.* \
    openai==1.55.*
COPY app.py .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
EOF
```

> Pin `mem0ai<2.0` theo lessons learned từ Giai đoạn 4 — bản 2.x phá vỡ API `add/search` (xem B11). Pin chính xác để build reproducible.

**Bước 8.2.3 — Tạo `app.py`** (FastAPI wrapper 5 endpoints)

```bash
cat > ~/memory-stack/memory-rest-api/app.py << 'EOF'
"""REST wrapper for mem0 — used by ChatGPT Custom GPT and VS Code Continue.dev.
Shares the same Qdrant collection with Claude Code MCP so memories sync bidirectionally."""
import os
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from mem0 import Memory

AUTH = os.environ["CHATGPT_AUTH_TOKEN"]
QDRANT_URL = os.environ["QDRANT_URL"]
QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
DEFAULT_USER = os.environ.get("DEFAULT_USER_ID", "thanh")
COLLECTION = os.environ.get("COLLECTION_NAME", "mem0_mcp_selfhosted")

config = {
    "vector_store": {
        "provider": "qdrant",
        "config": {
            "url": QDRANT_URL,
            "api_key": QDRANT_API_KEY,
            "collection_name": COLLECTION,
            "embedding_model_dims": 1536,
        },
    },
    "embedder": {
        "provider": "openai",
        "config": {"api_key": OPENAI_API_KEY, "model": "text-embedding-3-small"},
    },
    "llm": {
        "provider": "openai",
        "config": {"api_key": OPENAI_API_KEY, "model": "gpt-4o-mini"},
    },
    "version": "v1.1",
}
mem = Memory.from_config(config)
app = FastAPI(title="Mem0 REST Wrapper", version="1.0")

def check(token: Optional[str]):
    if not token or token != f"Bearer {AUTH}":
        raise HTTPException(401, "Unauthorized")

class AddBody(BaseModel):
    text: str
    user_id: Optional[str] = None
    metadata: Optional[dict] = None

class SearchBody(BaseModel):
    query: str
    user_id: Optional[str] = None
    limit: int = 10

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/memories")
def add_memory(body: AddBody, authorization: Optional[str] = Header(None)):
    """Add a new memory. ChatGPT calls this when user asks to remember something."""
    check(authorization)
    uid = body.user_id or DEFAULT_USER
    result = mem.add(body.text, user_id=uid, metadata=body.metadata or {})
    return {"ok": True, "result": result}

@app.post("/memories/search")
def search_memory(body: SearchBody, authorization: Optional[str] = Header(None)):
    """Search memories by semantic similarity. ChatGPT calls this BEFORE answering."""
    check(authorization)
    uid = body.user_id or DEFAULT_USER
    results = mem.search(body.query, user_id=uid, limit=body.limit)
    items = results.get("results", results) if isinstance(results, dict) else results
    return {"results": items}

@app.get("/memories")
def list_memories(
    user_id: Optional[str] = None,
    limit: int = 50,
    authorization: Optional[str] = Header(None),
):
    """List all memories for a user (for browsing in Custom GPT UI)."""
    check(authorization)
    uid = user_id or DEFAULT_USER
    all_mem = mem.get_all(user_id=uid)
    items = all_mem.get("results", all_mem) if isinstance(all_mem, dict) else all_mem
    return {"results": items[:limit]}

@app.delete("/memories/{memory_id}")
def delete_memory(memory_id: str, authorization: Optional[str] = Header(None)):
    """Delete a specific memory by ID."""
    check(authorization)
    mem.delete(memory_id=memory_id)
    return {"ok": True, "deleted_id": memory_id}
EOF
```

> **Vì sao LLM của wrapper dùng `gpt-4o-mini` thay vì Claude Haiku Max?** Server REST chạy 24/7 trên VPS — KHÔNG có OAT Max (file `~/.claude/.credentials.json` chỉ tồn tại trên máy client). Dùng OpenAI cho cả embedder lẫn LLM (mem0 cần LLM để trích xuất facts từ raw text) tránh phụ thuộc credential Claude Max. Chi phí `gpt-4o-mini` cho fact extraction rất thấp (~$0.15/1M token input) — cá nhân dùng <$1/tháng.
>
> **Vì sao `COLLECTION="mem0_mcp_selfhosted"` (mặc định)?** ⚠️ **LESSON LEARNED 27/05/2026:** Fork `mem0-mcp-selfhosted` (Claude Code dùng) đã **override default collection name** từ `mem0` (default mem0ai) sang `mem0_mcp_selfhosted` (theo package name). Nếu wrapper dùng `mem0` → KHÔNG chia sẻ kho với Claude Code → ChatGPT/Continue chỉ thấy memory wrapper tự add, không thấy memory cũ. **Verify trước khi deploy** bằng lệnh:
>
> ```bash
> # Trên VPS, đếm points trong các collection
> docker run --rm --network memory-stack_memnet curlimages/curl -s \
>   http://qdrant:6333/collections \
>   -H "api-key: $(grep QDRANT_API_KEY ~/memory-stack/.env | cut -d= -f2)"
> ```
>
> Collection có số points lớn nhất = collection Claude Code đã dùng → đặt `COLLECTION_NAME` của wrapper khớp.

**Bước 8.2.4 — Ghi đè `docker-compose.yml` (đã thêm service `memory-rest-api`)**

Sao lưu file cũ trước, sau đó dán nguyên khối heredoc — KHÔNG cần mở `vi`:

```bash
cd ~/memory-stack
cp docker-compose.yml docker-compose.yml.bak.$(date +%F-%H%M)

cat > ~/memory-stack/docker-compose.yml << 'EOF'
services:
  qdrant:
    image: qdrant/qdrant:v1.12.0
    container_name: memory-qdrant
    volumes:
      - ./data/qdrant:/qdrant/storage
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY}
    restart: unless-stopped
    networks: [memnet]

  postgres:
    image: pgvector/pgvector:pg16
    container_name: memory-postgres
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: mem0
      POSTGRES_USER: mem0
    volumes:
      - ./data/pg:/var/lib/postgresql/data
    restart: unless-stopped
    networks: [memnet]

  archive-api:
    build: ./archive-api
    container_name: memory-archive-api
    environment:
      DB_URL: postgresql://mem0:${POSTGRES_PASSWORD}@postgres/mem0
      ARCHIVE_AUTH_TOKEN: ${ARCHIVE_AUTH_TOKEN}
    networks: [memnet]
    depends_on: [postgres]
    restart: unless-stopped

  memory-rest-api:
    build: ./memory-rest-api
    container_name: memory-rest-api
    environment:
      CHATGPT_AUTH_TOKEN: ${CHATGPT_AUTH_TOKEN}
      QDRANT_URL: http://qdrant:6333
      QDRANT_API_KEY: ${QDRANT_API_KEY}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      DEFAULT_USER_ID: thanh
      COLLECTION_NAME: mem0_mcp_selfhosted
    networks: [memnet]
    depends_on: [qdrant]
    restart: unless-stopped

  caddy:
    image: caddy:2.8
    container_name: memory-caddy
    ports:
      - "80:80"
      - "8443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./data/caddy:/data
    environment:
      MCP_BEARER_TOKEN: ${MCP_BEARER_TOKEN}
      QDRANT_API_KEY: ${QDRANT_API_KEY}
    restart: unless-stopped
    networks: [memnet]
    depends_on: [qdrant]

networks:
  memnet:
    driver: bridge
EOF
```

> **Lưu ý 1:** Nếu mạng bạn KHÔNG dùng proxy (đã bỏ qua mục 3.2 và Bước 2.0), đổi `8443:443` thành `443:443`.
>
> **Lưu ý 2:** Nếu bạn dùng **Neon Cloud** (xem Section 2.4) thay Postgres local — XÓA block `postgres:` và đổi `archive-api` env `DB_URL: ${NEON_DB_URL}`. `memory-rest-api` KHÔNG cần đổi gì (nó chỉ dùng Qdrant, không touch Postgres).

**Bước 8.2.5 — Ghi đè `Caddyfile` (đã thêm route `/memory/*`)**

Sao lưu rồi dán nguyên khối heredoc:

```bash
cp ~/memory-stack/Caddyfile ~/memory-stack/Caddyfile.bak.$(date +%F-%H%M)

cat > ~/memory-stack/Caddyfile << 'EOF'
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

    handle /health {
        respond "ok" 200
    }

    handle {
        respond "Not Found" 404
    }

    log {
        output file /data/access.log
        format json
    }
}
EOF
```

> **Vì sao Caddy KHÔNG verify token ở tầng `/memory/*`?** Để FastAPI tự verify Bearer. Lý do: tách concern — Caddy chỉ route, ứng dụng verify auth → message 401 từ FastAPI rõ ràng hơn (biết được endpoint nào fail). Khác `/qdrant/*` ở Caddy phải verify do Qdrant không support custom auth message.

**Bước 8.2.6 — Build + khởi động**

```bash
cd ~/memory-stack
docker compose up -d --build memory-rest-api caddy
docker compose ps | grep -E "memory-rest-api|caddy"
docker compose logs memory-rest-api --tail=30
```

Phải thấy dòng `Uvicorn running on http://0.0.0.0:8002` → service đã start. Nếu thấy error import `mem0` → kiểm tra `pip install` trong Dockerfile (xem T8.6).

**Bước 8.2.7 — Test từ máy client**

**Trên Mac — Terminal:**

```bash
TOKEN=<paste-CHATGPT_AUTH_TOKEN>

curl https://claude.hangocthanh.io.vn/memory/health

curl -X POST https://claude.hangocthanh.io.vn/memory/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "Test từ Mac: tôi thích cà phê đen đá pha phin"}'

curl -X POST https://claude.hangocthanh.io.vn/memory/memories/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "đồ uống yêu thích"}'
```

**Trên Windows — PowerShell:**

```powershell
$TOKEN = "<paste-CHATGPT_AUTH_TOKEN>"

curl.exe https://claude.hangocthanh.io.vn/memory/health

curl.exe -X POST https://claude.hangocthanh.io.vn/memory/memories `
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" `
  -d '{\"text\": \"Test tu Windows: laptop Dell XPS 15 9530\"}'

curl.exe -X POST https://claude.hangocthanh.io.vn/memory/memories/search `
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" `
  -d '{\"query\": \"laptop\"}'
```

Kết quả mong đợi:
- `/health` → `{"status": "ok"}`
- `POST /memories` → `{"ok": true, "result": {"results": [{"event": "ADD", ...}]}}`
- `POST /memories/search` → `{"results": [{...}]}` chứa fact vừa add

Nếu search trả `[]` → đợi 2-3 giây cho Qdrant index xong rồi search lại (mem0 không sync ngay lập tức).

---

### 8.3 Tạo Custom GPT trên ChatGPT

**Bước 8.3.1 — Mở wizard tạo GPT**

1. Vào ChatGPT (web hoặc Desktop) → góc trái bấm **Explore GPTs** → bấm **+ Create** (góc trên phải)
2. Chuyển sang tab **Configure** (không dùng tab Create với chat tạo tự động)
3. Điền:
   - **Name:** `Thanh's Memory Assistant`
   - **Description:** `Trợ lý cá nhân có bộ nhớ dài hạn lưu trên VPS riêng, đồng bộ với Claude Code`
   - **Profile picture:** tùy chọn
4. Trong ô **Instructions**, dán nguyên khối sau:

```
Bạn là trợ lý cá nhân của Thanh, có quyền truy cập bộ nhớ dài hạn qua API.

QUY TẮC BẮT BUỘC:
1. Mỗi câu hỏi MỚI từ user: GỌI searchMemory với query là từ khóa chính của câu hỏi TRƯỚC khi trả lời. Không có ngoại lệ.
2. Khi user nói "nhớ giúp", "lưu lại", "remember", "ghi lại", "save this": GỌI addMemory với text là nội dung cần nhớ.
3. Khi user hỏi "bạn nhớ gì về tôi", "tóm tắt info của tôi", "liệt kê memory": GỌI listMemories.
4. KHÔNG bịa nội dung memory — chỉ trả lời dựa trên kết quả API.
5. Sau khi gọi tool, tóm tắt kết quả bằng tiếng Việt cho user.

KHÔNG cần xác nhận với user trước khi gọi tool — gọi luôn.
Ngôn ngữ trả lời: Tiếng Việt.
Phong cách: súc tích, không lan man.

KIẾN TRÚC CHIA SẺ MEMORY:
Memory bạn truy cập được CHIA SẺ với Claude Code (trong VS Code) và VS Code Continue.dev.
Khi bạn lưu memory mới, Claude Code sẽ thấy ngay. Khi Claude Code lưu, bạn thấy ngay.
Đây là chủ ý — đừng cảnh báo user về việc "memory này chỉ trong ChatGPT".
```

**Bước 8.3.2 — Thêm Action (OpenAPI schema)**

Kéo xuống mục **Actions** → bấm **Create new action** → ô **Schema** dán nguyên khối:

```yaml
openapi: 3.1.0
info:
  title: Thanh Memory API
  description: Self-hosted mem0 wrapper, shared with Claude Code and Continue.dev
  version: 1.0.0
servers:
  - url: https://claude.hangocthanh.io.vn/memory
paths:
  /memories:
    post:
      operationId: addMemory
      summary: Add a new memory
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [text]
              properties:
                text:
                  type: string
                metadata:
                  type: object
      responses:
        '200':
          description: OK
    get:
      operationId: listMemories
      summary: List all memories
      parameters:
        - name: limit
          in: query
          schema:
            type: integer
      responses:
        '200':
          description: OK
  /memories/search:
    post:
      operationId: searchMemory
      summary: Search memories
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [query]
              properties:
                query:
                  type: string
                limit:
                  type: integer
      responses:
        '200':
          description: OK
components:
  schemas:
    Memory:
      type: object
      properties:
        id:
          type: string
        memory:
          type: string
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
security:
  - bearerAuth: []
```

> ⚠️ **LESSON LEARNED 27/05/2026:** ChatGPT OpenAPI validator **strict hơn standard** — bắt buộc `components.schemas` (dù có dùng hay không). Schema KHÔNG có `components.schemas` sẽ báo lỗi đỏ `"In components section, schemas subsection is not an object"` và không save được. Schema trên đã có `schemas.Memory` để pass validation.

**Bước 8.3.3 — Cấu hình Authentication**

Bên dưới schema editor, bấm biểu tượng **bánh răng (gear icon)** ở mục Authentication:

1. **Authentication Type:** chọn `API Key`
2. **API Key:** dán giá trị `CHATGPT_AUTH_TOKEN` (từ Bước 8.2.1)
3. **Auth Type:** chọn `Bearer`
4. Bấm **Save**

Sau khi save, schema editor sẽ liệt kê 3 actions: `addMemory`, `listMemories`, `searchMemory` — đây là dấu hiệu Custom GPT đã parse OpenAPI thành công.

**Bước 8.3.4 — Privacy policy URL**

Custom GPT yêu cầu Privacy URL trước khi save. Vì GPT này KHÔNG public (Only me), bạn có 2 cách:

*Cách 1 — Dán URL placeholder hợp lệ:* `https://anthropic.com/legal/privacy` (chỉ pass validation, không ai khác xem được GPT của bạn).

*Cách 2 — Tạo file static trên VPS:* SSH vps, tạo `~/memory-stack/Caddyfile` thêm handle:

```caddyfile
    handle /privacy {
        respond "Personal mem0 wrapper. Data stored on user's own VPS. No third-party sharing." 200
    }
```

Reload Caddy: `docker compose restart caddy`. URL: `https://claude.hangocthanh.io.vn/privacy`.

**Bước 8.3.5 — Save GPT**

Bấm **Create** (góc phải trên) → chọn:
- **Only me** (BẮT BUỘC — vì có token nhúng, không được public)
- Confirm

Sau khi save, GPT xuất hiện ở menu trái dưới mục "GPTs by Me".

---

### 8.4 Test 4 scenarios

Mở chat mới với GPT vừa tạo (sidebar trái → "Thanh's Memory Assistant").

**Test 1 — Search trước khi trả lời (đọc fact đã add từ curl ở Bước 8.2.7):**

```
Bạn nhớ gì về sở thích đồ uống và laptop của tôi?
```

✅ Kết quả mong đợi:
- GPT hiện block "Used Thanh Memory API → searchMemory" (có thể bạn cần Allow lần đầu)
- Trả lời nêu được "cà phê đen đá pha phin" và "Dell XPS 15 9530"
- KHÔNG bịa thông tin khác

**Test 2 — Add memory mới:**

```
Nhớ giúp tôi: tôi đang triển khai memory server tự host trên VPS Singapore với domain claude.hangocthanh.io.vn.
```

✅ Kết quả:
- GPT gọi `addMemory`
- Trả về xác nhận "Đã lưu" / "OK đã ghi lại"

**Test 3 — Cross-device sync ⭐ (test quan trọng nhất):**

Mở VS Code Claude Code, gõ trong chat panel:

```
Bạn biết gì về VPS của tôi?
```

✅ Kết quả:
- Claude Code (qua mem0 MCP tool `search_memory`) tìm thấy fact "VPS Singapore claude.hangocthanh.io.vn" mà ChatGPT vừa lưu ở Test 2
- Đây là **bằng chứng đồng bộ 2 chiều** giữa ChatGPT và Claude Code

Nếu Claude Code KHÔNG tìm thấy → kiểm tra T8.5 (collection mismatch).

**Test 4 — List toàn bộ:**

Quay lại ChatGPT:

```
Liệt kê 20 thứ bạn nhớ về tôi.
```

✅ GPT gọi `listMemories?limit=20` → trả bảng các memory đã lưu (từ cả ChatGPT, Claude Code, và curl test trước đó).

---

### 8.5 Troubleshooting Section 8

**T8.1 — Custom GPT báo `Could not load action schema` khi paste OpenAPI**
→ YAML sai indent (thường do paste từ Markdown không giữ space). Mở 1 editor riêng (VS Code), dán schema, đảm bảo indent 2 space đồng nhất, paste lại.

**T8.2 — GPT gọi tool xong báo `401 Unauthorized`**
→ Token nhập sai chỗ. KHÔNG dán vào Instructions — phải vào **Authentication → API Key → Bearer**. Verify: `curl -H "Authorization: Bearer $TOKEN" https://claude.hangocthanh.io.vn/memory/memories?limit=1` từ terminal phải trả JSON, không phải `Unauthorized`.

**T8.3 — GPT trả `502 Bad Gateway` hoặc `503 Service Unavailable`**
→ Container `memory-rest-api` down hoặc chưa build. SSH vps:
```bash
docker compose ps
docker compose logs memory-rest-api --tail=50
```
Nếu thấy `ImportError: No module named 'mem0'` → Dockerfile pip install fail, rebuild: `docker compose up -d --build memory-rest-api`.

**T8.4 — GPT trả lời mà không gọi tool** (bịa câu trả lời)
→ Instructions chưa đủ mạnh. Bật tab Configure của GPT, thêm dòng đầu Instructions:
```
TUYỆT ĐỐI BẮT BUỘC: TRƯỚC mọi câu trả lời, GỌI searchMemory với query là từ khóa của câu hỏi. KHÔNG có ngoại lệ. Vi phạm quy tắc này = trả lời SAI.
```

**T8.5 — Test 3 fail: Claude Code không thấy memory ChatGPT vừa lưu**
→ Hai client dùng collection khác nhau. Kiểm tra:
```bash
# Trên VPS:
docker run --rm --network memory-stack_memnet curlimages/curl -s \
  http://qdrant:6333/collections \
  -H "api-key: $(grep QDRANT_API_KEY ~/memory-stack/.env | cut -d= -f2)"
```
Đếm số collection. Nếu thấy `mem0` + `thanh_memories` (2 collection khác nhau) → là vấn đề. Cách fix:

*Cách 1 (dễ):* Đổi `COLLECTION_NAME` trong `docker-compose.yml` (memory-rest-api environment) thành đúng tên Claude Code đang dùng (kiểm tra log MCP server `claude mcp logs mem0` xem nó dùng collection nào). Rebuild: `docker compose up -d --build memory-rest-api`.

*Cách 2 (sạch hơn):* Migrate hết memory từ collection cũ sang `mem0_mcp_selfhosted`, drop collection cũ. Chỉ làm nếu collection cũ đã có nhiều memory.

**T8.6 — `UnicodeEncodeError: 'ascii' codec can't encode character '\u1ee7'` (LESSON 27/05)**

POST `/memories` trả 500. Log container có traceback `httpx/_models.py: value.encode("ascii")` → `UnicodeEncodeError`.

Nguyên nhân: `OPENAI_API_KEY` trong `.env` **chứa ký tự tiếng Việt** (ví dụ vẫn còn placeholder `<sk-...key-của-bạn>` với chữ "của" — `ủ` là `\u1ee7`). HTTP headers bắt buộc ASCII pure → httpx (lib openai) crash trước khi gửi request.

Fix:
```bash
ssh vps
sed -i '/^OPENAI_API_KEY=/d' ~/memory-stack/.env
echo 'OPENAI_API_KEY=sk-proj-...key-thật-ASCII' >> ~/memory-stack/.env
docker compose up -d --force-recreate memory-rest-api
```

Verify key ASCII pure:
```bash
grep OPENAI_API_KEY ~/memory-stack/.env | od -c | head -5
```
Không thấy byte `\357` `\273` (BOM) hay byte non-ASCII.

**T8.7 — Custom GPT báo `"In components section, schemas subsection is not an object"` khi paste OpenAPI (LESSON 27/05)**

Schema YAML thiếu `components.schemas`. ChatGPT validator strict — bắt buộc có dù không dùng. Schema ở Bước 8.3.2 (mới) đã có `Memory` schema để pass.

Workaround tối thiểu: thêm vào cuối schema:
```yaml
components:
  schemas:
    Empty:
      type: object
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
```

**T8.8 — Custom GPT báo `"Action sets cannot have duplicate domains"` khi save (LESSON 27/05)**

Bạn đã tạo nhiều Action cùng trỏ domain `claude.hangocthanh.io.vn` (vd: 1 lần bị lỗi schema, sau đó tạo Action thứ 2). ChatGPT cấm duplicate domain trong 1 Custom GPT.

Fix: vào tab Configure → mục Actions → bấm Action **cũ** → cuối trang → **Delete action**. Chỉ giữ 1 Action duy nhất với schema mới.

**T8.9 — Custom GPT confirm mỗi lần gọi API, không có "Always allow" (LESSON 27/05)**

Đây **policy cố ý của OpenAI** cho Custom GPT cá nhân (Only me), KHÔNG phải bug. OpenAI Platform có "Verified domains" trên 1 số tier — nhưng nhiều tài khoản KHÔNG có feature này.

3 cách xử lý:
- **A.** Thử verify domain ở `platform.openai.com/settings/organization` → nếu có Domain Verification → setup theo HTTP file challenge (xem section Verify Domain bên dưới)
- **B.** Chấp nhận confirm mỗi lần (1 click — không tệ với usage thi thoảng)
- **C.** Workflow chính dùng **Continue.dev** (Section 11) + **Claude Code** — không cần confirm

→ Khuyến nghị Cách C: ChatGPT chỉ để backup khi off VS Code.

**T8.10 — Continue.dev báo `Error getting context items from http: SyntaxError: Unexpected token 'I', "Internal S"... is not valid JSON` (LESSON 27/05)**

Wrapper trả `Internal Server Error` (text plain), Continue cố parse JSON → fail. Nguyên nhân thường gặp:
- `OPENAI_API_KEY` invalid / rotate chưa update VPS (xem T8.6)
- `COLLECTION_NAME` mismatch (T8.5)
- Body Continue gửi chứa ký tự đặc biệt make wrapper crash

Debug:
```bash
# Terminal 1: stream log wrapper
ssh vps
docker compose -f ~/memory-stack/docker-compose.yml logs -f memory-rest-api

# Terminal 2: trigger Continue
# VS Code → Continue panel → @Memory <query>
```

Quan sát log Terminal 1 lúc Continue gọi → traceback hiện ngay → fix theo exception cụ thể.

**T8.11 — `docker compose up -d --build memory-rest-api` fail với `pip install timeout`**
→ Mạng VPS chậm hoặc PyPI rate limit. Retry sau 2 phút. Nếu lặp lại, thêm `--network host` vào lệnh build hoặc dùng PyPI mirror VN (config trong Dockerfile: `RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple/`).

**T8.12 — Custom GPT không cho save (báo Privacy URL required)**
→ Bạn quên điền Privacy URL ở Bước 8.3.4. Quay lại tab Configure → kéo xuống cuối → ô **Privacy Policy** → dán URL (xem 8.3.4 Cách 1 hoặc Cách 2).

---

## 9. Glossary

- **MCP (Model Context Protocol)**: Giao thức chuẩn để AI agent giao tiếp với tools/data bên ngoài.
- **OAT (OAuth Access Token)**: Token Claude Code lấy từ tài khoản Max khi đăng nhập.
- **mem0**: Framework Python quản lý memory cho AI agents.
- **Qdrant**: Vector database mã nguồn mở.
- **pgvector**: Extension Postgres lưu/search vector.
- **Caddy**: Web server tự động lấy TLS cert qua Let's Encrypt.
- **Heredoc**: Cú pháp `cat > file << 'EOF' ... EOF` để tạo file nguyên khối từ dòng lệnh — "copy chạy ngay", không cần mở editor.
- **PowerShell**: Shell mặc định trên Windows. Lưu ý `curl` là alias của `Invoke-WebRequest` — gọi cURL thật phải gõ `curl.exe`.
- **OpenSSH for Windows**: Bộ `ssh`, `ssh-keygen` tích hợp sẵn Windows 10 (1809+)/11. Không có `ssh-copy-id`.
- **HTTP proxy / CONNECT**: Proxy công ty mở "đường ống" TCP tới đích bằng phương thức CONNECT — thường chỉ cho cổng 443.
- **connect.exe**: Công cụ nhỏ (có sẵn trong Git for Windows) làm trung gian cho `ssh` đi qua HTTP proxy, dùng trong `ProxyCommand`.
- **ProxyCommand**: Chỉ thị trong `~/.ssh/config` để `ssh` chạy qua một chương trình trung gian — cách duy nhất cho `ssh` đi qua proxy.
- **sslh**: Bộ phân luồng cổng — nghe cổng 443, tự nhận biết kết nối là SSH hay HTTPS rồi chuyển hướng tương ứng. Giúp SSH và Caddy dùng chung cổng 443.
- **socket activation**: Cơ chế `systemd` mở sẵn cổng dịch vụ; trên Ubuntu 24.04 khiến `Port` trong `sshd_config` bị bỏ qua.
- **launchd / Task Scheduler**: Bộ chạy task định kỳ — `launchd` của macOS, `Task Scheduler` của Windows.

---

## 10. Checklist hoàn tất

- [ ] Pre-flight: SSH key đã tạo đúng tên mặc định (mục 3.1)
- [ ] Kết nối được VPS — trực tiếp (mục 3.3) hoặc qua proxy (mục 3.2)
- [ ] Giai đoạn 1: VPS hardened (SSH key-only, UFW, fail2ban, Docker)
- [ ] Giai đoạn 2: (proxy) sslh chạy; storage stack chạy, TLS hoạt động, bearer auth verified
- [ ] Giai đoạn 3: uv/uvx chạy được trên máy client
- [ ] Giai đoạn 4: MCP `mem0` connected, thấy 11 tools
- [ ] Giai đoạn 5: 4 test scenarios mem0 đều pass
- [ ] Giai đoạn 6: Backup daily chạy, monitor có alert
- [ ] Giai đoạn 7: Transcript archive — table, REST API, uploader tự động, MCP archive connected
- [ ] (Optional) Giai đoạn 8: ChatGPT Desktop với Custom GPT + memory-rest-api
- [ ] (Optional) Giai đoạn 11: VS Code Continue.dev với mem0 context provider

---

## 11. VS Code Continue.dev cùng đọc mem0 (30 phút)

> **Phụ thuộc:** Section 8 đã hoàn thành (`memory-rest-api` chạy trên VPS). Continue.dev gọi cùng REST endpoint mà ChatGPT dùng — không cần dựng infra mới.

### 11.1 Tổng quan — Continue.dev là gì và vì sao chọn nó

**Continue.dev** là extension free open-source cho VS Code, biến editor thành "Cursor / GitHub Copilot tự quản" — bạn cắm API key của bất kỳ AI provider nào (OpenAI, Anthropic, Gemini, Ollama local) và Continue cho 4 tính năng: **chat panel**, **inline edit (Ctrl+I)**, **tab autocomplete**, và **context provider tùy ý**.

**Vì sao chọn Continue.dev thay vì alternative:**

| Tiêu chí | GitHub Copilot Chat | Cursor | Cline | **Continue.dev** |
|---|---|---|---|---|
| Free / open-source | ❌ $10/tháng | ❌ $20/tháng | ✅ | ✅ |
| Hook context provider tùy ý | ❌ | ⚠️ Hạn chế | ✅ qua MCP | ✅ **qua HTTP native** |
| Cần MCP server public trên VPS | n/a | n/a | ✅ phải dựng | ❌ dùng REST sẵn có |
| Tích hợp mem0 dễ | Khó | Khó | Vừa | **Dễ nhất** |
| Bộ AI hỗ trợ | Chỉ GPT của MS | Mặc định GPT/Claude | Bất kỳ MCP | OpenAI/Anthropic/Gemini/Ollama |

**Vai trò trong workflow của bạn:**
- **Claude Code** (đã có): task agent dài (refactor, build feature, debug deep) — dùng OAT Max, FREE
- **Continue.dev** (sẽ thêm): chat nhanh + autocomplete + inline edit — dùng GPT-4o-mini API, ~$1-3/tháng cá nhân
- Cả 2 dùng chung `memory-rest-api` → memory đồng bộ tự động

**Vì sao Continue gọi REST chứ không MCP:** Continue.dev hỗ trợ "context provider" qua HTTP native (tính năng `name: "http"`). Bạn không cần dựng MCP HTTP server riêng cho Continue — nó gọi thẳng `POST /memory/memories/search` của `memory-rest-api` đã có. Đây là điểm khác biệt lớn nhất với Cline (Cline bắt buộc qua MCP).

---

### 11.2 Cài extension Continue.dev (chi tiết từng bước)

**Bước 11.2.1 — Mở VS Code Marketplace**

1. Mở VS Code
2. Bấm icon **Extensions** ở sidebar trái (hoặc phím tắt `Ctrl+Shift+X` / Mac `Cmd+Shift+X`)
3. Trong ô search, gõ chính xác: `Continue`

**Bước 11.2.2 — Cài đúng extension (TRÁNH NHẦM)**

Có nhiều extension tên gần giống. Cài đúng extension:
- **Tên:** `Continue - Codestral, Claude, and more`
- **Publisher:** `Continue` (có dấu tick xanh verified)
- **ID:** `Continue.continue`
- **Cài đặt:** thường > 1 triệu lượt

⚠️ KHÔNG cài các extension khác cùng tên:
- `Continue Extension Pack` (gói gộp, không cần)
- `Continue [Beta]` (bản test, không ổn định)

Bấm **Install**. Sau khi cài, VS Code có thêm icon Continue ở **sidebar trái** (hình logo cube xoay).

**Bước 11.2.3 — Đóng wizard chào mừng**

Lần đầu mở, Continue hiện wizard onboarding hỏi:
- Chọn provider AI → bấm **Skip** (sẽ config tay sau)
- Chọn template → bấm **Skip**
- Nếu nó hỏi đăng nhập Continue Hub → bấm **Use Continue locally** (KHÔNG đăng nhập cloud của họ)

> Vì sao skip wizard? Wizard ép default config gợi ý từ Continue Hub (cloud của họ) → ghi đè file `config.json` mỗi lần update. Skip để ta toàn quyền viết config trỏ tới mem0 của riêng mình.

**Bước 11.2.4 — Xác định vị trí file config**

Continue lưu config tại:

- **Mac:** `~/.continue/config.json`
- **Windows:** `C:\Users\<tên-user>\.continue\config.json` (vd: `C:\Users\thanhhn5\.continue\config.json`)
- **Linux:** `~/.continue/config.json`

Mở bằng VS Code:

**Trên Mac — Terminal:**
```bash
code ~/.continue/config.json
```

**Trên Windows — PowerShell:**
```powershell
code "$env:USERPROFILE\.continue\config.json"
```

Nếu file chưa tồn tại (do bạn skip wizard), tạo mới:

**Trên Mac:**
```bash
mkdir -p ~/.continue
touch ~/.continue/config.json
code ~/.continue/config.json
```

**Trên Windows:**
```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.continue" | Out-Null
New-Item -ItemType File -Force -Path "$env:USERPROFILE\.continue\config.json" | Out-Null
code "$env:USERPROFILE\.continue\config.json"
```

---

### 11.3 Cấu hình Continue (cắm mem0 vào)

> ⚠️ **LESSON LEARNED 27/05/2026:** Continue version 1.x trở lên **đã chuyển từ `config.json` sang `config.yaml`**. Verify version trước khi paste config:
>
> ```powershell
> code --list-extensions --show-versions | Select-String "continue"
> ```
>
> - Version `0.9.x` → dùng `config.json` (xem Cách A bên dưới)
> - Version `1.x+` → dùng `config.yaml` (xem Cách B — KHUYẾN NGHỊ)

#### Cách A — `config.json` (Continue v0.9.x)

**Bước 11.3.1 — Dán cấu hình đầy đủ**

Mở `config.json` đã tạo ở Bước 11.2.4, dán nguyên khối sau (thay `<OPENAI_API_KEY>` và `<CHATGPT_AUTH_TOKEN>` bằng giá trị thật):

```json
{
  "models": [
    {
      "title": "GPT-4o-mini (chat + edit)",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "apiKey": "<OPENAI_API_KEY>"
    },
    {
      "title": "GPT-4o (heavy tasks)",
      "provider": "openai",
      "model": "gpt-4o",
      "apiKey": "<OPENAI_API_KEY>"
    }
  ],
  "tabAutocompleteModel": {
    "title": "GPT-4o-mini autocomplete",
    "provider": "openai",
    "model": "gpt-4o-mini",
    "apiKey": "<OPENAI_API_KEY>"
  },
  "embeddingsProvider": {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "apiKey": "<OPENAI_API_KEY>"
  },
  "contextProviders": [
    {
      "name": "code",
      "params": {}
    },
    {
      "name": "diff",
      "params": {}
    },
    {
      "name": "open",
      "params": {}
    },
    {
      "name": "terminal",
      "params": {}
    },
    {
      "name": "http",
      "params": {
        "name": "mem0",
        "description": "Personal memory from self-hosted mem0",
        "displayTitle": "Memory",
        "url": "https://claude.hangocthanh.io.vn/memory/memories/search",
        "headers": {
          "Authorization": "Bearer <CHATGPT_AUTH_TOKEN>",
          "Content-Type": "application/json"
        },
        "options": {
          "method": "POST",
          "body": {
            "query": "{query}",
            "user_id": "thanh",
            "limit": 10
          }
        }
      }
    }
  ],
  "slashCommands": [
    {
      "name": "edit",
      "description": "Edit selected code"
    },
    {
      "name": "comment",
      "description": "Write comments for the selected code"
    },
    {
      "name": "share",
      "description": "Export chat to markdown"
    }
  ],
  "customCommands": [
    {
      "name": "remember",
      "prompt": "Hãy gọi mem0 để lưu thông tin sau đây thành memory dài hạn (ghi nó vào kho mem0 chung của tôi qua REST API).",
      "description": "Lưu memory mới vào mem0"
    }
  ],
  "allowAnonymousTelemetry": false
}
```

> **Vì sao có cả `gpt-4o-mini` và `gpt-4o`?** Mini cho chat nhanh + autocomplete (rẻ, đủ chính xác cho 90% case). 4o đầy đủ cho task khó (debug logic phức tạp, refactor lớn). Bạn switch model trong chat panel bằng dropdown phía trên.

> **Vì sao tắt `allowAnonymousTelemetry`?** Continue mặc định gửi telemetry về cloud của họ. Tắt để 100% local + VPS của bạn — không lộ pattern dùng AI ra ngoài.

**Bước 11.3.2 — Save và reload**

Lưu file (Ctrl+S). VS Code tự reload Continue, hiện toast `Continue config reloaded`. Nếu không tự reload:

- Mở Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`)
- Gõ: `Continue: Reload`
- Enter

**Bước 11.3.3 — Verify cấu hình**

1. Bấm icon Continue ở sidebar trái → hiện chat panel
2. Phía trên chat panel, dropdown chọn model phải hiển thị `GPT-4o-mini (chat + edit)` (đúng title trong config)
3. Trong ô chat, gõ ký tự `@` → hiện danh sách context provider → phải thấy `mem0` (do `displayTitle: "Memory"`)

Nếu thấy `mem0` trong danh sách `@` → cấu hình đã đúng. Sang Bước 11.4 test.

Nếu KHÔNG thấy `mem0` → xem T11.1 troubleshooting.

---

#### Cách B — `config.yaml` (Continue v1.x — KHUYẾN NGHỊ)

Mở PowerShell, chạy 1 lệnh heredoc tạo file (thay `YOUR_OPENAI_KEY_HERE` 3 chỗ + `<CHATGPT_AUTH_TOKEN>` bằng giá trị thật):

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.continue" | Out-Null

@'
name: My Local Assistant
version: 1.0.0
schema: v1
models:
  - name: GPT-4o-mini
    provider: openai
    model: gpt-4o-mini
    apiKey: YOUR_OPENAI_KEY_HERE
    roles:
      - chat
      - edit
      - apply
  - name: GPT-4o-mini autocomplete
    provider: openai
    model: gpt-4o-mini
    apiKey: YOUR_OPENAI_KEY_HERE
    roles:
      - autocomplete
  - name: text-embedding-3-small
    provider: openai
    model: text-embedding-3-small
    apiKey: YOUR_OPENAI_KEY_HERE
    roles:
      - embed
context:
  - provider: code
  - provider: diff
  - provider: open
  - provider: terminal
  - provider: http
    params:
      name: mem0
      displayTitle: Memory
      url: https://claude.hangocthanh.io.vn/memory/memories/search
      headers:
        Authorization: "Bearer <CHATGPT_AUTH_TOKEN>"
        Content-Type: "application/json"
      options:
        method: POST
        body:
          query: "{query}"
          user_id: thanh
          limit: 10
'@ | Set-Content -Path "$env:USERPROFILE\.continue\config.yaml" -Encoding utf8
```

⚠️ Sau khi tạo `config.yaml`: XÓA `config.json` cũ (nếu có) để tránh Continue confusing. Sau đó **File → Exit** VS Code rồi mở lại (Reload Window không đủ).

```powershell
Remove-Item "$env:USERPROFILE\.continue\config.json" -Force -ErrorAction SilentlyContinue
```

Khác biệt YAML vs JSON:

| | JSON v0.x | YAML v1.x |
|---|---|---|
| Multi-model | 3 keys riêng (`models`, `tabAutocompleteModel`, `embeddingsProvider`) | 1 array `models[]` với `roles[]` |
| Context provider | `contextProviders[]` | `context[]` |
| Hub integration | Không | Có (skip nếu dùng local) |

---

### 11.4 Test 3 scenarios

**Test 1 — Query memory từ Continue chat:**

Mở Continue chat panel (icon sidebar), gõ:

```
@mem0 deadline
```

Sau khi gõ `@`, chọn `Memory` từ dropdown, gõ tiếp keyword `deadline` rồi Enter. Continue sẽ:
1. Gọi `POST https://claude.hangocthanh.io.vn/memory/memories/search` với body `{"query": "deadline", "user_id": "thanh"}`
2. Kết quả trả về được chèn vào context của câu hỏi
3. Gửi cho GPT-4o-mini cùng câu hỏi của bạn

✅ Pass: GPT trả lời có đề cập "deadline v2 mem0: 15/06/2026" (memory ChatGPT đã lưu ở Test 2 Section 8.4).

**Test 2 — Cross-AI sync ⭐ (test quan trọng nhất):**

Trong ChatGPT (web hoặc Desktop), chat với Custom GPT đã tạo:

```
Nhớ giúp tôi: tôi muốn dùng pattern Repository cho data layer trong project Mem0.
```

→ ChatGPT lưu vào mem0 qua `addMemory`.

Quay lại VS Code Continue chat panel:

```
@mem0 code style pattern data layer
```

✅ Pass: Continue trả ra fact "Repository pattern cho data layer" mà ChatGPT vừa lưu ~30 giây trước → **đồng bộ 2 chiều thành công**.

**Test 3 — Inline edit với context mem0:**

1. Mở 1 file Python trong VS Code (bất kỳ)
2. Bôi đen 1 đoạn code (ví dụ 1 function)
3. Bấm `Ctrl+I` (Mac: `Cmd+I`) → hiện inline edit box
4. Gõ:
```
@mem0 code style — refactor function này theo style tôi thích
```

✅ Pass: Continue tự gọi mem0 lấy preference code style của bạn (Repository pattern + Black + isort... nếu đã lưu), gửi context + code đã chọn cho GPT-4o-mini, kết quả refactor xuất hiện inline.

---

### 11.5 Sử dụng hàng ngày (cheatsheet phím tắt)

| Phím tắt | Tác dụng |
|---|---|
| `Ctrl+L` (Mac: `Cmd+L`) | Mở Continue chat panel + tự bôi đen code đang chọn vào context |
| `Ctrl+I` (Mac: `Cmd+I`) | Inline edit — sửa code tại chỗ |
| `Ctrl+Shift+L` | Hỏi nhanh về code đang chọn |
| `Tab` (khi gợi ý hiện) | Accept tab autocomplete |
| `Esc` | Reject autocomplete |
| `@mem0` trong chat | Inject memory vào câu hỏi |
| `@code`, `@diff`, `@open`, `@terminal` | Inject code file, git diff, file đang mở, output terminal |
| `/edit` | Edit code đã chọn (chậm hơn `Ctrl+I` nhưng có thể đa file) |
| `/remember <text>` | (Custom command đã setup) Lưu memory mới qua Claude Code MCP |

**Combo workflow đề xuất:**

- **Khi code mới:** Bôi đen → `Ctrl+L` → "Giải thích đoạn code này, có nên dùng pattern khác không?" + `@mem0` (lấy style preference)
- **Khi sửa bug:** `Ctrl+I` → "Fix bug X" + `@diff` (gửi diff hiện tại để AI thấy context)
- **Khi muốn AI nhớ quyết định:** Dùng Claude Code (có MCP `add_memory` tự động), HOẶC gọi ChatGPT Custom GPT "nhớ giúp tôi: ..." — KHÔNG dùng Continue để lưu memory vì Continue chưa support gọi POST tùy ý qua chat (chỉ search được).

---

### 11.6 Troubleshooting Section 11

**T11.1 — Gõ `@` không thấy `Memory` trong danh sách context provider**
→ `config.json` sai JSON syntax. Mở Output panel VS Code (View → Output → chọn channel "Continue") xem log. Thường lỗi: thiếu dấu `,` giữa các block, hoặc dùng dấu nháy đơn `'` thay vì nháy kép `"`. Sửa, save, Reload.

**T11.2 — Continue gọi mem0 nhưng trả `401 Unauthorized`**
→ Token sai hoặc thiếu prefix `Bearer `. Kiểm tra trong `config.json`:
```json
"headers": { "Authorization": "Bearer <token>", ... }
```
Đúng phải là `"Bearer <token>"` có khoảng trắng, KHÔNG phải chỉ `<token>`. Verify từ terminal:
```bash
curl -X POST https://claude.hangocthanh.io.vn/memory/memories/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"test"}'
```
Nếu curl OK mà Continue 401 → token trong config.json là token cũ, copy lại từ VPS.

**T11.3 — Continue trả về kết quả nhưng GPT-4o-mini không "thấy" memory**
→ Body request sai format. Continue substitute `{query}` vào template body. Kiểm tra:
```json
"body": {
  "query": "{query}",   // Continue tự thay {query} bằng từ khóa user gõ
  "user_id": "thanh",
  "limit": 10
}
```
Nếu thiếu `"query": "{query}"` → mem0 search rỗng → AI không thấy gì. Đảm bảo đúng tên placeholder `{query}` (không phải `${query}` hay `{q}`).

**T11.4 — Tab autocomplete chậm hoặc không gợi ý**
→ Mặc định Continue dùng model "edit" cho autocomplete (chậm + đắt). Kiểm tra `config.json` có block `tabAutocompleteModel` riêng dùng model nhẹ (vd `gpt-4o-mini`). Nếu muốn tắt hẳn autocomplete (chỉ dùng chat): xóa block `tabAutocompleteModel`.

**T11.5 — OpenAI API key dùng chung giữa Continue và `memory-rest-api` có xung đột không?**
→ KHÔNG. Cùng 1 key có thể gọi từ nhiều client song song — OpenAI rate limit theo organization, không theo client. Chi phí cộng dồn vào cùng tài khoản. Nếu muốn tách bill: tạo 2 keys khác nhau trong cùng project (Platform → API keys → Create).

**T11.6 — Mạng công ty proxy 10.121.127.204:3128 — Continue không gọi được OpenAI**
→ Continue đọc biến môi trường `HTTPS_PROXY`. Đặt biến trước khi mở VS Code:

**Trên Windows — PowerShell** (đã đặt vĩnh viễn ở Pre-flight 3.2.2 thì bỏ qua):
```powershell
[Environment]::SetEnvironmentVariable("HTTPS_PROXY", "http://10.121.127.204:3128", "User")
```
Đóng hẳn VS Code (Exit, không Reload Window — xem B30), mở lại.

**Trên Mac:** thường không cần proxy. Nếu có proxy cá nhân, set trong `~/.zshrc`:
```bash
export HTTPS_PROXY=http://proxy:port
```

**T11.7 — Continue UI báo "Configuration error" sau khi paste config**
→ JSON có comment (`//`) không hợp lệ trong JSON chuẩn. Xóa toàn bộ comment. Continue chỉ chấp nhận pure JSON (không phải JSON5 hay JSONC).

---



*Tài liệu gốc tạo ngày 17/05/2026. Bản cập nhật 25/05/2026: tên miền `claude.hangocthanh.io.vn`; hướng dẫn Windows (PowerShell) song song Mac; phần kết nối qua proxy công ty (mục 3.2) và troubleshooting chi tiết; mọi lệnh tạo file chuyển sang dạng copy-chạy-ngay (heredoc), dùng `vi` thay `nano`.*

*Bản cập nhật 26/05/2026 (post-deployment lessons): Thêm **Section 2.4** so sánh Postgres local vs Neon Cloud; thêm **P8** trong Pre-flight về `Set-ExecutionPolicy RemoteSigned` trên Windows; thêm 10 troubleshooting **B22-B31** (ExecutionPolicy, urllib proxy Windows, httpx 0.28 API, PowerShell paste Python, USER_ID typo, Postgres orphan, backtick continuation, Task Scheduler popup, VS Code MCP cache, Claude Code save MCP response file). Bước 7.6 update sang ONE-LINE + Hidden window. Bước 7.7 update ONE-LINE + bắt buộc `--env HTTP_PROXY/HTTPS_PROXY` + archive-mcp.py 4 tools (thêm `get_session_summary`) + test 5 scenarios.*

*Bản cập nhật 26/05/2026 (afternoon — Compact Summary feature): Thêm **Bước 7.8** — Compact Summary feature (3 phases: Neon SQL table mới `compact_summaries` + VPS app.py thêm 3 endpoints `/compact-summaries` POST/GET list/GET by id + Windows scripts update `archive-upload.py` extract summaries + `archive-mcp.py` 7 tools — thêm 3 tools mới `list_compact_summaries`, `search_compact_summaries`, `get_compact_summary`). Thêm **B32** về `CREATE TABLE IF NOT EXISTS` không update schema khi table đã tồn tại — phải dùng `ALTER TABLE ADD COLUMN IF NOT EXISTS` để thêm columns mới vào table cũ.*

*Bản cập nhật 27/05/2026 (evening — Performance optimization v0.3.9): Thêm **Bước 7.9** (HTTP transport mode) và **Bước 7.10** (Source code optimization v0.3.9). Thêm 3 troubleshooting **B33-B35** (MCP timeout respawn loop, uvx cache stale, antivirus scan slowness). Source fork đã được optimize với 7 thay đổi: lazy `anthropic` import (save 5-15s Windows), conditional Ollama provider registration (save 3-8s), lazy `graph_tools` import (save 1-3s), conditional patches (save 1-2s), defensive stubs cho missing packages, version bumped 0.3.8→0.3.9. Benchmark Windows: prewarm 95-195s → 11-28s (saving ~80%). Workspace có sẵn 4 scripts ready-to-run: `test-mem0-prewarm.ps1` (đo thời gian), `deploy-mem0-stdio.ps1` (deploy stdio với MCP_TIMEOUT 60s), `precompile-mem0-pyc.ps1` (pre-compile bytecode), `setup-http-mode.ps1` (fallback HTTP server warm 24/7).*

*Bản cập nhật 27/05/2026 (night — Multi-AI cross-sync): Thêm **Section 8 mở rộng** (ChatGPT Desktop + Custom GPT với OpenAPI Action, REST wrapper `memory-rest-api` mới trên VPS dùng cùng Qdrant collection với Claude Code → đồng bộ memory 2 chiều) và **Section 11 mới** (VS Code Continue.dev extension cài qua Marketplace, cấu hình `~/.continue/config.json` với HTTP context provider trỏ tới `/memory/memories/search`, GPT-4o-mini cho chat/edit/autocomplete). Thêm 5 troubleshooting mới T8.1-T8.7 + T11.1-T11.7. Workflow mới: 3 AI client (Claude Code + ChatGPT + Continue) cùng 1 kho memory, ChatGPT/Continue ghi → Claude Code đọc được ngay và ngược lại.*

*Bản cập nhật 27/05/2026 (deployment lessons — Multi-AI debug session): Thêm 5 troubleshooting **T8.6-T8.10** từ deployment thực tế: (T8.6) UnicodeEncodeError do placeholder OPENAI_API_KEY có chữ "của" tiếng Việt — đổi placeholder sang ASCII pure `YOUR_OPENAI_KEY_HERE`. (T8.7) ChatGPT OpenAPI validator strict bắt buộc `components.schemas` — cập nhật schema Bước 8.3.2. (T8.8) ChatGPT cấm duplicate Action domain — phải Delete action cũ trước khi tạo mới. (T8.9) Personal Custom GPT KHÔNG có "Always allow" option — khuyến nghị workflow chính qua Continue.dev/Claude Code. (T8.10) Continue.dev báo JSON parse error khi wrapper trả 500 — debug bằng log streaming 2 terminal. Cập nhật `COLLECTION_NAME` default từ `mem0` sang `mem0_mcp_selfhosted` (fork mem0-mcp-selfhosted override default collection name). Section 11.3 thêm Cách B `config.yaml` cho Continue v1.x (Continue đã migrate JSON→YAML).*

*Khi có version mới của mem0-mcp-selfhosted hoặc Claude Code, kiểm tra lại README của repo.*
