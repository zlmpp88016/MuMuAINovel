"""独立 book-analyzer 的 HTTP API 层。

本模块保持 request/response 处理足够薄：路由函数只负责把 FastAPI 输入
转换为 service 调用，并把领域错误映射为 HTTP 响应。
业务逻辑保留在 ``service.py`` 中。
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from book_analyzer.schemas import BookDetail, BookListItem, DeleteResponse, ExportResponse, SearchRequest, SearchResponse
from book_analyzer.service import _progress_event


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
    """上传一本 TXT 书籍，仅保存与解析，不触发分析。

    参数：
        request: 用于访问 service 的 FastAPI request。
        file: 上传的 TXT 文件。service 会校验扩展名、大小和空内容。

    返回：
        处于 ``pending`` / ``uploaded`` 状态的书籍详情，前端据此再决定何时开始分析。

    抛出：
        HTTPException: 上传文件不合法时返回 ``400``。
    """
    try:
        payload = await file.read()
        logger.info("API 上传: filename=%s, size=%d bytes", file.filename, len(payload))
        book = await _service(request).upload_book(file.filename or "unknown.txt", payload)
        logger.info("API 上传完成: book_id=%s, title=%s, status=%s", book.id, book.title, book.status)
        return _book_to_detail(book)
    except ValueError as exc:
        logger.warning("API 上传校验失败: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{book_id}/analyze")
async def analyze_book(book_id: str, request: Request) -> StreamingResponse:
    """订阅单本书分析进度的 SSE 流；如后台任务未在跑则启动。

    使用 GET 以兼容浏览器 EventSource（仅支持 GET）。分析任务在 service
    端独立运行，**断开此 SSE 连接不会取消后台任务**——刷新页面后只需
    再次访问该端点：任务在跑则订阅最新进度（重放最近一次），已完成则
    返回终态事件。前端因此可随时重连查看正在后台推进的分析。

    事件类型：``progress``（进行中）、``done``（完成）、``error``（失败）。

    抛出：
        HTTPException:书籍不存在返回 ``404``。
    """
    service = _service(request)
    try:
        book = service.get_book(book_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="书籍不存在") from exc

    # 已完成的书不必再启后台任务，直接返回终态事件以免重复 build summary。
    if book.status == "completed":
        async def _completed_stream():
            event = _progress_event("done", 100, len(book.chunks), len(book.chunks), "completed", "已完成")
            yield f"event: done\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            _completed_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 确保后台任务在跑：未跑则启动一个（从断点恢复），已运行则复用。
    handle = await service.start_analysis(book_id)
    queue = await handle.broadcaster.subscribe()

    async def _event_stream():
        """把订阅队列事件格式化为 SSE 文本帧；客户端断连只退订。"""
        try:
            while True:
                # 后台任务结束后队列不再有新事件，监听双方以判断收尾。
                item = await queue.get()
                event_type = (
                    "error" if item.get("status") in {"failed", "interrupted"}
                    else "done" if item.get("status") == "completed"
                    else "progress"
                )
                yield f"event: {event_type}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                if event_type in {"done", "error"}:
                    break
        finally:
            handle.broadcaster.unsubscribe(queue)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    """把 ORM ``Book`` 行转换为轻量列表 schema，附带断点进度字段。"""
    completed_chunks, total_chunks = None, None
    try:
        progress = book.parse_progress
        if progress is not None:
            completed_chunks = progress.completed_chunks
            total_chunks = progress.total_chunks
    except Exception:  # noqa: BLE001 — 书籍可能已脱离 session，跳过断点字段。
        progress = None
    return BookListItem(
        id=book.id,
        title=book.title,
        original_filename=book.original_filename,
        status=book.status,
        stage=book.stage,
        progress=book.progress,
        created_at=book.created_at,
        updated_at=book.updated_at,
        error_message=book.error_message,
        completed_chunks=completed_chunks,
        total_chunks=total_chunks,
    )


def _book_to_detail(book) -> BookDetail:
    """把 ORM ``Book`` 行转换为详情响应 schema。"""
    return BookDetail(
        **_book_to_list_item(book).model_dump(),
        completed_at=book.completed_at,
        summary_json=book.summary_json or {},
        chunks_count=len(book.chunks),
    )
