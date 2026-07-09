from __future__ import annotations

import asyncio
from pathlib import Path
import sys

# 允许直接 python tests/test_tagging.py 运行
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

import pytest

from book_analyzer.analysis import RuleBasedAnalyzer
from book_analyzer.config import Settings
from book_analyzer.llm_service import AIService
from book_analyzer.parser import Chunk
from book_analyzer.tagging import LLMTagger, RuleBasedTagger, TaggingResult, create_tagger


class FakeFallback:
    def extract_tags(self, text: str) -> list[str]:
        return ["rule-tag"]

    def extract_characters(self, text: str) -> list[str]:
        return ["RuleName"]


class FakeAIService:
    default_model = "small-tag-model"

    def __init__(self, content: str | None = None, available: bool = True) -> None:
        self.content = content
        self.available = available

    def is_available(self, provider: str | None = None) -> bool:
        return self.available

    async def generate_text(self, **kwargs):
        if self.content is None:
            raise RuntimeError("model unavailable")
        return {"content": self.content, "finish_reason": "stop"}


class FixedTagger:
    async def tag_chunk(self, chunk: Chunk) -> TaggingResult:
        return TaggingResult(
            tags=["ai-tag"],
            characters=["AIName"],
            scene_type="battle",
            mood="tense",
            source="llm",
            model="small-tag-model",
        )


def _chunk() -> Chunk:
    return Chunk(
        chapter_no=1,
        chapter_title="chapter",
        chunk_index=0,
        content="Zhang San entered the city and found a secret.",
    )


def test_llm_tagger_returns_model_tags() -> None:
    ai_service = FakeAIService(
        '{"tags":["conflict","secret"],"scene_type":"fight","mood":"tense",'
        '"characters":["Zhang San"],"location":"capital","items":["jade"]}'
    )
    tagger = LLMTagger(ai_service=ai_service, fallback=FakeFallback())

    result = asyncio.run(tagger.tag_chunk(_chunk()))

    assert result.source == "llm"
    assert result.model == "small-tag-model"
    assert result.tags == ["conflict", "secret"]
    assert result.characters == ["Zhang San"]
    assert result.metadata()["scene_type"] == "fight"
    assert result.metadata()["location"] == "capital"


def test_llm_tagger_falls_back_when_model_unavailable() -> None:
    tagger = LLMTagger(ai_service=FakeAIService(available=False), fallback=FakeFallback())

    result = asyncio.run(tagger.tag_chunk(_chunk()))

    assert result.source == "rule"
    assert result.tags == ["rule-tag"]
    assert result.characters == ["RuleName"]


def test_llm_tagger_raises_on_generate_failure() -> None:
    tagger = LLMTagger(ai_service=FakeAIService(content=None), fallback=FakeFallback())

    with pytest.raises(RuntimeError, match="model unavailable"):
        asyncio.run(tagger.tag_chunk(_chunk()))


def test_llm_tagger_handles_markdown_fenced_json() -> None:
    ai_service = FakeAIService(
        '```json\n{"tags":["fight"],"scene_type":"battle","mood":"",'
        '"characters":[],"location":"","items":[]}\n```'
    )
    tagger = LLMTagger(ai_service=ai_service, fallback=FakeFallback())

    result = asyncio.run(tagger.tag_chunk(_chunk()))

    assert result.source == "llm"
    assert result.tags == ["fight"]
    assert result.scene_type == "battle"


def test_llm_tagger_handles_item_key_as_items_fallback() -> None:
    ai_service = FakeAIService(
        '{"tags":["clue"],"scene_type":"mystery","mood":"","characters":[],'
        '"location":"","item":["ancient book"]}'
    )
    tagger = LLMTagger(ai_service=ai_service, fallback=FakeFallback())

    result = asyncio.run(tagger.tag_chunk(_chunk()))

    assert result.source == "llm"
    assert result.items == ["ancient book"]


def test_llm_tagger_deduplicates_tags_and_characters() -> None:
    ai_service = FakeAIService(
        '{"tags":["fight","fight","secret","secret"],"scene_type":"battle",'
        '"mood":"","characters":["A","A","B"],"location":"","items":[]}'
    )
    tagger = LLMTagger(ai_service=ai_service, fallback=FakeFallback())

    result = asyncio.run(tagger.tag_chunk(_chunk()))

    assert result.tags == ["fight", "secret"]
    assert result.characters == ["A", "B"]


