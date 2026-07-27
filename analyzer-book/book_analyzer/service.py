"""上传、分析、搜索和导出的核心编排 service。

API 层会把所有流程决策委托到这里。该 service 负责 metadata 数据库事务、
调用 analyzer、写入向量索引，并让导出的 JSON 文件与数据库状态保持同步。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker, selectinload

from book_analyzer.analysis import Analyzer, ChunkAnalysis, RuleBasedAnalyzer
from book_analyzer.config import Settings
from book_analyzer.models import Book, BookChunk, BookParseProgress
from book_analyzer.parser import (
    Chunk,
    chunk_chapters,
    decode_text_file,
    filename_to_title,
    parse_book_text,
)
from book_analyzer.vector_store import VectorStore


logger = logging.getLogger(__name__)

ANALYSIS_CHUNK_CONCURRENCY = 5


def _progress_event(
    stage: str,
    progress: int,
    completed_chunks: int,
    total_chunks: int,
    status: str = "running",
    message: str | None = None,
) -> dict[str, Any]:
    """构造统一的进度事件，供 SSE 透传给前端。"""
    return {
        "stage": stage,
        "progress": progress,
        "completed_chunks": completed_chunks,
        "total_chunks": total_chunks,
        "status": status,
        "message": message,
    }


@dataclass
class _AnalysisHandle:
    """单个后台分析任务的运行句柄。

    保存 asyncio task 和广播器 ``broadcaster``：SSE 订阅者通过 broadcaster
    拿到事件流，断连只丢弃订阅者，不影响后台 task 继续推进。``latest``
    缓存最近一次进度事件，供新订阅者立即拿到当前状态（重连不丢上下文）。
    """

    task: asyncio.Task
    broadcaster: "ProgressBroadcaster"
    latest: dict[str, Any] = field(default_factory=dict)


class ProgressBroadcaster:
    """单本书进度的发布-订阅器。

    ``publish`` 把事件广播给所有活跃订阅队列；``subscribe`` 返回一个
    ``asyncio.Queue``，并立即投递最近一次进度（重放），避免新订阅者
    看不到已开始的进度。订阅者断连时只需停止消费队列即可。
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._latest: dict[str, Any] = {}

    def publish(self, event: dict[str, Any]) -> None:
        """广播事件并缓存为最近一次进度。"""
        self._latest = event
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    async def subscribe(self) -> asyncio.Queue:
        """返回一个订阅队列，立即重放最近一次进度。"""
        queue: asyncio.Queue = asyncio.Queue()
        if self._latest:
            queue.put_nowait(self._latest)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """移除订阅者，被取消或断连时调用。"""
        self._subscribers.discard(queue)


