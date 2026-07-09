from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from book_analyzer.analysis import ChunkAnalysis
from book_analyzer.config import Settings
from book_analyzer.db import create_session_factory, create_sqlalchemy_engine, ensure_schema_compatibility
from book_analyzer.models import Base, Book, BookChunk, BookParseProgress
from book_analyzer.parser import Chunk
from book_analyzer.service import BookAnalysisService
from book_analyzer.vector_store import SimpleVectorStore


class RecordingAnalyzer:
    def __init__(self, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.calls: list[tuple[int, int]] = []

    async def analyze_chunk(self, chunk: Chunk) -> ChunkAnalysis:
        self.calls.append((chunk.chapter_no, chunk.chunk_index))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("boom")
        return ChunkAnalysis(
            chapter_no=chunk.chapter_no,
            chapter_title=chunk.chapter_title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            summary=f"summary-{chunk.chapter_no}-{chunk.chunk_index}",
            tags=["断点"],
            importance=0.8,
            characters=[f"角色{chunk.chunk_index}"],
            vector_id=f"vec-{chunk.chapter_no}-{chunk.chunk_index}",
            tag_metadata={"source": "test"},
        )

    async def build_book_summary(
        self,
        title: str,
        chapters,
        analyses: list[ChunkAnalysis],
    ) -> dict:
        return {
            "title": title,
            "stats": {"chunks": len(analyses), "chapters": len(chapters)},
        }


def _make_service(
    settings: Settings,
    analyzer: RecordingAnalyzer,
    vector_store: SimpleVectorStore | None = None,
) -> tuple[BookAnalysisService, sessionmaker[Session], SimpleVectorStore]:
    settings.ensure_directories()
    engine = create_sqlalchemy_engine(settings.database_url)
    Base.metadata.create_all(engine)
    ensure_schema_compatibility(engine)
    session_factory = create_session_factory(engine)
    store = vector_store or SimpleVectorStore()
    return (
        BookAnalysisService(
            settings=settings,
            session_factory=session_factory,
            vector_store=store,
            analyzer=analyzer,
        ),
        session_factory,
        store,
    )


def _multi_chunk_text() -> bytes:
    content = "张三在京城追查秘密，李四记录每一个线索。" * 50
    return f"断点测试\n第1章 开端\n{content}".encode("utf-8")


def test_ingest_upload_resumes_from_persisted_chunk(settings: Settings) -> None:
    first_analyzer = RecordingAnalyzer(fail_on_call=2)
    service, session_factory, vector_store = _make_service(settings, first_analyzer)
    raw_bytes = _multi_chunk_text()

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(service.ingest_upload("resume.txt", raw_bytes))

    with session_factory() as session:
        book = session.scalar(select(Book).where(Book.original_filename == "resume.txt"))
        assert book is not None
        progress = session.scalar(
            select(BookParseProgress).where(BookParseProgress.book_id == book.id)
        )
        chunks = session.scalars(select(BookChunk).where(BookChunk.book_id == book.id)).all()
        assert book.status == "failed"
        assert progress is not None
        assert progress.completed_chunks == 1
        assert progress.next_chunk_order == 1
        assert progress.current_node.startswith("chunk:2/")
        assert len(chunks) == 1
        assert len(vector_store._by_book[book.id]) == 1

    resume_analyzer = RecordingAnalyzer()
    resumed_service = BookAnalysisService(
        settings=settings,
        session_factory=session_factory,
        vector_store=vector_store,
        analyzer=resume_analyzer,
    )
    completed = asyncio.run(resumed_service.ingest_upload("resume.txt", raw_bytes))

    assert completed.status == "completed"
    assert (1, 0) not in resume_analyzer.calls

    with session_factory() as session:
        chunks = session.scalars(
            select(BookChunk)
            .where(BookChunk.book_id == completed.id)
            .order_by(BookChunk.chapter_no, BookChunk.chunk_index)
        ).all()
        progress = session.scalar(
            select(BookParseProgress).where(BookParseProgress.book_id == completed.id)
        )
        assert progress is not None
        assert progress.status == "completed"
        assert progress.completed_chunks == len(chunks)
        assert len({(chunk.chapter_no, chunk.chunk_index) for chunk in chunks}) == len(chunks)
        assert all(chunk.characters for chunk in chunks)
        assert len(vector_store._by_book[completed.id]) == len(chunks)
        assert completed.summary_json["stats"]["chunks"] == len(chunks)
