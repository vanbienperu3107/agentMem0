#!/usr/bin/env python3
"""MCP server (stdio transport) exposing archive tools to Claude Code.

NEW on branch new-features: load_context_for_continuation, search_old_sessions_semantic.

Used by Claude Code in VS Code/CLI as a local stdio MCP server.
For remote MCP (Claude App, ChatGPT App), see mcp-http-server/.

Required env vars:
    ARCHIVE_URL          e.g. https://claude.hangocthanh.io.vn/archive
    ARCHIVE_AUTH_TOKEN   bearer token (matches VPS .env)
    USER_ID              default "thanh"
"""
import os
import asyncio
import json
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

ARCHIVE_URL = os.environ["ARCHIVE_URL"]
TOKEN = os.environ["ARCHIVE_AUTH_TOKEN"]
USER_ID = os.environ.get("USER_ID", "thanh")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

server = Server("archive")


@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="list_old_sessions",
            description="List archived chat sessions by project and date range.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_tag": {"type": "string"},
                    "date_from": {"type": "string"},
                    "date_to": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        ),
        Tool(
            name="get_session_summary",
            description=(
                "Get compact view: metadata + first/last 5 messages. "
                "USE THIS instead of get_old_session for most cases."
            ),
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        Tool(
            name="get_old_session",
            description=(
                "Fetch FULL transcript. WARNING: very large response. "
                "Prefer get_session_summary for overview."
            ),
            inputSchema={
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
        ),
        Tool(
            name="search_old_sessions",
            description=(
                "Keyword search across session summaries (ILIKE). Fast, exact match."
            ),
            inputSchema={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        ),
        Tool(
            name="search_old_sessions_semantic",
            description=(
                "Semantic search via embeddings. Better for fuzzy queries "
                "like 'sessions about deployment' vs exact keyword."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["q"],
            },
        ),
        Tool(
            name="load_context_for_continuation",
            description=(
                "Load context from a past session so user can CONTINUE the conversation. "
                "Returns formatted block ready to inject into prompt. "
                "Choose strategy: 'compressed' (default, summary + first/last), "
                "'full' (small sessions only), 'rag' (find chunks relevant to a new query)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "strategy": {
                        "type": "string",
                        "enum": ["full", "compressed", "rag"],
                        "default": "compressed",
                    },
                    "query": {
                        "type": "string",
                        "description": "Required when strategy=rag",
                    },
                },
                "required": ["session_id"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name, args):
    async with httpx.AsyncClient(timeout=60, headers=HEADERS) as c:

        if name == "list_old_sessions":
            r = await c.get(
                f"{ARCHIVE_URL}/sessions",
                params={"user_id": USER_ID, **args},
            )
            return [TextContent(type="text", text=r.text)]

        if name == "get_session_summary":
            r = await c.get(f"{ARCHIVE_URL}/sessions/{args['session_id']}")
            data = r.json()
            transcript = data.get("transcript") or []
            compact = {
                "id": data["id"],
                "started_at": data["started_at"],
                "ended_at": data.get("ended_at"),
                "project_tag": data.get("project_tag"),
                "summary": data.get("llm_summary") or data.get("summary"),
                "message_count": data["message_count"],
                "first_messages": transcript[:5],
                "last_messages": transcript[-5:] if len(transcript) > 5 else [],
            }
            return [TextContent(
                type="text",
                text=json.dumps(compact, ensure_ascii=False, indent=2),
            )]

        if name == "get_old_session":
            r = await c.get(f"{ARCHIVE_URL}/sessions/{args['session_id']}")
            return [TextContent(type="text", text=r.text)]

        if name == "search_old_sessions":
            r = await c.get(
                f"{ARCHIVE_URL}/sessions",
                params={"user_id": USER_ID, "q": args["q"], "limit": 20},
            )
            return [TextContent(type="text", text=r.text)]

        if name == "search_old_sessions_semantic":
            r = await c.get(
                f"{ARCHIVE_URL}/sessions/search-semantic",
                params={"user_id": USER_ID, "q": args["q"],
                        "limit": args.get("limit", 10)},
            )
            return [TextContent(type="text", text=r.text)]

        if name == "load_context_for_continuation":
            params = {"strategy": args.get("strategy", "compressed")}
            if "query" in args:
                params["query"] = args["query"]
            r = await c.get(
                f"{ARCHIVE_URL}/sessions/{args['session_id']}/context",
                params=params,
            )
            return [TextContent(type="text", text=r.text)]

        return [TextContent(type="text", text=f"unknown tool {name}")]


async def main():
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