class BookAnalysisService:
    """独立 book-analyzer 的应用 service。

    参数：
        settings: 运行时配置，例如 chunk 大小和存储路径。
        session_factory: 绑定 metadata DB 的 SQLAlchemy session factory。
        vector_store: 搜索索引实现，可以是 Chroma 或兜底实现。
        analyzer: 根据当前 settings 选择的 chunk / book analyzer。
    """

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        vector_store: VectorStore,
        analyzer: Analyzer,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.vector_store = vector_store
        self.analyzer = analyzer
        # rule_analyzer 只用于确定性回退和从已存 chunk 重建索引，不改变主分析器选择。
        self.rule_analyzer = analyzer if isinstance(analyzer, RuleBasedAnalyzer) else RuleBasedAnalyzer()
        # 运行中的分析任务按 book_id 串行：start_analysis 创建后台 task，
        # SSE 订阅者通过 handle.broadcaster 拿进度，断连不影响后台推进。
        self._analysis_handles: dict[str, _AnalysisHandle] = {}

    def list_books(self) -> list[Book]:
        """返回所有书籍，并按最近更新时间倒序排列。

        返回：
            ORM ``Book`` 行，不附带较重的导出 payload，但已预加载
            ``parse_progress`` 关系以便列表层读取断点进度。
        """
        with self._session() as session:
            stmt = (
                select(Book)
                .options(selectinload(Book.parse_progress))
                .order_by(Book.updated_at.desc())
            )
            return list(session.scalars(stmt).all())

    def get_book(self, book_id: str) -> Book:
        """返回单本书，并预加载其 chunks。

        参数：
            book_id: 要加载书籍的 UUID 字符串。

        返回：
            匹配的 ``Book`` ORM 行。

        抛出：
            KeyError: 书籍不存在。
        """
        with self._session() as session:
            book = session.get(Book, book_id)
            if book is None:
                raise KeyError(book_id)
            book.chunks
            return book

    async def ingest_upload(self, filename: str, raw_bytes: bytes) -> Book:
        """上传并同步分析一本书（兼容旧调用方）。

        等价于先 :meth:`upload_book` 再 :meth:`start_analysis` 并等待完成。
        新前端应分别调用这两步以支持上传/分析分离与 SSE 订阅。

        参数：
            filename: 原始上传文件名。
            raw_bytes: 上传请求中的 TXT 原始字节。

        返回：
            已完成分析的 ``Book`` 行。

        抛出：
            ValueError: 上传为空、超过大小限制，或不是 TXT 文件。
        """
        book = await self.upload_book(filename, raw_bytes)
        handle = await self.start_analysis(book.id)
        return await handle.task

    async def upload_book(self, filename: str, raw_bytes: bytes) -> Book:
        """上传并解析 TXT，仅落库与建断点，不触发 LLM 分析。

        校验通过后 decode+parse+chunk，写入 ``Book``（status=pending，
        stage=uploaded）和 ``BookParseProgress``（total_chunks 已知），
        并持久化上传副本。重复文件复用已有 Book 行，必要时补齐解析结论。

        参数：
            filename: 原始上传文件名，最终只保存 basename。
            raw_bytes: 上传请求中的 TXT 原始字节。

        返回：
            处于 pending / uploaded 阶段的 ``Book`` 行（已加载 chunks 关系）。

        抛出：
            ValueError: 上传为空、超过大小限制，或不是 TXT 文件。
        """
        # 先做纯输入校验，避免无效文件进入磁盘、数据库或模型调用链路。
        self._validate_upload(filename, raw_bytes)
        logger.info("上传校验通过: %s (%d bytes)", filename, len(raw_bytes))

        file_hash = hashlib.sha256(raw_bytes).hexdigest()
        filename_key = Path(filename).name

        with self._session() as session:
            existing = session.scalar(select(Book).where(Book.file_hash == file_hash))
            if existing is not None and existing.status == "completed":
                existing.chunks
                logger.info("检测到重复文件 (hash=%s...), 复用已有记录 %s", file_hash[:12], existing.id)
                await self._ensure_book_index(existing, session)
                return existing

        logger.info("开始解析上传文件: filename=%s, hash=%s...", filename_key, file_hash[:12])
        decoded, detected_encoding = decode_text_file(raw_bytes)
        parsed_book = parse_book_text(decoded, title_hint=filename)
        title = parsed_book.title or filename_to_title(filename)
        logger.info("文本解析完成: 书名=%s, 章节数=%d, 总字符=%d, 编码=%s",
                     title, len(parsed_book.chapters), len(decoded), detected_encoding)
        chunks = chunk_chapters(
            parsed_book.chapters,
            chunk_size=self.settings.chunk_size,
            overlap=self.settings.chunk_overlap,
        )
        logger.info("文本切块完成: chunk_size=%d, overlap=%d, 总chunk数=%d",
                     self.settings.chunk_size, self.settings.chunk_overlap, len(chunks))

        with self._session() as session:
            book = session.scalar(select(Book).where(Book.file_hash == file_hash))
            if book is None:
                book = Book(
                    title=title,
                    original_filename=filename_key,
                    file_hash=file_hash,
                    status="pending",
                    stage="uploaded",
                    progress=0,
                    summary_json={},
                )
                session.add(book)
                session.flush()
                # 以 UTF-8 编码持久化上传副本，保证后续重解析无需再次检测编码。
                self._persist_upload_copy(book.id, filename, decoded.encode("utf-8"))
                logger.info("上传副本已持久化为 UTF-8: book_id=%s, 原始编码=%s", book.id, detected_encoding)
            else:
                logger.info("检测到已上传未完成书籍, 复用并刷新元信息: book_id=%s, status=%s", book.id, book.status)
                book.title = title
                book.original_filename = filename_key
                book.error_message = None

            progress = self._ensure_parse_progress(
                session=session,
                book=book,
                filename_key=filename_key,
                file_hash=file_hash,
                total_chunks=len(chunks),
            )
            completed_keys = {(chunk.chapter_no, chunk.chunk_index) for chunk in book.chunks}
            progress.completed_chunks = len(completed_keys)
            progress.next_chunk_order = self._next_missing_chunk_order(chunks, completed_keys)
            # 上传阶段只到 uploaded：分析未开始过时，断点也回到 analyzing 入口前的状态。
            progress.status = book.status if book.status != "pending" else "pending"
            progress.stage = book.stage if book.stage != "uploaded" else "uploaded"
            progress.current_node = "resume" if completed_keys else "start"
            progress.error_message = None
            progress.updated_at = self._utc_now()
            book.updated_at = self._utc_now()
            session.flush()
            book.chunks
            return book

    def interrupt_running_books(self) -> int:
        """把上次未完成的运行中书籍标记为中断。

        程序启动时调用：进程上次可能被硬杀，留下 ``status=running`` 的行。
        这些任务对应的内存 task 已不存在，改为 ``interrupted`` 让前端展示
        「继续」按钮从断点恢复。只改 status/stage 与 error_message，不动
        已落库的 chunks 与断点位置。

        返回：
            被标记为中断的书籍数量。
        """
        with self._session() as session:
            books = list(session.scalars(select(Book).where(Book.status == "running")).all())
            for book in books:
                book.status = "interrupted"
                book.stage = "interrupted"
                book.error_message = "进程重启中断，可点击继续从断点恢复"
                book.updated_at = self._utc_now()
                progress = self._get_parse_progress(session, book.id)
                if progress is not None:
                    progress.status = "interrupted"
                    progress.stage = "interrupted"
                    progress.error_message = book.error_message
                    progress.updated_at = self._utc_now()
            return len(books)

    async def start_analysis(self, book_id: str) -> _AnalysisHandle:
        """为已上传书籍启动后台分析任务，立即返回句柄。

        任务在事件循环里独立运行，不受订阅 SSE 的连接生命周期影响。
        进度通过 :attr:`_AnalysisHandle.broadcaster` 广播，任意数量的
        订阅者可中途加入（会重放最近一次进度）。重复启动同一本书会被
        串行保护：已运行则直接返回现有句柄。

        参数：
            book_id: 目标书籍 UUID。

        返回：
            该书的后台分析句柄，可用于订阅进度或等待完成。

        抛出：
            KeyError: 书籍不存在。
        """
        with self._session() as session:
            if session.get(Book, book_id) is None:
                raise KeyError(book_id)

        existing = self._analysis_handles.get(book_id)
        if existing is not None and not existing.task.done():
            # 已有后台任务在跑，复用句柄，避免双写。
            return existing

        broadcaster = ProgressBroadcaster()
        task = asyncio.create_task(self._run_analysis(book_id, broadcaster))
        handle = _AnalysisHandle(task=task, broadcaster=broadcaster)
        self._analysis_handles[book_id] = handle
        return handle

    async def subscribe_progress(self, book_id: str) -> asyncio.Queue | None:
        """订阅单本书的进度事件队列，断连后调用方停止消费即可。

        若该书没有运行中的后台任务，返回 ``None``，调用方可决定是否
        :meth:`start_analysis` 启动一个新的。

        参数：
            book_id: 目标书籍 UUID。

        返回：
            进度事件队列，或 ``None`` 表示无运行中的任务。
        """
        handle = self._analysis_handles.get(book_id)
        if handle is None or handle.task.done():
            return None
        return await handle.broadcaster.subscribe()

    def get_analysis_handle(self, book_id: str) -> _AnalysisHandle | None:
        """返回运行中或已完成但未清理的分析句柄，无则 ``None``。"""
        return self._analysis_handles.get(book_id)

    async def _run_analysis(
        self,
        book_id: str,
        broadcaster: "ProgressBroadcaster",
    ) -> Book:
        """执行单本书的 chunk 分析与整书摘要主流程，进度广播给订阅者。

        中途抛异常会落 failed 状态并广播 failed 事件，最后清理句柄注册，
        下一次 :meth:`start_analysis` 可从断点恢复。
        """
        try:
            return await self._do_run_analysis(book_id, broadcaster)
        except Exception as exc:
            logger.exception("分析任务中止: book_id=%s", book_id)
            broadcaster.publish(_progress_event("failed", 0, 0, 0, "failed", str(exc)[:4000]))
            raise
        finally:
            # 句柄保留以让正在订阅的 SSE 拿到终态事件，由 start_analysis 创建下一次时覆盖。
            self._analysis_handles.pop(book_id, None)

    async def _do_run_analysis(
        self,
        book_id: str,
        broadcaster: "ProgressBroadcaster",
    ) -> Book:
        """分析主流程实现细节。"""
        with self._session() as session:
            book = session.get(Book, book_id)
            if book is None:
                raise KeyError(book_id)
            existing_chunks = list(book.chunks)
            filename_key = book.original_filename
            file_hash = book.file_hash
            title = book.title
            existing_completed_keys = {(c.chapter_no, c.chunk_index) for c in existing_chunks}

        # 从上传副本重解析，保证分析链路与原始文本一致。
        raw_bytes = self._read_upload_copy(book_id)
        decoded, _ = decode_text_file(raw_bytes)
        parsed_book = parse_book_text(decoded, title_hint=title)
        chunks = chunk_chapters(
            parsed_book.chapters,
            chunk_size=self.settings.chunk_size,
            overlap=self.settings.chunk_overlap,
        )
        total = len(chunks)

        with self._session() as session:
            book = session.get(Book, book_id)
            progress = self._ensure_parse_progress(
                session=session,
                book=book,
                filename_key=filename_key,
                file_hash=file_hash,
                total_chunks=total,
            )
            completed_keys = {(c.chapter_no, c.chunk_index) for c in book.chunks}
            progress.completed_chunks = len(completed_keys)
            progress.next_chunk_order = self._next_missing_chunk_order(chunks, completed_keys)
            progress.status = "running"
            progress.stage = "analyzing"
            progress.current_node = "resume" if completed_keys else "start"
            progress.error_message = None
            book.status = "running"
            book.stage = "analyzing"
            book.progress = self._analysis_progress(progress.completed_chunks, total)
            book.error_message = None
            book.updated_at = self._utc_now()
            session.flush()

        if existing_chunks:
            logger.info("恢复已完成 chunk 向量索引: book_id=%s, chunks=%d", book_id, len(existing_chunks))
            await self._upsert_chunk_vectors(book_id, title, existing_chunks)

        completed_keys = existing_completed_keys | {
            (c.chapter_no, c.chunk_index) for c in existing_chunks
        }

        def _emit(stage: str, message: str | None = None, status: str = "running") -> None:
            with self._session() as session:
                prog = self._get_parse_progress(session, book_id)
                cur = session.get(Book, book_id)
                done = prog.completed_chunks if prog else len(completed_keys)
                total_now = prog.total_chunks if prog else total
                pct = cur.progress if cur else self._analysis_progress(done, total)
                event = _progress_event(stage, int(pct), int(done), int(total_now), status, message)
                broadcaster.publish(event)

        _emit("analyzing", "开始分析")

        await self._analyze_chunks_with_ordered_writes(
            book_id=book_id,
            book_title=title,
            chunks=chunks,
            total_chunks=total,
            completed_keys=completed_keys,
            emit=_emit,
        )

        analyses = self._load_chunk_analyses(book_id)
        _emit("summarizing", "生成整书摘要")
        try:
            logger.info("开始生成整书摘要: book_id=%s, chunks=%d", book_id, len(analyses))
            summary_json = await self.analyzer.build_book_summary(title, parsed_book.chapters, analyses)
            logger.info("整书摘要生成完成: book_id=%s", book_id)
        except Exception as exc:
            logger.exception("整书摘要节点失败: book_id=%s", book_id)
            self._mark_parse_failed(book_id, "book_summary", exc)
            _emit("summarizing", str(exc), status="failed")
            raise

        with self._session() as session:
            book = session.get(Book, book_id)
            if book is None:
                raise KeyError(book_id)
            progress = self._get_parse_progress(session, book_id)
            book.summary_json = summary_json
            book.style_profile_json = summary_json.get("style_profile", {})
            book.status = "completed"
            book.stage = "done"
            book.progress = 100
            book.error_message = None
            book.completed_at = self._utc_now()
            book.updated_at = self._utc_now()
            if progress is not None:
                progress.status = "completed"
                progress.stage = "done"
                progress.completed_chunks = len(analyses)
                progress.next_chunk_order = len(chunks)
                progress.current_node = "completed"
                progress.error_message = None
                progress.updated_at = self._utc_now()
            session.flush()
            book.chunks

            # 导出文件是数据库内容的派生产物，写在成功分析后，删除书籍时同步清理。
            export_path = self._export_payload_path(book.id)
            export_path.write_text(
                json.dumps(self._build_export_payload(book, analyses), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("导出文件已写入: %s", export_path)
            logger.info("分析全流程完成: book_id=%s, title=%s", book.id, book.title)
            _emit("done", "分析完成", status="completed")
            return book

    async def search_book(self, book_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """搜索单本书中已索引的 chunks。

        参数：
            book_id: 要搜索书籍的 UUID 字符串。
            query: 自然语言搜索文本。
            limit: 最大返回命中数量。

        返回：
            已规范化为 API schema 所需格式的搜索命中结果。

        抛出：
            KeyError: 书籍不存在。
        """
        with self._session() as session:
            book = session.get(Book, book_id)
            if book is None:
                raise KeyError(book_id)
            # 内存检索或 Chroma 目录被清空时，可以从数据库 chunk 自动补索引。
            await self._ensure_book_index(book, session)
            hits = await self.vector_store.search(book_id, query=query, limit=limit)
            logger.info("搜索完成: book_id=%s, query=\"%s\", 命中=%d", book_id, query, len(hits))
            return [
                {
                    "chunk_id": hit["id"],
                    "chapter_no": hit["metadata"]["chapter_no"],
                    "chapter_title": hit["metadata"]["chapter_title"],
                    "chunk_index": hit["metadata"]["chunk_index"],
                    "summary": hit["metadata"]["summary"],
                    "content": hit["text"],
                    "tags": list(hit["metadata"].get("tags", [])),
                    "tag_metadata": dict(hit["metadata"].get("tag_metadata", {})),
                    "score": hit["score"],
                }
                for hit in hits
            ]

    async def search_reference_passages(
        self,
        query: str,
        scene_type: str | None = None,
        mood: str | None = None,
        genre: str | None = None,
        style_tags: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """跨语料库检索可供写作参考的片段。"""
        await self._ensure_completed_books_index()
        books = self._book_summary_lookup()
        filters: dict[str, Any] = {}
        if scene_type:
            filters["scene_type"] = scene_type
        if mood:
            filters["mood"] = mood
        if style_tags:
            filters["tags"] = style_tags
        if genre:
            filters["book_ids"] = [
                book_id
                for book_id, book_info in books.items()
                if (book_info.get("summary") or {}).get("book_profile", {}).get("genre") == genre
            ]
            if not filters["book_ids"]:
                return []

        hits = await self.vector_store.search_corpus(
            query=query,
            limit=limit,
            filters=filters or None,
        )
        results = [self._format_corpus_hit(hit, books) for hit in hits]
        return results[:limit]

    async def search_highlight_passages(
        self,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """检索高质量片段用于 AI 去味校准。"""
        hits = await self.search_reference_passages(query=query, limit=max(limit * 2, limit))
        hits.sort(key=lambda item: (item.get("importance", 0), item.get("score", 0)), reverse=True)
        return hits[:limit]

    def get_style_profile(
        self,
        genre: str | None = None,
        mood: str | None = None,
        sample: int = 5,
    ) -> dict[str, Any]:
        """聚合已完成书籍的写作风格画像。"""
        with self._session() as session:
            stmt = select(Book).where(Book.status == "completed").order_by(Book.updated_at.desc())
            books = list(session.scalars(stmt).all())

        profiles: list[dict[str, Any]] = []
        for book in books:
            summary = book.summary_json or {}
            profile = book.style_profile_json or summary.get("style_profile") or {}
            book_profile = summary.get("book_profile") or {}
            if genre and book_profile.get("genre") != genre:
                continue
            if mood and mood not in json.dumps(profile, ensure_ascii=False):
                continue
            if profile:
                profiles.append(
                    {
                        "book_id": book.id,
                        "title": book.title,
                        "genre": book_profile.get("genre", ""),
                        "profile": profile,
                    }
                )
            if len(profiles) >= sample:
                break
        return {
            "genre": genre or "全部",
            "mood": mood or "",
            "sample_count": len(profiles),
            "profiles": profiles,
        }

    async def search_plot_patterns(
        self,
        query: str,
        genre: str | None = None,
        plot_stage: str | None = None,
        pattern_tags: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """返回有来源依据的情节结构，不暴露来源故事细节。"""
        hits = await self.search_reference_passages(
            query=query,
            genre=genre,
            style_tags=pattern_tags,
            limit=min(max(limit * 3, limit), 30),
        )
        books = self._book_summary_lookup()
        patterns: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for hit in hits:
            book_id = str(hit.get("book_id") or "")
            chapter_no = self._as_positive_int(hit.get("chapter_no"), default=1)
            source_key = (book_id, chapter_no)
            if source_key in seen:
                continue
            seen.add(source_key)

            summary = (books.get(book_id, {}).get("summary") or {})
            outline = summary.get("outline") or []
            stage = self._infer_plot_stage(chapter_no, len(outline))
            if plot_stage and stage != plot_stage:
                continue
            tags = [str(tag) for tag in hit.get("tags", []) if str(tag).strip()]
            tag_metadata = hit.get("tag_metadata") or {}
            patterns.append(
                {
                    "pattern_id": self._stable_asset_id("plot", book_id, chapter_no),
                    "plot_stage": stage,
                    "genre": (hit.get("book_profile") or {}).get("genre", ""),
                    "pattern_tags": tags[:8],
                    "beats": self._plot_beats(stage, tags, tag_metadata),
                    "conflict_escalation": self._conflict_escalation(tags),
                    "turning_point": self._turning_point(stage, tags),
                    "outcome_effect": self._outcome_effect(stage),
                    "source": self._asset_source(hit),
                    "relevance_score": hit.get("score", 0),
                    "importance": hit.get("importance", 0.5),
                }
            )
        return self._select_diverse_assets(patterns, limit)

    def search_character_archetypes(
        self,
        query: str,
        genre: str | None = None,
        role_type: str | None = None,
        arc_type: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """将角色卡标准化并匿名化为可复用原型。"""
        with self._session() as session:
            books = list(
                session.scalars(
                    select(Book)
                    .where(Book.status == "completed")
                    .order_by(Book.updated_at.desc())
                ).all()
            )

        candidates: list[tuple[float, dict[str, Any]]] = []
        for book in books:
            summary = book.summary_json or {}
            book_genre = (summary.get("book_profile") or {}).get("genre", "")
            if genre and book_genre != genre:
                continue
            cards = summary.get("character_cards") or []
            names = [
                str(card.get("name") or "").strip()
                for card in cards
                if isinstance(card, dict)
            ]
            for index, card in enumerate(cards):
                if not isinstance(card, dict):
                    continue
                normalized_role = self._character_role(card, index)
                if role_type and normalized_role != role_type:
                    continue
                normalized_arc = self._character_field(
                    card,
                    "arc_type",
                    "growth_arc_type",
                    default="transformation",
                )
                if arc_type and normalized_arc != arc_type:
                    continue

                current_name = str(card.get("name") or "").strip()
                sanitize = lambda value: self._anonymize_character_text(  # noqa: E731
                    value,
                    current_name,
                    names,
                )
                archetype = {
                    "archetype_id": self._stable_asset_id("character", book.id, index),
                    "role_type": normalized_role,
                    "arc_type": normalized_arc,
                    "external_goal": sanitize(
                        self._character_field(
                            card,
                            "external_goal",
                            "goal",
                            "motivation",
                            default=self._default_external_goal(normalized_role),
                        )
                    ),
                    "internal_need": sanitize(
                        self._character_field(
                            card,
                            "internal_need",
                            "need",
                            default="正视内在缺口，并在选择中完成价值确认。",
                        )
                    ),
                    "core_conflict": sanitize(
                        self._character_field(
                            card,
                            "core_conflict",
                            "conflict",
                            "description",
                            default="外在目标与内在需求相互牵制。",
                        )
                    ),
                    "personality_flaw": sanitize(
                        self._character_field(
                            card,
                            "personality_flaw",
                            "flaw",
                            default="在压力下会放大其惯性判断。",
                        )
                    ),
                    "relationship_pattern": sanitize(
                        self._character_field(
                            card,
                            "relationship_pattern",
                            "relationships",
                            default="通过合作、对抗与误解推动关系变化。",
                        )
                    ),
                    "growth_arc": sanitize(
                        self._character_field(
                            card,
                            "growth_arc",
                            "arc",
                            default="在选择与代价中修正核心缺陷。",
                        )
                    ),
                    "traits": [sanitize(trait) for trait in self._character_traits(card)],
                    "source": {"book_id": book.id, "book_title": book.title},
                }
                searchable = json.dumps(card, ensure_ascii=False) + " " + json.dumps(
                    archetype,
                    ensure_ascii=False,
                )
                candidates.append((self._text_relevance(query, searchable), archetype))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return self._select_diverse_assets(
            [candidate for _, candidate in candidates],
            limit,
        )

    async def find_foreshadow_patterns(
        self,
        query: str,
        pattern_type: str,
        genre: str | None = None,
        subtlety: str | None = None,
        payoff_span: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """将来源事件标准化为伏笔埋设、回收或配对技法。"""
        candidate_limit = min(max(limit * 10, 20), 100)
        search_queries = [f"{query} 暗示 伏笔 线索"]
        if pattern_type in {"payoff", "pair"}:
            search_queries.append(f"{query} 真相 揭晓 回收 谜底")
        hits_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
        for search_query in search_queries:
            hits = await self.search_reference_passages(
                query=search_query,
                genre=genre,
                limit=candidate_limit,
            )
            for hit in hits:
                key = (
                    str(hit.get("book_id") or ""),
                    self._as_positive_int(hit.get("chapter_no"), default=1),
                    self._as_positive_int(hit.get("chunk_index"), default=0),
                )
                hits_by_key.setdefault(key, hit)

        events: list[dict[str, Any]] = []
        for hit in hits_by_key.values():
            text = f"{hit.get('summary', '')}\n{hit.get('content', '')}"
            event_type = self._foreshadow_event_type(hit.get("tags", []), text)
            if event_type is None:
                continue
            importance = float(hit.get("importance", 0.5))
            event_subtlety = self._foreshadow_subtlety(importance)
            if subtlety and event_subtlety != subtlety:
                continue
            chapter_no = self._as_positive_int(hit.get("chapter_no"), default=1)
            chunk_index = self._as_positive_int(hit.get("chunk_index"), default=0)
            metadata = hit.get("tag_metadata") or {}
            events.append(
                {
                    "event_id": self._stable_asset_id(
                        "foreshadow",
                        hit.get("book_id", ""),
                        chapter_no,
                        chunk_index,
                    ),
                    "pattern_type": event_type,
                    "chapter_no": chapter_no,
                    "subtlety": event_subtlety,
                    "plant_technique": self._plant_technique(metadata),
                    "surface_function": self._surface_function(metadata),
                    "hidden_information": "延后解释关键事实或因果关系。",
                    "payoff_method": self._payoff_method(metadata),
                    "source": self._asset_source(hit),
                    "_search_text": text,
                    "_relevance": self._text_relevance(query, text),
                }
            )

        if pattern_type == "pair":
            patterns = self._pair_foreshadow_events(events)
            if payoff_span:
                patterns = [
                    pattern
                    for pattern in patterns
                    if pattern.get("payoff_span") == payoff_span
                ]
        else:
            patterns = [event for event in events if event["pattern_type"] == pattern_type]

        patterns.sort(
            key=lambda item: (item.get("_relevance", 0), item.get("chapter_no", 0)),
            reverse=True,
        )
        cleaned = [
            {key: value for key, value in pattern.items() if not key.startswith("_")}
            for pattern in patterns
        ]
        return self._select_diverse_assets(cleaned, limit)

    def get_corpus_version(self) -> str:
        """返回最近完成书籍的更新时间，作为缓存版本。"""
        with self._session() as session:
            latest = session.scalar(
                select(func.max(Book.updated_at)).where(Book.status == "completed")
            )
        if latest is None:
            return ""
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return latest.isoformat().replace("+00:00", "Z")

    def export_book(self, book_id: str) -> dict[str, Any]:
        """返回并持久化一本书的导出 payload。

        参数：
            book_id: 要导出书籍的 UUID 字符串。

        返回：
            可 JSON 序列化的 payload，包含书籍摘要和 chunks。

        抛出：
            KeyError: 书籍不存在。
        """
        with self._session() as session:
            book = session.get(Book, book_id)
            if book is None:
                raise KeyError(book_id)
            analyses = [
                ChunkAnalysis(
                    chapter_no=chunk.chapter_no,
                    chapter_title=chunk.chapter_title,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    summary=chunk.summary,
                    tags=list(chunk.tags),
                    tag_metadata=dict(chunk.tag_metadata or {}),
                    importance=float(chunk.importance),
                    characters=list(chunk.characters or []),
                    vector_id=chunk.vector_id,
                )
                for chunk in book.chunks
            ]
            payload = self._build_export_payload(book, analyses)
            self._export_payload_path(book.id).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("导出完成: book_id=%s, chunks=%d", book_id, len(analyses))
            return payload

    def delete_book(self, book_id: str) -> None:
        """删除一本书、对应 chunks、向量文档和派生文件。

        参数：
            book_id: 要删除书籍的 UUID 字符串。

        抛出：
            KeyError: 书籍不存在。
        """
        with self._session() as session:
            book = session.get(Book, book_id)
            if book is None:
                raise KeyError(book_id)
            # 数据库级 cascade 负责 chunk；向量库和文件系统派生产物在事务外清理。
            session.delete(book)

        self.vector_store.delete_book(book_id)
        self._delete_file_if_exists(self._export_payload_path(book_id))
        self._delete_uploaded_copies(book_id)
        logger.info("删除完成: book_id=%s", book_id)

    @contextmanager
    def _session(self) -> Session:
        """打开一个短生命周期的事务性 SQLAlchemy session。

        生成：
            成功时提交、异常时回滚的数据库 session。
        """
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _validate_upload(self, filename: str, raw_bytes: bytes) -> None:
        """校验 MVP 使用的最小上传规则集合。

        参数：
            filename: 原始上传文件名。
            raw_bytes: 原始文件内容。

        抛出：
            ValueError: 文件扩展名、大小或内容不合法。
        """
        if not filename.lower().endswith(".txt"):
            raise ValueError("只支持 TXT 文件")
        if len(raw_bytes) > self.settings.max_upload_bytes:
            raise ValueError("文件大小超过限制")
        if not raw_bytes:
            raise ValueError("上传文件不能为空")

    def _utc_now(self) -> datetime:
        """返回适配 SQLAlchemy DateTime 列的无时区 UTC 时间。"""
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def _persist_upload_copy(self, book_id: str, filename: str, raw_bytes: bytes) -> None:
        """在 ``data/uploads`` 下持久化一份上传源文件副本。

        参数：
            book_id: 用于给保存文件命名空间隔离的 UUID 字符串。
            filename: 原始文件名，仅在清洗后用于生成保存文件名。
            raw_bytes: 要写入的上传原始字节。
        """
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)
        upload_path = self.settings.uploads_dir / f"{book_id}_{safe_name}"
        upload_path.write_bytes(raw_bytes)

    def _read_upload_copy(self, book_id: str) -> bytes:
        """读回上传副本字节，供分析阶段重新解析。

        抛出：
            RuntimeError: 上传副本已丢失，无法重跑分析。
        """
        for path in self.settings.uploads_dir.glob(f"{book_id}_*"):
            if path.is_file():
                return path.read_bytes()
        raise RuntimeError("上传副本已丢失，无法重新分析")

    def _build_export_payload(self, book: Book, analyses: list[ChunkAnalysis]) -> dict[str, Any]:
        """构建 export endpoint 返回的 JSON payload。

        参数：
            book: 包含聚合书籍 metadata 的 ORM 行。
            analyses: 按导出顺序写入的 chunk 分析结果。

        返回：
            可 JSON 序列化的 dict，包含书籍 metadata、summary 和 chunks。
        """
        return {
            "book": {
                "id": book.id,
                "title": book.title,
                "original_filename": book.original_filename,
                "status": book.status,
                "stage": book.stage,
                "progress": book.progress,
                "created_at": book.created_at.isoformat(),
                "completed_at": book.completed_at.isoformat() if book.completed_at else None,
            },
            "summary": book.summary_json,
            "chunks": [asdict(analysis) for analysis in analyses],
        }

    def _ensure_parse_progress(
        self,
        session: Session,
        book: Book,
        filename_key: str,
        file_hash: str,
        total_chunks: int,
    ) -> BookParseProgress:
        """加载或创建解析断点记录。"""
        progress = self._get_parse_progress(session, book.id)
        if progress is None:
            progress = session.scalar(
                select(BookParseProgress).where(
                    BookParseProgress.filename_key == filename_key,
                    BookParseProgress.file_hash == file_hash,
                )
            )
        if progress is None:
            progress = BookParseProgress(
                book_id=book.id,
                filename_key=filename_key,
                file_hash=file_hash,
                total_chunks=total_chunks,
                current_node="start",
            )
            session.add(progress)
        progress.filename_key = filename_key
        progress.file_hash = file_hash
        progress.total_chunks = total_chunks
        progress.updated_at = self._utc_now()
        return progress

    def _get_parse_progress(self, session: Session, book_id: str) -> BookParseProgress | None:
        """按 book_id 获取解析断点记录。"""
        return session.scalar(
            select(BookParseProgress).where(BookParseProgress.book_id == book_id)
        )

    def _next_missing_chunk_order(
        self,
        chunks: list[Chunk],
        completed_keys: set[tuple[int, int]],
    ) -> int:
        """返回第一个尚未落库的全书 chunk 序号。"""
        for order, chunk in enumerate(chunks):
            if (chunk.chapter_no, chunk.chunk_index) not in completed_keys:
                return order
        return len(chunks)

    def _analysis_progress(self, completed_chunks: int, total_chunks: int) -> int:
        """把 chunk 完成数映射到书籍进度，最终完成仍由 done 阶段置为 100。"""
        if total_chunks <= 0:
            return 90
        return min(95, 10 + int(80 * completed_chunks / total_chunks))

    def _analysis_node(self, order: int, total_chunks: int, chunk: Chunk) -> str:
        """生成可记录在断点表中的分析节点名称。"""
        return (
            f"chunk:{order + 1}/{total_chunks} "
            f"chapter:{chunk.chapter_no} index:{chunk.chunk_index}"
        )

    async def _analyze_chunks_with_ordered_writes(
        self,
        *,
        book_id: str,
        book_title: str,
        chunks: list[Chunk],
        total_chunks: int,
        completed_keys: set[tuple[int, int]],
        emit: Callable[[str, str | None, str], None],
    ) -> None:
        """并发分析 chunk，但严格按原始顺序落库和写入向量库。"""
        pending_items = [
            (order, chunk)
            for order, chunk in enumerate(chunks)
            if (chunk.chapter_no, chunk.chunk_index) not in completed_keys
        ]
        if not pending_items:
            return

        work_queue: asyncio.Queue[tuple[int, Chunk]] = asyncio.Queue()
        for item in pending_items:
            work_queue.put_nowait(item)
        results: dict[int, ChunkAnalysis | Exception] = {}
        result_ready = asyncio.Condition()
        stop_requested = False

        async def _worker() -> None:
            nonlocal stop_requested
            while not stop_requested:
                try:
                    order, chunk = work_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                node = self._analysis_node(order, total_chunks, chunk)
                logger.info("开始分析节点: book_id=%s, %s", book_id, node)
                try:
                    result: ChunkAnalysis | Exception = await self.analyzer.analyze_chunk(chunk)
                except Exception as exc:
                    result = exc
                    stop_requested = True
                finally:
                    work_queue.task_done()
                async with result_ready:
                    results[order] = result
                    result_ready.notify_all()

        workers = [
            asyncio.create_task(_worker())
            for _ in range(min(ANALYSIS_CHUNK_CONCURRENCY, len(pending_items)))
        ]

        try:
            for order, chunk in pending_items:
                async with result_ready:
                    await result_ready.wait_for(lambda: order in results)
                    result = results.pop(order)

                node = self._analysis_node(order, total_chunks, chunk)
                self._mark_parse_running(book_id, node, order, total_chunks, chunk)
                if isinstance(result, Exception):
                    logger.error(
                        "分析节点失败: book_id=%s, %s",
                        book_id,
                        node,
                        exc_info=(type(result), result, result.__traceback__),
                    )
                    self._mark_parse_failed(book_id, node, result)
                    emit("analyzing", str(result), "failed")
                    raise result

                self._persist_chunk_analysis(book_id, result, order, total_chunks)
                await self.vector_store.upsert_documents(
                    book_id,
                    [self._analysis_vector_document(book_title, result)],
                )
                completed_keys.add((chunk.chapter_no, chunk.chunk_index))
                logger.info("分析节点完成并按顺序写入向量库: book_id=%s, %s", book_id, node)
                emit("analyzing", None, "running")
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    def _mark_parse_running(
        self,
        book_id: str,
        node: str,
        order: int,
        total_chunks: int,
        chunk: Chunk,
    ) -> None:
        """记录当前正在分析的节点。"""
        with self._session() as session:
            book = session.get(Book, book_id)
            progress = self._get_parse_progress(session, book_id)
            if book is not None:
                book.status = "running"
                book.stage = "analyzing"
                book.progress = self._analysis_progress(order, total_chunks)
                book.error_message = None
                book.updated_at = self._utc_now()
            if progress is not None:
                progress.status = "running"
                progress.stage = "analyzing"
                progress.current_node = node
                progress.next_chunk_order = order
                progress.total_chunks = total_chunks
                progress.last_chapter_no = chunk.chapter_no
                progress.last_chunk_index = chunk.chunk_index
                progress.error_message = None
                progress.updated_at = self._utc_now()

    def _persist_chunk_analysis(
        self,
        book_id: str,
        analysis: ChunkAnalysis,
        order: int,
        total_chunks: int,
    ) -> None:
        """把单个 chunk 分析结果写入数据库，并推进断点。"""
        with self._session() as session:
            book = session.get(Book, book_id)
            if book is None:
                raise KeyError(book_id)
            chunk = session.scalar(
                select(BookChunk).where(
                    BookChunk.book_id == book_id,
                    BookChunk.chapter_no == analysis.chapter_no,
                    BookChunk.chunk_index == analysis.chunk_index,
                )
            )
            if chunk is None:
                chunk = BookChunk(book_id=book_id, vector_id=analysis.vector_id)
                session.add(chunk)
            chunk.chapter_no = analysis.chapter_no
            chunk.chapter_title = analysis.chapter_title
            chunk.chunk_index = analysis.chunk_index
            chunk.chunk_type = "narrative"
            chunk.content = analysis.content
            chunk.summary = analysis.summary
            chunk.tags = analysis.tags
            chunk.tag_metadata = analysis.tag_metadata
            chunk.characters = analysis.characters
            chunk.importance = analysis.importance

            session.flush()
            completed_chunks = session.scalar(
                select(func.count(BookChunk.id)).where(BookChunk.book_id == book_id)
            ) or 0
            progress = self._get_parse_progress(session, book_id)
            if progress is not None:
                progress.completed_chunks = int(completed_chunks)
                progress.next_chunk_order = order + 1
                progress.current_node = (
                    f"chunk:{order + 1}/{total_chunks} "
                    f"chapter:{analysis.chapter_no} index:{analysis.chunk_index}"
                )
                progress.last_chapter_no = analysis.chapter_no
                progress.last_chunk_index = analysis.chunk_index
                progress.updated_at = self._utc_now()
            book.progress = self._analysis_progress(int(completed_chunks), total_chunks)
            book.updated_at = self._utc_now()

    def _mark_parse_failed(self, book_id: str, node: str, error: Exception) -> None:
        """记录失败节点和异常信息，保留已完成 chunks 供下次续跑。"""
        message = str(error)[:4000]
        with self._session() as session:
            book = session.get(Book, book_id)
            progress = self._get_parse_progress(session, book_id)
            if book is not None:
                book.status = "failed"
                book.stage = "failed"
                book.error_message = message
                book.updated_at = self._utc_now()
            if progress is not None:
                progress.status = "failed"
                progress.stage = "failed"
                progress.current_node = node
                progress.error_message = message
                progress.updated_at = self._utc_now()

    def _load_chunk_analyses(self, book_id: str) -> list[ChunkAnalysis]:
        """从数据库按稳定顺序加载所有 chunk 分析结果。"""
        with self._session() as session:
            chunks = session.scalars(
                select(BookChunk)
                .where(BookChunk.book_id == book_id)
                .order_by(BookChunk.chapter_no, BookChunk.chunk_index)
            ).all()
            return [self._chunk_row_to_analysis(chunk) for chunk in chunks]

    async def _upsert_chunk_vectors(
        self,
        book_id: str,
        book_title: str,
        chunks: list[BookChunk],
    ) -> None:
        """把已落库 chunks 增量补齐到向量库，跳过已存在的文档。"""
        existing_ids = self.vector_store.get_book_doc_ids(book_id)
        missing = [c for c in chunks if c.vector_id not in existing_ids]
        if not missing:
            logger.info("向量库已完整，跳过: book_id=%s", book_id)
            return
        await self.vector_store.upsert_documents(
            book_id,
            [self._chunk_row_vector_document(book_title, chunk) for chunk in missing],
        )

    def _chunk_row_to_analysis(self, chunk: BookChunk) -> ChunkAnalysis:
        """把数据库 chunk 行还原成分析对象。"""
        return ChunkAnalysis(
            chapter_no=chunk.chapter_no,
            chapter_title=chunk.chapter_title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            summary=chunk.summary,
            tags=list(chunk.tags),
            tag_metadata=dict(chunk.tag_metadata or {}),
            importance=float(chunk.importance),
            characters=list(chunk.characters or []),
            vector_id=chunk.vector_id,
        )

    def _analysis_vector_document(
        self,
        book_title: str,
        analysis: ChunkAnalysis,
    ) -> dict[str, Any]:
        """把分析结果转换为向量库文档。"""
        return {
            "id": analysis.vector_id,
            "text": analysis.content,
            "metadata": {
                "book_title": book_title,
                "chunk_id": analysis.vector_id,
                "chapter_no": analysis.chapter_no,
                "chapter_title": analysis.chapter_title,
                "chunk_index": analysis.chunk_index,
                "summary": analysis.summary,
                "importance": analysis.importance,
                "tags": analysis.tags,
                "tag_metadata": analysis.tag_metadata,
                "character_names": analysis.characters,
            },
        }

    def _chunk_row_vector_document(self, book_title: str, chunk: BookChunk) -> dict[str, Any]:
        """把数据库 chunk 行转换为向量库文档。"""
        return self._analysis_vector_document(book_title, self._chunk_row_to_analysis(chunk))

    async def _ensure_book_index(self, book: Book, session: Session) -> None:
        """根据数据库 chunks 重建缺失的向量文档。

        参数：
            book: 需要保证 chunks 可搜索的 Book。
            session: 用于加载 chunk 行的活动 SQLAlchemy session。
        """
        if self.vector_store.has_book(book.id):
            return
        # 向量索引是可恢复缓存：只要数据库 chunk 还在，就可以按 book_id 重建。
        logger.info("向量索引缺失, 从数据库重建: book_id=%s", book.id)
        chunks = session.scalars(
            select(BookChunk).where(BookChunk.book_id == book.id).order_by(BookChunk.chapter_no, BookChunk.chunk_index)
        ).all()
        await self.vector_store.add_documents(
            book.id,
            [self._chunk_row_vector_document(book.title, chunk) for chunk in chunks],
        )

    async def _ensure_completed_books_index(self) -> None:
        """确保已完成书籍在向量库中可被 corpus 级检索。"""
        with self._session() as session:
            books = list(session.scalars(select(Book).where(Book.status == "completed")).all())
            for book in books:
                await self._ensure_book_index(book, session)

    def _book_summary_lookup(self) -> dict[str, dict[str, Any]]:
        """加载书籍摘要 lookup，供 corpus hit 补充 book profile。"""
        with self._session() as session:
            books = session.scalars(select(Book)).all()
            return {
                book.id: {
                    "title": book.title,
                    "summary": book.summary_json or {},
                    "style_profile": book.style_profile_json or {},
                }
                for book in books
            }

    def _format_corpus_hit(
        self,
        hit: dict[str, Any],
        books: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """把向量命中转换成 MCP tool 返回的稳定 JSON 结构。"""
        metadata = hit.get("metadata", {})
        book_id = metadata.get("book_id", "")
        book_info = books.get(book_id, {})
        summary = book_info.get("summary") or {}
        return {
            "book_id": book_id,
            "book_title": metadata.get("book_title") or book_info.get("title", ""),
            "chapter_no": metadata.get("chapter_no", 0),
            "chapter_title": metadata.get("chapter_title", ""),
            "chunk_index": metadata.get("chunk_index", 0),
            "summary": metadata.get("summary", ""),
            "content": hit.get("text", ""),
            "tags": list(metadata.get("tags", [])),
            "tag_metadata": dict(metadata.get("tag_metadata", {})),
            "importance": float(metadata.get("importance", 0.5)),
            "score": hit.get("score", 0),
            "book_profile": summary.get("book_profile", {}),
            "style_profile": book_info.get("style_profile") or summary.get("style_profile", {}),
        }

    def _stable_asset_id(self, kind: str, *parts: Any) -> str:
        """为派生语料资产构建稳定的公开标识。"""
        raw = ":".join([kind, *(str(part) for part in parts)])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _as_positive_int(self, value: Any, default: int) -> int:
        """标准化来源位置，并确保格式错误的数据不会造成影响。"""
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return default
        return normalized if normalized > 0 else default

    def _infer_plot_stage(self, chapter_no: int, chapter_count: int) -> str:
        """将章节位置映射为稳定的四阶段情节标签。"""
        if chapter_count <= 0:
            return "development"
        ratio = chapter_no / chapter_count
        if ratio <= 0.2:
            return "opening"
        if ratio <= 0.7:
            return "development"
        if ratio <= 0.9:
            return "climax"
        return "ending"

    def _plot_beats(
        self,
        stage: str,
        tags: list[str],
        metadata: dict[str, Any],
    ) -> list[str]:
        """描述可迁移的情节节拍，不复现来源情节事实。"""
        opening = {
            "opening": "建立人物目标、场景约束与读者预期",
            "development": "承接既有目标并制造新的执行阻力",
            "climax": "收束支线压力并集中核心矛盾",
            "ending": "兑现主要因果并展示选择后的余波",
        }.get(stage, "推进当前阶段的核心目标")
        scene_type = str(metadata.get("scene_type") or "关键场景")
        mood = str(metadata.get("mood") or "目标情绪")
        tag_text = "、".join(tags[:3]) if tags else "信息差与行动阻力"
        return [
            opening,
            f"在{scene_type}中通过{tag_text}逐步加压",
            f"以{mood}变化制造转折，并留下下一步行动动机",
        ]

    def _conflict_escalation(self, tags: list[str]) -> str:
        """将来源标签映射为可复用的冲突升级描述。"""
        if "冲突" in tags:
            return "让目标、阻力和代价同步上升，避免只增加表面事件。"
        if "伏笔" in tags:
            return "先扩大信息差，再让隐藏信息改变角色选择的成本。"
        if "成长" in tags:
            return "用更困难的选择迫使角色暴露并修正原有缺陷。"
        return "由局部阻力升级到目标受阻，再引出不可回避的选择。"

    def _turning_point(self, stage: str, tags: list[str]) -> str:
        """返回适配情节阶段的转折技法。"""
        trigger = "关键信息" if "伏笔" in tags else "行动结果"
        if stage == "climax":
            return f"让{trigger}改变胜负条件，并迫使角色立即表态。"
        if stage == "ending":
            return f"让{trigger}完成因果闭环，同时保留必要余韵。"
        return f"让{trigger}打破原计划，产生明确的新目标。"

    def _outcome_effect(self, stage: str) -> str:
        """描述某一情节阶段希望为读者带来的效果。"""
        return {
            "opening": "建立期待并形成继续阅读的问题。",
            "development": "扩大因果链与角色选择空间。",
            "climax": "集中兑现压力、信息和情绪。",
            "ending": "完成核心承诺并留下变化后的状态。",
        }.get(stage, "推动主线进入下一状态。")

    def _asset_source(self, hit: dict[str, Any]) -> dict[str, Any]:
        """从语料命中结果中提取稳定的来源引用。"""
        return {
            "book_id": hit.get("book_id", ""),
            "book_title": hit.get("book_title", ""),
            "chapter_no": hit.get("chapter_no", 0),
            "chapter_title": hit.get("chapter_title", ""),
        }

    def _character_role(self, card: dict[str, Any], index: int) -> str:
        """标准化常见角色标签，并以排序结果作为确定性兜底。"""
        raw_role = str(card.get("role_type") or card.get("role") or "").lower()
        mappings = {
            "主角": "protagonist",
            "protagonist": "protagonist",
            "反派": "antagonist",
            "敌人": "antagonist",
            "antagonist": "antagonist",
            "配角": "supporting",
            "supporting": "supporting",
        }
        return mappings.get(raw_role, "protagonist" if index == 0 else "supporting")

    def _character_field(
        self,
        card: dict[str, Any],
        *keys: str,
        default: str,
    ) -> str:
        """从不同分析器版本中读取首个有效的角色卡字段。"""
        for key in keys:
            value = card.get(key)
            if value is None or value == "" or value == [] or value == {}:
                continue
            if isinstance(value, (list, dict)):
                return json.dumps(value, ensure_ascii=False)
            return str(value)
        return default

    def _default_external_goal(self, role_type: str) -> str:
        """当规则角色卡只有提及时，提供角色层级的兜底内容。"""
        return {
            "protagonist": "解决核心矛盾并推动主线目标。",
            "antagonist": "维护与主角相冲突的目标或秩序。",
            "supporting": "协助、检验或阻碍核心目标的实现。",
        }.get(role_type, "推动与其立场一致的目标。")

    def _anonymize_character_text(
        self,
        value: Any,
        current_name: str,
        all_names: list[str],
    ) -> str:
        """移除来源角色姓名，并限制原型字段长度。"""
        text = str(value or "").strip()
        for name in sorted((name for name in all_names if name), key=len, reverse=True):
            replacement = "该角色" if name == current_name else "其他角色"
            text = text.replace(name, replacement)
        return self._clip_asset_text(text, 180)

    def _character_traits(self, card: dict[str, Any]) -> list[str]:
        """将可选特征字段标准化为简短列表。"""
        value = card.get("traits") or card.get("personality") or card.get("tags") or []
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[,，、;；]", value) if item.strip()][:6]
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()][:6]
        return []

    def _foreshadow_event_type(self, tags: list[str], text: str) -> str | None:
        """根据明确措辞将文本块分类为伏笔埋设或回收候选。"""
        payoff_markers = ("揭晓", "真相", "原来", "回收", "兑现", "揭开", "谜底", "暴露")
        plant_markers = ("暗示", "伏笔", "埋下", "线索", "异常", "神秘", "隐约")
        if any(marker in text for marker in payoff_markers):
            return "payoff"
        if "伏笔" in tags or any(marker in text for marker in plant_markers):
            return "plant"
        return None

    def _foreshadow_subtlety(self, importance: float) -> str:
        """使用重要性作为表现隐晦程度的稳定代理指标。"""
        if importance >= 0.8:
            return "explicit"
        if importance >= 0.55:
            return "balanced"
        return "subtle"

    def _plant_technique(self, metadata: dict[str, Any]) -> str:
        """根据场景元数据描述伏笔埋设技法，不包含来源专有名词。"""
        scene_type = str(metadata.get("scene_type") or "日常行动")
        return f"在{scene_type}中嵌入可被首次阅读合理忽略的异常细节。"

    def _surface_function(self, metadata: dict[str, Any]) -> str:
        """描述线索在当前情节中的直接叙事作用。"""
        mood = str(metadata.get("mood") or "当前情绪")
        return f"先服务于{mood}氛围或眼前行动，使线索不显突兀。"

    def _payoff_method(self, metadata: dict[str, Any]) -> str:
        """描述伏笔回收方法，不复现来源事实。"""
        scene_type = str(metadata.get("scene_type") or "关键场景")
        return f"在{scene_type}中重释先前细节，让新事实改变角色判断。"

    def _pair_foreshadow_events(
        self,
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """在同一本来源书籍中，将早期伏笔与后续回收配对。"""
        by_book: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            book_id = str((event.get("source") or {}).get("book_id") or "")
            by_book.setdefault(book_id, []).append(event)

        pairs: list[dict[str, Any]] = []
        for book_id, book_events in by_book.items():
            plants = sorted(
                (event for event in book_events if event["pattern_type"] == "plant"),
                key=lambda item: item["chapter_no"],
            )
            payoffs = sorted(
                (event for event in book_events if event["pattern_type"] == "payoff"),
                key=lambda item: item["chapter_no"],
            )
            used_plants: set[str] = set()
            for payoff in payoffs:
                eligible = [
                    plant
                    for plant in plants
                    if plant["chapter_no"] < payoff["chapter_no"]
                    and plant["event_id"] not in used_plants
                ]
                if not eligible:
                    continue
                plant = max(
                    eligible,
                    key=lambda item: (
                        self._text_relevance(item["_search_text"], payoff["_search_text"]),
                        item["chapter_no"],
                    ),
                )
                used_plants.add(plant["event_id"])
                chapter_gap = payoff["chapter_no"] - plant["chapter_no"]
                span = "short" if chapter_gap <= 3 else "medium" if chapter_gap <= 10 else "long"
                pairs.append(
                    {
                        "pattern_id": self._stable_asset_id(
                            "foreshadow-pair",
                            book_id,
                            plant["event_id"],
                            payoff["event_id"],
                        ),
                        "pattern_type": "pair",
                        "subtlety": plant["subtlety"],
                        "payoff_span": span,
                        "plant_technique": plant["plant_technique"],
                        "surface_function": plant["surface_function"],
                        "hidden_information": plant["hidden_information"],
                        "payoff_method": payoff["payoff_method"],
                        "source": [plant["source"], payoff["source"]],
                        "chapter_no": payoff["chapter_no"],
                        "_relevance": max(plant["_relevance"], payoff["_relevance"]),
                    }
                )
        return pairs

    def _text_relevance(self, query: str, text: str) -> float:
        """返回不依赖外部库且适用于派生元数据的词法评分。"""
        query_terms = self._search_terms(query)
        if not query_terms:
            return 0.0
        text_terms = self._search_terms(text)
        overlap = len(query_terms & text_terms) / len(query_terms)
        phrase_bonus = 1.0 if query.strip().lower() in text.lower() else 0.0
        return round(overlap + phrase_bonus, 4)

    def _search_terms(self, text: str) -> set[str]:
        """对拉丁字母词和中文一元词、二元词分词，用于本地排序。"""
        terms: set[str] = set()
        for token in re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", str(text).lower()):
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                terms.update(token)
                terms.update(token[index:index + 2] for index in range(len(token) - 1))
            else:
                terms.add(token)
        return terms

    def _select_diverse_assets(
        self,
        assets: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        """保持排序结果，同时每本书最多保留两个资产。"""
        if limit <= 0:
            return []
        selected: list[dict[str, Any]] = []
        selected_indexes: set[int] = set()
        per_book: dict[str, int] = {}
        for max_per_book in (1, 2):
            for index, asset in enumerate(assets):
                if index in selected_indexes:
                    continue
                book_key = self._asset_book_key(asset)
                if per_book.get(book_key, 0) >= max_per_book:
                    continue
                selected.append(asset)
                selected_indexes.add(index)
                per_book[book_key] = per_book.get(book_key, 0) + 1
                if len(selected) >= limit:
                    return selected
        return selected

    def _asset_book_key(self, asset: dict[str, Any]) -> str:
        """从单来源或配对资产中读取主要书籍 ID。"""
        source = asset.get("source") or {}
        if isinstance(source, list):
            source = source[0] if source else {}
        if not isinstance(source, dict):
            return "unknown"
        return str(source.get("book_id") or source.get("book_title") or "unknown")

    def _clip_asset_text(self, text: str, limit: int) -> str:
        """压缩并截断派生文本字段，以满足 MCP 响应长度预算。"""
        compact = " ".join(str(text).split())
        if len(compact) <= limit:
            return compact
        return compact[:limit].rstrip() + "..."

    def _export_payload_path(self, book_id: str) -> Path:
        """返回指定 book id 的标准 JSON 导出路径。"""
        return self.settings.exports_dir / f"{book_id}.json"

    def _delete_uploaded_copies(self, book_id: str) -> None:
        """删除属于指定 book id 的已保存上传文件。"""
        for path in self.settings.uploads_dir.glob(f"{book_id}_*"):
            self._delete_file_if_exists(path)

    def _delete_file_if_exists(self, path: Path) -> None:
        """当文件存在时删除该路径。"""
        if path.exists():
            path.unlink()
