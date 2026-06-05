# Hướng dẫn triển khai R2 Storage cho mem0custom

Tài liệu này hướng dẫn từng bước thiết lập **Cloudflare R2** làm hybrid object storage cho lớp transcript archive của mem0custom, kèm **4 lớp bảo vệ chi phí** (defense in depth) để tránh bill bất ngờ.

**Thông số bản triển khai này:**

| Hạng mục | Giá trị |
|---|---|
| VPS | `claude.hangocthanh.io.vn` (45.119.87.220), Ubuntu 24.04 |
| User VPS | `thanh` |
| Bucket R2 | `mem0-transcripts` (APAC jurisdiction) |
| Free tier R2 | 10 GB storage, 1M Class A ops/tháng, 10M Class B ops/tháng, 0 egress |
| Card limit | $5/tháng (set tại bank) |

> **Quy ước đọc tài liệu:**
>
> - **Lệnh trên VPS (Ubuntu) — khối `bash`.** Đăng nhập bằng `ssh vps`.
> - **Lệnh máy client Windows — khối `powershell`.**
> - **Lệnh máy client Mac — khối `bash`.**
> - **Mọi file đều copy-dán-chạy ngay (heredoc `cat > ... << 'EOF'`).** Không phải mở editor gõ tay.
> - **Trong các giá trị mẫu (`<account-id>`, `<token>`, ...) — bạn thay bằng giá trị thật của mình.**

---

## 1. Tổng quan & Kiến trúc

R2 (object storage) lưu trữ các transcript gzipped từ archive layer. Postgres chỉ giữ metadata + `r2_key` pointer. Khi `GET /sessions/{id}` được gọi → archive-api transparent hydrate từ R2.

```
┌──────────────────────────────────────────────┐
│  Máy client (Mac/Windows)                    │
│  ┌────────────────────────────────────────┐  │
│  │ archive-upload.py (chạy hàng giờ)      │  │
│  │   1. Đọc ~/.claude/projects/*.jsonl    │  │
│  │   2. POST /archive/sessions tới VPS    │  │
│  └────────────────────────────────────────┘  │
└──────────────┬───────────────────────────────┘
               │ HTTPS (443) qua proxy công ty
               ▼
┌──────────────────────────────────────────────┐
│  VPS Singapore — claude.hangocthanh.io.vn    │
│  ┌────────────────────────────────────────┐  │
│  │ archive-api (FastAPI)                  │  │
│  │   1. Nhận POST /sessions                │  │
│  │   2. Gzip transcript                   │  │
│  │   3. PUT lên R2 (boto3)                 │  │
│  │   4. Lưu metadata + r2_key vào Postgres│  │
│  └─────────────┬──────────────────────────┘  │
│                │                              │
│  ┌─────────────▼──────────────────┐  ┌────┐  │
│  │ Postgres / Neon                │  │R2  │  │
│  │ chat_sessions (metadata only)  │  │API │  │
│  │   - id, started_at, summary    │  │443 │  │
│  │   - r2_key, r2_size_bytes      │  │    │  │
│  └────────────────────────────────┘  └─┬──┘  │
│                                         │     │
│  ┌──────────────────────────────────────┘     │
│  │ HTTPS (outbound 443)                       │
└──┼─────────────────────────────────────────────┘
   ▼
┌──────────────────────────────────────────────┐
│  Cloudflare R2 — APAC region                 │
│  Bucket: mem0-transcripts                    │
│  Object key: sessions/<user>/<yyyy>/<mm>/    │
│              <uuid>.json.gz                  │
└──────────────────────────────────────────────┘
```

**Luồng đọc transcript khi cần (load_context_for_continuation strategy=full):**

1. MCP tool `get_old_session` được gọi với `session_id`
2. archive-api query Postgres → lấy `r2_key`
3. archive-api GET object từ R2 → unzip → trả về

Latency từ VPS Singapore tới R2 APAC: ~5-15ms. Hầu hết thời gian dùng cho unzip + serialize, không phải network.

---

## 2. Pre-flight Checklist

Trước khi bắt đầu, hoàn thành các mục sau:

