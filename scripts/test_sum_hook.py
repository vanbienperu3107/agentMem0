#!/usr/bin/env python3
"""Unit tests cho scripts/sum_hook.py (khong can mang / claude CLI)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sum_hook  # noqa: E402


class TestParseHookInput(unittest.TestCase):
    def test_valid_json(self):
        self.assertEqual(
            sum_hook.parse_hook_input('{"transcript_path": "/a/b.jsonl"}'),
            {"transcript_path": "/a/b.jsonl"})

    def test_empty(self):
        self.assertEqual(sum_hook.parse_hook_input(""), {})
        self.assertEqual(sum_hook.parse_hook_input("   "), {})

    def test_invalid_json(self):
        self.assertEqual(sum_hook.parse_hook_input("{not json"), {})

    def test_non_dict(self):
        self.assertEqual(sum_hook.parse_hook_input("[1,2,3]"), {})


class TestReadAndExtract(unittest.TestCase):
    def _write(self, lines):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for ln in lines:
                f.write(ln + "\n")
        self.addCleanup(os.remove, path)
        return path

    def test_read_skips_bad_lines(self):
        path = self._write([
            json.dumps({"type": "user", "message": {"content": "xin chao"}}),
            "rac khong phai json", "",
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "text", "text": "chao"}]}}),
        ])
        self.assertEqual(len(sum_hook.read_transcript(path)), 2)

    def test_extract_filters_roles(self):
        msgs = [
            {"type": "system", "message": {"content": "boot"}},
            {"type": "user", "message": {"content": "cau hoi"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "tra loi"}]}},
            {"type": "tool", "message": {"content": "noise"}},
        ]
        text = sum_hook.extract_text(msgs)
        self.assertIn("[user] cau hoi", text)
        self.assertIn("[assistant] tra loi", text)
        self.assertNotIn("boot", text)
        self.assertNotIn("noise", text)

    def test_extract_empty(self):
        self.assertEqual(sum_hook.extract_text([]), "")


class TestBuildPrompt(unittest.TestCase):
    def test_contains_text_param_and_instructions(self):
        p = sum_hook.build_prompt("[user] hello", user_id="thanh")
        self.assertIn("thanh", p)
        self.assertIn("add_memory", p)
        self.assertIn("text =", p)
        self.assertIn("Transcript:", p)
        self.assertIn("hello", p)

    def test_truncates(self):
        p = sum_hook.build_prompt("x" * 50000, user_id="thanh", max_chars=1000)
        self.assertLess(p.count("x"), 1100)


class TestBuildClaudeCmd(unittest.TestCase):
    def test_cmd_shape(self):
        cmd = sum_hook.build_claude_cmd("PROMPT", model="claude-haiku-4-5-20251001", mcp_server="mem0")
        self.assertEqual(cmd[0], "claude")
        self.assertIn("-p", cmd)
        self.assertIn("PROMPT", cmd)
        self.assertIn("claude-haiku-4-5-20251001", cmd)
        self.assertIn("mcp__mem0__add_memory", cmd)

    def test_custom_server_name(self):
        cmd = sum_hook.build_claude_cmd("P", mcp_server="mem0-selfhosted")
        self.assertIn("mcp__mem0-selfhosted__add_memory", cmd)


class TestMainSafety(unittest.TestCase):
    def test_missing_transcript_exits_zero(self):
        self.assertEqual(sum_hook.main(["--transcript", "/khong/ton/tai.jsonl"]), 0)

    def test_dry_run_prints_prompt(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "deploy mem0"}}) + "\n")
        self.addCleanup(os.remove, path)
        self.assertEqual(sum_hook.main(["--transcript", path, "--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
