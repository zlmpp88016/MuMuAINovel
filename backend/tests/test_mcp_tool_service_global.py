from __future__ import annotations

import asyncio
import logging
import sys
from types import ModuleType
from types import SimpleNamespace

registry_module = ModuleType("app.mcp.registry")
registry_module.mcp_registry = SimpleNamespace()
sys.modules.setdefault("app.mcp.registry", registry_module)

from app import database as _database  # noqa: F401
from app.services.mcp_tool_service import (
    GLOBAL_MCP_USER_ID,
    MCPToolService,
)
from app.services import mcp_tool_service as mcp_tool_module


class FakeScalarResult:
    def __init__(self, values: list[SimpleNamespace]) -> None:
        self.values = values

    def scalars(self) -> "FakeScalarResult":
        return self

    def all(self) -> list[SimpleNamespace]:
        return self.values


class FakeSession:
    def __init__(self, plugins: list[SimpleNamespace]) -> None:
        self.plugins = plugins

    async def execute(self, _query) -> FakeScalarResult:
        return FakeScalarResult(self.plugins)


def _plugin(
    user_id: str,
    name: str,
    category: str,
    sort_order: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        plugin_name=name,
        category=category,
        sort_order=sort_order,
        enabled=True,
    )


def test_tool_discovery_merges_user_override_and_read_only_global(monkeypatch) -> None:
    plugins = [
        _plugin(GLOBAL_MCP_USER_ID, "book-corpus", "corpus"),
        _plugin("user-1", "book-corpus", "general"),
        _plugin(GLOBAL_MCP_USER_ID, "shared-corpus", "corpus"),
        _plugin(GLOBAL_MCP_USER_ID, "admin-tools", "general"),
    ]
    service = MCPToolService()
    namespaces: list[tuple[str, str]] = []

    monkeypatch.setattr(
        mcp_tool_module.mcp_registry,
        "get_client",
        lambda user_id, plugin_name: object(),
        raising=False,
    )

    async def fake_tools(user_id: str, plugin_name: str) -> list[dict]:
        namespaces.append((user_id, plugin_name))
        if plugin_name == "shared-corpus":
            return [
                {"name": "corpus_search", "inputSchema": {}},
                {"name": "delete_book", "inputSchema": {}},
            ]
        return [{"name": "user_tool", "inputSchema": {}}]

    service._get_plugin_tools_cached = fake_tools

    tools = asyncio.run(
        service.get_user_enabled_tools("user-1", FakeSession(plugins))
    )

    names = [tool["function"]["name"] for tool in tools]
    assert names == ["book-corpus_user_tool", "shared-corpus_corpus_search"]
    assert ("user-1", "book-corpus") in namespaces
    assert (GLOBAL_MCP_USER_ID, "book-corpus") not in namespaces
    assert (GLOBAL_MCP_USER_ID, "shared-corpus") in namespaces


def test_global_tool_executes_in_plugin_registry_namespace(caplog) -> None:
    plugin = _plugin(GLOBAL_MCP_USER_ID, "book-corpus", "corpus")
    service = MCPToolService(max_retries=1)
    calls: list[dict] = []

    async def fake_call(**kwargs):
        calls.append(kwargs)
        return {"total": 0, "passages": []}

    service._call_tool_with_retry = fake_call
    tool_call = {
        "id": "call-1",
        "function": {
            "name": "book-corpus_corpus_search_reference_passages",
            "arguments": '{"query": "雨夜"}',
        },
    }

    with caplog.at_level(logging.INFO):
        result = asyncio.run(
            service.execute_tool_calls(
                user_id="user-1",
                tool_calls=[tool_call],
                db_session=FakeSession([plugin]),
            )
        )

    assert result[0]["success"] is True
    assert calls[0]["user_id"] == GLOBAL_MCP_USER_ID
    assert calls[0]["plugin_name"] == "book-corpus"
    assert "[MCP调用][批量执行] 开始" in caplog.text
    assert "[MCP调用][单工具] 成功" in caplog.text
    assert "雨夜" not in caplog.text


def test_global_plugin_rejects_non_corpus_tool() -> None:
    plugin = _plugin(GLOBAL_MCP_USER_ID, "book-corpus", "corpus")
    service = MCPToolService(max_retries=1)
    tool_call = {
        "id": "call-2",
        "function": {
            "name": "book-corpus_delete_book",
            "arguments": "{}",
        },
    }

    result = asyncio.run(
        service.execute_tool_calls(
            user_id="user-1",
            tool_calls=[tool_call],
            db_session=FakeSession([plugin]),
        )
    )

    assert result[0]["success"] is False
    assert "只允许调用只读 corpus 工具" in result[0]["error"]
