"""提供语料检索工具的 MCP 服务。"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from book_analyzer.analysis import create_analyzer
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


logger = logging.getLogger(__name__)

MCP_SCHEMA_VERSION = "1.0"
QueryText = Annotated[str, Field(min_length=1, max_length=2000)]
ResultLimit = Annotated[int, Field(ge=1, le=10)]


def _log_mcp_interface(
    display_name: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """为 MCP 接口记录统一的中文入口和出口日志。"""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        interface_name = f"{display_name}（{func.__name__}）"

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started_at = time.perf_counter()
                logger.info("MCP 接口入口：%s", interface_name)
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    elapsed_ms = (time.perf_counter() - started_at) * 1000
                    logger.error(
                        "MCP 接口出口：%s；状态：失败；异常类型：%s；耗时：%.2f 毫秒",
                        interface_name,
                        type(exc).__name__,
                        elapsed_ms,
                    )
                    raise
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                logger.info(
                    "MCP 接口出口：%s；状态：成功；耗时：%.2f 毫秒",
                    interface_name,
                    elapsed_ms,
                )
                return result

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            started_at = time.perf_counter()
            logger.info("MCP 接口入口：%s", interface_name)
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                logger.error(
                    "MCP 接口出口：%s；状态：失败；异常类型：%s；耗时：%.2f 毫秒",
                    interface_name,
                    type(exc).__name__,
                    elapsed_ms,
                )
                raise
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            logger.info(
                "MCP 接口出口：%s；状态：成功；耗时：%.2f 毫秒",
                interface_name,
                elapsed_ms,
            )
            return result

        return sync_wrapper

    return decorator


settings = Settings.from_env()
settings.ensure_directories()

engine = create_sqlalchemy_engine(settings.database_url)
Base.metadata.create_all(engine)
ensure_schema_compatibility(engine)

session_factory = create_session_factory(engine)
ai_service = AIService(settings)
tagging_ai_service = AIService(
    settings,
    api_provider=settings.tagging_ai_provider,
    api_key=settings.tagging_api_key,
    api_base_url=settings.tagging_base_url,
    default_model=settings.tagging_model,
    default_temperature=settings.tagging_temperature,
    default_max_tokens=settings.tagging_max_tokens,
)
vector_store = create_vector_store(settings)
analyzer = create_analyzer(settings, ai_service, tagging_ai_service=tagging_ai_service)
book_service = BookAnalysisService(
    settings=settings,
    session_factory=session_factory,
    vector_store=vector_store,
    analyzer=analyzer,
)

mcp = FastMCP(
    "book-analyzer-corpus",
    host=settings.mcp_host,
    port=settings.mcp_port,
)


@mcp.tool()
@_log_mcp_interface("检索参考段落")
async def corpus_search_reference_passages(
    query: QueryText,
    scene_type: str | None = None,
    mood: str | None = None,
    genre: str | None = None,
    style_tags: list[str] | None = None,
    limit: ResultLimit = 5,
) -> dict[str, Any]:
    """检索可供风格与场景写作参考的语料片段。"""
    results = await book_service.search_reference_passages(
        query=query,
        scene_type=scene_type,
        mood=mood,
        genre=genre,
        style_tags=style_tags,
        limit=limit,
    )
    filters_applied = {
        key: value
        for key, value in {
            "scene_type": scene_type,
            "mood": mood,
            "genre": genre,
            "style_tags": style_tags,
        }.items()
        if value
    }
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "corpus_version": book_service.get_corpus_version(),
        "query": query,
        "filters_applied": filters_applied,
        "total": len(results),
        "passages": results,
    }


@mcp.tool()
@_log_mcp_interface("检索高质量段落")
async def corpus_get_highlight_passages(
    query: QueryText,
    limit: ResultLimit = 5,
) -> dict[str, Any]:
    """检索用于校准 AI 文本去味效果的高质量片段。"""
    results = await book_service.search_highlight_passages(query=query, limit=limit)
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "corpus_version": book_service.get_corpus_version(),
        "query": query,
        "filters_applied": {},
        "total": len(results),
        "passages": results,
    }


@mcp.tool()
@_log_mcp_interface("获取写作风格画像")
async def corpus_get_style_profile(
    genre: str | None = None,
    mood: str | None = None,
    sample: ResultLimit = 5,
) -> dict[str, Any]:
    """返回从已完成语料书籍中聚合的写作风格画像。"""
    payload = book_service.get_style_profile(genre=genre, mood=mood, sample=sample)
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "corpus_version": book_service.get_corpus_version(),
        "filters_applied": {
            key: value
            for key, value in {"genre": genre, "mood": mood}.items()
            if value
        },
        **payload,
    }


@mcp.tool()
@_log_mcp_interface("获取参考标签目录")
def corpus_list_reference_tag_catalog(genre: str | None = None) -> dict[str, Any]:
    """返回当前完成语料中可精确选择的场景、情绪和参考标签。"""
    payload = book_service.get_reference_tag_catalog(genre=genre)
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "corpus_version": book_service.get_corpus_version(),
        "filters_applied": {"genre": genre} if genre else {},
        **payload,
    }


@mcp.tool()
@_log_mcp_interface("检索情节模式")
async def corpus_search_plot_patterns(
    query: QueryText,
    genre: str | None = None,
    plot_stage: Literal["opening", "development", "climax", "ending"] | None = None,
    pattern_tags: list[str] | None = None,
    limit: ResultLimit = 5,
) -> dict[str, Any]:
    """返回标准化情节节拍，不复现来源书籍的具体情节。"""
    patterns = await book_service.search_plot_patterns(
        query=query,
        genre=genre,
        plot_stage=plot_stage,
        pattern_tags=pattern_tags,
        limit=limit,
    )
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "corpus_version": book_service.get_corpus_version(),
        "query": query,
        "filters_applied": {
            key: value
            for key, value in {
                "genre": genre,
                "plot_stage": plot_stage,
                "pattern_tags": pattern_tags,
            }.items()
            if value
        },
        "total": len(patterns),
        "patterns": patterns,
    }


@mcp.tool()
@_log_mcp_interface("检索角色原型")
def corpus_search_character_archetypes(
    query: QueryText,
    genre: str | None = None,
    role_type: Literal["protagonist", "supporting", "antagonist"] | None = None,
    arc_type: str | None = None,
    limit: ResultLimit = 5,
) -> dict[str, Any]:
    """返回从语料角色卡提炼的匿名角色结构。"""
    archetypes = book_service.search_character_archetypes(
        query=query,
        genre=genre,
        role_type=role_type,
        arc_type=arc_type,
        limit=limit,
    )
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "corpus_version": book_service.get_corpus_version(),
        "query": query,
        "filters_applied": {
            key: value
            for key, value in {
                "genre": genre,
                "role_type": role_type,
                "arc_type": arc_type,
            }.items()
            if value
        },
        "total": len(archetypes),
        "archetypes": archetypes,
    }


@mcp.tool()
@_log_mcp_interface("检索伏笔模式")
async def corpus_find_foreshadow_patterns(
    query: QueryText,
    pattern_type: Literal["plant", "payoff", "pair"],
    genre: str | None = None,
    subtlety: Literal["subtle", "balanced", "explicit"] | None = None,
    payoff_span: Literal["short", "medium", "long"] | None = None,
    limit: ResultLimit = 5,
) -> dict[str, Any]:
    """返回有来源依据的伏笔埋设、回收或配对技法。"""
    patterns = await book_service.find_foreshadow_patterns(
        query=query,
        pattern_type=pattern_type,
        genre=genre,
        subtlety=subtlety,
        payoff_span=payoff_span,
        limit=limit,
    )
    return {
        "schema_version": MCP_SCHEMA_VERSION,
        "corpus_version": book_service.get_corpus_version(),
        "query": query,
        "filters_applied": {
            key: value
            for key, value in {
                "pattern_type": pattern_type,
                "genre": genre,
                "subtlety": subtlety,
                "payoff_span": payoff_span,
            }.items()
            if value
        },
        "total": len(patterns),
        "patterns": patterns,
    }


def main() -> None:
    """运行支持流式传输的 HTTP MCP 服务。"""
    configure_logging()
    logger.info(
        "正在启动图书分析 MCP 服务：http://%s:%s/mcp",
        settings.mcp_host,
        settings.mcp_port,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
