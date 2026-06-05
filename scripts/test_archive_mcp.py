"""Unit tests for archive-mcp.py (Claude Code stdio MCP server).

Validates:
- All 6 tools registered
- Tool names match expected
- Each tool has required schema fields (name, description, inputSchema)
- inputSchema declares required parameters correctly

Runs without hitting real archive-api — only loads tool definitions.

Usage:
    python -m unittest scripts.test_archive_mcp -v
"""
from __future__ import annotations
import asyncio
import importlib.util
import os
import sys
import unittest
from pathlib import Path

# Required env vars before importing (the module reads at import time)
os.environ.setdefault("ARCHIVE_URL", "https://test.example.com/archive")
os.environ.setdefault("ARCHIVE_AUTH_TOKEN", "test-token")
os.environ.setdefault("USER_ID", "thanh")


def _load_module():
    """Load archive-mcp.py (hyphen in filename → can't normal import)."""
    spec = importlib.util.spec_from_file_location(
        "amcp",
        Path(__file__).parent / "archive-mcp.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


amcp = _load_module()


def _get_tools():
    """Call the @server.list_tools() decorated async function and return tools list.

    mcp.server.Server wraps handler so it returns ServerResult(root=ListToolsResult).
    Tools are at result.root.tools.
    """
    server = amcp.server
    handlers = getattr(server, "request_handlers", None)
    if handlers is None:
        return None
    for req_type, handler in handlers.items():
        if "ListTools" in str(req_type):
            result = asyncio.run(handler(None))
            # ServerResult.root → ListToolsResult.tools
            root = getattr(result, "root", result)
            return getattr(root, "tools", None)
    return None


EXPECTED_TOOLS = {
    "list_old_sessions",
    "get_session_summary",
    "get_old_session",
    "search_old_sessions",
    "search_old_sessions_semantic",
    "load_context_for_continuation",
}


class TestToolRegistration(unittest.TestCase):
    """Validate all 6 tools are registered with correct schema."""

    def setUp(self):
        self.tools = _get_tools()

    def test_tools_loaded(self):
        self.assertIsNotNone(
            self.tools,
            "Could not find list_tools handler. mcp.server internals may have changed.",
        )

    def test_exactly_six_tools(self):
        if self.tools is None:
            self.skipTest("tools not loaded")
        self.assertEqual(len(self.tools), 6, f"Expected 6 tools, got {len(self.tools)}")

    def test_tool_names_match_expected(self):
        if self.tools is None:
            self.skipTest("tools not loaded")
        actual = {t.name for t in self.tools}
        self.assertEqual(actual, EXPECTED_TOOLS)

    def test_every_tool_has_description(self):
        if self.tools is None:
            self.skipTest("tools not loaded")
        for tool in self.tools:
            self.assertTrue(
                tool.description and len(tool.description) > 10,
                f"Tool {tool.name} missing/short description",
            )

    def test_every_tool_has_inputschema(self):
        if self.tools is None:
            self.skipTest("tools not loaded")
        for tool in self.tools:
            self.assertIsNotNone(tool.inputSchema)
            self.assertEqual(tool.inputSchema.get("type"), "object")
            self.assertIn("properties", tool.inputSchema)


class TestRequiredParams(unittest.TestCase):
    """Validate required parameters declared correctly per tool."""

    def setUp(self):
        self.tools = _get_tools()
        if self.tools is None:
            self.skipTest("tools not loaded")
        self.by_name = {t.name: t for t in self.tools}

    def test_get_session_summary_requires_session_id(self):
        tool = self.by_name["get_session_summary"]
        self.assertIn("session_id", tool.inputSchema.get("required", []))

    def test_get_old_session_requires_session_id(self):
        tool = self.by_name["get_old_session"]
        self.assertIn("session_id", tool.inputSchema.get("required", []))

    def test_search_old_sessions_requires_q(self):
        tool = self.by_name["search_old_sessions"]
        self.assertIn("q", tool.inputSchema.get("required", []))

    def test_search_old_sessions_semantic_requires_q(self):
        tool = self.by_name["search_old_sessions_semantic"]
        self.assertIn("q", tool.inputSchema.get("required", []))

    def test_load_context_for_continuation_requires_session_id(self):
        tool = self.by_name["load_context_for_continuation"]
        self.assertIn("session_id", tool.inputSchema.get("required", []))

    def test_load_context_strategy_enum(self):
        """strategy parameter must be enum: full | compressed | rag."""
        tool = self.by_name["load_context_for_continuation"]
        strategy_prop = tool.inputSchema["properties"].get("strategy")
        self.assertIsNotNone(strategy_prop)
        self.assertEqual(set(strategy_prop["enum"]), {"full", "compressed", "rag"})

    def test_list_old_sessions_no_required(self):
        """list_old_sessions all params optional."""
        tool = self.by_name["list_old_sessions"]
        required = tool.inputSchema.get("required", [])
        self.assertEqual(required, [], f"Expected no required params, got {required}")


class TestModuleConstants(unittest.TestCase):
    """Validate module-level constants from env vars."""

    def test_archive_url_set(self):
        self.assertEqual(amcp.ARCHIVE_URL, "https://test.example.com/archive")

    def test_token_set(self):
        self.assertEqual(amcp.TOKEN, "test-token")

    def test_user_id_default(self):
        self.assertEqual(amcp.USER_ID, "thanh")

    def test_headers_built_correctly(self):
        self.assertEqual(amcp.HEADERS["Authorization"], "Bearer test-token")


if __name__ == "__main__":
    unittest.main(verbosity=2)
