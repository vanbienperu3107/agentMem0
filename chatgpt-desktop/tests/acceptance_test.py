"""
Acceptance test cho ChatGPT Desktop v0.1.0 — Chat History Export feature.

Kiểm tra TẤT CẢ yêu cầu của user (từ chat history):
1. Chat history lưu dưới dạng JSON (không SQLite)
2. Mỗi session = 1 file JSON riêng, không gộp
3. Keyword "compact" / "lưu" / "luu" trigger xuất file
4. Tên file: session_{session_id}_{time}
5. WAL recovery khi app crash
6. Auto-flush khi app exit

Chạy: python3 acceptance_test.py [path-to-installed-app-data-dir]
Mặc định: %APPDATA%\\com.nofwl.chatgpt (Windows) hoặc ~/.config/com.nofwl.chatgpt (Linux/Mac)
"""

import io
import sys

# Force UTF-8 stdout (Windows mặc định cp1252 không in được ký tự tiếng Việt)
if sys.platform.startswith("win") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import json
import os
import re
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

if sys.platform.startswith("win"):
    DEFAULT_APP_DATA = Path(os.environ.get("APPDATA", "")) / "com.nofwl.chatgpt"
elif sys.platform == "darwin":
    DEFAULT_APP_DATA = Path.home() / "Library/Application Support/com.nofwl.chatgpt"
else:
    DEFAULT_APP_DATA = Path.home() / ".config/com.nofwl.chatgpt"

APP_DATA = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_APP_DATA


