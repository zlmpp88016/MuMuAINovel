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
