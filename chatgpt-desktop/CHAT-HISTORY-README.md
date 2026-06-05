# Tính năng lưu lịch sử chat — Hướng dẫn build và sử dụng

## Tổng quan

App đã được bổ sung cơ chế lưu toàn bộ chat history theo từng phiên. Mỗi lần bạn gõ
keyword `compact` hoặc `lưu` ở ô input "Ask" → app sẽ xuất 1 file JSON chứa
toàn bộ message của phiên hiện tại.

## File và vị trí

Sau khi cài đặt, app sẽ tạo các file trong thư mục:

- Windows: `%APPDATA%\com.nofwl.chatgpt\`
- macOS: `~/Library/Application Support/com.nofwl.chatgpt/`
- Linux: `~/.config/com.nofwl.chatgpt/`

Cấu trúc:

```
com.nofwl.chatgpt/
├── config.json             (cấu hình app, đã có sẵn)
├── current.session         (metadata phiên hiện tại — tự quản lý)
├── current.wal             (write-ahead log — tự quản lý, durable)
└── sessions/
    ├── session_s7a3f2b9_20260528-143022.json     (phiên đã compact bình thường)
    ├── session_skx9m4n2_20260528-150115.json
    └── recovered/
        └── session_recovered_s2x8y1q5_20260528-093011.json   (phục hồi sau crash)
```

## Cách dùng

1. Mở app → mở ChatGPT bằng cách bấm Cmd/Ctrl + L (toggle Ask mode) hoặc dùng app như bình thường.
2. Chat thoải mái — mọi message bạn thấy đều được log ngầm vào WAL.
3. Khi muốn lưu phiên hiện tại → ở ô input "Ask" (dưới cùng app), gõ một trong các keyword sau và nội dung sẽ tự động được xuất:
   - `compact`
   - `/compact`
   - `lưu`
   - `/lưu`
   - `luu` (fallback không dấu)
4. App sinh 1 file `session_{id}_{time}.json` trong folder `sessions/`. Ô input tự clear.
5. Phiên mới tự động bắt đầu (session_id khác). Tiếp tục chat → tiếp tục được log.

### Khi app crash hoặc tắt đột ngột

Không cần lo. Vì mọi message được fsync xuống WAL ngay lập tức, lần khởi động sau
app sẽ tự động:

1. Phát hiện WAL còn data từ phiên cũ.
2. Chuyển toàn bộ data đó thành file `sessions/recovered/session_recovered_*.json`.
3. Xoá WAL, tạo phiên mới sạch.

File recovery có flag `"exported_via": "crash_recovery"` để process bên ngoài biết.

### Khi đóng app bằng nút X

App có handler `RunEvent::ExitRequested` tự động compact session còn dở thành file
`session_*.json` với `"exported_via": "app_exit"`. Không cần gõ `compact` trước
khi đóng.

## Cấu trúc 1 file JSON

```json
{
  "session_id": "s7a3f2b9",
  "started_at": 1748430000,
  "started_at_iso": "2026-05-28T13:00:00+07:00",
  "exported_at": 1748441422,
  "exported_at_iso": "2026-05-28T14:30:22+07:00",
  "exported_via": "compact",
  "message_count": 12,
  "messages": [
    {
      "id": "msg-abc",
      "conversation_id": "67abc...",
      "role": "user",
      "content": "...",
      "captured_at": 1748441100
    },
    ...
  ]
}
```

## Build .exe trên Windows

### Yêu cầu trước khi build

- Windows 10 hoặc mới hơn
- PowerShell 5+
- Disk free ~5 GB (Rust toolchain + dependencies)
- Kết nối internet (lần build đầu để tải cargo crates + npm deps)

### Cách build (1 lệnh)

Mở PowerShell tại thư mục dự án:

```powershell
cd "E:\Thanhhn5\41. Khoa hoc\Claude\ChatGPT-2-dev\ChatGPT-2-dev"
.\build-windows.ps1
```

Script sẽ tự động:

1. Cài Rust (nếu chưa có)
2. Cài pnpm (nếu chưa có)
3. Cài WebView2 Runtime (nếu chưa có)
4. `pnpm install` cho frontend
5. `cargo test --lib core::history` — verify logic Rust trước khi build
6. `pnpm tauri build` — sinh .exe + installer

### Nếu PowerShell chặn script

Lần đầu chạy có thể PowerShell báo "execution policy". Fix:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Sau đó chạy lại `.\build-windows.ps1`.

### Output

Sau khi build xong:

```
src-tauri\target\release\bundle\
├── msi\
│   └── ChatGPT_2.0.0_x64_en-US.msi          ← installer 1 click
├── nsis\
│   └── ChatGPT_2.0.0_x64-setup.exe          ← installer khác
└── ChatGPT.exe                              ← .exe portable
```

Double-click file `.msi` hoặc `.exe` setup để cài vào máy như app bình thường.

## Test logic độc lập (không cần build)

Trước khi build, có thể chạy test harness Python để verify cùng logic flow:

```powershell
python3 test_history_logic.py
```

(File này nằm cùng thư mục build script). Đã có 17 test case bao phủ toàn bộ
init/log/compact/recovery/corruption/Unicode/multi-session.

## Tiến trình bên ngoài đọc JSON gửi mem0

Pattern khuyến nghị:

```python
import json, time, shutil
from pathlib import Path

SESSIONS = Path.home() / "AppData/Roaming/com.nofwl.chatgpt/sessions"
PROCESSED = SESSIONS / "processed"
PROCESSED.mkdir(exist_ok=True)

while True:
    for f in sorted(SESSIONS.glob("session_*.json")):
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
            # TODO: gửi data["messages"] lên mem0 server
            shutil.move(f, PROCESSED / f.name)
    time.sleep(10)
```

File `recovered/` có thể flag riêng vì dữ liệu có thể không đầy đủ (mất message cuối nếu crash giữa ghi).

## Cấu trúc thay đổi trong source code

| File | Mô tả |
|---|---|
| `src-tauri/src/core/history.rs` | **MỚI** — module quản lý WAL + session, có 8 unit test |
| `src-tauri/src/core/mod.rs` | Thêm `pub mod history;` |
| `src-tauri/src/core/cmd.rs` | Thêm 2 command: `log_message`, `compact_session` |
| `src-tauri/src/core/template.rs` | Thêm `SCRIPT_CHAT_LOGGER`, copy `chat-logger.js` ra disk |
| `src-tauri/src/core/setup.rs` | Inject `chat-logger.js` vào webview ChatGPT |
| `src-tauri/src/main.rs` | Register `HistoryState`, init session khi start, auto-compact khi exit |
| `src-tauri/Cargo.toml` | Thêm `chrono`, dev-dep `tempfile` |
| `src-tauri/scripts/chat-logger.js` | **MỚI** — MutationObserver scrape DOM ChatGPT |
| `src/view/Ask.tsx` | Detect keyword `compact`/`lưu` → gọi `compact_session` |