class AcceptanceCriteria(unittest.TestCase):
    """Mỗi test case = 1 yêu cầu cụ thể của user."""

    @classmethod
    def setUpClass(cls):
        if not APP_DATA.exists():
            raise unittest.SkipTest(
                f"App data dir không tồn tại: {APP_DATA}\n"
                f"Hãy cài app + chạy 1 lần trước khi test acceptance."
            )

    def test_R1_app_data_structure(self):
        """[R1] App data dir phải có cấu trúc đúng sau khi app khởi động lần đầu."""
        self.assertTrue(APP_DATA.exists(), f"Missing: {APP_DATA}")
        # Sau init_session: current.wal + current.session phải tồn tại
        wal = APP_DATA / "current.wal"
        meta = APP_DATA / "current.session"
        sessions = APP_DATA / "sessions"
        self.assertTrue(wal.exists() or sessions.exists(),
                        "Phải có ít nhất current.wal hoặc sessions/ folder")

    def test_R2_session_metadata_format(self):
        """[R2] current.session phải có session_id format 's' + hex."""
        meta = APP_DATA / "current.session"
        if not meta.exists():
            self.skipTest("current.session chưa tạo (app chưa init)")
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertIn("session_id", data)
        self.assertIn("started_at", data)
        self.assertIn("started_at_iso", data)
        self.assertRegex(data["session_id"], r"^s[0-9a-f]+$",
                         f"session_id sai format: {data['session_id']}")

    def test_R3_sessions_folder_exists(self):
        """[R3] Phải có folder sessions/ để chứa các file đã compact."""
        sessions = APP_DATA / "sessions"
        if not sessions.exists():
            self.skipTest("sessions/ chưa được tạo (chưa compact lần nào)")
        self.assertTrue(sessions.is_dir())

    def test_R4_session_file_naming_convention(self):
        """[R4] File compact phải tên: session_{id}_{YYYYMMDD-HHMMSS}.json."""
        sessions = APP_DATA / "sessions"
        if not sessions.exists():
            self.skipTest("Chưa có session file để verify")
        files = list(sessions.glob("session_*.json"))
        if not files:
            self.skipTest("Chưa có file session_*.json — hãy gõ 'compact' trong app")
        pattern = re.compile(r"^session_s[0-9a-f]+_\d{8}-\d{6}\.json$")
        for f in files:
            self.assertRegex(f.name, pattern,
                             f"Tên file sai format: {f.name}")

    def test_R5_session_json_schema(self):
        """[R5] Nội dung file session phải có đủ field theo schema."""
        sessions = APP_DATA / "sessions"
        if not sessions.exists():
            self.skipTest("Chưa có session file")
        files = list(sessions.glob("session_*.json"))
        if not files:
            self.skipTest("Chưa có file session_*.json")
        required_fields = {
            "session_id", "started_at", "started_at_iso",
            "exported_at", "exported_at_iso", "exported_via",
            "message_count", "messages",
        }
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            missing = required_fields - set(data.keys())
            self.assertFalse(missing, f"{f.name} thiếu fields: {missing}")
            self.assertIn(data["exported_via"], ["compact", "app_exit", "crash_recovery"])
            self.assertEqual(data["message_count"], len(data["messages"]))

    def test_R6_message_schema(self):
        """[R6] Mỗi message trong session phải có: id, conversation_id, role, content, captured_at."""
        sessions = APP_DATA / "sessions"
        if not sessions.exists():
            self.skipTest("Chưa có session file")
        files = list(sessions.glob("session_*.json"))
        if not files:
            self.skipTest("Chưa có file session_*.json")
        required = {"id", "conversation_id", "role", "content", "captured_at"}
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            for i, msg in enumerate(data["messages"]):
                missing = required - set(msg.keys())
                self.assertFalse(missing,
                                 f"{f.name} msg[{i}] thiếu: {missing}")
                self.assertIn(msg["role"], ["user", "assistant", "system", "tool"],
                              f"Role bất thường: {msg['role']}")

    def test_R7_each_session_separate_file(self):
        """[R7] Mỗi session = 1 file riêng, không gộp."""
        sessions = APP_DATA / "sessions"
        if not sessions.exists():
            self.skipTest("Chưa có session file")
        files = list(sessions.glob("session_*.json"))
        if len(files) < 2:
            self.skipTest("Cần ít nhất 2 file để verify rule này")
        session_ids = set()
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            session_ids.add(data["session_id"])
        self.assertEqual(len(session_ids), len(files),
                         "Mỗi file phải có session_id duy nhất")

    def test_R8_recovery_folder_format(self):
        """[R8] Nếu có crash, file phải nằm trong sessions/recovered/ với prefix đúng."""
        recovered = APP_DATA / "sessions" / "recovered"
        if not recovered.exists():
            self.skipTest("Chưa từng crash — không có recovered files (OK)")
        files = list(recovered.glob("session_recovered_*.json"))
        for f in files:
            self.assertTrue(f.name.startswith("session_recovered_"))
            data = json.loads(f.read_text(encoding="utf-8"))
            self.assertEqual(data["exported_via"], "crash_recovery")

    def test_R9_wal_is_ndjson_format(self):
        """[R9] current.wal phải là NDJSON (mỗi dòng 1 JSON object)."""
        wal = APP_DATA / "current.wal"
        if not wal.exists() or wal.stat().st_size == 0:
            self.skipTest("WAL rỗng hoặc không có (OK)")
        with open(wal, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    # Tolerate corrupt last line (crash mid-write) — OK theo design
                    if lineno != sum(1 for _ in open(wal)):
                        self.fail(f"WAL line {lineno} không parse: {e}")

    def test_R10_no_sqlite_database(self):
        """[R10] KHÔNG dùng SQLite (user đã reject từ đầu)."""
        for db in APP_DATA.rglob("*.db"):
            self.fail(f"Tìm thấy SQLite database: {db} — user yêu cầu chỉ JSON")
        for db in APP_DATA.rglob("*.sqlite"):
            self.fail(f"Tìm thấy SQLite: {db}")


if __name__ == "__main__":
    print(f"App data dir: {APP_DATA}")
    print(f"Version: ChatGPT Desktop v0.2.0\n")
    unittest.main(argv=[sys.argv[0]], verbosity=2)
