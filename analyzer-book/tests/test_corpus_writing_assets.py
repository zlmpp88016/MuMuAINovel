from __future__ import annotations

import asyncio
import json

from book_analyzer.analysis import RuleBasedAnalyzer
from book_analyzer.db import create_session_factory, create_sqlalchemy_engine
from book_analyzer.models import Base, Book, BookChunk
from book_analyzer.service import BookAnalysisService
from book_analyzer.vector_store import SimpleVectorStore


def _make_service(settings):
    settings.ensure_directories()
    engine = create_sqlalchemy_engine(settings.database_url)
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    service = BookAnalysisService(
        settings=settings,
        session_factory=session_factory,
        vector_store=SimpleVectorStore(),
        analyzer=RuleBasedAnalyzer(),
    )

    with session_factory() as session:
        first = Book(
            id="book-1",
            title="甲书",
            original_filename="first.txt",
            file_hash="hash-1",
            status="completed",
            stage="completed",
            progress=100,
            summary_json={
                "book_profile": {"genre": "悬疑"},
                "outline": [
                    {"chapter_no": index, "chapter_title": f"第{index}章"}
                    for index in range(1, 7)
                ],
                "character_cards": [
                    {
                        "name": "张三",
                        "role": "主角",
                        "external_goal": "张三要查清旧案真相",
                        "internal_need": "张三需要放下复仇执念",
                        "conflict": "张三不愿信任李四",
                        "traits": ["固执", "敏锐"],
                    },
                    {"name": "李四", "role": "配角", "description": "李四协助张三"},
                ],
            },
        )
        first.chunks = [
            BookChunk(
                chapter_no=1,
                chapter_title="雨夜",
                chunk_index=0,
                content="张三在雨夜发现旧怀表的指针异常，这是一条隐约线索。",
                summary="雨夜埋下旧怀表异常的伏笔。",
                tags=["伏笔"],
                tag_metadata={"scene_type": "调查", "mood": "紧张"},
                characters=["张三"],
                importance=0.4,
                vector_id="vec-1-1",
            ),
            BookChunk(
                chapter_no=5,
                chapter_title="揭晓",
                chunk_index=0,
                content="真相揭晓，原来旧怀表记录了关键时间。",
                summary="旧怀表线索得到回收，谜底揭开。",
                tags=["冲突"],
                tag_metadata={"scene_type": "对峙", "mood": "震惊"},
                characters=["张三"],
                importance=0.9,
                vector_id="vec-1-5",
            ),
        ]

        second = Book(
            id="book-2",
            title="乙书",
            original_filename="second.txt",
            file_hash="hash-2",
            status="completed",
            stage="completed",
            progress=100,
            summary_json={
                "book_profile": {"genre": "悬疑"},
                "outline": [
                    {"chapter_no": index, "chapter_title": f"章节{index}"}
                    for index in range(1, 5)
                ],
                "character_cards": [
                    {"name": "王五", "role": "反派", "motivation": "维护秘密秩序"}
                ],
            },
        )
        second.chunks = [
            BookChunk(
                chapter_no=2,
                chapter_title="追踪",
                chunk_index=0,
                content="调查者在雨夜追踪线索，阻力逐步升级。",
                summary="雨夜调查遭遇阻力。",
                tags=["冲突"],
                tag_metadata={"scene_type": "追逐", "mood": "紧张"},
                characters=["王五"],
                importance=0.7,
                vector_id="vec-2-2",
            )
        ]
        session.add_all([first, second])
        session.commit()

    return service


def test_plot_patterns_are_normalized_and_source_backed(settings) -> None:
    service = _make_service(settings)

    patterns = asyncio.run(
        service.search_plot_patterns(query="雨夜调查", genre="悬疑", limit=3)
    )

    assert patterns
    assert all(len(pattern["beats"]) == 3 for pattern in patterns)
    assert all(pattern["source"]["book_id"] for pattern in patterns)
    serialized = json.dumps(patterns, ensure_ascii=False)
    assert "张三在雨夜发现旧怀表" not in serialized


def test_character_archetypes_remove_source_names(settings) -> None:
    service = _make_service(settings)

    archetypes = service.search_character_archetypes(
        query="复仇与信任",
        genre="悬疑",
        role_type="protagonist",
        limit=3,
    )

    assert len(archetypes) == 1
    assert archetypes[0]["role_type"] == "protagonist"
    assert archetypes[0]["source"]["book_title"] == "甲书"
    serialized = json.dumps(archetypes, ensure_ascii=False)
    assert "张三" not in serialized
    assert "李四" not in serialized


def test_reference_tag_catalog_uses_completed_metadata_and_stable_counts(settings) -> None:
    service = _make_service(settings)

    catalog = service.get_reference_tag_catalog(genre="悬疑")

    assert catalog["book_count"] == 2
    assert catalog["chunk_count"] == 3
    assert catalog["scene_types"] == [
        {"value": "对峙", "chunk_count": 1},
        {"value": "调查", "chunk_count": 1},
        {"value": "追逐", "chunk_count": 1},
    ]
    assert catalog["moods"] == [
        {"value": "紧张", "chunk_count": 2},
        {"value": "震惊", "chunk_count": 1},
    ]
    assert catalog["reference_tags"] == [
        {"value": "冲突", "chunk_count": 2},
        {"value": "伏笔", "chunk_count": 1},
    ]
    assert service.get_reference_tag_catalog(genre="科幻")["chunk_count"] == 0


def test_foreshadow_pair_requires_earlier_plant_and_later_payoff(settings) -> None:
    service = _make_service(settings)

    patterns = asyncio.run(
        service.find_foreshadow_patterns(
            query="怀表真相",
            pattern_type="pair",
            genre="悬疑",
            payoff_span="medium",
            limit=3,
        )
    )

    assert len(patterns) == 1
    assert patterns[0]["pattern_type"] == "pair"
    assert patterns[0]["payoff_span"] == "medium"
    assert [source["chapter_no"] for source in patterns[0]["source"]] == [1, 5]
    serialized = json.dumps(patterns, ensure_ascii=False)
    assert "旧怀表记录了关键时间" not in serialized
