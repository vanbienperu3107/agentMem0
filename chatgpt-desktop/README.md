# ChatGPT Desktop — Chat History Export

[🇻🇳 Tiếng Việt](#tiếng-việt) | [🇬🇧 English](#english)

**Latest release: [v0.2.1](https://github.com/thanhiont423/mem0custom/releases/tag/v0.2.1)** ([Changelog](#changelog))

---

## Tiếng Việt

### Giới thiệu

**ChatGPT Desktop** là ứng dụng desktop wrapper cho [chatgpt.com](https://chatgpt.com), bổ sung tính năng **xuất chat history tự động dưới dạng JSON** để tích hợp với hệ thống mem0 (long-term memory). Dựa trên dự án mã nguồn mở [lencx/ChatGPT](https://github.com/lencx/ChatGPT) (Tauri 2.0).

### Tính năng chính

- 🔄 **Tự động ghi log mọi tin nhắn** user gõ và assistant trả lời, song song với chat bình thường
- 💾 **Xuất từng phiên ra file JSON riêng** khi user gõ keyword `compact`, `lưu`, `luu`
- 🎯 **Trigger từ textarea CHÍNH của ChatGPT** (v0.2.1+) — không cần mở ô Ask riêng
- 🛡️ **Chống mất data khi crash** — Write-Ahead Log (WAL), tự phục hồi khi mở lại
- 📦 **Auto-flush khi đóng app** — không cần nhớ gõ keyword trước khi tắt
- 🚀 **Auto-portable mode** (v0.2.0+) — chạy từ USB/Downloads → data cạnh exe; cài qua installer → AppData
- 📝 **Full logging** (v0.2.0+) — mọi hành vi ghi vào `logs/app.log`
- 🆔 Tên file rõ ràng: `session_{id}_{YYYYMMDD-HHMMSS}.json`

### Cài đặt

#### Cách 1 — Portable (recommended, không cần admin)

1. Tải `chatgpt.exe` từ [Releases](https://github.com/thanhiont423/mem0custom/releases/latest)
2. Đặt vào folder bất kỳ ngoài Program Files (vd `E:\Tool\chatgpt\`, USB)
3. **Double-click `chatgpt.exe`** — app tự tạo folder `data/` cạnh exe
4. Yêu cầu: Windows 10+, Microsoft Edge WebView2 (thường có sẵn)

#### Cách 2 — Installer

1. Tải `ChatGPT_0.2.1_x64-setup.exe` (NSIS) hoặc `.msi` từ Releases
2. Double-click cài như app bình thường → data lưu vào `%APPDATA%\com.nofwl.chatgpt\`

#### Cách 3 — Build từ source

```powershell
git clone https://github.com/thanhiont423/mem0custom.git
cd mem0custom/chatgpt-desktop
.\build-windows.ps1
```

### Cách sử dụng

1. Mở app → đăng nhập chatgpt.com như bình thường
2. Chat thoải mái — mọi message được log ngầm vào WAL
3. Khi muốn xuất phiên hiện tại, gõ vào **ô textarea chính của ChatGPT** (cái ô bạn vẫn dùng để chat):
   - `compact`
   - `lưu`
   - `luu` (không dấu)
4. Bấm **Enter** → app chặn không gửi lên OpenAI, tự xuất file JSON
5. Ô input tự clear, phiên mới bắt đầu

> **Bonus:** vẫn có thể gõ keyword ở ô Ask riêng của app (Ctrl+L để toggle)

### Cấu trúc file JSON

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
    {"id": "msg-abc", "conversation_id": "67abc...", "role": "user", "content": "Xin chào", "captured_at": 1748441100},
    {"id": "msg-def", "conversation_id": "67abc...", "role": "assistant", "content": "Chào bạn", "captured_at": 1748441120}
  ]
}
```

### Vị trí file

| Mode | data/ | logs/ |
|------|-------|-------|
| **Portable** (auto-detect khi exe ngoài Program Files) | `<exe-dir>/data/com.nofwl.chatgpt/` | `<exe-dir>/data/com.nofwl.chatgpt/logs/app.log` |
| **Installer** (exe trong Program Files) | `%APPDATA%/com.nofwl.chatgpt/` | `%APPDATA%/com.nofwl.chatgpt/logs/app.log` |
| macOS | `~/Library/Application Support/com.nofwl.chatgpt/` | tương tự |
| Linux | `~/.config/com.nofwl.chatgpt/` | tương tự |

**Override mode:** tạo file rỗng cạnh exe để ép:
- `portable.flag` → luôn portable
- `use-appdata.flag` → luôn AppData

### Crash recovery

Khi app crash hoặc tắt đột ngột:
1. Mở lại app
2. App phát hiện WAL còn data → chuyển thành `sessions/recovered/session_recovered_*.json`
3. File có flag `"exported_via": "crash_recovery"`

### Log debug

Mở file `<data_dir>/logs/app.log` xem mọi hành vi:

```
[INFO] [app] starting ChatGPT Desktop
[INFO] [portable] auto-detected portable mode -> E:\Tool\chatgpt\data
[INFO] [history] new session created: id=s7a3f2b9
[DEBUG] [history] log_message: role=user content_len=42
[INFO] [event] compact triggered from frontend
[INFO] [event] compact OK: ...\sessions\session_s7a3f2b9_20260529-...
```

### Tích hợp mem0custom server

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
            # TODO: POST lên archive-api
            shutil.move(f, PROCESSED / f.name)
    time.sleep(10)
```

### Acceptance test

```powershell
python tests/acceptance_test.py
```

Verify 10 criteria: app data structure, session_id format, file naming, JSON schema, recovery, no SQLite, v.v.

### ☕ Buy Me a Coffee

- **Số tài khoản:** `0869649888`
- **Hỗ trợ:** Mọi ngân hàng VN (Vietcombank, Techcombank, MB, BIDV, ACB, VPBank, TPBank, MoMo, ZaloPay, ViettelPay)

Cảm ơn bạn rất nhiều! 🙏

### ⚠️ Tuyên bố miễn trừ trách nhiệm

Phần mềm này chỉ được phép sử dụng **CÁ NHÂN, PHI THƯƠNG MẠI**. Chi tiết: [DISCLAIMER.md](DISCLAIMER.md).

---

## English

### Overview

**ChatGPT Desktop** is a desktop wrapper for [chatgpt.com](https://chatgpt.com) with **automatic chat history export to JSON** for integration with mem0 systems. Based on [lencx/ChatGPT](https://github.com/lencx/ChatGPT) (Tauri 2.0).

### Features

- 🔄 Auto-log every message (user + assistant) silently
- 💾 Export per-session JSON when typing `compact`, `lưu`, or `luu`
- 🎯 **Trigger from ChatGPT main textarea** (v0.2.1+) — no need to open separate Ask box
- 🛡️ Write-Ahead Log (WAL) crash recovery
- 📦 Auto-flush on app exit
- 🚀 **Auto-portable** (v0.2.0+) — running from USB/Downloads → data next to exe; via installer → AppData
- 📝 **Full logging** (v0.2.0+) — all behavior in `logs/app.log`
- 🆔 Clear filenames: `session_{id}_{YYYYMMDD-HHMMSS}.json`

### Installation

#### Option 1 — Portable (recommended)

1. Download `chatgpt.exe` from [Releases](https://github.com/thanhiont423/mem0custom/releases/latest)
2. Place in any folder outside Program Files (USB, Downloads...)
3. **Double-click `chatgpt.exe`** — auto-creates `data/` folder next to exe
4. Requires: Windows 10+, WebView2 Runtime

#### Option 2 — Installer

Download `ChatGPT_0.2.1_x64-setup.exe` or `.msi` → double-click → installs to system, data in `%APPDATA%`.

### Usage

1. Launch app → sign in to chatgpt.com
2. Chat normally
3. To export current session, type one of these into the **main ChatGPT textarea**:
   - `compact`
   - `lưu` (Vietnamese: "save")
   - `luu` (no diacritics)
4. Press **Enter** → app blocks submission, exports JSON file, clears input

### File Locations

| Mode | data/ | logs/ |
|------|-------|-------|
| Portable | `<exe-dir>/data/com.nofwl.chatgpt/` | `<exe-dir>/data/com.nofwl.chatgpt/logs/app.log` |
| Installer | `%APPDATA%/com.nofwl.chatgpt/` | same |

Override: create `portable.flag` or `use-appdata.flag` next to exe.

### ☕ Buy Me a Coffee

- **Account:** `0869649888`
- **Banks:** All Vietnamese banks + MoMo/ZaloPay/ViettelPay

### ⚠️ Disclaimer

PERSONAL USE ONLY. See [DISCLAIMER.md](DISCLAIMER.md).

---

## Changelog

### v0.2.1 (latest)
- 🎯 **Hook ChatGPT main textarea** — gõ `compact`/`lưu` vào ô chính cũng trigger, không chỉ ô Ask
- ⚡ Capture-phase keydown listener — chặn submit trước khi gửi lên OpenAI
- 🚫 Scan() skip message có content match keyword (không log "compact" thành user message)

### v0.2.0
- 🚀 **Auto-portable mode** — detect exe location, lưu data cạnh exe nếu KHÔNG trong Program Files
- 📝 **Full logging** via `simplelog` — file `app.log` + stdout
- 🛡️ **CSP bypass** — chuyển từ HTTP IPC (`invoke`) sang postMessage event (`event.emit`)
- 🧪 Validate-release CI job — 15 acceptance criteria check sau build
- 🐛 Fix tauri-plugin-log conflict, TermLogger API, template.rs truncate, upload paths

### v0.1.0
- 💾 Feature gốc compact JSON
- 🔧 Build script Windows + GitHub Actions CI
- 📋 17 Python test + 3 Rust unit tests
- 📄 README + DISCLAIMER + acceptance test

---

## Links

- 🐙 Source: https://github.com/thanhiont423/mem0custom
- 📦 Releases: https://github.com/thanhiont423/mem0custom/releases
- 🐛 Issues: https://github.com/thanhiont423/mem0custom/issues
- 💬 mem0 server: https://github.com/thanhiont423/mem0custom (same repo, `main` branch)

## Credits

- Base: [lencx/ChatGPT](https://github.com/lencx/ChatGPT) — AGPL-3.0
- Stack: Tauri 2.0, Rust, React, TypeScript, simplelog