- [ ] **C1** — Có thẻ Visa/Master quốc tế (hoặc virtual card từ Cake/Timo/Wise)
- [ ] **C2** — Quyết định limit thẻ: **$5/tháng** giao dịch quốc tế
- [ ] **C3** — Có email để signup Cloudflare
- [ ] **C4** — VPS đang chạy stack mem0custom branch `main` (đã có archive-api)
- [ ] **C5** — Máy Windows/Mac có `python3` + `pip` + AWS CLI hoặc `boto3`
- [ ] **C6** — Có quyền SSH vào VPS qua `ssh vps`
- [ ] **C7** — Telegram app cài trên điện thoại (cho Lớp 4 protection)
- [ ] **C8** — Hiểu rõ 4 lớp protection ở Section 4 trước khi triển khai

---

## 3. Lựa chọn R2 vs B2

Có thể dùng Cloudflare R2 hoặc Backblaze B2 (cả 2 đều S3-compatible). Code `archive-api/r2_storage.py` chạy được cả 2 chỉ cần đổi env var.

| Tiêu chí | Cloudflare R2 | Backblaze B2 |
|---|---|---|
| **Free tier** | 10 GB storage, 0 egress | 10 GB storage, 1 GB egress/ngày |
| **Storage giá sau free** | $0.015/GB/tháng | **$0.006/GB/tháng** (2.5x rẻ hơn) |
| **Egress giá** | **$0 (mọi lúc)** | $0.01/GB sau 1GB/ngày |
| **Cần thẻ tín dụng** | **CÓ** (verify, không trừ dưới free) | KHÔNG |
| **Latency từ VPS SG** | ~5-15ms (APAC region) | ~150-200ms (US-West/EU) |
| **API** | S3-compatible | S3-compatible (+ native B2) |
| **Region APAC** | ✅ | ❌ |

**Khuyến nghị cho VPS Singapore:**
- **R2** nếu có thẻ + ưu tiên latency
- **B2** nếu không có thẻ hoặc ưu tiên "no card commitment"

Tài liệu này hướng dẫn **R2**. Nếu chọn B2, đọc Section 9 (Troubleshooting → Switch to B2).

---

## 4. 4-Layer Cost Protection

Cloudflare KHÔNG có hard cap tự ngắt service. Để tránh surprise bill, dùng 4 lớp chồng lên nhau:

```
Lớp 1 — Virtual card hạn mức $5/tháng           ← HARD CAP duy nhất
Lớp 2 — Bucket lifecycle rule (auto-delete cũ)   ← Storage growth control
Lớp 3 — Cloudflare Billing Alert ($1, $3)        ← Early warning email
Lớp 4 — VPS cron + Telegram bot                  ← Defense in depth + push
```

| Lớp | Trigger | Hành động |
|---|---|---|
| 1 | Spend tháng > $5 | Bank REJECT charge → service grace period 7-14 ngày |
| 2 | Object > 720 ngày | Cloudflare daily job auto-delete |
| 3 | Spend > $1 (warn), > $3 (critical) | Email tới bạn |
| 4 | Storage > 7GB (warn), > 9GB (critical) | Telegram push từ cron VPS |

**Worst case scenario:** mọi lớp 2-4 fail + token leak + kẻ xấu upload 10TB → Layer 1 reject ở $5 → mất tối đa $5. Không phải catastrophic.

---

## 5. Triển khai chi tiết

### Phase 1 — Set Card Limit (5 phút)

**Vì sao bắt buộc đầu tiên:** Cloudflare không có hard cap. Lớp duy nhất chặn được charge bất ngờ là bank-side limit.

**Hành động:**
1. Mở internet banking / app ngân hàng của thẻ bạn sẽ dùng
2. Tìm mục **"Hạn mức giao dịch trực tuyến quốc tế"** / **"International Online Transaction Limit"**
3. Set 3 limits:
   - **Per transaction:** `$5` (~125,000 VND)
   - **Daily:** `$5`
   - **Monthly:** `$5`
