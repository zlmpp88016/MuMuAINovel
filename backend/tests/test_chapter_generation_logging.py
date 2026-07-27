from __future__ import annotations

import logging

from app.utils.chapter_generation_logging import (
    log_chapter_generation_step,
    log_final_chapter_prompt,
)


def test_log_chapter_generation_step_includes_number_and_details(caplog) -> None:
    test_logger = logging.getLogger("test.chapter_generation.step")

    with caplog.at_level(logging.INFO, logger=test_logger.name):
        log_chapter_generation_step(
            test_logger,
            chapter_id="chapter-1",
            step=6,
            message="执行 MCP 工具规划与资料收集",
            tool_calls=2,
            ignored=None,
        )

    assert "[章节生成][步骤 06/10]" in caplog.text
    assert "章节ID=chapter-1" in caplog.text
    assert "tool_calls=2" in caplog.text
    assert "ignored" not in caplog.text


def test_log_final_chapter_prompt_prints_complete_prompt(caplog) -> None:
    test_logger = logging.getLogger("test.chapter_generation.prompt")
    prompt = "第一行：章节设定\n第二行：MCP 参考资料\n第三行：开始创作"

    with caplog.at_level(logging.INFO, logger=test_logger.name):
        log_final_chapter_prompt(
            test_logger,
            mode="单章流式生成",
            chapter_id="chapter-2",
            chapter_number=2,
            chapter_title="雨夜",
            prompt=prompt,
        )

    assert "[章节生成][最终 Prompt]" in caplog.text
    assert "第2章《雨夜》" in caplog.text
    assert "========== 最终 Prompt 开始 ==========" in caplog.text
    assert prompt in caplog.text
    assert "========== 最终 Prompt 结束 ==========" in caplog.text
