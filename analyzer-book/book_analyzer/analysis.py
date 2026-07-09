"""内容分析引擎。

本模块负责把解析后的 chunk 转成结构化分析结果，并在整本书层面汇总。
它同时支持纯规则分析和 LLM 分析；段落标签由 ``tagging.py`` 的独立
tagger 负责，避免主分析模型和小模型打标逻辑耦合。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
import json
import re
import uuid
from typing import Any, Protocol

from book_analyzer.llm_service import AIService
from book_analyzer.parser import Chapter, Chunk
from book_analyzer.tagging import ParagraphTagger, TaggingResult, create_tagger


logger = logging.getLogger(__name__)


GENRE_KEYWORDS = {
    "武侠": ["江湖", "门派", "剑", "掌门", "侠客"],
    "仙侠": ["修仙", "灵气", "宗门", "丹田", "飞升"],
    "宫廷": ["皇帝", "太子", "后宫", "宫殿", "朝堂"],
    "科幻": ["星舰", "宇宙", "机甲", "人工智能", "量子"],
    "校园": ["学校", "老师", "同学", "考试", "校园"],
}

TAG_RULES = {
    "冲突": ["冲突", "争执", "追杀", "决斗", "危机", "大战"],
    "伏笔": ["秘密", "疑团", "伏笔", "预言", "真相", "异象"],
    "关系": ["结盟", "背叛", "朋友", "兄弟", "师徒", "恋人"],
    "成长": ["修炼", "成长", "觉醒", "突破", "决定", "誓言"],
    "世界观": ["京城", "宗门", "王朝", "山谷", "学院", "边境"],
}

CHINESE_NAME_RE = re.compile(
    r"[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜][\u4e00-\u9fff]{1,2}"
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")


@dataclass(slots=True)
class ChunkAnalysis:
    """单个文本片段的结构化分析结果。

    Args:
        chapter_no: 片段所属章节序号。
        chapter_title: 片段所属章节标题。
        chunk_index: 片段在章节内的序号。
        content: 原始片段文本。
        summary: 片段摘要。
        tags: 检索与过滤用标签。
        importance: 重要性分数，范围通常为 0 到 1。
        characters: 片段中出现的核心角色。
        vector_id: 写入向量库时使用的稳定 ID。
        tag_metadata: 标签来源和扩展维度，如 scene_type、mood、location。
    """

    chapter_no: int
    chapter_title: str
    chunk_index: int
    content: str
    summary: str
    tags: list[str]
    importance: float
    characters: list[str]
    vector_id: str
    tag_metadata: dict[str, Any] = field(default_factory=dict)


class Analyzer(Protocol):
    """分析器统一接口。"""

    async def analyze_chunk(self, chunk: Chunk) -> ChunkAnalysis:
        """分析单个文本片段。

        Args:
            chunk: 由 parser 切出的章节片段。

        Returns:
            可持久化、可索引的片段分析结果。
        """

    async def build_book_summary(
        self,
        title: str,
        chapters: list[Chapter],
        analyses: list[ChunkAnalysis],
    ) -> dict[str, Any]:
        """汇总整本书分析。

        Args:
            title: 书名。
            chapters: 解析后的章节列表。
            analyses: 所有 chunk 的分析结果。

        Returns:
            JSON-serializable 的整书分析摘要。
        """


class RuleBasedAnalyzer(Analyzer):
    """确定性的规则分析器。

    规则分析器是测试默认路径，也是 LLM 不可用或调用失败时的兜底路径。
    可选 ``tagger`` 用于接入独立的小模型打标签链路。
    """

    def __init__(self, tagger: ParagraphTagger | None = None) -> None:
        """初始化规则分析器。

        Args:
            tagger: 可选段落打标签器；为空时使用内置规则打标。
        """
        self.tagger = tagger

    async def analyze_chunk(self, chunk: Chunk) -> ChunkAnalysis:
        """分析单个 chunk，并优先使用独立 tagger 的标签结果。

        小模型打标失败不会让主分析失败；这里会统一回退到规则标签。
        """
        tagging = await self._resolve_tagging(chunk)
        logger.info("[规则分析] chunk %d-%d: tags=%s, chars=%d",
                     chunk.chapter_no, chunk.chunk_index, tagging.tags, len(tagging.characters))
        return self.build_chunk_analysis(chunk, tagging=tagging)

    def build_chunk_analysis(
        self,
        chunk: Chunk,
        tagging: TaggingResult | None = None,
        summary: str | None = None,
        importance: float | None = None,
    ) -> ChunkAnalysis:
        """组装片段分析结果。

        Args:
            chunk: 待分析的文本片段。
            tagging: 已解析出的标签结果；为空时使用规则标签。
            summary: 可选摘要；为空时用规则摘要。
            importance: 可选重要性分数；为空时按规则估算。

        Returns:
            标准化后的 ``ChunkAnalysis``。
        """
        tagging = tagging or self._rule_tagging(chunk.content)
        tags = tagging.tags or self._extract_tags(chunk.content)
        characters = tagging.characters or self._extract_characters(chunk.content)
        resolved_importance = (
            importance
            if importance is not None
            else self._estimate_importance(chunk.content, tags, characters)
        )
        return ChunkAnalysis(
            chapter_no=chunk.chapter_no,
            chapter_title=chunk.chapter_title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            summary=summary or self._summarize_text(chunk.content),
            tags=tags,
            importance=resolved_importance,
            characters=characters,
            vector_id=str(uuid.uuid4()),
            tag_metadata=tagging.metadata(),
        )

    async def _resolve_tagging(self, chunk: Chunk) -> TaggingResult:
        """解析 chunk 标签，tagger 失败时回退到规则逻辑。"""
        if self.tagger is not None:
            try:
                return await self.tagger.tag_chunk(chunk)
            except Exception as e:
                # 打标签是增强能力，不应该阻断上传分析主流程。
                logger.exception("[标签解析] chunk %d-%d 失败, 回退规则标签",
                                 chunk.chapter_no, chunk.chunk_index)
                raise e
        return self._rule_tagging(chunk.content)

    def _rule_tagging(self, text: str) -> TaggingResult:
        """使用内置规则生成标签结果。"""
        return TaggingResult(
            tags=self._extract_tags(text),
            characters=self._extract_characters(text),
            source="rule",
        )

    async def build_book_summary(
        self,
        title: str,
        chapters: list[Chapter],
        analyses: list[ChunkAnalysis],
    ) -> dict[str, Any]:
        """根据所有 chunk 分析结果生成整书摘要。

        Args:
            title: 书名。
            chapters: 原始章节列表。
            analyses: 已完成的 chunk 分析结果。

        Returns:
            包含书籍画像、章节大纲、角色卡和情节线索的字典。
        """
        full_text = "\n".join(chunk.content for chunk in analyses)
        chapter_first_chunks: dict[int, ChunkAnalysis] = {}
        for analysis in analyses:
            chapter_first_chunks.setdefault(analysis.chapter_no, analysis)

        genre = self._infer_genre(full_text)
        all_tags = sorted({tag for analysis in analyses for tag in analysis.tags})
        top_characters = self._rank_characters(analyses)
        foreshadows = [analysis.summary for analysis in analyses if "伏笔" in analysis.tags][:5]
        major_conflicts = [analysis.summary for analysis in analyses if "冲突" in analysis.tags][:5]

        logger.info("[规则汇总] 书籍=%s, 类型=%s, 角色=%d, 章节=%d, chunks=%d",
                     title, genre, len(top_characters), len(chapters), len(analyses))

        return {
            "title": title,
            "book_profile": {
                "genre": genre,
                "background": self._summarize_text(full_text, max_chars=160),
                "geo_culture": self._extract_geo_culture(full_text),
                "style_tags": all_tags[:8],
            },
            "outline": [
                {
                    "chapter_no": chapter.number,
                    "chapter_title": chapter.title,
                    "summary": chapter_first_chunks.get(chapter.number).summary
                    if chapter.number in chapter_first_chunks
                    else self._summarize_text(chapter.content),
                }
                for chapter in chapters
            ],
            "character_cards": [
                {
                    "name": name,
                    "mentions": mentions,
                    "description": f"{name} 在文本中出现 {mentions} 次。",
                }
                for name, mentions in top_characters
            ],
            "plot_threads": {
                "foreshadows": foreshadows,
                "major_conflicts": major_conflicts,
                "highlights": [analysis.summary for analysis in analyses[:5]],
            },
            "stats": {
                "chapters": len(chapters),
                "chunks": len(analyses),
                "characters": len(top_characters),
            },
            "style_profile": self.build_rule_style_profile(full_text, analyses),
        }

    def build_rule_style_profile(
        self,
        full_text: str,
        analyses: list[ChunkAnalysis],
    ) -> dict[str, Any]:
        """根据文本统计生成可兜底的写作风格画像。"""
        sentence_count = max(1, len(SENTENCE_SPLIT_RE.findall(full_text)))
        paragraph_count = max(1, full_text.count("\n") + 1)
        dialogue_count = full_text.count("“") + full_text.count('"')
        action_tags = {"冲突", "成长", "伏笔"}
        action_hits = sum(1 for analysis in analyses if set(analysis.tags) & action_tags)
        avg_sentence_len = round(len(full_text) / sentence_count, 1) if full_text else 0
        dialogue_density = round(dialogue_count / paragraph_count, 2)
        description_density = round(1 - min(action_hits / max(1, len(analyses)), 1), 2)
        return {
            "sentence_rhythm": "短促利落" if avg_sentence_len < 35 else "舒展细密",
            "dialogue_density": "高" if dialogue_density > 0.7 else "中" if dialogue_density > 0.25 else "低",
            "description_density": "高" if description_density > 0.66 else "中" if description_density > 0.33 else "低",
            "diction": "通俗直白",
            "signature_techniques": sorted({tag for analysis in analyses for tag in analysis.tags})[:6],
            "sample_guidance": (
                "借鉴其句式节奏、场景密度和情绪推进方式，避免复用具体情节、人名或设定。"
            ),
        }

    def summarize_text(self, text: str, max_chars: int = 120) -> str:
        """公开的规则摘要入口，供 LLM 失败时复用。"""
        return self._summarize_text(text, max_chars=max_chars)

    def extract_tags(self, text: str) -> list[str]:
        """公开的规则标签入口，供 tagger 回退时复用。"""
        return self._extract_tags(text)

    def extract_characters(self, text: str) -> list[str]:
        """公开的角色名抽取入口，供索引重建和回退逻辑复用。"""
        return self._extract_characters(text)

    def _infer_genre(self, text: str) -> str:
        """根据关键词命中数推断一个粗粒度类型。"""
        best_genre = "通用"
        best_score = 0
        for genre, keywords in GENRE_KEYWORDS.items():
            score = sum(text.count(keyword) for keyword in keywords)
            if score > best_score:
                best_genre = genre
                best_score = score
        return best_genre

    def _extract_tags(self, text: str) -> list[str]:
        """按关键词规则抽取用于检索过滤的基础标签。"""
        tags: list[str] = []
        for tag, keywords in TAG_RULES.items():
            if any(keyword in text for keyword in keywords):
                tags.append(tag)
        if not tags:
            tags.append("叙事")
        return tags

    def _extract_characters(self, text: str) -> list[str]:
        """用常见中文姓氏模式粗略抽取角色名。"""
        names = CHINESE_NAME_RE.findall(text)
        counts: dict[str, int] = {}
        for name in names:
            counts[name] = counts.get(name, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [name for name, _ in ranked[:5]]

    def _rank_characters(self, analyses: list[ChunkAnalysis]) -> list[tuple[str, int]]:
        """按 chunk 出现次数排序核心角色。"""
        counts: dict[str, int] = {}
        for analysis in analyses:
            for name in analysis.characters:
                counts[name] = counts.get(name, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:8]

    def _estimate_importance(
        self,
        text: str,
        tags: list[str],
        characters: list[str],
    ) -> float:
        """按文本长度、标签密度和角色数量估算片段重要性。"""
        score = 0.35
        score += min(len(text) / 4000, 0.2)
        score += min(len(tags) * 0.08, 0.24)
        score += min(len(characters) * 0.05, 0.2)
        return round(min(score, 1.0), 2)

    def _extract_geo_culture(self, text: str) -> list[str]:
        """抽取少量地点/文化背景关键词，作为书籍画像补充。"""
        geo_keywords = ["京城", "江南", "边境", "宗门", "学院", "王朝", "山谷", "城池"]
        return [keyword for keyword in geo_keywords if keyword in text][:6]

    def _summarize_text(self, text: str, max_chars: int = 120) -> str:
        """取前两句生成确定性短摘要。"""
        compact = re.sub(r"\s+", " ", text).strip()
        if not compact:
            return ""
        sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(compact) if part.strip()]
        summary = "".join(sentences[:2]) or compact
        return summary[:max_chars]


class LLMAnalyzer(Analyzer):
    """LLM 分析器。

    主 LLM 只负责摘要、重要性和整书汇总；段落标签仍由 fallback 中挂载的
    dedicated tagger 负责。默认情况下模型调用异常会回退到规则分析。
    """

    def __init__(
        self,
        ai_service: AIService,
        fallback: RuleBasedAnalyzer,
        allow_rule_fallback: bool = True,
    ) -> None:
        """初始化 LLM 分析器。

        Args:
            ai_service: 主分析模型客户端。
            fallback: 规则兜底分析器，内部也承载 dedicated tagger。
            allow_rule_fallback: LLM 调用失败时是否允许回退规则分析。
        """
        self.ai_service = ai_service
        self.fallback = fallback
        self.allow_rule_fallback = allow_rule_fallback

    async def analyze_chunk(self, chunk: Chunk) -> ChunkAnalysis:
        """使用主 LLM 分析 chunk，并合并独立 tagger 的标签结果。

        主模型只补摘要和重要性；标签由 dedicated tagger 负责，避免两套模型
        对 tags 产生冲突。任一调用失败都会回退到规则分析。
        """
        tagging = await self.fallback._resolve_tagging(chunk)
        prompt = self._chunk_prompt(chunk)
        try:
            logger.info("[LLM分析] chunk %d-%d 调用主模型 ...", chunk.chapter_no, chunk.chunk_index)
            response = await self.ai_service.generate_text(
                prompt=prompt,
                system_prompt="你是一个严谨的中文小说分析助手，只返回 JSON。",
            )
            payload = self._parse_json_response(response["content"])
            if not tagging.characters:
                tagging.characters = self._as_str_list(payload.get("characters"))
            logger.info("[LLM分析] chunk %d-%d 成功, importance=%s",
                         chunk.chapter_no, chunk.chunk_index, payload.get("importance"))
            return self.fallback.build_chunk_analysis(
                chunk,
                tagging=tagging,
                summary=str(payload.get("summary", ""))
                or self.fallback.summarize_text(chunk.content),
                importance=self._as_float(payload.get("importance")),
            )
        except Exception:
            if not self.allow_rule_fallback:
                logger.exception("[LLM分析] chunk %d-%d 失败, 已禁用规则分析",
                                 chunk.chapter_no, chunk.chunk_index)
                raise
            logger.exception("[LLM分析] chunk %d-%d 失败, 回退规则分析",
                             chunk.chapter_no, chunk.chunk_index)
            # 上传链路优先保证可用性：LLM 临时不可用时仍产出规则分析。
            return self.fallback.build_chunk_analysis(chunk, tagging=tagging)

    async def build_book_summary(
        self,
        title: str,
        chapters: list[Chapter],
        analyses: list[ChunkAnalysis],
    ) -> dict[str, Any]:
        """使用主 LLM 汇总整书，失败时回退规则汇总。"""
        chunk_summaries = [
            {
                "chapter_no": analysis.chapter_no,
                "chapter_title": analysis.chapter_title,
                "summary": analysis.summary,
                "tags": analysis.tags,
                "characters": analysis.characters,
                "tag_metadata": analysis.tag_metadata,
            }
            for analysis in analyses
        ]
        prompt = self._book_prompt(title, chunk_summaries)
        try:
            logger.info("[LLM汇总] 调用主模型生成整书摘要: 书名=%s", title)
            response = await self.ai_service.generate_text(
                prompt=prompt,
                system_prompt="你是一个严谨的中文小说分析助手，只返回 JSON。",
                max_tokens=2500,
            )
            payload = self._parse_json_response(response["content"])
            if isinstance(payload, dict) and payload:
                payload["style_profile"] = await self._build_style_profile(title, analyses)
                logger.info("[LLM汇总] 整书摘要成功: 书名=%s", title)
                return payload
        except Exception:
            if not self.allow_rule_fallback:
                logger.exception("[LLM汇总] 主模型失败, 已禁用规则分析")
                raise
            logger.exception("[LLM汇总] 主模型失败, 回退规则汇总")
            # 整书汇总失败时不影响 chunk 结果，直接使用规则汇总。
            pass
        return await self.fallback.build_book_summary(title, chapters, analyses)

    def _chunk_prompt(self, chunk: Chunk) -> str:
        """构造 chunk 摘要提示词。

        注意：这里不要求主模型返回 tags，标签由 dedicated tagger 负责。
        """
        return (
            "请分析以下小说片段，并返回 JSON，字段固定为 "
            "`summary`、`importance`、`characters`。\n\n"
            f"章节标题：{chunk.chapter_title}\n"
            f"片段内容：\n{chunk.content}\n"
        )

    def _book_prompt(self, title: str, chunk_summaries: list[dict[str, Any]]) -> str:
        """构造整书汇总提示词。"""
        return (
            "请基于以下分段摘要生成整本书的分析结果，返回 JSON，结构包含："
            "`title`、`book_profile`、`outline`、`character_cards`、`plot_threads`、`stats`。"
            "`book_profile.style_tags` 应概括作品风格标签。\n\n"
            f"书名：{title}\n"
            f"片段摘要：{json.dumps(chunk_summaries, ensure_ascii=False)}"
        )

    async def _build_style_profile(
        self,
        title: str,
        analyses: list[ChunkAnalysis],
    ) -> dict[str, Any]:
        """用主 LLM 生成书级风格画像，失败时回退规则画像。"""
        full_text = "\n".join(analysis.content for analysis in analyses)
        fallback_profile = self.fallback.build_rule_style_profile(full_text, analyses)
        sample_chunks = [
            {
                "chapter_no": analysis.chapter_no,
                "summary": analysis.summary,
                "tags": analysis.tags,
                "text": analysis.content[:800],
            }
            for analysis in analyses[:8]
        ]
        try:
            logger.info("[LLM风格画像] 调用主模型: 书名=%s", title)
            response = await self.ai_service.generate_text(
                prompt=self._style_profile_prompt(title, sample_chunks),
                system_prompt="你是中文小说文风分析师，只返回 JSON。",
                max_tokens=900,
            )
            payload = self._parse_json_response(response["content"])
            if isinstance(payload, dict) and payload:
                return {**fallback_profile, **payload}
        except Exception:
            logger.exception("[LLM风格画像] 生成失败, 回退规则画像: 书名=%s", title)
        return fallback_profile

    def _style_profile_prompt(self, title: str, sample_chunks: list[dict[str, Any]]) -> str:
        """构造书级风格画像提示词。"""
        return (
            "请基于样本文本生成写作风格画像，返回 JSON，字段固定为："
            "`sentence_rhythm`、`dialogue_density`、`description_density`、"
            "`diction`、`signature_techniques`、`sample_guidance`。"
            "不要复述剧情，不要输出 markdown。\n\n"
            f"书名：{title}\n"
            f"样本：{json.dumps(sample_chunks, ensure_ascii=False)}"
        )

    def _parse_json_response(self, response: str) -> dict[str, Any]:
        """解析模型返回的 JSON，兼容 markdown fenced JSON。"""
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
        raise ValueError("LLM 返回内容不是有效 JSON")

    def _as_str_list(self, value: Any) -> list[str]:
        """将模型字段转换为字符串列表。"""
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    def _as_float(self, value: Any) -> float | None:
        """将模型字段转换为 float，失败时返回 ``None``。"""
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


def create_analyzer(
    settings,
    ai_service: AIService,
    tagging_ai_service: AIService | None = None,
) -> Analyzer:
    """根据配置和模型可用性创建分析器。

    Args:
        settings: 当前服务配置。
        ai_service: 主分析模型客户端。
        tagging_ai_service: 可选 tagging 专用小模型客户端。

    Returns:
        ``LLMAnalyzer`` 或 ``RuleBasedAnalyzer``。

    Raises:
        RuntimeError: 强制使用 LLM 分析但主模型不可用。
    """
    tag_rule_fallback = RuleBasedAnalyzer()
    tagger = create_tagger(settings, tagging_ai_service, tag_rule_fallback)
    fallback = RuleBasedAnalyzer(tagger=tagger)
    backend = (settings.analysis_backend or "auto").lower()
    disable_rule_analysis = bool(getattr(settings, "disable_rule_analysis", False))
    if backend == "rule":
        if disable_rule_analysis:
            raise RuntimeError("analysis_backend=rule 但已禁用规则分析")
        return fallback
    if backend == "llm":
        if not ai_service.is_available():
            raise RuntimeError("analysis_backend=llm 但未配置可用的 LLM")
        return LLMAnalyzer(
            ai_service=ai_service,
            fallback=fallback,
            allow_rule_fallback=not disable_rule_analysis,
        )
    # auto 模式保持开发友好：有主模型就增强分析，没有就稳定走规则。
    if ai_service.is_available():
        return LLMAnalyzer(
            ai_service=ai_service,
            fallback=fallback,
            allow_rule_fallback=not disable_rule_analysis,
        )
    if disable_rule_analysis:
        raise RuntimeError("已禁用规则分析且未配置可用的 LLM")
    return fallback