4. Bật **SMS notification** cho mọi giao dịch quốc tế
5. Ghi lại số thẻ + expiry + CVV vào notepad (sẽ paste vào Cloudflare ở Phase 2)

**Verify:** thử thanh toán $1 ở web khác (vd PayPal sandbox) → bank reject với message "Vượt hạn mức".

**Alternative với virtual card (Cake by VPBank):**
- App Cake → "Thẻ" → "Tạo thẻ ảo mới"
- Tên thẻ: `Cloudflare R2`
- Hạn mức tháng: `$5`
- Topup tài khoản Cake ~150k VND
- Lấy số thẻ + CVV + expiry

---

### Phase 2 — Signup Cloudflare + Enable R2 + Create Bucket (15 phút)

#### Step 2A — Signup Cloudflare

1. Mở https://dash.cloudflare.com/sign-up
2. Email + password mạnh (lưu password manager)
3. Verify email qua link Cloudflare gửi
4. Vào dashboard → bỏ qua "Add a site" (chưa cần domain)
5. **Bật 2FA:** góc trên phải → **My Profile** → **Authentication** → setup TOTP/Google Authenticator + lưu backup codes

> 2FA quan trọng: account bị hack = kẻ xấu có thể tạo Workers/R2 tiêu tiền.

#### Step 2B — Enable R2 + Add Card

1. Sidebar trái → click **R2 Object Storage**
2. Bấm **Purchase R2 Plan** (đừng lo từ "Purchase" — free tier auto, không trừ dưới 10GB)
3. Điền billing info:
   - Country: **Vietnam**
   - Address: vd "Hanoi, Vietnam"
   - ZIP: `100000`
4. **Add Payment Method:**
   - Tab **Credit/Debit Card**
   - Card number / Expiry / CVV (từ Phase 1)
5. Bấm **Save**

> Cloudflare có thể charge $1 verify rồi refund trong 1-7 ngày. Bình thường, không phải bug.

#### Step 2C — Create Bucket APAC

1. Bấm **Create bucket**
2. **Bucket name:** `mem0-transcripts` (globally unique — nếu trùng, thử `mem0-transcripts-<your-suffix>`)
3. **Location:** click **"Optional: Specify location"** → chọn **Asia-Pacific (APAC)**
4. **Default storage class:** **Standard** (không phải Infrequent Access)
5. Bấm **Create bucket**

#### Step 2D — Copy Account ID + Endpoint URL

Trong bucket vừa tạo → tab **Settings** → mục **Bucket Details**:

```
Account ID:    <chuỗi 32 ký tự hex>
Endpoint URL:  https://<account-id>.r2.cloudflarestorage.com
```

**Copy 2 giá trị này** vào notepad — dùng cho Phase 3 + Phase 7.

---

### Phase 3 — Tạo API Token + Test PowerShell (15 phút)

#### Step 3A — Tạo Account API Token

1. R2 dashboard → góc trên phải hoặc sidebar → **Manage R2 API Tokens**
2. Có 2 loại token: **Account API Token** và **User API Token** → chọn **Account API Token** (recommended cho production VPS)
3. Bấm **Create Account API token**
4. Điền form:
   - **Token name:** `mem0custom-archive-api`
   - **Permissions:** chọn **Object Read & Write**
   - **Specify bucket(s):** chọn **"Apply to specific buckets only"** → tick **`mem0-transcripts`**
     > KHÔNG chọn "All buckets" — security principle of least privilege
   - **TTL:** **Forever** (hoặc 365 ngày để rotate annual)
   - **Client IP Address Filtering:** để trống

5. Bấm **Create API Token**

6. Cloudflare hiện **một lần duy nhất** 3 giá trị:

```
Token value:            <chuỗi dài>
Access Key ID:          <32 ký tự hex>
Secret Access Key:      <chuỗi dài>
```

**COPY NGAY 3 GIÁ TRỊ NÀY VÀO NOTEPAD** — không lấy lại được sau khi đóng dialog. Phải tạo token mới nếu lỡ.

#### Step 3B — Test từ Windows PowerShell

Cài AWS CLI (nếu chưa):

