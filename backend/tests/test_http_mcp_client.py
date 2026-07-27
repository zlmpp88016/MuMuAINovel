from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

# cd e:\work\python_ws\MuMuAINovel\backend; python -m pytest tests/test_http_mcp_client.py::test_call_tool_prefers_structured_content -v



MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "mcp" / "http_client.py"
SPEC = importlib.util.spec_from_file_location("http_mcp_client_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
HTTPMCPClient = MODULE.HTTPMCPClient


class _FakeSession:
    async def call_tool(self, tool_name: str, arguments: dict) -> SimpleNamespace:
        return SimpleNamespace(
            structuredContent={"tool": tool_name, "arguments": arguments},
            content=[SimpleNamespace(text='{"tool": "text-fallback"}')],
        )


def test_call_tool_prefers_structured_content(caplog) -> None:
    client = HTTPMCPClient("http://localhost:8765/mcp")
    client._session = _FakeSession()

    async def _already_connected() -> None:
        return None

    client._ensure_connected = _already_connected

    with caplog.at_level(logging.INFO):
        result = asyncio.run(client.call_tool("corpus_search", {"query": "雨夜"}))
    print("aaa")
    print(result)
    assert result == {"tool": "corpus_search", "arguments": {"query": "雨夜"}}
    assert "[MCP调用][HTTP请求] 开始" in caplog.text
    assert "参数字段=['query']" in caplog.text
    assert "[MCP调用][HTTP响应] 收到结果" in caplog.text
    assert "雨夜" not in caplog.text
