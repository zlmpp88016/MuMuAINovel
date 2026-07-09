"""上传、分析、搜索和导出的核心编排 service。

API 层会把所有流程决策委托到这里。该 service 负责 metadata 数据库事务、
调用 analyzer、写入向量索引，并让导出的 JSON 文件与数据库状态保持同步。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

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


class BookAnalysisService:
    """独立 book-analyzer 的应用 service。

    参数：
        settings: 运行时配置，例如 chunk 大小和存储路径。
        session_factory: 绑定 metadata DB 的 SQLAlchemy session factory。
        vector_store: 搜索索引实现，可以是 Chroma 或 fallback。
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

    def list_books(self) -> list[Book]:
        """返回所有书籍，并按最近更新时间倒序排列。

        返回：
            ORM ``Book`` 行，不附带较重的导出 payload。
        """
        with self._session() as session:
            stmt = select(Book).order_by(Book.updated_at.desc())
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
        """根据上传的 TXT 文件创建或复用书籍记录。

        流程会先创建书籍和断点占位，再逐个分析 chunk。每个 chunk 成功后
        立即写入 metadata 行并增量写入向量库，便于异常后从断点恢复。

        参数：
            filename: 原始上传文件名，最终只保存 basename。
            raw_bytes: 上传请求中的 TXT 原始字节。

        返回：
            已完成分析或之前已导入过的 ``Book`` 行。

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
                # 命中重复文件时直接复用已有分析结果，只补齐可能丢失的向量索引。
                logger.info("检测到重复文件 (hash=%s...), 复用已有记录 %s", file_hash[:12], existing.id)
                await self._ensure_book_index(existing, session)
                return existing

        logger.info("开始解析上传文件: filename=%s, hash=%s...", filename_key, file_hash[:12])
        decoded = decode_text_file(raw_bytes)
        parsed_book = parse_book_text(decoded, title_hint=filename)
        title = parsed_book.title or filename_to_title(filename)
        logger.info("文本解析完成: 书名=%s, 章节数=%d, 总字符=%d",
                     title, len(parsed_book.chapters), len(decoded))
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
                    status="running",
                    stage="parsing",
                    progress=10,
                    summary_json={},
                )
                session.add(book)
                session.flush()
                # 保留原始上传副本，便于后续排查解析问题或重跑分析。
                self._persist_upload_copy(book.id, filename, raw_bytes)
                logger.info("上传副本已持久化: book_id=%s", book.id)
            else:
                logger.info("检测到未完成解析, 从断点恢复: book_id=%s, status=%s", book.id, book.status)
                book.title = title
                book.original_filename = filename_key
                book.status = "running"
                book.stage = "analyzing"
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
            progress.status = "running"
            progress.stage = "analyzing"
            progress.current_node = "resume" if completed_keys else "start"
            progress.error_message = None
            book.stage = "analyzing"
            book.progress = self._analysis_progress(progress.completed_chunks, len(chunks))
            book.updated_at = self._utc_now()
            session.flush()
            existing_chunks = list(book.chunks)
            book_id = book.id

        if existing_chunks:
            logger.info("恢复已完成 chunk 向量索引: book_id=%s, chunks=%d", book_id, len(existing_chunks))
            await self._upsert_chunk_vectors(book_id, title, existing_chunks)

        completed_keys = {(chunk.chapter_no, chunk.chunk_index) for chunk in existing_chunks}
        for order, chunk in enumerate(chunks):
            chunk_key = (chunk.chapter_no, chunk.chunk_index)
            if chunk_key in completed_keys:
                continue

            node = self._analysis_node(order, len(chunks), chunk)
            self._mark_parse_running(book_id, node, order, len(chunks), chunk)
            try:
                logger.info("开始分析节点: book_id=%s, %s", book_id, node)
                analysis = await self.analyzer.analyze_chunk(chunk)
                self._persist_chunk_analysis(book_id, analysis, order, len(chunks))
                await self.vector_store.upsert_documents(
                    book_id,
                    [self._analysis_vector_document(title, analysis)],
                )
                completed_keys.add(chunk_key)
                logger.info("分析节点完成并写入向量库: book_id=%s, %s", book_id, node)
            except Exception as exc:
                logger.exception("分析节点失败: book_id=%s, %s", book_id, node)
                self._mark_parse_failed(book_id, node, exc)
                raise

        analyses = self._load_chunk_analyses(book_id)
        try:
            logger.info("开始生成整书摘要: book_id=%s, chunks=%d", book_id, len(analyses))
            summary_json = await self.analyzer.build_book_summary(title, parsed_book.chapters, analyses)
            logger.info("整书摘要生成完成: book_id=%s", book_id)
        except Exception as exc:
            logger.exception("整书摘要节点失败: book_id=%s", book_id)
            self._mark_parse_failed(book_id, "book_summary", exc)
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
            logger.info("上传分析全流程完成: book_id=%s, title=%s", book.id, book.title)
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
        filters: dict[str, Any] = {}
        if scene_type:
            filters["scene_type"] = scene_type
        if mood:
            filters["mood"] = mood
        if style_tags:
            filters["tags"] = style_tags

        hits = await self.vector_store.search_corpus(
            query=query,
            limit=limit,
            filters=filters or None,
        )
        books = self._book_summary_lookup()
        results = [self._format_corpus_hit(hit, books) for hit in hits]
        if genre:
            genre_results = [
                item for item in results
                if item.get("book_profile", {}).get("genre") == genre
            ]
            if genre_results:
                return genre_results
        return results

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
