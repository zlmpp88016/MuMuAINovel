from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import book_analyzer.config as config_module
from book_analyzer.config import Settings


@pytest.fixture
def mcp_server_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> ModuleType:
    settings = Settings(
        project_root=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'book_analyzer.db').as_posix()}",
        data_dir=tmp_path / "data",
        vector_backend="simple",
    )
    monkeypatch.setattr(
        config_module.Settings,
        "from_env",
        classmethod(lambda cls: settings),
    )
    sys.modules.pop("book_analyzer.mcp_server", None)
    module = importlib.import_module("book_analyzer.mcp_server")
    yield module
    module.engine.dispose()
    sys.modules.pop("book_analyzer.mcp_server", None)


def _book_service_stub() -> SimpleNamespace:
    return SimpleNamespace(
        get_corpus_version=Mock(return_value="test-version"),
        search_reference_passages=AsyncMock(return_value=[]),
        search_highlight_passages=AsyncMock(return_value=[]),
        get_style_profile=Mock(return_value={"sample_count": 0, "profiles": []}),
        search_plot_patterns=AsyncMock(return_value=[]),
        search_character_archetypes=Mock(return_value=[]),
        find_foreshadow_patterns=AsyncMock(return_value=[]),
    )


def test_every_mcp_interface_logs_chinese_entry_and_success_exit(
    mcp_server_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = mcp_server_module
    module.book_service = _book_service_stub()

    async def invoke_async_interfaces() -> None:
        await module.corpus_search_reference_passages("江湖夜雨")
        await module.corpus_get_highlight_passages("人物冲突")
        await module.corpus_get_style_profile()
        await module.corpus_search_plot_patterns("成长线")
        await module.corpus_find_foreshadow_patterns("旧信物", "pair")

    with caplog.at_level(logging.INFO, logger=module.__name__):
        asyncio.run(invoke_async_interfaces())
        module.corpus_search_character_archetypes("侠客")

    interface_names = [
        "corpus_search_reference_passages",
        "corpus_get_highlight_passages",
        "corpus_get_style_profile",
        "corpus_search_plot_patterns",
        "corpus_search_character_archetypes",
        "corpus_find_foreshadow_patterns",
    ]
    messages = [record.getMessage() for record in caplog.records]
    for interface_name in interface_names:
        assert any(
            "MCP 接口入口" in message and interface_name in message
            for message in messages
        )
        assert any(
            "MCP 接口出口" in message
            and interface_name in message
            and "状态：成功" in message
            for message in messages
        )

    assert "query" in inspect.signature(
        module.corpus_search_reference_passages
    ).parameters


def test_mcp_interfaces_log_failed_exit_without_business_data(
    mcp_server_module: ModuleType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    module = mcp_server_module
    service = _book_service_stub()
    service.search_reference_passages = AsyncMock(
        side_effect=RuntimeError("不要记录这段业务数据")
    )
    service.search_character_archetypes = Mock(
        side_effect=LookupError("不要记录这段同步业务数据")
    )
    module.book_service = service

    with caplog.at_level(logging.INFO, logger=module.__name__):
        with pytest.raises(RuntimeError, match="不要记录这段业务数据"):
            asyncio.run(module.corpus_search_reference_passages("敏感查询正文"))
        with pytest.raises(LookupError, match="不要记录这段同步业务数据"):
            module.corpus_search_character_archetypes("同步敏感查询正文")

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "MCP 接口出口" in message
        and "corpus_search_reference_passages" in message
        and "状态：失败" in message
        and "异常类型：RuntimeError" in message
        for message in messages
    )
    assert any(
        "MCP 接口出口" in message
        and "corpus_search_character_archetypes" in message
        and "状态：失败" in message
        and "异常类型：LookupError" in message
        for message in messages
    )
    assert all("敏感查询正文" not in message for message in messages)
    assert all("同步敏感查询正文" not in message for message in messages)
    assert all("不要记录这段业务数据" not in message for message in messages)
    assert all("不要记录这段同步业务数据" not in message for message in messages)