def test_llm_tagger_falls_back_when_result_has_no_meaningful_fields() -> None:
    ai_service = FakeAIService(
        '{"tags":[],"scene_type":"","mood":"","characters":[],"location":"","items":[]}'
    )
    tagger = LLMTagger(ai_service=ai_service, fallback=FakeFallback())

    result = asyncio.run(tagger.tag_chunk(_chunk()))

    assert result.source == "rule"
    assert result.tags == ["rule-tag"]


def test_tagging_result_metadata_when_all_empty() -> None:
    result = TaggingResult()

    meta = result.metadata()

    assert meta == {"source": "rule"}


def test_tagging_result_metadata_when_all_populated() -> None:
    result = TaggingResult(
        tags=["a"],
        characters=["b"],
        scene_type="fight",
        mood="tense",
        location="capital",
        items=["jade"],
        source="llm",
        model="small-tag-model",
    )

    meta = result.metadata()

    assert meta["source"] == "llm"
    assert meta["model"] == "small-tag-model"
    assert meta["scene_type"] == "fight"
    assert meta["mood"] == "tense"
    assert meta["location"] == "capital"
    assert meta["items"] == ["jade"]


def test_rule_based_tagger_uses_fallback() -> None:
    from book_analyzer.tagging import RuleBasedTagger

    tagger = RuleBasedTagger(FakeFallback())

    result = asyncio.run(tagger.tag_chunk(_chunk()))

    assert result.source == "rule"
    assert result.tags == ["rule-tag"]
    assert result.characters == ["RuleName"]
    assert result.model is None


# ============================================================
# 集成测试：需要 .env 中配置了可用的 TAGGING 模型
# 跳过条件：pytest -m "not integration"
# ============================================================

def _chinese_chunk() -> Chunk:
    return Chunk(
        chapter_no=3,
        chapter_title="第三章 夜探皇宫",
        chunk_index=2,
        content=(
            "张三屏住呼吸，贴着宫墙的阴影缓缓移动。远处传来禁军巡逻的脚步声，"
            "他握紧腰间的短刀，心跳如鼓。就在他准备翻墙而入的瞬间，"
            "一道黑影从屋顶掠过——李四早已埋伏在此。"
            "两人对视一眼，默契地分头潜入内殿。"
        ),
    )


@pytest.mark.integration
def test_llm_tagger_with_real_env() -> None:
    """用 .env 真实调用 LLM 打标签，校验返回结构的合法性。"""
    settings = Settings.from_env()
    fallback = RuleBasedAnalyzer()

    ai_service = AIService(
        settings,
        api_provider=settings.tagging_ai_provider,
        api_key=settings.tagging_api_key,
        api_base_url=settings.tagging_base_url,
        default_model=settings.tagging_model,
        default_temperature=settings.tagging_temperature,
        default_max_tokens=settings.tagging_max_tokens,
    )

    if not ai_service.is_available():
        pytest.skip("TAGGING 模型不可用, 跳过集成测试")

    tagger = LLMTagger(ai_service=ai_service, fallback=fallback)
    result = asyncio.run(tagger.tag_chunk(_chinese_chunk()))

    # 真实 LLM 应该返回 source=llm
    assert result.source == "llm"
    assert result.model == settings.tagging_model

    # tags 应该是非空的中文标签列表
    assert isinstance(result.tags, list), "tags 必须是 list"
    if result.tags:
        assert all(isinstance(t, str) and len(t) > 0 for t in result.tags), (
            f"tags 每项必须是非空 str，实际: {result.tags}"
        )

    # scene_type 应该是短字符串（可能为空）
    assert isinstance(result.scene_type, str), "scene_type 必须是 str"

    # mood 应该是短字符串（可能为空）
    assert isinstance(result.mood, str), "mood 必须是 str"

    # characters 应该是字符串列表
    assert isinstance(result.characters, list), "characters 必须是 list"

    # location 应该是短字符串（可能为空）
    assert isinstance(result.location, str), "location 必须是 str"

    # items 应该是字符串列表
    assert isinstance(result.items, list), "items 必须是 list"

    # metadata 必须包含 source 和 model
    meta = result.metadata()
    assert meta["source"] == "llm"
    assert meta["model"] == settings.tagging_model


if __name__ == "__main__":
    test_llm_tagger_with_real_env()

