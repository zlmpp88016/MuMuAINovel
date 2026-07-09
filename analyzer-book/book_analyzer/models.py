"""SQLAlchemy ORM models.

这里只定义 book-analyzer 自己的元数据表，不依赖主项目 backend 的模型。
向量内容保存在 Chroma 或 fallback store 中，数据库只保存可回显和可重建索引的元数据。
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all local tables."""


def utc_now() -> datetime:
    """返回无时区 UTC 时间，匹配当前 DateTime 列定义。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Book(Base):
    """上传书籍的主记录。

    保存文件身份、处理状态、整书 summary JSON，以及与 chunk 的一对多关系。
    """

    __tablename__ = "books"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    stage: Mapped[str] = mapped_column(String(20), nullable=False, default="uploaded")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    style_profile_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    chunks: Mapped[list["BookChunk"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="BookChunk.chapter_no, BookChunk.chunk_index",
    )
    parse_progress: Mapped["BookParseProgress | None"] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        uselist=False,
    )


class BookChunk(Base):
    """书籍片段记录。

    每条记录对应一个分析 chunk，保存摘要、标签、标签元数据、重要性和向量 ID。
    ``vector_id`` 用于和向量库文档关联。
    """

    __tablename__ = "book_chunks"
    __table_args__ = (
        UniqueConstraint("book_id", "chapter_no", "chunk_index", name="uq_book_chunk_position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chapter_title: Mapped[str] = mapped_column(String(200), nullable=False, default="正文")
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(50), nullable=False, default="narrative")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tag_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    characters: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    vector_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)

    book: Mapped[Book] = relationship(back_populates="chunks")


class BookParseProgress(Base):
    """单本上传文件的解析断点记录。

    ``filename_key`` 和 ``file_hash`` 一起标识一次可恢复解析；进度字段只记录
    已经成功持久化的 chunk 边界，避免重跑时重复入库或漏建向量索引。
    """

    __tablename__ = "book_parse_progress"
    __table_args__ = (
        UniqueConstraint("filename_key", "file_hash", name="uq_book_parse_progress_file"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("books.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    filename_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    stage: Mapped[str] = mapped_column(String(30), nullable=False, default="parsing")
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_chunk_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_node: Mapped[str] = mapped_column(String(100), nullable=False, default="queued")
    last_chapter_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    book: Mapped[Book] = relationship(back_populates="parse_progress")
