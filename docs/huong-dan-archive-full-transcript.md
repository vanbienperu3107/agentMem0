# Hướng dẫn: Tự lưu FULL TRANSCRIPT khi kết thúc phiên

> Mục tiêu: khi đóng một phiên Claude Code, hệ thống tự động lưu **toàn bộ nội dung chat dạng nguyên văn (full transcript)** lên archive, đồng thời tóm tắt fact vào mem0.

## 1. Solution — dùng gì

Repo đã có sẵn 2 mảnh, chỉ cần nối vào hook `Stop`:

| Thành phần | Việc làm | Lưu ở đâu |
|---|---|---|
| `scripts/archive-upload.py` | Lưu **FULL transcript** (nguyên văn mọi message) | archive-api → Postgres (cột `transcript` JSONB), hoặc R2 nếu bật `R2_ENDPOINT_URL` |
| `scripts/sum_hook.py` | Tóm tắt fact ngắn gọn | mem0 (Qdrant) |

Hook `Stop` của Claude Code chạy khi phiên kết thúc → gọi cả hai. Đây đúng yêu cầu "khi kết thúc phiên tự gọi archive lưu full transcript".

## 2. Step by step

### Bước 1 — Bật hook
Trong thư mục repo, đổi tên file mẫu:
```bash
cp .claude/settings.json.example .claude/settings.json
```
File này khai báo hook `Stop` chạy 2 lệnh: `archive-upload.py` (full transcript) + `sum_hook.py` (mem0).

### Bước 2 — Đặt biến môi trường
`archive-upload.py` cần (đặt trong shell profile, vd `~/.bashrc` / `~/.zshrc` / PowerShell profile):
```bash
export ARCHIVE_URL="https://claude.hangocthanh.io.vn/archive"
export ARCHIVE_AUTH_TOKEN="<đúng ARCHIVE_AUTH_TOKEN của VPS>"
export USER_ID="thanh"
export SUMMARIZE_ON_UPLOAD="1"   # (tuỳ chọn) archive tự sinh LLM summary + embedding để search semantic
# cho mem0 (sum_hook.py)
export MEM0_USER_ID="thanh"
```

### Bước 3 — Test khô trước khi dùng thật
```bash
# Xem archive-upload sẽ gửi gì (chạy thật nhưng chỉ quét + báo, dùng state file chống trùng)
python scripts/archive-upload.py

# Xem prompt sum_hook sẽ gửi (không gọi LLM)
echo '{"transcript_path":"<path>.jsonl"}' | python scripts/sum_hook.py --dry-run
```

### Bước 4 — Dùng thật
Mở rồi đóng một phiên Claude Code → hook tự chạy. Kiểm tra đã lưu:
```bash
# Liệt kê session đã archive
curl -s -H "Authorization: Bearer $ARCHIVE_AUTH_TOKEN" \
  "$ARCHIVE_URL/sessions?user_id=thanh&limit=5" | python -m json.tool

# Lấy FULL transcript của 1 session (trả nguyên văn)
curl -s -H "Authorization: Bearer $ARCHIVE_AUTH_TOKEN" \
  "$ARCHIVE_URL/sessions/<session_id>" | python -m json.tool
```

## 3. Cách archive-upload.py lưu full transcript (để hiểu rõ)

- Quét mọi file `.jsonl` trong `~/.claude/projects/` (transcript gốc của Claude Code).
- Với mỗi phiên: gom **toàn bộ message** user + assistant (trường `transcript` = list nguyên văn), kèm `started_at`, `ended_at`, `message_count`, `project_tag`, `workspace_path`.
- POST `/sessions` → archive-api ghi vào cột `transcript JSONB` (Postgres). Nếu VPS có bật `R2_ENDPOINT_URL`, transcript được đẩy lên R2 (object storage rẻ), cột `transcript` để rỗng và lưu `r2_key`. Khi GET lại, API tự tải từ R2 ra.
- Dùng state file `~/.cache/claude-archive-state.json` để **không upload trùng** một phiên hai lần.

## 4. Why — vì sao cách này hợp lý

- **Full transcript dùng đúng đường có sẵn**: `archive-upload.py` + endpoint `/sessions` được thiết kế đúng cho việc lưu nguyên văn — không cần viết mới.
- **Hai tầng bổ trợ nhau**: archive giữ nguyên văn để xem lại/khôi phục ngữ cảnh (`/sessions/{id}/context?strategy=full`); mem0 giữ fact ngắn để tìm nhanh. Lưu cả hai cho trải nghiệm tốt nhất.
- **R2 (nếu bật) tiết kiệm DB**: transcript nguyên văn rất nặng; đẩy lên R2 giữ Postgres nhẹ mà vẫn xem lại được.
- **An toàn phiên**: `sum_hook.py` luôn thoát êm (exit 0) nếu lỗi; `archive-upload.py` bắt lỗi từng file, không làm vỡ phiên.

## 5. Lưu ý
- Nếu trước đây đã bật auto-upload theo giờ (bản v0.4.2), bật thêm hook `Stop` có thể upload trùng thời điểm — nhưng state file chống trùng theo từng file nên không tạo bản ghi kép cho cùng một phiên.
- Muốn CHỈ lưu full transcript (không tóm tắt mem0): xoá khối lệnh `sum_hook.py` trong `settings.json`.
- Khôi phục/đọc lại nguyên văn: `GET /sessions/{id}` (full) hoặc `GET /sessions/{id}/context?strategy=full` (định dạng sẵn để dán làm ngữ cảnh).
