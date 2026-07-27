from __future__ import annotations

from app.services.prompt_service import prompt_service


def test_user_style_is_appended_after_corpus_context() -> None:
    corpus_context = '<corpus_context purpose="style_only">语料风格</corpus_context>'
    user_style = "【用户指定风格】句子必须简短。"

    prompt = prompt_service.get_chapter_generation_prompt(
        title="测试书",
        theme="成长",
        genre="悬疑",
        narrative_perspective="第三人称",
        time_period="现代",
        location="城市",
        atmosphere="紧张",
        rules="现实规则",
        characters_info="张三",
        outlines_context="全书大纲",
        chapter_number=1,
        chapter_title="雨夜",
        chapter_outline="追查秘密",
        style_content=user_style,
        corpus_context=corpus_context,
    )

    assert prompt.index(corpus_context) < prompt.index(user_style)


def test_outline_prompt_accepts_normalized_corpus_context() -> None:
    corpus_context = '<corpus_context purpose="plot_patterns">节拍参考</corpus_context>'

    prompt = prompt_service.get_complete_outline_prompt(
        title="测试书",
        theme="成长",
        genre="悬疑",
        chapter_count=10,
        narrative_perspective="第三人称",
        target_words=100000,
        time_period="现代",
        location="城市",
        atmosphere="紧张",
        rules="现实规则",
        characters_info="暂无",
        corpus_context=corpus_context,
    )

    assert corpus_context in prompt


def test_character_prompt_accepts_anonymized_archetype_context() -> None:
    corpus_context = (
        '<corpus_context purpose="character_archetypes">匿名原型</corpus_context>'
    )

    prompt = prompt_service.get_single_character_prompt(
        project_context="悬疑项目",
        user_input="生成主角",
        corpus_context=corpus_context,
    )

    assert prompt.endswith(corpus_context)
