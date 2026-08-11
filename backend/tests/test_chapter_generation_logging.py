from __future__ import annotations

import logging

from app.utils.chapter_generation_logging import (
    log_chapter_corpus_trace,
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


def test_log_chapter_corpus_trace_logs_allowlisted_decisions_only(caplog) -> None:
    test_logger = logging.getLogger("test.chapter_generation.corpus")
    trace = {
        "catalog_status": "hit",
        "selector_status": "accepted",
        "selected_scene_type": "调查",
        "selected_mood": "紧张",
        "selected_tags": ["伏笔"],
        "mcp_methods": ["corpus_list_reference_tag_catalog", "corpus_search_reference_passages"],
        "fallback_stage": "drop_reference_tags",
        "attempts": [{"stage": "strict", "hit_count": 0}],
        "passage_count": 2,
        "template_injected": True,
        "reference_passage_content": "不应记录的范文正文",
        "chapter_outline": "不应记录的大纲",
        "raw_exception_message": "不应记录的异常正文",
    }

    with caplog.at_level(logging.INFO, logger=test_logger.name):
        log_chapter_corpus_trace(test_logger, chapter_id="chapter-3", trace=trace)

    assert "selected_tags=['伏笔']" in caplog.text
    assert "corpus_list_reference_tag_catalog" in caplog.text
    assert "fallback_stage=drop_reference_tags" in caplog.text
    assert "template_injected=True" in caplog.text
    assert "不应记录的范文正文" not in caplog.text
    assert "不应记录的大纲" not in caplog.text
    assert "不应记录的异常正文" not in caplog.text