```powershell
pip install --user awscli
$env:Path += ";$env:APPDATA\Python\Scripts"
aws --version
```

Tạo profile cho R2:

```powershell
aws configure --profile r2
# Điền:
#   AWS Access Key ID:     <paste Access Key ID>
#   AWS Secret Access Key: <paste Secret>
#   Default region name:   auto
#   Default output format: json
```

Test 4 thao tác CRUD:

```powershell
$EP = "https://<account-id>.r2.cloudflarestorage.com"

# 1. PUT upload file
"Hello from PS @ $(Get-Date)" | Out-File r2-test.txt -Encoding ascii
aws s3 cp r2-test.txt s3://mem0-transcripts/r2-test.txt --endpoint-url $EP --profile r2

# 2. LIST
aws s3 ls s3://mem0-transcripts/ --endpoint-url $EP --profile r2

# 3. GET download
aws s3 cp s3://mem0-transcripts/r2-test.txt r2-downloaded.txt --endpoint-url $EP --profile r2
Get-Content r2-downloaded.txt

# 4. DELETE cleanup
aws s3 rm s3://mem0-transcripts/r2-test.txt --endpoint-url $EP --profile r2

# 5. Confirm empty
aws s3 ls s3://mem0-transcripts/ --endpoint-url $EP --profile r2
```

**Pass condition:** 5 lệnh trên chạy không lỗi, lệnh cuối in ra empty.

#### Step 3C — Test từ Mac Terminal

Tương tự PowerShell, lệnh giống nhau nhưng dùng `bash`:

```bash
brew install awscli
aws configure --profile r2
# ... (paste Access Key ID, Secret, region=auto, format=json)

EP="https://<account-id>.r2.cloudflarestorage.com"

echo "Hello from Mac @ $(date)" > r2-test.txt
aws s3 cp r2-test.txt s3://mem0-transcripts/r2-test.txt --endpoint-url $EP --profile r2
aws s3 ls s3://mem0-transcripts/ --endpoint-url $EP --profile r2
aws s3 rm s3://mem0-transcripts/r2-test.txt --endpoint-url $EP --profile r2
```

---

### Phase 4 — Lifecycle Rule (Lớp 2) (3 phút)

1. R2 dashboard → click bucket `mem0-transcripts`
2. Tab **Settings** → cuộn xuống **Object lifecycle rules** → bấm **Add rule**
3. Form:
   - **Rule name:** `auto-delete-transcripts-over-2y`
   - **Object lifecycle rule is enabled:** **ON** (toggle xanh)
   - **Rule scope:** Apply to objects with prefix **`sessions/`** (để chỉ ảnh hưởng transcript, không động test files)
   - **Lifecycle action:**
     - ☑ **Delete uploaded objects after:** `720` Days
     - ☑ **Abort incomplete multipart uploads after:** `7` Days
     - ❌ KHÔNG tick "Transition to Infrequent Access"
4. Bấm **Save changes**

**Verify:** quay lại tab Lifecycle → thấy rule với trạng thái **Enabled**.

---

### Phase 5 — Cloudflare Billing Alert (Lớp 3) (5 phút)

#### Step 5A — Billing Budget Alert (cho spend $)

