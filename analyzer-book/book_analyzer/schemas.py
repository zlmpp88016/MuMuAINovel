"""API 请求和响应模型。

这些 Pydantic schema 是 HTTP 边界的稳定契约，避免直接把 ORM 对象或内部
分析对象暴露给调用方。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BookListItem(BaseModel):
    """书籍列表页使用的轻量响应项。"""

    id: str
    title: str
    original_filename: str
    status: str
    stage: str
    progress: int
    created_at: datetime
    updated_at: datetime
    error_message: str | None = None
    completed_chunks: int | None = None
    total_chunks: int | None = None


class BookDetail(BookListItem):
    """单本书详情响应，包含汇总结果和处理状态。"""

    completed_at: datetime | None = None
    error_message: str | None = None
    summary_json: dict[str, Any]
    chunks_count: int


class SearchRequest(BaseModel):
    """书内搜索请求体。

    参数：
        query: 用户输入的搜索文本，不能为空。
        limit: 最大返回条数，限制在 1 到 20 之间。
    """

    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class SearchHit(BaseModel):
    """单条搜索命中。"""

    chunk_id: str
    chapter_no: int
    chapter_title: str
    chunk_index: int
    summary: str
    content: str
    tags: list[str]
    tag_metadata: dict[str, Any] = Field(default_factory=dict)
    score: float


class SearchResponse(BaseModel):
    """书内搜索响应。"""

    book_id: str
    query: str
    total: int
    hits: list[SearchHit]


class DeleteResponse(BaseModel):
    """删除书籍后的确认响应。"""

    book_id: str
    deleted: bool


class ExportResponse(BaseModel):
    """导出接口响应，包含文件路径和 JSON payload。"""

    book_id: str
    export_path: str
    payload: dict[str, Any]
