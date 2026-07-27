"""FastAPI 应用工厂。

应用启动时初始化本系统自己的数据库、AI 客户端、tagging 小模型客户端、
向量库和业务服务；关闭时释放 HTTP 客户端与数据库 engine。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from book_analyzer.analysis import create_analyzer
from book_analyzer.api import router
from book_analyzer.config import Settings
from book_analyzer.db import (
    create_session_factory,
    create_sqlalchemy_engine,
    ensure_schema_compatibility,
)
from book_analyzer.llm_service import AIService
from book_analyzer.logging_config import configure_logging
from book_analyzer.models import Base
from book_analyzer.service import BookAnalysisService
from book_analyzer.vector_store import create_vector_store


STATIC_DIR = Path(__file__).resolve().parent / "static"

configure_logging()

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建配置好的 FastAPI 应用。

    参数：
        settings: 可选运行配置。测试会传入临时路径配置；生产运行默认从
            ``BOOK_ANALYZER_*`` 环境变量读取。

    返回：
        已挂载路由和 lifespan 初始化逻辑的 FastAPI 实例。
    """
    resolved_settings = settings or Settings.from_env()
    resolved_settings.ensure_directories()

    # Engine 和 SessionFactory 在应用级复用；单个请求内再创建短生命周期 Session。
    engine = create_sqlalchemy_engine(resolved_settings.database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """初始化并清理应用级依赖。

        参数：
            app: 当前 FastAPI 应用实例，依赖对象会挂载到 ``app.state``。
        """
        Base.metadata.create_all(engine)
        ensure_schema_compatibility(engine)

        # 主分析模型和段落打标签模型分开创建，便于主模型用强模型、
        # tags/scene/mood 等轻量任务接低成本小模型。
        ai_service = AIService(resolved_settings)
        tagging_ai_service = AIService(
            resolved_settings,
            api_provider=resolved_settings.tagging_ai_provider,
            api_key=resolved_settings.tagging_api_key,
            api_base_url=resolved_settings.tagging_base_url,
            default_model=resolved_settings.tagging_model,
            default_temperature=resolved_settings.tagging_temperature,
            default_max_tokens=resolved_settings.tagging_max_tokens,
        )
        vector_store = create_vector_store(resolved_settings)
        analyzer = create_analyzer(
            resolved_settings,
            ai_service,
            tagging_ai_service=tagging_ai_service,
        )

        # API 层通过 app.state 取服务实例，避免在每个路由里重复装配依赖。
        app.state.settings = resolved_settings
        app.state.book_service = BookAnalysisService(
            settings=resolved_settings,
            session_factory=session_factory,
            vector_store=vector_store,
            analyzer=analyzer,
        )
        # 上次进程可能被硬杀，留下 status=running 的书籍：启动时标记为中断，
        # 前端展示「继续」按钮，从断点恢复而非僵在 running。
        interrupted = app.state.book_service.interrupt_running_books()
        if interrupted:
            logger.info("启动时标记 %d 本 running 书籍为中断", interrupted)
        try:
            yield
        finally:
            # FastAPI 关闭时释放长生命周期 HTTP client 和数据库连接池。
            await tagging_ai_service.close()
            await ai_service.close()
            engine.dispose()

    app = FastAPI(title=resolved_settings.app_name, lifespan=lifespan)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index_page() -> FileResponse:
        """返回内置上传与预览页面。"""
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
