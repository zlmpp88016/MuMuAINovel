"""Smoke-test the analyzer-book streamable HTTP MCP contract."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


EXPECTED_TOOLS = {
    "corpus_search_reference_passages",
    "corpus_get_highlight_passages",
    "corpus_get_style_profile",
    "corpus_search_plot_patterns",
    "corpus_search_character_archetypes",
    "corpus_find_foreshadow_patterns",
}

SMOKE_CALLS = [
    ("corpus_search_plot_patterns", {"query": "雨夜冲突", "limit": 2}),
    ("corpus_search_character_archetypes", {"query": "主角成长", "limit": 2}),
    (
        "corpus_find_foreshadow_patterns",
        {"query": "线索回收", "pattern_type": "pair", "limit": 2},
    ),
]


def _structured_payload(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structuredContent", None)
    if payload is None:
        payload = getattr(result, "structured_content", None)
    return payload if isinstance(payload, dict) else {}


async def check_contract(server_url: str) -> dict[str, Any]:
    """Discover tools and call each Phase 2 writing-asset contract once."""
    async with streamablehttp_client(server_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            tool_names = {tool.name for tool in tools_result.tools}
            missing = sorted(EXPECTED_TOOLS - tool_names)
            if missing:
                raise RuntimeError(f"MCP tools missing: {', '.join(missing)}")

            calls: dict[str, Any] = {}
            for tool_name, arguments in SMOKE_CALLS:
                result = await session.call_tool(tool_name, arguments)
                if getattr(result, "isError", False):
                    raise RuntimeError(f"MCP tool returned an error: {tool_name}")
                payload = _structured_payload(result)
                if payload.get("schema_version") != "1.0":
                    raise RuntimeError(f"Invalid schema version: {tool_name}")
                calls[tool_name] = {
                    "total": payload.get("total", 0),
                    "fields": sorted(payload),
                }

            return {
                "server_url": server_url,
                "tools": sorted(tool_names),
                "calls": calls,
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default="http://127.0.0.1:8765/mcp")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check_contract(args.server_url)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
