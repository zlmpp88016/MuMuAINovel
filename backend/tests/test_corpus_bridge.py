from __future__ import annotations

from types import SimpleNamespace

from app.services.corpus_bridge import CorpusBridge, GLOBAL_CORPUS_USER_ID


def test_extract_payload_from_mcp_text_content() -> None:
    bridge = CorpusBridge("user-1", db_session=None)
    result = SimpleNamespace(
        content=[
            SimpleNamespace(text='{"passages": [{"book_title": "甲书", "content": "雨夜追查秘密"}]}')
        ]
    )

    payload = bridge._extract_payload(result)

    assert payload["passages"][0]["book_title"] == "甲书"


def test_format_chapter_context_includes_guardrail() -> None:
    bridge = CorpusBridge("user-1", db_session=None)

    text = bridge._format_chapter_context(
        {
            "passages": [
                {
                    "book_title": "甲书",
                    "chapter_no": 1,
                    "chapter_title": "雨夜",
                    "content": "张三在雨夜里追查秘密。",
                }
            ]
        },
        {
            "profiles": [
                {
                    "title": "甲书",
                    "profile": {
                        "sentence_rhythm": "短促利落",
                        "dialogue_density": "中",
                        "description_density": "高",
                        "signature_techniques": ["伏笔", "冲突"],
                    },
                }
            ]
        },
    )

    assert "勿抄情节/人名/设定" in text
    assert "短促利落" in text


def test_global_plugin_uses_global_registry_namespace() -> None:
    bridge = CorpusBridge("real-user", db_session=None)
    plugin = SimpleNamespace(user_id=GLOBAL_CORPUS_USER_ID)

    assert bridge._plugin_runtime_user_id(plugin) == GLOBAL_CORPUS_USER_ID
