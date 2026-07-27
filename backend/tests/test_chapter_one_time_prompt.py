from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.chapter import ChapterGenerateRequest
from app.utils.chapter_prompt import append_one_time_prompt


def test_append_one_time_prompt_adds_delimited_section_to_final_prompt() -> None:
    base_prompt = "基础章节 Prompt"

    final_prompt = append_one_time_prompt(
        base_prompt,
        "  加强雨夜的悬疑氛围。\n结尾保留一个线索。  ",
    )

    assert final_prompt.startswith(base_prompt)
    assert "【本次生成的自定义要求（仅本章有效）】" in final_prompt
    assert "加强雨夜的悬疑氛围。\n结尾保留一个线索。" in final_prompt
    assert final_prompt.count("====================") == 2


@pytest.mark.parametrize("one_time_prompt", [None, "", "  \n\t "])
def test_append_one_time_prompt_keeps_original_prompt_for_blank_input(
    one_time_prompt: str | None,
) -> None:
    base_prompt = "基础章节 Prompt\n"

    assert append_one_time_prompt(base_prompt, one_time_prompt) == base_prompt


def test_chapter_generate_request_accepts_one_time_prompt() -> None:
    request = ChapterGenerateRequest(one_time_prompt="只在本章使用的要求")

    assert request.one_time_prompt == "只在本章使用的要求"


def test_chapter_generate_request_rejects_oversized_one_time_prompt() -> None:
    with pytest.raises(ValidationError):
        ChapterGenerateRequest(one_time_prompt="要求" * 5001)
