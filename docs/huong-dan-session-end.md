# Hướng dẫn: dùng hook SessionEnd để tự lưu phiên (full transcript + mem0)

> Mục tiêu: khi một phiên Claude Code **kết thúc**, tự động lưu full transcript lên archive
> và tóm tắt fact vào mem0 — chạy đúng MỘT lần, không lặp.

## 1. Solution — vì sao dùng SessionEnd (không phải Stop)

| Hook | Khi nào chạy | Hợp với "lưu phiên" |
|---|---|---|
| `Stop` | Sau MỖI lượt Claude trả lời (nhiều lần/phiên) | Không — lưu lặp lại |
| `SessionEnd` | ĐÚNG MỘT LẦN khi phiên kết thúc | ✅ Đúng mục đích |

`SessionEnd` thuộc nhóm "once per session". Trường `reason` cho biết lý do kết thúc:
`clear` (gõ /clear), `logout`, `prompt_input_exit` (thoát bình thường), `resume`, `other`.
Hook này chỉ dùng cho side-effect (lưu/log), không chặn được gì — đúng nhu cầu archive.

## 2. Step by step — bật hook

### Bước 1: bật cấu hình
```bash
cd <repo>/mem0custom
cp .claude/settings.json.example .claude/settings.json
```
File này khai báo `SessionEnd` chạy 2 lệnh: `archive-upload.py` (full transcript) + `sum_hook.py` (mem0).

### Bước 2: đặt biến môi trường (trong ~/.bashrc / ~/.zshrc / PowerShell profile)
```bash
export ARCHIVE_URL="https://claude.hangocthanh.io.vn/archive"
export ARCHIVE_AUTH_TOKEN="<ARCHIVE_AUTH_TOKEN của VPS>"
export USER_ID="thanh"
export MEM0_USER_ID="thanh"
export SUMMARIZE_ON_UPLOAD="1"   # tùy chọn: archive tự sinh LLM summary + embedding
```

### Bước 3 (tùy chọn): chỉ lưu khi thoát thật, bỏ qua /clear
Sửa `matcher` trong settings.json:
```json
"SessionEnd": [
  { "matcher": "prompt_input_exit|logout", "hooks": [ ... ] }
]
```
matcher rỗng `""` = lưu với mọi lý do kết thúc.

## 3. Cách TEST (4 mức, từ an toàn → thật)

### Test 1 — Mô phỏng payload SessionEnd cho sum_hook (không gọi LLM)
SessionEnd gửi JSON qua STDIN có `transcript_path`. Mô phỏng:
```bash
# Lấy 1 file transcript thật
F=$(ls -t ~/.claude/projects/*/*.jsonl | head -1)
echo "{\"transcript_path\":\"$F\",\"reason\":\"prompt_input_exit\"}" | python scripts/sum_hook.py --dry-run
```
Kỳ vọng: in ra prompt tiếng Việt tóm tắt + gọi add_memory. (--dry-run KHÔNG gọi LLM, an toàn.)

### Test 2 — Chạy archive-upload thật (lưu full transcript)
```bash
python scripts/archive-upload.py
```
Kỳ vọng: "Uploaded <file>.jsonl -> project=... id=<uuid>". Nó tự quét ~/.claude/projects/, chống trùng bằng ~/.cache/claude-archive-state.json.

### Test 3 — Kiểm tra đã lưu trên server
```bash
# Danh sách session mới nhất
curl -s -H "Authorization: Bearer $ARCHIVE_AUTH_TOKEN" \
  "$ARCHIVE_URL/sessions?user_id=thanh&limit=3" | python -m json.tool
# Full transcript của 1 session
curl -s -H "Authorization: Bearer $ARCHIVE_AUTH_TOKEN" \
  "$ARCHIVE_URL/sessions/<id>" | python -m json.tool | head -40
```

### Test 4 — Test hook thật end-to-end
1. Bật `--debug` để thấy hook chạy: `claude --debug`
2. Mở một phiên, chat vài câu, rồi **thoát phiên** (Ctrl+D hoặc /quit).
3. Trong log debug phải thấy dòng kích hoạt `SessionEnd` và output 2 lệnh.
4. Chạy lại Test 3 để xác nhận session vừa rồi đã xuất hiện trên server.

> Mẹo verify nhanh hook có nạp đúng không: `claude` rồi gõ lệnh kiểm tra cấu hình hook (hoặc xem ~/.claude/settings.json đã đúng JSON). Nếu hook lỗi, Claude Code hiện "<hook name> hook error" + dòng đầu stderr.

## 4. Why — giải thích thiết kế

- **archive-upload.py tự quét thư mục** nên không phụ thuộc `transcript_path` từ payload — chạy ở SessionEnd hay thủ công đều được; state file tránh upload trùng.
- **sum_hook.py đọc `transcript_path` từ STDIN** — SessionEnd có cung cấp trường này, nên tóm tắt đúng phiên vừa kết thúc.
- **SessionEnd không chặn được** (no decision control) → an toàn, không bao giờ làm kẹt việc đóng phiên. Lỗi hook chỉ hiện cảnh báo, không cản trở.
- Lọc `matcher` theo `reason` giúp tránh lưu rác khi chỉ /clear giữa chừng.

## 5. Lưu ý về Cowork & Claude Chat
- **Claude Code**: SessionEnd hoạt động như trên.
- **Cowork**: cũng tạo file transcript .jsonl trên máy, nhưng CHƯA xác nhận Cowork thực thi hook SessionEnd như Claude Code CLI. Với Cowork nên dùng tool read_transcript / skill thay vì hook.
- **Claude Chat (web/app)**: chạy trên cloud, KHÔNG có hook và KHÔNG có file transcript cục bộ → chỉ lưu tóm tắt qua add_memory, hoặc export thủ công.
