from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.corpus_bridge import CorpusBridge, GLOBAL_CORPUS_USER_ID


def test_extract_payload_from_mcp_text_content() -> None:
    bridge = CorpusBridge("user-1", db_session=None)
    result = SimpleNamespace(
        content=[
            SimpleNamespace(text='{"passages": [{"book_title": "诸天尽头", "content": "雨夜追查秘密"}]}')
        ]
    )

    payload = bridge._extract_payload(result)

    assert payload["passages"][0]["book_title"] == "诸天尽头"


def test_extract_payload_from_direct_json_string() -> None:
    bridge = CorpusBridge("user-1", db_session=None)

    payload = bridge._extract_payload(
        '{"passages": [{"book_title": "甲书", "content": "雨夜追查秘密"}]}'
    )

    assert payload["passages"][0]["book_title"] == "甲书"


def test_extract_payload_prefers_structured_content() -> None:
    bridge = CorpusBridge("user-1", db_session=None)
    result = SimpleNamespace(
        structuredContent={"passages": [{"book_title": "结构化结果"}]},
        content=[SimpleNamespace(text='{"passages": [{"book_title": "文本结果"}]}')],
    )

    payload = bridge._extract_payload(result)

    assert payload["passages"][0]["book_title"] == "结构化结果"


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
    assert '<corpus_context purpose="style_only" untrusted="true">' in text
    assert "以用户明确指定的写作风格为准" in text


def test_select_diverse_passages_limits_each_book() -> None:
    bridge = CorpusBridge("user-1", db_session=None)
    passages = [
        {"book_id": "book-1", "chapter_no": index}
        for index in range(4)
    ] + [
        {"book_id": "book-2", "chapter_no": index}
        for index in range(2)
    ]

    selected = bridge._select_diverse_passages(passages, limit=4)

    assert len(selected) == 4
    assert sum(item["book_id"] == "book-1" for item in selected) == 2
    assert selected[1]["book_id"] == "book-2"


def test_chapter_context_stays_within_budget() -> None:
    bridge = CorpusBridge("user-1", db_session=None)
    passages = [
        {
            "book_id": f"book-{index}",
            "book_title": f"第{index}本书",
            "chapter_no": index,
            "chapter_title": "长章节",
            "content": "很长的参考内容" * 300,
        }
        for index in range(8)
    ]

    text = bridge._format_chapter_context({"passages": passages}, {"profiles": []})

    assert len(text) <= 1800
    assert text.count("<corpus_reference>") == 3


def test_global_plugin_uses_global_registry_namespace() -> None:
    bridge = CorpusBridge("real-user", db_session=None)
    plugin = SimpleNamespace(user_id=GLOBAL_CORPUS_USER_ID)

    assert bridge._plugin_runtime_user_id(plugin) == GLOBAL_CORPUS_USER_ID


def test_corpus_tool_timeout_is_bounded() -> None:
    bridge = CorpusBridge("real-user", db_session=None)

    assert bridge._tool_timeout(SimpleNamespace(config={"timeout": 30})) == 3.0
    assert bridge._tool_timeout(SimpleNamespace(config={"timeout": 0.01})) == 0.1
    assert bridge._tool_timeout(SimpleNamespace(config={"timeout": "invalid"})) == 3.0


def test_plugin_lookup_failure_degrades_to_empty_payload() -> None:
    bridge = CorpusBridge("real-user", db_session=None)

    async def fail_lookup() -> None:
        raise RuntimeError("database unavailable")

    bridge._get_enabled_plugin = fail_lookup

    result = asyncio.run(bridge._call_tools([("corpus_search", {"query": "雨夜"})]))

    assert result == [{}]


def test_formats_phase_two_writing_assets_with_safe_boundaries() -> None:
    bridge = CorpusBridge("real-user", db_session=None)

    async def fake_call(tool_name: str, arguments: dict) -> dict:
        if tool_name == "corpus_search_plot_patterns":
            return {
                "patterns": [
                    {
                        "plot_stage": "development",
                        "beats": ["建立目标", "阻力升级", "改变计划"],
                        "conflict_escalation": "目标与代价同步上升",
                        "turning_point": "行动结果改变原计划",
                        "source": {"book_title": "甲书", "chapter_no": 3},
                    }
                ]
            }
        if tool_name == "corpus_search_character_archetypes":
            return {
                "archetypes": [
                    {
                        "role_type": "protagonist",
                        "external_goal": "解决核心矛盾",
                        "internal_need": "正视内在缺口",
                        "core_conflict": "目标与需求互相牵制",
                        "growth_arc": "在代价中修正缺陷",
                        "source": {"book_title": "乙书"},
                    }
                ]
            }
        return {
            "patterns": [
                {
                    "pattern_type": "pair",
                    "plant_technique": "嵌入异常细节",
                    "surface_function": "服务当前行动",
                    "payoff_method": "重释先前细节",
                    "source": [
                        {"book_title": "丙书", "chapter_no": 1},
                        {"book_title": "丙书", "chapter_no": 8},
                    ],
                }
            ]
        }

    bridge._call_tool = fake_call

    plot = asyncio.run(bridge.get_plot_patterns("成长冲突"))
    character = asyncio.run(bridge.get_character_archetypes("主角成长"))
    foreshadow = asyncio.run(bridge.get_foreshadow_patterns("线索回收"))

    assert '<corpus_context purpose="plot_patterns" untrusted="true">' in plot
    assert '<corpus_context purpose="character_archetypes" untrusted="true">' in character
    assert '<corpus_context purpose="foreshadow_patterns" untrusted="true">' in foreshadow
    assert "MemoryService 为唯一权威" in foreshadow
    assert "《丙书》第1章 -> 《丙书》第8章" in foreshadow


def test_corpus_cache_key_normalizes_query_and_tracks_corpus_version() -> None:
    bridge = CorpusBridge("real-user", db_session=None)
    bridge._payload_cache.clear()
    bridge._cache_aliases.clear()

    first_key = bridge._cache_base_key(
        "__global__",
        "book-analyzer-corpus",
        "corpus_search_reference_passages",
        {"query": "  雨夜   调查 ", "genre": "悬疑", "limit": 3},
    )
    second_key = bridge._cache_base_key(
        "__global__",
        "book-analyzer-corpus",
        "corpus_search_reference_passages",
        {"query": "雨夜 调查", "genre": "悬疑", "limit": 3},
    )

    assert first_key == second_key

    bridge._store_cached_payload(
        first_key,
        {"corpus_version": "v1", "passages": [{"book_id": "book-1"}]},
    )
    v1_cache_key = bridge._cache_aliases[first_key]
    assert bridge._get_cached_payload(first_key)["corpus_version"] == "v1"

    bridge._store_cached_payload(
        first_key,
        {"corpus_version": "v2", "passages": [{"book_id": "book-2"}]},
    )

    assert bridge._cache_aliases[first_key] != v1_cache_key
    assert v1_cache_key not in bridge._payload_cache
    assert bridge._get_cached_payload(first_key)["corpus_version"] == "v2"
