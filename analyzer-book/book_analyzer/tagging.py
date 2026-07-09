"""段落级标签抽取。

标签抽取被设计成独立链路：可以用便宜的小模型为 chunk 生成检索标签，
也可以在模型不可用或输出异常时回退到规则逻辑，避免影响主分析流程。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
import json
import re
from typing import Any, Protocol

from book_analyzer.config import Settings
from book_analyzer.llm_service import AIService
from book_analyzer.parser import Chunk


logger = logging.getLogger(__name__)


class RuleTagFallback(Protocol):
    """规则回退所需的最小接口。"""

    def extract_tags(self, text: str) -> list[str]:
        """从文本中提取规则标签。"""

    def extract_characters(self, text: str) -> list[str]:
        """从文本中提取规则角色名。"""


class ParagraphTagger(Protocol):
    """段落打标签器的统一接口。"""

    async def tag_chunk(self, chunk: Chunk) -> "TaggingResult":
        """为单个 chunk 返回标签和轻量元数据。"""


@dataclass(slots=True)
class TaggingResult:
    """标准化后的段落标签结果。

    Args:
        tags: 面向检索和过滤的短标签，如冲突、伏笔、日常。
        characters: 片段中出现的核心角色名。
        scene_type: 场景类型，如打斗、会议、感情、升级。
        mood: 情绪基调，如紧张、压抑、爽、暧昧。
        location: 核心地点。
        items: 关键物品或线索。
        source: 标签来源，通常为 ``llm`` 或 ``rule``。
        model: 产出标签的小模型名称，规则回退时为空。
    """

    tags: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    scene_type: str = ""
    mood: str = ""
    location: str = ""
    items: list[str] = field(default_factory=list)
    source: str = "rule"
    model: str | None = None

    def metadata(self) -> dict[str, Any]:
        """生成适合数据库和向量库保存的紧凑元数据。

        tags 和 characters 有独立字段，这里只放辅助过滤维度和来源信息。
        """
        metadata: dict[str, Any] = {"source": self.source}
        if self.model:
            metadata["model"] = self.model
        if self.scene_type:
            metadata["scene_type"] = self.scene_type
        if self.mood:
            metadata["mood"] = self.mood
        if self.location:
            metadata["location"] = self.location
        if self.items:
            metadata["items"] = self.items
        return metadata


class RuleBasedTagger:
    """确定性的规则回退打标签器。"""

    def __init__(self, fallback: RuleTagFallback) -> None:
        """保存规则提取器。

        Args:
            fallback: 提供规则标签和角色抽取能力的对象。
        """
        self.fallback = fallback

    async def tag_chunk(self, chunk: Chunk) -> TaggingResult:
        """使用规则逻辑为 chunk 打标签。

        Args:
            chunk: 待标注的文本片段。

        Returns:
            ``source`` 为 ``rule`` 的标签结果。
        """
        logger.info("[规则标签] chunk %d-%d", chunk.chapter_no, chunk.chunk_index)
        return TaggingResult(
            tags=self.fallback.extract_tags(chunk.content),
            characters=self.fallback.extract_characters(chunk.content),
            source="rule",
        )


class LLMTagger:
    """外部小模型打标签器，失败时自动回退规则逻辑。"""

    def __init__(self, ai_service: AIService, fallback: RuleTagFallback) -> None:
        """初始化小模型客户端和规则兜底。

        Args:
            ai_service: 专用于 tagging 的 AIService 实例。
            fallback: 模型不可用或输出非法时使用的规则提取器。
        """
        self.ai_service = ai_service
        self.fallback_tagger = RuleBasedTagger(fallback)

    async def tag_chunk(self, chunk: Chunk) -> TaggingResult:
        """调用小模型为 chunk 生成结构化标签。

        Args:
            chunk: 待标注的文本片段。

        Returns:
            模型成功时返回 ``source=llm``，否则返回规则回退结果。
        """
        if not self.ai_service.is_available():
            logger.info("[LLM标签] chunk %d-%d 模型不可用, 回退规则", chunk.chapter_no, chunk.chunk_index)
            return await self.fallback_tagger.tag_chunk(chunk)

        try:
            # 小模型只负责低成本分类字段，提示词要求 JSON，方便稳定入库和索引。
            logger.info("[LLM标签] chunk %d-%d 调用小模型 ...", chunk.chapter_no, chunk.chunk_index)
            response = await self.ai_service.generate_text(
                prompt=self._prompt(chunk),
                system_prompt=(
                    "你负责为中文网文段落打标签，仅返回紧凑的 JSON，"
                    "不要 markdown，不要解释说明。"
                ),
            )
            payload = _parse_json_response(response["content"])
            result = TaggingResult(
                tags=_as_str_list(payload.get("tags")),
                characters=_as_str_list(payload.get("characters")),
                scene_type=_as_str(payload.get("scene_type")),
                mood=_as_str(payload.get("mood")),
                location=_as_str(payload.get("location")),
                items=_as_str_list(payload.get("items") or payload.get("item")),
                source="llm",
                model=self.ai_service.default_model,
            )
            if result.tags or result.characters or result.scene_type:
                logger.info("[LLM标签] chunk %d-%d 成功: tags=%s",
                             chunk.chapter_no, chunk.chunk_index, result.tags)
                return result
        except Exception as e:
            logger.exception("[LLM标签] chunk %d-%d 失败, 回退规则",
                             chunk.chapter_no, chunk.chunk_index)
            # tagging 是增强链路：模型超时、格式错误或限流时都回退规则标签。
            # pass
            raise e
        return await self.fallback_tagger.tag_chunk(chunk)

    def _prompt(self, chunk: Chunk) -> str:
        """构造要求模型只返回 JSON 的标签提示词。"""
        return (
            "请对这段文本进行分类，用于后续检索。返回 JSON，且仅包含以下字段：\n"
            "- tags: 检索标签数组，如 [\"冲突\", \"伏笔\", \"修炼\", \"日常\"]\n"
            "- scene_type: 场景类型，如 \"打斗\"、\"会议\"、\"感情\"、\"升级\"\n"
            "- mood: 情绪基调，如 \"紧张\"、\"压抑\"、\"爽\"、\"暧昧\"\n"
            "- characters: 出现的核心角色名数组，如 [\"萧炎\", \"药老\"]\n"
            "- location: 核心地点，如 \"练功房\"、\"拍卖场\"、\"宗门大殿\"\n"
            "- items: 关键物品或线索数组，如 [\"筑基丹\", \"神秘玉佩\"]\n"
            "未知字段返回空数组或空字符串。\n\n"
            f"chapter_title: {chunk.chapter_title}\n"
            f"chunk_index: {chunk.chunk_index}\n"
            f"text:\n{chunk.content}"
        )


def create_tagger(
    settings: Settings,
    ai_service: AIService | None,
    fallback: RuleTagFallback,
) -> ParagraphTagger:
    """根据配置创建段落打标签器。

    Args:
        settings: 当前服务配置。
        ai_service: tagging 专用小模型客户端，可为空。
        fallback: 规则回退提取器。

    Returns:
        可直接用于分析流程的 ``ParagraphTagger``。

    Raises:
        RuntimeError: 配置强制使用 LLM，但没有可用模型。
    """
    backend = (settings.tagging_backend or "auto").lower()
    rule_tagger = RuleBasedTagger(fallback)

    if backend == "rule":
        return rule_tagger

    if ai_service is not None and ai_service.is_available():
        return LLMTagger(ai_service=ai_service, fallback=fallback)

    if backend == "llm":
        raise RuntimeError("tagging_backend=llm but no tagging model is available")

    # auto 模式下没有小模型也能正常运行，标签由规则系统提供。
    return rule_tagger


def _parse_json_response(response: str) -> dict[str, Any]:
    """从模型响应中解析 JSON 对象，兼容 markdown fenced JSON。

    小模型偶尔会包一层 ```json，这里做宽松解析，但仍要求最终是对象。
    """
    cleaned = response.strip()
    cleaned = re.sub(r"^```json\s*", "", cleaned)
    cleaned = re.sub(r"^```\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        json_match = re.search(r"\{[\s\S]*\}", response)
        if json_match:
            payload = json.loads(json_match.group())
            if isinstance(payload, dict):
                return payload
    raise ValueError("Tagging model did not return valid JSON")


def _as_str(value: Any) -> str:
    """将模型字段规范化为短字符串。"""
    if value is None:
        return ""
    return str(value).strip()[:80]


def _as_str_list(value: Any) -> list[str]:
    """将模型字段规范化为去重后的字符串列表。"""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _as_str(item)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result[:12]
