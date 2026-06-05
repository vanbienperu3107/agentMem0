"""Unit tests for r2-budget-check.py.

Runs without hitting real R2 or Telegram — mocks all external calls.
Tests:
- classify_level boundaries (ok / warning / critical)
- days_since (date math, ISO parsing, tz handling)
- build_message format (3 levels with correct emoji + content)
- should_alert logic via main() with mocked boto3 + urllib

Usage:
    pip install pytest
    pytest scripts/test_r2_budget_check.py -v

Or via CI: see .github/workflows/test.yml step "Run cron unit tests".
"""
from __future__ import annotations
import importlib.util
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


# Required env vars before importing the module under test
os.environ.setdefault("R2_ENDPOINT_URL", "https://test.r2.example.com")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-key-id")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret")
os.environ.setdefault("R2_BUCKET", "test-bucket")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-bot-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "12345")


def _load_module():
    """Load r2-budget-check.py (hyphen in name → can't normal import)."""
    spec = importlib.util.spec_from_file_location(
        "r2bc",
        Path(__file__).parent / "r2-budget-check.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


r2bc = _load_module()


class TestClassifyLevel(unittest.TestCase):
    """Boundary tests for classify_level()."""

    def test_below_warn_returns_ok(self):
        self.assertEqual(r2bc.classify_level(0.0), "ok")
        self.assertEqual(r2bc.classify_level(5.0), "ok")
        self.assertEqual(r2bc.classify_level(r2bc.WARN_GB - 0.001), "ok")

    def test_exactly_warn_returns_warning(self):
        self.assertEqual(r2bc.classify_level(r2bc.WARN_GB), "warning")

    def test_between_warn_critical_returns_warning(self):
        mid = (r2bc.WARN_GB + r2bc.CRITICAL_GB) / 2
        self.assertEqual(r2bc.classify_level(mid), "warning")

    def test_exactly_critical_returns_critical(self):
        self.assertEqual(r2bc.classify_level(r2bc.CRITICAL_GB), "critical")

    def test_above_critical_returns_critical(self):
        self.assertEqual(r2bc.classify_level(r2bc.CRITICAL_GB + 1.0), "critical")
        self.assertEqual(r2bc.classify_level(100.0), "critical")


class TestDaysSince(unittest.TestCase):
    """Tests for days_since() date math."""

    def test_none_returns_infinity(self):
        self.assertEqual(r2bc.days_since(None), float("inf"))

    def test_empty_string_returns_infinity(self):
        self.assertEqual(r2bc.days_since(""), float("inf"))

    def test_recent_timestamp_small_value(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        d = r2bc.days_since(recent)
        self.assertGreater(d, 0)
        self.assertLess(d, 1)

    def test_one_week_ago(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        d = r2bc.days_since(ts)
        self.assertAlmostEqual(d, 7.0, delta=0.01)

    def test_handles_z_suffix(self):
        """ISO string with 'Z' (Zulu) suffix instead of +00:00."""
        ts = "2026-01-01T00:00:00Z"
        d = r2bc.days_since(ts)
        self.assertGreater(d, 100)  # Was a while ago

    def test_handles_naive_datetime_object(self):
        """If a naive datetime is passed in, function should still work."""
        naive = datetime.utcnow() - timedelta(days=3)
        d = r2bc.days_since(naive)
        self.assertAlmostEqual(d, 3.0, delta=0.1)


class TestBuildMessage(unittest.TestCase):
    """Test Telegram message formatting per level."""

    def test_critical_message_has_red_emoji(self):
        msg = r2bc.build_message("critical", 9.5, 95.0, 1000)
        self.assertIn("🔴", msg)
        self.assertIn("CRITICAL", msg)
        self.assertIn("9.50 GB", msg)
        self.assertIn("95.0%", msg)
        self.assertIn("1000", msg)

    def test_warning_message_has_yellow_emoji(self):
        msg = r2bc.build_message("warning", 7.5, 75.0, 500)
        self.assertIn("🟡", msg)
        self.assertIn("Warning", msg)
        self.assertIn("7.50 GB", msg)
        self.assertNotIn("🔴", msg)

    def test_ok_message_has_check_emoji(self):
        msg = r2bc.build_message("ok", 1.2, 12.0, 50)
        self.assertIn("✅", msg)
        self.assertIn("OK", msg)
        self.assertIn("weekly check", msg)
        self.assertNotIn("🔴", msg)
        self.assertNotIn("🟡", msg)

    def test_message_contains_bucket_name(self):
        msg = r2bc.build_message("ok", 0.0, 0.0, 0)
        self.assertIn(r2bc.R2_BUCKET, msg)


class TestMainFlow(unittest.TestCase):
    """End-to-end flow with mocked boto3 + Telegram + state file."""

    def setUp(self):
        # Use a temp state file per test
        self.tmp_state = Path("/tmp/test_r2_state.json")
        if self.tmp_state.exists():
            self.tmp_state.unlink()
        r2bc.STATE_FILE = self.tmp_state

    def tearDown(self):
        if self.tmp_state.exists():
            self.tmp_state.unlink()

    def _mock_bucket(self, total_bytes: int, object_count: int):
        """Build a mocked boto3 paginator that returns N objects of given total size."""
        per_obj = total_bytes // max(object_count, 1)
        contents = [{"Size": per_obj, "Key": f"obj{i}"} for i in range(object_count)]
        page = {"Contents": contents}
        paginator = MagicMock()
        paginator.paginate.return_value = [page]
        client = MagicMock()
        client.get_paginator.return_value = paginator
        return client

    @patch("urllib.request.urlopen")
    @patch.object(r2bc, "r2_client")
    def test_ok_level_with_empty_bucket_no_state(self, mock_client_fn, mock_urlopen):
        """First run, empty bucket → OK level, weekly heartbeat alert sent."""
        mock_client_fn.return_value = self._mock_bucket(0, 0)
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'

        result = r2bc.main()

        self.assertEqual(result, 0)
        # Verify alert was sent (Telegram API called once)
        self.assertEqual(mock_urlopen.call_count, 1)
        # Verify state was persisted
        self.assertTrue(self.tmp_state.exists())
        state = json.loads(self.tmp_state.read_text())
        self.assertEqual(state["last_alert_level"], "ok")
        self.assertEqual(state["last_gb"], 0.0)

    @patch("urllib.request.urlopen")
    @patch.object(r2bc, "r2_client")
    def test_warning_threshold_triggers_alert(self, mock_client_fn, mock_urlopen):
        """Bucket exceeds WARN_GB → warning alert sent."""
        # 8 GB = above default 7 GB warn threshold
        eight_gb = int(8 * 1024 ** 3)
        mock_client_fn.return_value = self._mock_bucket(eight_gb, 1)
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'

        result = r2bc.main()

        self.assertEqual(result, 0)
        self.assertEqual(mock_urlopen.call_count, 1)
        # Verify state recorded warning
        state = json.loads(self.tmp_state.read_text())
        self.assertEqual(state["last_alert_level"], "warning")

    @patch("urllib.request.urlopen")
    @patch.object(r2bc, "r2_client")
    def test_critical_threshold_triggers_alert(self, mock_client_fn, mock_urlopen):
        """Bucket exceeds CRITICAL_GB → critical alert sent."""
        ten_gb = int(10 * 1024 ** 3)  # Above 9 GB critical
        mock_client_fn.return_value = self._mock_bucket(ten_gb, 1)
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'

        result = r2bc.main()
        self.assertEqual(result, 0)
        state = json.loads(self.tmp_state.read_text())
        self.assertEqual(state["last_alert_level"], "critical")

    @patch("urllib.request.urlopen")
    @patch.object(r2bc, "r2_client")
    def test_same_level_within_day_no_alert(self, mock_client_fn, mock_urlopen):
        """If level=ok and last_check < 7 days, no alert sent."""
        mock_client_fn.return_value = self._mock_bucket(0, 0)
        # Pre-populate state with recent OK check
        recent = datetime.now(timezone.utc).isoformat()
        self.tmp_state.write_text(json.dumps({
            "last_alert_level": "ok",
            "last_check": recent,
        }))

        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
        result = r2bc.main()

        self.assertEqual(result, 0)
        # No alert sent (same level, too soon for weekly summary)
        self.assertEqual(mock_urlopen.call_count, 0)

    @patch("urllib.request.urlopen")
    @patch.object(r2bc, "r2_client")
    def test_bucket_stats_failure_sends_error_alert(self, mock_client_fn, mock_urlopen):
        """If boto3 raises, send error notification and return non-zero."""
        mock_client_fn.return_value.get_paginator.side_effect = Exception("R2 timeout")
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'

        result = r2bc.main()

        self.assertEqual(result, 1)
        self.assertEqual(mock_urlopen.call_count, 1)  # Error alert sent


if __name__ == "__main__":
    unittest.main(verbosity=2)
