"""独立 book-analyzer 的 HTTP API 层。

本模块保持 request/response 处理足够薄：路由函数只负责把 FastAPI 输入
转换为 service 调用，并把领域错误映射为 HTTP 响应。
业务逻辑保留在 ``service.py`` 中。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from book_analyzer.schemas import BookDetail, BookListItem, DeleteResponse, ExportResponse, SearchRequest, SearchResponse


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/books", tags=["books"])


def _service(request: Request):
    """返回应用级 ``BookAnalysisService`` 实例。

    参数：
        request: 携带已初始化 application state 的 FastAPI request。

    返回：
        应用启动时创建的 service 对象。
    """
    return request.app.state.book_service


@router.post("/upload", response_model=BookDetail, status_code=status.HTTP_201_CREATED)
async def upload_book(request: Request, file: UploadFile = File(...)) -> BookDetail:
    """上传并同步分析一本 TXT 书籍。

    参数：
        request: 用于访问 service 的 FastAPI request。
        file: 上传的 TXT 文件。service 会校验扩展名、大小和空内容。

    返回：
        新建或复用的书籍详情响应。

    抛出：
        HTTPException: 上传文件不合法时返回 ``400``。
    """
    try:
        payload = await file.read()
        logger.info("API 上传: filename=%s, size=%d bytes", file.filename, len(payload))
        book = await _service(request).ingest_upload(file.filename or "unknown.txt", payload)
        logger.info("API 上传完成: book_id=%s, title=%s", book.id, book.title)
        return _book_to_detail(book)
    except ValueError as exc:
        logger.warning("API 上传校验失败: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[BookListItem])
def list_books(request: Request) -> list[BookListItem]:
    """列出所有已分析书籍。

    参数：
        request: 用于访问 service 的 FastAPI request。

    返回：
        按最近更新时间倒序排列的书籍列表。
    """
    books = _service(request).list_books()
    logger.info("API 列表: 返回 %d 本书", len(books))
    return [_book_to_list_item(book) for book in books]


@router.get("/{book_id}", response_model=BookDetail)
def get_book(book_id: str, request: Request) -> BookDetail:
    """获取单本书及其聚合状态。

    参数：
        book_id: 目标书籍的 UUID 字符串。
        request: 用于访问 service 的 FastAPI request。

    返回：
        书籍状态、summary JSON 和 chunk 数量。

    抛出：
        HTTPException: 书籍不存在时返回 ``404``。
    """
    try:
        book = _service(request).get_book(book_id)
        logger.info("API 详情: book_id=%s, title=%s", book_id, book.title)
        return _book_to_detail(book)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="书籍不存在") from exc


@router.post("/{book_id}/search", response_model=SearchResponse)
async def search_book(book_id: str, body: SearchRequest, request: Request) -> SearchResponse:
    """在一本已索引书籍内搜索。

    参数：
        book_id: 目标书籍的 UUID 字符串。
        body: 搜索关键词和结果数量限制。
        request: 用于访问 service 的 FastAPI request。

    返回：
        按相关性排序的 chunk 命中结果，包含摘要、tags 和 tag metadata。

    抛出：
        HTTPException: 书籍不存在时返回 ``404``。
    """
    try:
        hits = await _service(request).search_book(book_id, query=body.query, limit=body.limit)
        logger.info("API 搜索: book_id=%s, query=\"%s\", 命中=%d", book_id, body.query, len(hits))
        return SearchResponse(book_id=book_id, query=body.query, total=len(hits), hits=hits)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="书籍不存在") from exc


@router.get("/{book_id}/export", response_model=ExportResponse)
def export_book(book_id: str, request: Request) -> ExportResponse:
    """构建并返回单本书的 JSON 导出。

    参数：
        book_id: 目标书籍的 UUID 字符串。
        request: 用于访问 settings 和 service state 的 FastAPI request。

    返回：
        导出文件路径，以及内存中的导出 payload。

    抛出：
        HTTPException: 书籍不存在时返回 ``404``。
    """
    try:
        payload = _service(request).export_book(book_id)
        export_path = request.app.state.settings.exports_dir / f"{book_id}.json"
        logger.info("API 导出: book_id=%s", book_id)
        return ExportResponse(book_id=book_id, export_path=str(export_path), payload=payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="书籍不存在") from exc


@router.delete("/{book_id}", response_model=DeleteResponse)
def delete_book(book_id: str, request: Request) -> DeleteResponse:
    """删除一本书及其所有派生产物。

    参数：
        book_id: 目标书籍的 UUID 字符串。
        request: 用于访问 service 的 FastAPI request。

    返回：
        删除确认响应。

    抛出：
        HTTPException: 书籍不存在时返回 ``404``。
    """
    try:
        _service(request).delete_book(book_id)
        logger.info("API 删除: book_id=%s", book_id)
        return DeleteResponse(book_id=book_id, deleted=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="书籍不存在") from exc


def _book_to_list_item(book) -> BookListItem:
    """把 ORM ``Book`` 行转换为轻量列表 schema。"""
    return BookListItem(
        id=book.id,
        title=book.title,
        original_filename=book.original_filename,
        status=book.status,
        stage=book.stage,
        progress=book.progress,
        created_at=book.created_at,
        updated_at=book.updated_at,
    )


def _book_to_detail(book) -> BookDetail:
    """把 ORM ``Book`` 行转换为详情响应 schema。"""
    return BookDetail(
        **_book_to_list_item(book).model_dump(),
        completed_at=book.completed_at,
        error_message=book.error_message,
        summary_json=book.summary_json or {},
        chunks_count=len(book.chunks),
    )
