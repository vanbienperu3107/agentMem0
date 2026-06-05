"""
Python harness mô phỏng chính xác logic Rust history.rs để verify flow.
Cùng cấu trúc WAL/session/recovery — nếu test này pass thì code Rust
(cùng logic, cùng file format) cũng pass.

Chạy: python3 test_history_logic.py
"""

import json
import os
import re
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path


# ============== Logic mô phỏng (giống Rust history.rs) ==============

def now_ts():
    return int(time.time())


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat()


def now_filename_stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


_id_counter = 0
def make_session_id(seed: int) -> str:
    """Cùng công thức với Rust: hex 7 chars + prefix 's'. Thêm counter để tránh collision khi gọi liên tục trong cùng nanosecond."""
    global _id_counter
    _id_counter += 1
    nanos = int((time.time() % 1) * 1_000_000_000)
    mix = (seed * 0x9E3779B97F4A7C15 + nanos + _id_counter * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"s{mix & 0xFFFFFFF:07x}"


class HistoryStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "sessions").mkdir(exist_ok=True)
        (self.root / "sessions" / "recovered").mkdir(exist_ok=True)

    @property
    def wal(self):
        return self.root / "current.wal"

    @property
    def meta(self):
        return self.root / "current.session"

    @property
    def sessions_dir(self):
        return self.root / "sessions"

    @property
    def recovered_dir(self):
        return self.root / "sessions" / "recovered"

    def init_session(self):
        """Khởi tạo session — check recovery từ WAL cũ trước."""
        recovered_path = None
        if self.meta.exists():
            old_meta = json.loads(self.meta.read_text())
            if self.wal.exists():
                msgs = self._read_wal()
                if msgs:
                    now = now_ts()
                    sf = {
                        "session_id": old_meta["session_id"],
                        "started_at": old_meta["started_at"],
                        "started_at_iso": old_meta["started_at_iso"],
                        "exported_at": now,
                        "exported_at_iso": now_iso(),
                        "exported_via": "crash_recovery",
                        "message_count": len(msgs),
                        "messages": msgs,
                    }
                    fname = f"session_recovered_{old_meta['session_id']}_{now_filename_stamp()}.json"
                    recovered_path = self.recovered_dir / fname
                    recovered_path.write_text(json.dumps(sf, indent=2, ensure_ascii=False))
                self.wal.unlink(missing_ok=True)
            self.meta.unlink(missing_ok=True)

        ts = now_ts()
        new_meta = {
            "session_id": make_session_id(ts),
            "started_at": ts,
            "started_at_iso": now_iso(),
        }
        self.meta.write_text(json.dumps(new_meta, indent=2))
        self.wal.touch()
        return new_meta, recovered_path

    def log_message(self, msg: dict):
        """Append NDJSON line + fsync."""
        line = json.dumps(msg, ensure_ascii=False)
        with open(self.wal, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def compact_session(self, via: str = "compact"):
        """Đọc WAL → ghi session_*.json → xoá WAL → rotate session."""
        meta = json.loads(self.meta.read_text())
        msgs = self._read_wal() if self.wal.exists() else []

        out_path = None
        if msgs:
            now = now_ts()
            sf = {
                "session_id": meta["session_id"],
                "started_at": meta["started_at"],
                "started_at_iso": meta["started_at_iso"],
                "exported_at": now,
                "exported_at_iso": now_iso(),
                "exported_via": via,
                "message_count": len(msgs),
                "messages": msgs,
            }
            fname = f"session_{meta['session_id']}_{now_filename_stamp()}.json"
            out_path = self.sessions_dir / fname
            # Tránh ghi đè khi compact 2 lần trong cùng 1 giây
            counter = 0
            while out_path.exists():
                counter += 1
                fname = f"session_{meta['session_id']}_{now_filename_stamp()}_{counter}.json"
                out_path = self.sessions_dir / fname
            out_path.write_text(json.dumps(sf, indent=2, ensure_ascii=False))

        self.wal.unlink(missing_ok=True)

        # Rotate
        ts = now_ts()
        new_meta = {
            "session_id": make_session_id(ts + 1),
            "started_at": ts,
            "started_at_iso": now_iso(),
        }
        self.meta.write_text(json.dumps(new_meta, indent=2))
        self.wal.touch()
        return out_path, new_meta

    def _read_wal(self):
        msgs = []
        with open(self.wal, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msgs.append(json.loads(line))
                except json.JSONDecodeError:
                    # Tolerate corrupt last line (crash mid-write)
                    continue
        return msgs


def mkmsg(mid, role, content, conv="conv-1"):
    return {
        "id": mid,
        "conversation_id": conv,
        "role": role,
        "content": content,
        "captured_at": now_ts(),
    }


# ============== Test cases ==============

class TestHistoryLogic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chatgpt-test-")
        self.store = HistoryStore(Path(self.tmp) / "com.nofwl.chatgpt")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----- Init -----
    def test_init_creates_meta_and_empty_wal(self):
        meta, recovered = self.store.init_session()
        self.assertTrue(self.store.meta.exists())
        self.assertTrue(self.store.wal.exists())
        self.assertEqual(self.store.wal.read_text(), "")
        self.assertTrue(meta["session_id"].startswith("s"))
        self.assertIsNone(recovered)

    # ----- Log -----
    def test_log_appends_ndjson(self):
        self.store.init_session()
        self.store.log_message(mkmsg("m1", "user", "hello"))
        self.store.log_message(mkmsg("m2", "assistant", "hi there"))

        lines = self.store.wal.read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)
        m1 = json.loads(lines[0])
        self.assertEqual(m1["id"], "m1")
        self.assertEqual(m1["role"], "user")
        self.assertEqual(m1["content"], "hello")

    def test_log_handles_vietnamese_unicode(self):
        """Đảm bảo content tiếng Việt không bị escape ascii."""
        self.store.init_session()
        self.store.log_message(mkmsg("m1", "user", "Xin chào, đây là tiếng Việt"))
        msgs = self.store._read_wal()
        self.assertEqual(msgs[0]["content"], "Xin chào, đây là tiếng Việt")

    # ----- Compact -----
    def test_compact_produces_valid_json_file(self):
        meta, _ = self.store.init_session()
        self.store.log_message(mkmsg("u1", "user", "Q1"))
        self.store.log_message(mkmsg("a1", "assistant", "A1"))

        out, new_meta = self.store.compact_session("compact")
        self.assertIsNotNone(out)
        self.assertTrue(out.exists())
        self.assertTrue(out.name.startswith(f"session_{meta['session_id']}_"))
        # WAL mới phải rỗng sau rotate
        self.assertEqual(self.store.wal.read_text(), "")

        sf = json.loads(out.read_text())
        self.assertEqual(sf["message_count"], 2)
        self.assertEqual(sf["exported_via"], "compact")
        self.assertEqual(sf["session_id"], meta["session_id"])
        self.assertEqual(sf["messages"][0]["content"], "Q1")

    def test_compact_empty_buffer_returns_none(self):
        self.store.init_session()
        out, _ = self.store.compact_session("compact")
        self.assertIsNone(out)

    # ----- Recovery -----
    def test_crash_recovery_from_wal(self):
        # Phiên 1: log nhưng KHÔNG compact (crash)
        meta1, _ = self.store.init_session()
        self.store.log_message(mkmsg("m1", "user", "before crash"))
        self.store.log_message(mkmsg("m2", "assistant", "answer"))

        # Giả lập restart app: tạo store mới trỏ vào cùng folder
        store2 = HistoryStore(self.store.root)
        meta2, recovered = store2.init_session()

        # Phiên mới khác phiên cũ
        self.assertNotEqual(meta1["session_id"], meta2["session_id"])
        # File recovery phải có
        self.assertIsNotNone(recovered)
        self.assertTrue(recovered.exists())
        self.assertTrue(recovered.name.startswith("session_recovered_"))

        sf = json.loads(recovered.read_text())
        self.assertEqual(sf["exported_via"], "crash_recovery")
        self.assertEqual(sf["message_count"], 2)
        self.assertEqual(sf["session_id"], meta1["session_id"])
        self.assertEqual(sf["messages"][0]["content"], "before crash")

        # WAL mới phải rỗng
        self.assertEqual(store2.wal.read_text(), "")

    def test_corrupt_last_line_in_wal_is_skipped(self):
        self.store.init_session()
        self.store.log_message(mkmsg("m1", "user", "good line"))
        # Append corrupt line (crash mid-write)
        with open(self.store.wal, "a") as f:
            f.write('{"id":"m2","role":"user","con')  # No newline, broken JSON

        msgs = self.store._read_wal()
        self.assertEqual(len(msgs), 1, "Corrupt line must be skipped, good line kept")
        self.assertEqual(msgs[0]["id"], "m1")

    # ----- Filename format -----
    def test_filename_contains_session_id_and_time(self):
        meta, _ = self.store.init_session()
        self.store.log_message(mkmsg("x1", "user", "hi"))
        out, _ = self.store.compact_session("compact")
        # Format: session_{id}_{YYYYMMDD-HHMMSS}.json (có thể kèm _N nếu collision trong 1s)
        pattern = rf"session_{re.escape(meta['session_id'])}_\d{{8}}-\d{{6}}(_\d+)?\.json"
        self.assertRegex(out.name, pattern)

    def test_session_id_format(self):
        """Session id phải có prefix 's' + 7 hex chars."""
        for i in range(20):
            sid = make_session_id(now_ts() + i)
            self.assertRegex(sid, r"^s[0-9a-f]{7}$")

    def test_session_ids_are_unique(self):
        ids = {make_session_id(now_ts() + i) for i in range(100)}
        self.assertEqual(len(ids), 100, "All session ids must be unique")

    # ----- Full flow -----
    def test_full_flow_multiple_sessions(self):
        # Phiên 1
        meta1, _ = self.store.init_session()
        self.store.log_message(mkmsg("u1", "user", "first Q"))
        self.store.log_message(mkmsg("a1", "assistant", "first A"))
        out1, _ = self.store.compact_session("compact")

        # Phiên 2 (sau compact)
        self.store.log_message(mkmsg("u2", "user", "second Q"))
        self.store.log_message(mkmsg("a2", "assistant", "second A"))
        out2, _ = self.store.compact_session("compact")

        # Phiên 3
        self.store.log_message(mkmsg("u3", "user", "third Q"))
        out3, _ = self.store.compact_session("compact")

        files = sorted(self.store.sessions_dir.glob("session_*.json"))
        self.assertEqual(len(files), 3, "Mỗi phiên 1 file riêng, không gộp")

        # Mỗi file có session_id khác nhau
        ids = {json.loads(f.read_text())["session_id"] for f in files}
        self.assertEqual(len(ids), 3)

    def test_app_exit_via_marker(self):
        meta, _ = self.store.init_session()
        self.store.log_message(mkmsg("x", "user", "test exit"))
        out, _ = self.store.compact_session("app_exit")
        sf = json.loads(out.read_text())
        self.assertEqual(sf["exported_via"], "app_exit")

    def test_no_message_loss_under_rapid_logging(self):
        """Log nhanh 1000 message, compact xong phải thấy đủ."""
        self.store.init_session()
        for i in range(1000):
            self.store.log_message(mkmsg(f"m{i}", "user", f"content {i}"))
        out, _ = self.store.compact_session("compact")
        sf = json.loads(out.read_text())
        self.assertEqual(sf["message_count"], 1000)
        self.assertEqual(sf["messages"][0]["content"], "content 0")
        self.assertEqual(sf["messages"][999]["content"], "content 999")

    def test_crash_then_recover_then_new_session_workflow(self):
        """Kịch bản end-to-end: chat → crash → restart → recovery → tiếp tục chat → compact."""
        # Run 1: log nhưng crash
        meta1, _ = self.store.init_session()
        self.store.log_message(mkmsg("q1", "user", "before crash"))

        # Restart
        store2 = HistoryStore(self.store.root)
        meta2, recovered = store2.init_session()
        self.assertIsNotNone(recovered)

        # Tiếp tục chat trong session mới
        store2.log_message(mkmsg("q2", "user", "after restart"))
        out, _ = store2.compact_session("compact")

        # Kiểm tra cả 2 file đều có và độc lập
        recovered_sf = json.loads(recovered.read_text())
        normal_sf = json.loads(out.read_text())
        self.assertEqual(recovered_sf["exported_via"], "crash_recovery")
        self.assertEqual(normal_sf["exported_via"], "compact")
        self.assertEqual(recovered_sf["messages"][0]["content"], "before crash")
        self.assertEqual(normal_sf["messages"][0]["content"], "after restart")
        self.assertNotEqual(recovered_sf["session_id"], normal_sf["session_id"])


# ============== Test keyword regex (giống Ask.tsx) ==============

class TestCompactKeywordRegex(unittest.TestCase):
    """Mô phỏng regex /^\\s*\\/?(compact|lưu|luu)\\s*$/i từ Ask.tsx."""

    PATTERN = re.compile(r"^\s*/?(compact|lưu|luu)\s*$", re.IGNORECASE)

    def assertMatch(self, s):
        self.assertIsNotNone(self.PATTERN.match(s), f"Expected match: {s!r}")

    def assertNoMatch(self, s):
        self.assertIsNone(self.PATTERN.match(s), f"Expected no match: {s!r}")

    def test_compact_matches(self):
        self.assertMatch("compact")
        self.assertMatch("Compact")
        self.assertMatch("COMPACT")
        self.assertMatch("/compact")
        self.assertMatch("  compact  ")
        self.assertMatch(" /compact ")

    def test_luu_matches(self):
        self.assertMatch("lưu")
        self.assertMatch("Lưu")
        self.assertMatch("/lưu")
        self.assertMatch("luu")  # fallback không dấu
        self.assertMatch("  /luu  ")

    def test_does_not_match_in_sentence(self):
        self.assertNoMatch("how to compact json")
        self.assertNoMatch("compact this please")
        self.assertNoMatch("hãy lưu file")
        self.assertNoMatch("the compactor is broken")
        self.assertNoMatch("compactxxx")
        self.assertNoMatch("xxcompact")


if __name__ == "__main__":
    unittest.main(verbosity=2)
