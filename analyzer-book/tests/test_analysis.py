from __future__ import annotations

import asyncio
from typing import Any

import pytest

from book_analyzer.analysis import LLMAnalyzer, RuleBasedAnalyzer, create_analyzer
from book_analyzer.config import Settings
from book_analyzer.parser import Chapter, Chunk


class FakeAIService:
    default_model = "analysis-model"

    def __init__(self, available: bool = True, content: str | None = None) -> None:
        self.available = available
        self.content = content

    def is_available(self, provider: str | None = None) -> bool:
        return self.available

    async def generate_text(self, **kwargs: Any) -> dict[str, str]:
        if self.content is None:
            raise RuntimeError("llm unavailable")
        return {"content": self.content, "finish_reason": "stop"}


def _chunk() -> Chunk:
    return Chunk(
        chapter_no=1,
        chapter_title="第一章",
        chunk_index=0,
        content="张三来到京城，发现皇城中暗藏秘密。",
    )


def test_create_analyzer_raises_when_rule_analysis_disabled_without_llm() -> None:
    settings = Settings(disable_rule_analysis=True)

    with pytest.raises(RuntimeError, match="已禁用规则分析"):
        create_analyzer(settings, FakeAIService(available=False))


def test_llm_analyzer_raises_when_rule_fallback_disabled() -> None:
    analyzer = LLMAnalyzer(
        ai_service=FakeAIService(content=None),
        fallback=RuleBasedAnalyzer(),
        allow_rule_fallback=False,
    )

    with pytest.raises(RuntimeError, match="llm unavailable"):
        asyncio.run(analyzer.analyze_chunk(_chunk()))


def test_llm_analyzer_keeps_rule_fallback_by_default() -> None:
    analyzer = LLMAnalyzer(
        ai_service=FakeAIService(content=None),
        fallback=RuleBasedAnalyzer(),
    )

    result = asyncio.run(analyzer.analyze_chunk(_chunk()))

    assert result.tag_metadata == {"source": "rule"}
    assert result.summary


def test_rule_book_summary_contains_style_profile() -> None:
    analyzer = RuleBasedAnalyzer()
    chunk = _chunk()
    analysis = analyzer.build_chunk_analysis(chunk)

    summary = asyncio.run(
        analyzer.build_book_summary(
            "测试书",
            [Chapter(number=1, title="第一章", content=chunk.content)],
            [analysis],
        )
    )

    assert summary["style_profile"]["sentence_rhythm"]
    assert "sample_guidance" in summary["style_profile"]
