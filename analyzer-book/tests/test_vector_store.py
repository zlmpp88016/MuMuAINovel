from __future__ import annotations

import asyncio

from book_analyzer.vector_store import SimpleVectorStore


def test_vector_store_search_and_delete() -> None:
    store = SimpleVectorStore()
    asyncio.run(
        store.add_documents(
            "book-1",
            [
                {
                    "id": "doc-1",
                    "text": "张三在京城追查秘密。",
                    "metadata": {"chapter_no": 1, "chapter_title": "第1章", "chunk_index": 0, "summary": "张三追查秘密", "tags": ["伏笔"]},
                },
                {
                    "id": "doc-2",
                    "text": "李四在山谷修炼。",
                    "metadata": {"chapter_no": 2, "chapter_title": "第2章", "chunk_index": 0, "summary": "李四修炼", "tags": ["成长"]},
                },
            ],
        )
    )

    hits = asyncio.run(store.search("book-1", "京城秘密", limit=5))
    assert hits
    assert hits[0]["id"] == "doc-1"

    asyncio.run(
        store.upsert_documents(
            "book-1",
            [
                {
                    "id": "doc-3",
                    "text": "王五在皇城发现新的秘密。",
                    "metadata": {"chapter_no": 3, "chapter_title": "第3章", "chunk_index": 0, "summary": "王五发现秘密", "tags": ["伏笔"]},
                }
            ],
        )
    )
    assert len(store._by_book["book-1"]) == 3

    store.delete_book("book-1")
    assert asyncio.run(store.search("book-1", "京城秘密", limit=5)) == []


def test_vector_store_search_corpus_with_filters() -> None:
    store = SimpleVectorStore()
    asyncio.run(
        store.add_documents(
            "book-1",
            [
                {
                    "id": "doc-1",
                    "text": "雨夜里，张三在京城追查秘密。",
                    "metadata": {
                        "book_title": "甲书",
                        "chapter_no": 1,
                        "chapter_title": "雨夜",
                        "chunk_index": 0,
                        "summary": "张三雨夜追查秘密",
                        "tags": ["伏笔"],
                        "tag_metadata": {"scene_type": "悬疑", "mood": "紧张"},
                    },
                }
            ],
        )
    )

    hits = asyncio.run(
        store.search_corpus("京城秘密", limit=5, filters={"scene_type": "悬疑"})
    )

    assert hits
    assert hits[0]["metadata"]["book_id"] == "book-1"
    assert asyncio.run(
        store.search_corpus("京城秘密", limit=5, filters={"scene_type": "日常"})
    ) == []
