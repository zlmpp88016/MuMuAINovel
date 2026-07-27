from __future__ import annotations

import asyncio
import ast
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


if "anthropic" not in sys.modules:
    anthropic_stub = types.ModuleType("anthropic")
    anthropic_stub.AsyncAnthropic = type("AsyncAnthropic", (), {})
    sys.modules["anthropic"] = anthropic_stub

from app.api import polish
from app.schemas.polish import PolishRequest


class _FakeDB:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class _FakeAIService:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_text(self, prompt: str, **kwargs) -> dict[str, str]:
        self.prompts.append(prompt)
        return {"content": "自然一些的改写"}


def _bridge_with_result(result: str):
    class FakeBridge:
        def __init__(self, user_id: str, db) -> None:
            self.user_id = user_id

        async def get_highlight_for_denoise(self, query: str, limit: int = 5) -> str:
            return result

    return FakeBridge


def test_polish_router_is_mounted_and_requires_login() -> None:
    main_path = Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    source = ast.unparse(tree)

    assert "polish.router" in source

    route = next(route for route in polish.router.routes if route.path == "/polish")
    dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
    assert polish.require_login in dependency_calls


def test_polish_text_uses_corpus_reference(monkeypatch) -> None:
    monkeypatch.setattr(polish, "CorpusBridge", _bridge_with_result("【参考范文】自然节奏"))
    db = _FakeDB()
    ai_service = _FakeAIService()

    response = asyncio.run(
        polish.polish_text(
            PolishRequest(original_text="原始文本"),
            user=SimpleNamespace(user_id="user-1"),
            db=db,
            user_ai_service=ai_service,
        )
    )

    assert response.polished_text == "自然一些的改写"
    assert "【参考范文】自然节奏" in ai_service.prompts[0]
    assert db.added == []


def test_polish_text_degrades_when_corpus_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(polish, "CorpusBridge", _bridge_with_result(""))
    ai_service = _FakeAIService()

    response = asyncio.run(
        polish.polish_text(
            PolishRequest(original_text="原始文本"),
            user=SimpleNamespace(user_id="user-1"),
            db=_FakeDB(),
            user_ai_service=ai_service,
        )
    )

    assert response.polished_text == "自然一些的改写"
    assert "参考范文" not in ai_service.prompts[0]


def test_polish_history_uses_generation_history_columns(monkeypatch) -> None:
    monkeypatch.setattr(polish, "CorpusBridge", _bridge_with_result(""))
    db = _FakeDB()

    asyncio.run(
        polish.polish_text(
            PolishRequest(original_text="原始文本", project_id="project-1"),
            user=SimpleNamespace(user_id="user-1"),
            db=db,
            user_ai_service=_FakeAIService(),
        )
    )

    history = db.added[0]
    assert history.project_id == "project-1"
    assert history.generated_content == "自然一些的改写"
    assert history.model == "default"
    assert db.commits == 1


def test_polish_request_rejects_empty_text() -> None:
    with pytest.raises(ValidationError):
        PolishRequest(original_text="")
