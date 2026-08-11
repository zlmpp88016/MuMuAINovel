"""章节生成链路的统一日志格式。"""

from __future__ import annotations

import logging
from typing import Any


CHAPTER_GENERATION_STEP_TOTAL = 10


def log_chapter_generation_step(
    logger: logging.Logger,
    *,
    chapter_id: str,
    step: int,
    message: str,
    **details: Any,
) -> None:
    """按统一编号记录章节生成步骤。"""
    detail_text = ", ".join(
        f"{key}={value}" for key, value in details.items() if value is not None
    )
    suffix = f" | {detail_text}" if detail_text else ""
    logger.info(
        "🧭 [章节生成][步骤 %02d/%02d] 章节ID=%s | %s%s",
        step,
        CHAPTER_GENERATION_STEP_TOTAL,
        chapter_id,
        message,
        suffix,
    )


def log_chapter_corpus_trace(
    logger: logging.Logger,
    *,
    chapter_id: str,
    trace: dict[str, Any],
) -> None:
    """记录章节语料决策摘要，只序列化 allowlist 字段，绝不记录上下文正文。"""
    attempts = trace.get("attempts") if isinstance(trace.get("attempts"), list) else []
    log_chapter_generation_step(
        logger,
        chapter_id=chapter_id,
        step=7,
        message="语料库 MCP 编排完成",
        catalog_status=trace.get("catalog_status"),
        corpus_version=trace.get("corpus_version"),
        catalog_counts=trace.get("catalog_counts"),
        selector_status=trace.get("selector_status"),
        selected_scene_type=trace.get("selected_scene_type"),
        selected_mood=trace.get("selected_mood"),
        selected_tags=trace.get("selected_tags"),
        mcp_methods=trace.get("mcp_methods"),
        fallback_stage=trace.get("fallback_stage"),
        fallback_stages=[item.get("stage") for item in attempts if isinstance(item, dict)],
        attempt_hit_counts=[item.get("hit_count") for item in attempts if isinstance(item, dict)],
        passage_count=trace.get("passage_count"),
        style_profile_count=trace.get("style_profile_count"),
        template_injected=trace.get("template_injected"),
        context_chars=trace.get("context_chars"),
        elapsed_ms=trace.get("elapsed_ms"),
    )


def log_final_chapter_prompt(
    logger: logging.Logger,
    *,
    mode: str,
    chapter_id: str,
    chapter_number: int,
    chapter_title: str,
    prompt: str,
) -> None:
    """完整记录一次章节生成实际发送给 AI 的最终 Prompt。"""
    logger.info(
        "📝 [章节生成][最终 Prompt] 模式=%s | 章节ID=%s | 第%s章《%s》 | 字符数=%d\n"
        "========== 最终 Prompt 开始 ==========\n%s\n"
        "========== 最终 Prompt 结束 ==========",
        mode,
        chapter_id,
        chapter_number,
        chapter_title,
        len(prompt),
        prompt,
    )