1. Cloudflare dashboard → góc trên phải click **Avatar** → **Billing**
2. Tab **Notifications** (hoặc URL: https://dash.cloudflare.com/?to=/:account/billing)
3. Bấm **Add** → chọn **Billing Budget Alert**
4. Tạo 2 alerts:

**Alert 1 — Warning:**
```
Name:              R2 spending warning
Threshold amount:  $1 USD
Currency:          USD
Frequency:         Monthly
Notification:      Email <your-email>
```

**Alert 2 — Critical:**
```
Name:              R2 spending critical
Threshold amount:  $3 USD
Notification:      Email <your-email>
```

5. Save từng alert.

#### Step 5B — R2 Storage Notification (nếu UI có)

Cloudflare đang rollout dần R2 storage notification. Một số account chưa có. Cách check:

1. Sidebar → **Notifications** → bấm **Add**
2. Trong list **Product** → scroll xuống tìm **R2** hoặc **R2 Object Storage**

**Nếu CÓ:** điền 2 thresholds (8GB warning, 9.5GB critical)

**Nếu KHÔNG CÓ:** skip phase này — Lớp 4 (Telegram cron ở Phase 6) sẽ cover.

> Đa số account hiện chỉ có Billing alerts (Phase 5A), chưa có R2 storage alert dedicated. Đây là bình thường.

---

### Phase 6 — Telegram Bot + Cron VPS (Lớp 4) (30 phút)

#### Step 6A — Tạo Telegram Bot

1. Trong Telegram app, search **@BotFather** (có tick xanh verified)
2. Gửi `/start` cho @BotFather
3. Gửi `/newbot`
4. Bot name (hiển thị): `Mem0 R2 Alert`
5. Bot username: `<your_prefix>_r2_alert_bot` (phải kết thúc `_bot` hoặc `Bot`, globally unique)
6. BotFather trả về:
   ```
   Use this token to access the HTTP API:
   <BOT_TOKEN>
   ```
   **Copy BOT_TOKEN** vào notepad.

#### Step 6B — Lấy CHAT_ID

1. Trong Telegram, search username bot bạn vừa tạo (vd `@your_prefix_r2_alert_bot`)
2. Click vào bot → bấm nút **START** (hoặc gửi `/start`)
   
   > **QUAN TRỌNG:** bước này bắt buộc — Telegram yêu cầu user opt-in trước khi bot được phép message.

3. Bot SẼ KHÔNG REPLY (vì ta chưa code logic) — đó là **bình thường**. Telegram chỉ cần ghi nhận event /start.

4. Trong PowerShell:

```powershell
$BOT = "<BOT_TOKEN>"

# Verify bot tồn tại + token đúng
curl.exe "https://api.telegram.org/bot$BOT/getMe"
# → Phải thấy {"ok":true,"result":{"id":...,"is_bot":true,...}}

# Lấy CHAT_ID
curl.exe "https://api.telegram.org/bot$BOT/getUpdates"
# → Tìm dòng "chat":{"id":SOMETHING,...} — SOMETHING là CHAT_ID
```

5. **Copy CHAT_ID** (số) vào notepad.

#### Step 6C — Test sendMessage

```powershell
$CHAT = "<CHAT_ID>"
curl.exe -X POST "https://api.telegram.org/bot$BOT/sendMessage" -d "chat_id=$CHAT" -d "text=Hello from cron test!"
```

→ Telegram của bạn nhận được tin nhắn **"Hello from cron test!"** từ bot Mem0 R2 Alert. **Đây mới là tin nhắn từ bot.**

#### Step 6D — Deploy script lên VPS

```bash
ssh vps

# Clone repo (nếu chưa)
cd ~
git clone https://github.com/thanhiont423/mem0custom.git
# hoặc: cd ~/mem0custom && git pull

# Cài boto3 trên user-level (không cần root)
pip3 install --user boto3

# Copy script tới ~/scripts/
mkdir -p ~/scripts
cp ~/mem0custom/scripts/r2-budget-check.py ~/scripts/
chmod +x ~/scripts/r2-budget-check.py

# Tạo env file
cat > ~/scripts/r2-budget-env.sh << 'EOF'
export R2_ENDPOINT_URL="https://<account-id>.r2.cloudflarestorage.com"
export R2_ACCESS_KEY_ID="<paste>"
export R2_SECRET_ACCESS_KEY="<paste>"
export R2_BUCKET="mem0-transcripts"
export TELEGRAM_BOT_TOKEN="<paste>"
export TELEGRAM_CHAT_ID="<paste>"
EOF
chmod 600 ~/scripts/r2-budget-env.sh

# Verify env loaded OK
source ~/scripts/r2-budget-env.sh
echo $R2_BUCKET   # phải in: mem0-transcripts
echo $TELEGRAM_CHAT_ID   # phải in: số chat_id
```

#### Step 6E — Test chạy thủ công

```bash
source ~/scripts/r2-budget-env.sh
python3 ~/scripts/r2-budget-check.py
```

**Pass condition:**
- Output stdout in: `[<timestamp>] R2 usage: 0.000 GB (0.0% of 10GB free), 0 objects, level=ok`
- Telegram nhận được tin nhắn weekly OK summary (vì state file lần đầu chưa có)

#### Step 6F — Setup Cron

```bash
# Tạo wrapper script (để cron load đúng env)
cat > ~/scripts/r2-budget-check-run.sh << 'EOF'
#!/bin/bash
source $HOME/scripts/r2-budget-env.sh
python3 $HOME/scripts/r2-budget-check.py >> $HOME/scripts/r2-budget-check.log 2>&1
EOF
chmod +x ~/scripts/r2-budget-check-run.sh

# Add to crontab (chạy 7:00 AM mỗi ngày)
(crontab -l 2>/dev/null; echo "0 7 * * * $HOME/scripts/r2-budget-check-run.sh") | crontab -

# Verify
crontab -l
```

#### Step 6G — Verify cron sẽ chạy

```bash
# Test wrapper script
~/scripts/r2-budget-check-run.sh
cat ~/scripts/r2-budget-check.log | tail -3
```

Phải thấy dòng log mới nhất với timestamp + level.

---

### Phase 7 — Update VPS .env + Verify R2 Hoạt Động (15 phút)

Đây là phase **bắt buộc** — không có nó thì R2 chưa connect với archive-api.

#### Step 7A — Update `.env` trên VPS

```bash
ssh vps
cd ~/memory-stack  # hoặc folder mem0custom đã deploy

# Backup .env hiện tại
cp .env .env.backup.$(date +%Y%m%d)

# Append R2 vars
cat >> .env << 'EOF'

# Cloudflare R2 Object Storage (hybrid transcript storage)
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=<paste Access Key ID>
R2_SECRET_ACCESS_KEY=<paste Secret>
R2_BUCKET=mem0-transcripts

# Telegram Bot (cho cron r2-budget-check)
TELEGRAM_BOT_TOKEN=<paste>
TELEGRAM_CHAT_ID=<paste>
EOF

# Verify
grep R2_ .env
grep TELEGRAM_ .env
```

> Nếu Secret có ký tự đặc biệt (`$`, `` ` ``, `\`), dùng `vi .env` rồi paste để tránh shell evaluate.

#### Step 7B — Rebuild archive-api container

```bash
cd ~/memory-stack
docker compose up -d --build archive-api
docker compose logs archive-api --tail 30
```

Phải thấy log `Uvicorn running on http://0.0.0.0:8001` không có error.

#### Step 7C — Test connection R2 từ container

```bash
docker exec memory-archive-api python3 -c "
import os, boto3
from botocore.client import Config

c = boto3.client('s3',
    endpoint_url=os.environ['R2_ENDPOINT_URL'],
    aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],
    config=Config(signature_version='s3v4'))

# Test PUT
c.put_object(Bucket='mem0-transcripts', Key='test-from-vps.txt', Body=b'hello from VPS')
print('PUT OK')

# Test LIST
keys = [o['Key'] for o in c.list_objects_v2(Bucket='mem0-transcripts').get('Contents', [])]
print(f'Objects: {keys}')

# Test DELETE
c.delete_object(Bucket='mem0-transcripts', Key='test-from-vps.txt')
print('DELETE OK')
"
```

Phải in:
```
PUT OK
Objects: ['test-from-vps.txt']
DELETE OK
```

#### Step 7D — Test end-to-end qua archive-upload.py

Trên máy client (Windows/Mac), set `SUMMARIZE_ON_UPLOAD=1` và chạy upload:

**Windows PowerShell:**
```powershell
$env:SUMMARIZE_ON_UPLOAD = "1"
. "$env:USERPROFILE\scripts\archive-env.ps1"
python "$env:USERPROFILE\scripts\archive-upload.py"
```

**Mac:**
```bash
export SUMMARIZE_ON_UPLOAD=1
source ~/.config/archive-env
python3 ~/scripts/archive-upload.py
```

Output mong đợi:
```
Uploaded <session>.jsonl -> project=<tag> id=<uuid>
  + LLM summary: <preview text>...
Done. N new sessions uploaded.
```

Verify trong R2 dashboard → bucket `mem0-transcripts` → thấy objects xuất hiện dưới prefix `sessions/<user_id>/<yyyy>/<mm>/`.

---

## 6. Kiểm tra cuối — All Layers Verification

Sau khi xong tất cả phases, verify từng lớp một lần nữa:

### Lớp 1 — Bank limit
- Mở bank app → check hạn mức giao dịch QT = $5/tháng

### Lớp 2 — Bucket lifecycle
```
R2 dashboard → bucket → Settings → Object Lifecycle Rules
→ thấy rule `auto-delete-transcripts-over-2y` status Enabled
```

### Lớp 3 — Billing alerts
```
Cloudflare → Avatar → Billing → Notifications
→ thấy 2 alerts: R2 spending warning ($1), R2 spending critical ($3) — Active
```

### Lớp 4 — Telegram cron
```bash
ssh vps
crontab -l                                # phải thấy dòng 0 7 * * *
~/scripts/r2-budget-check-run.sh          # manual run
cat ~/scripts/r2-budget-check.log | tail  # thấy log entry mới
# Telegram nhận tin nhắn weekly OK
```

### End-to-end
```bash
# Trên VPS
curl -H "Authorization: Bearer $(grep ARCHIVE_AUTH_TOKEN ~/memory-stack/.env | cut -d= -f2)" \
     https://claude.hangocthanh.io.vn/archive/sessions?user_id=thanh&limit=5

# Sau khi upload session mới, response phải có session với r2_key non-null
```

---

## 7. Troubleshooting

### T1. AWS CLI test fail với `An error occurred (InvalidAccessKeyId)`

**Nguyên nhân:** paste sai Access Key (khoảng trắng đầu/cuối).

**Fix:**
```powershell
# Re-paste cẩn thận
aws configure --profile r2
# Hoặc edit file: $env:USERPROFILE\.aws\credentials
notepad $env:USERPROFILE\.aws\credentials
```

### T2. AWS CLI test fail với `Could not connect to the endpoint URL`

**Nguyên nhân:** sai endpoint hoặc proxy chặn `*.r2.cloudflarestorage.com`.

**Fix:**
1. Verify endpoint format đúng — phải có `<account-id>` ở đầu, không phải tên bucket
2. Verify proxy: `curl.exe -I https://r2.cloudflarestorage.com` → phải có response (kể cả 401/400)
3. Nếu proxy chặn → xin IT allowlist domain `*.r2.cloudflarestorage.com`

### T3. Cloudflare account chưa có R2 Storage trong Notifications list

**Đây là bình thường.** Cloudflare đang rollout dần. Skip Phase 5B, dùng Lớp 4 (Telegram cron) thay thế. Cron query trực tiếp R2 API → không phụ thuộc UI rollout.

### T4. Bot không reply khi gửi /start

**Đây là bình thường** — bạn chưa code logic cho bot. Bot chỉ cần "nhận" /start (Telegram server ghi lại). Verify bằng:

```powershell
curl.exe "https://api.telegram.org/bot$BOT/getUpdates"
# → phải có result chứa text:"/start"
```

Nếu result rỗng → bạn search SAI bot. Search lại username đúng (`<your_prefix>_r2_alert_bot`).

### T5. sendMessage trả `chat not found`

**Nguyên nhân:** chưa /start cho bot CỦA BẠN (đã /start cho @BotFather/@userinfobot khác).

**Fix:**
1. Search username bot của bạn trong Telegram
2. Click vào bot → bấm START
3. Chạy lại `getUpdates` để xác nhận event ghi lại
4. Retry sendMessage

### T6. boto3 import error trong container archive-api

**Nguyên nhân:** image cũ chưa có boto3 (branch main không cần).

**Fix:**
```bash
cd ~/memory-stack
docker compose build --no-cache archive-api
docker compose up -d archive-api
docker exec memory-archive-api pip list | grep boto3
```

### T7. Cron không chạy

**Debug:**
```bash
# Check cron service
sudo systemctl status cron

# Check logs
grep CRON /var/log/syslog | tail -20

# Test wrapper directly
bash -x ~/scripts/r2-budget-check-run.sh
```

Phổ biến: env vars không load. Wrapper script PHẢI có `source ~/scripts/r2-budget-env.sh` ở đầu.

### T8. Cloudflare card verify charge $1 chưa refund sau 7 ngày

**Bình thường:** quá trình refund qua bank quốc tế đôi khi mất 7-14 ngày. Nếu >14 ngày chưa thấy → liên hệ bank, không phải Cloudflare.

### T9. Quên copy Secret Access Key, đã đóng dialog

**Không lấy lại được Secret.** Phải:
1. R2 dashboard → API Tokens → Find token cũ → **Delete**
2. **Create Account API token** mới (theo Phase 3A)
3. Update lại `.env` trên VPS + AWS CLI profile + cron env script

### T10. Muốn switch từ R2 sang B2

R2 zero-egress = migrate ra free. Cách:

```bash
# Cài rclone
sudo apt install rclone

# Config 2 remotes
rclone config
# → New remote 'r2' (Amazon S3 type, Cloudflare R2 provider)
# → New remote 'b2' (Amazon S3 type, hoặc native B2)

# Sync
rclone sync r2:mem0-transcripts b2:mem0-transcripts --transfers=4 --progress

# Verify
rclone size b2:mem0-transcripts
```

Sau đó update VPS `.env`:
- Đổi `R2_ENDPOINT_URL` → endpoint B2 (`https://s3.us-west-004.backblazeb2.com`)
- Đổi `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` → credentials B2

Code `r2_storage.py` không cần đổi (cùng S3 API).

---

## 8. Maintenance & Operations

### Hàng tháng
- Check Cloudflare Billing dashboard — verify spend = $0
- Check R2 dashboard → bucket → Metrics → storage usage trending
- Verify Telegram nhận weekly OK summary

### Hàng quý
- Review lifecycle rule — có cần điều chỉnh threshold không?
- Check object count growth — dự báo khi nào chạm 10GB

### Hàng năm
- Rotate API token (nếu TTL = 365 ngày, hoặc proactive)
- Review billing alerts thresholds — match với usage pattern thực

### Khi có incident
1. Nhận Telegram alert CRITICAL → check ngay
2. Run `python3 ~/scripts/r2-budget-check.py` manual để verify
3. Nếu storage thật vượt → action:
   - Reduce lifecycle threshold (vd 720 → 365 ngày)
   - Hoặc migrate cũ sang B2/S3 archive tier
   - Hoặc upgrade Cloudflare Pro plan

---

## 9. Cost Projection

Cho use case 50 sessions/ngày × 200KB gzipped:

| Năm | Storage (GB) | R2 cost/tháng | Trong free? |
|---|---|---|---|
| 1 | 3.6 | $0 | ✅ |
| 2 | 7.2 | $0 | ✅ |
| 3 (steady, có lifecycle 720d) | ~7.2 | $0 | ✅ |
| 4-10 | ~7.2 | $0 | ✅ |

→ Với lifecycle 720 ngày, storage ổn định ~7GB indefinitely → **không bao giờ vượt free tier**.

Trường hợp ngoại lệ:
- Tăng từ 50 → 200 sessions/ngày → 4x growth → ~28GB sau 2 năm → vượt → cần giảm lifecycle xuống ~180 ngày
- Session size lớn hơn (file binary embedded) → adjust accordingly

---

## 10. References

- **Cloudflare R2 docs:** https://developers.cloudflare.com/r2/
- **R2 pricing:** https://developers.cloudflare.com/r2/pricing/
- **boto3 docs:** https://boto3.amazonaws.com/v1/documentation/api/latest/index.html
- **Telegram Bot API:** https://core.telegram.org/bots/api
- **Repo mem0custom:** https://github.com/thanhiont423/mem0custom
- **Branch new-features:** chứa code R2 integration

Tài liệu này được tạo ngày 2026-05-28, version 1.0.
