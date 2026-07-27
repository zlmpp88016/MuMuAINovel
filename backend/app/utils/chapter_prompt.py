"""章节生成 Prompt 处理工具。"""


ONE_TIME_PROMPT_SEPARATOR = "=" * 20


def append_one_time_prompt(prompt: str, one_time_prompt: str | None) -> str:
    """将仅用于本次生成的自定义要求追加到最终 Prompt。"""
    normalized_prompt = (one_time_prompt or "").strip()
    if not normalized_prompt:
        return prompt

    return (
        f"{prompt}\n\n"
        f"{ONE_TIME_PROMPT_SEPARATOR}\n"
        "【本次生成的自定义要求（仅本章有效）】\n"
        f"{normalized_prompt}\n"
        f"{ONE_TIME_PROMPT_SEPARATOR}"
    )
