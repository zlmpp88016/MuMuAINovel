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
from book_analyzer.service import ANALYSIS_CHUNK_CONCURRENCY, BookAnalysisService
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


class DelayedAnalyzer(RecordingAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0
        self.completed_order: list[tuple[int, int]] = []

    async def analyze_chunk(self, chunk: Chunk) -> ChunkAnalysis:
        self.calls.append((chunk.chapter_no, chunk.chunk_index))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            delay_slot = ANALYSIS_CHUNK_CONCURRENCY - (
                chunk.chunk_index % ANALYSIS_CHUNK_CONCURRENCY
            )
            await asyncio.sleep(delay_slot * 0.01)
            self.completed_order.append((chunk.chapter_no, chunk.chunk_index))
            return ChunkAnalysis(
                chapter_no=chunk.chapter_no,
                chapter_title=chunk.chapter_title,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                summary=f"summary-{chunk.chapter_no}-{chunk.chunk_index}",
                tags=["并发"],
                importance=0.8,
                characters=[f"角色{chunk.chunk_index}"],
                vector_id=f"vec-{chunk.chapter_no}-{chunk.chunk_index}",
                tag_metadata={"source": "test"},
            )
        finally:
            self.active -= 1


class OrderedVectorStore(SimpleVectorStore):
    def __init__(self) -> None:
        super().__init__()
        self.upsert_order: list[tuple[int, int]] = []

    async def upsert_documents(self, book_id: str, documents: list[dict]) -> None:
        self.upsert_order.extend(
            (document["metadata"]["chapter_no"], document["metadata"]["chunk_index"])
            for document in documents
        )
        await super().upsert_documents(book_id, documents)


class RecordingPersistService(BookAnalysisService):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.persist_order: list[tuple[int, int]] = []

    def _persist_chunk_analysis(
        self,
        book_id: str,
        analysis: ChunkAnalysis,
        order: int,
        total_chunks: int,
    ) -> None:
        self.persist_order.append((analysis.chapter_no, analysis.chunk_index))
        super()._persist_chunk_analysis(book_id, analysis, order, total_chunks)


def _make_service(
    settings: Settings,
    analyzer: RecordingAnalyzer,
    vector_store: SimpleVectorStore | None = None,
    service_cls: type[BookAnalysisService] = BookAnalysisService,
) -> tuple[BookAnalysisService, sessionmaker[Session], SimpleVectorStore]:
    settings.ensure_directories()
    engine = create_sqlalchemy_engine(settings.database_url)
    Base.metadata.create_all(engine)
    ensure_schema_compatibility(engine)
    session_factory = create_session_factory(engine)
    store = vector_store or SimpleVectorStore()
    return (
        service_cls(
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


def _large_multi_chunk_text() -> bytes:
    content = "张三在京城追查秘密，李四记录每一个线索。" * 120
    return f"并发测试\n第1章 开端\n{content}".encode("utf-8")


def test_ingest_upload_resumes_from_persisted_chunk(settings: Settings) -> None:
    # 旧 ingest_upload 串联 upload+analyze，行为兼容：第一次失败、第二次恢复。
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


def test_analysis_uses_five_coroutines_but_writes_in_chunk_order(settings: Settings) -> None:
    analyzer = DelayedAnalyzer()
    vector_store = OrderedVectorStore()
    service, session_factory, store = _make_service(
        settings,
        analyzer,
        vector_store=vector_store,
        service_cls=RecordingPersistService,
    )
    book = asyncio.run(service.upload_book("ordered.txt", _large_multi_chunk_text()))

    async def _run() -> Book:
        handle = await service.start_analysis(book.id)
        return await handle.task

    completed = asyncio.run(_run())

    with session_factory() as session:
        rows = session.scalars(
            select(BookChunk)
            .where(BookChunk.book_id == completed.id)
            .order_by(BookChunk.chapter_no, BookChunk.chunk_index)
        ).all()
        progress = session.scalar(
            select(BookParseProgress).where(BookParseProgress.book_id == completed.id)
        )

    expected_order = [(chunk.chapter_no, chunk.chunk_index) for chunk in rows]
    assert progress is not None
    assert progress.total_chunks >= ANALYSIS_CHUNK_CONCURRENCY
    assert analyzer.max_active == ANALYSIS_CHUNK_CONCURRENCY
    assert analyzer.completed_order[:ANALYSIS_CHUNK_CONCURRENCY] != expected_order[
        :ANALYSIS_CHUNK_CONCURRENCY
    ]
    assert service.persist_order == expected_order
    assert store.upsert_order == expected_order


def test_upload_only_does_not_analyze(settings: Settings) -> None:
    analyzer = RecordingAnalyzer()
    service, session_factory, _ = _make_service(settings, analyzer)

    book = asyncio.run(service.upload_book("pending.txt", _multi_chunk_text()))

    assert book.status == "pending"
    assert book.stage == "uploaded"
    assert analyzer.calls == []
    with session_factory() as session:
        progress = session.scalar(
            select(BookParseProgress).where(BookParseProgress.book_id == book.id)
        )
        assert progress is not None
        assert progress.total_chunks > 0
        assert progress.completed_chunks == 0
        assert progress.status == "pending"


def test_analyze_book_resumes_failed_from_breakpoint(settings: Settings) -> None:
    first_analyzer = RecordingAnalyzer(fail_on_call=2)
    service, session_factory, vector_store = _make_service(settings, first_analyzer)
    raw_bytes = _multi_chunk_text()

    book = asyncio.run(service.upload_book("resume2.txt", raw_bytes))
    with pytest.raises(RuntimeError, match="boom"):

        async def _fail() -> None:
            handle = await service.start_analysis(book.id)
            await handle.task

        asyncio.run(_fail())

    resume_analyzer = RecordingAnalyzer()
    resumed_service, _, _ = _make_service(settings, resume_analyzer, vector_store=vector_store)

    async def _resume() -> Book:
        handle = await resumed_service.start_analysis(book.id)
        return await handle.task

    completed = asyncio.run(_resume())
    assert completed.status == "completed"
    assert (1, 0) not in resume_analyzer.calls


def test_start_analysis_reuses_running_handle(settings: Settings) -> None:
    """同一本书重复启动应复用同一后台句柄，不创建双写任务。"""
    analyzer = RecordingAnalyzer()
    service, _, _ = _make_service(settings, analyzer)
    book = asyncio.run(service.upload_book("dup.txt", _multi_chunk_text()))

    async def _scenario() -> None:
        handle1 = await service.start_analysis(book.id)
        handle2 = await service.start_analysis(book.id)
        assert handle2 is handle1
        await handle1.task

    asyncio.run(_scenario())


def test_interrupt_running_books_marks_interrupted(settings: Settings) -> None:
    """进程重启后，running 状态书籍应被标记为 interrupted 供继续恢复。"""
    analyzer = RecordingAnalyzer()
    service, session_factory, _ = _make_service(settings, analyzer)
    book = asyncio.run(service.upload_book("running.txt", _multi_chunk_text()))

    # 模拟上次进程被硬杀：直接把书标成 running（后台 task 已不存在）。
    with session_factory() as session:
        row = session.get(Book, book.id)
        assert row is not None
        row.status = "running"
        row.stage = "analyzing"
        session.commit()

    marked = service.interrupt_running_books()
    assert marked == 1

    with session_factory() as session:
        row = session.get(Book, book.id)
        assert row.status == "interrupted"
        assert row.stage == "interrupted"
        assert row.error_message is not None
        progress = session.scalar(
            select(BookParseProgress).where(BookParseProgress.book_id == book.id)
        )
        assert progress is not None
        assert progress.status == "interrupted"

    # 继续分析：从断点恢复应能完成。
    resume_analyzer = RecordingAnalyzer()
    resumed_service, _, _ = _make_service(settings, resume_analyzer)

    async def _resume() -> Book:
        handle = await resumed_service.start_analysis(book.id)
        return await handle.task

    completed = asyncio.run(_resume())
    assert completed.status == "completed"
