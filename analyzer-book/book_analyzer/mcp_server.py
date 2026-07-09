"""MCP server exposing corpus retrieval tools."""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from book_analyzer.analysis import create_analyzer
from book_analyzer.config import Settings
from book_analyzer.db import (
    create_session_factory,
    create_sqlalchemy_engine,
    ensure_schema_compatibility,
)
from book_analyzer.llm_service import AIService
from book_analyzer.models import Base
from book_analyzer.service import BookAnalysisService
from book_analyzer.vector_store import create_vector_store


logger = logging.getLogger(__name__)

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
async def corpus_search_reference_passages(
    query: str,
    scene_type: str | None = None,
    mood: str | None = None,
    genre: str | None = None,
    style_tags: list[str] | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Search corpus passages for style and scene-writing references."""
    results = await book_service.search_reference_passages(
        query=query,
        scene_type=scene_type,
        mood=mood,
        genre=genre,
        style_tags=style_tags,
        limit=limit,
    )
    return {"query": query, "total": len(results), "passages": results}


@mcp.tool()
async def corpus_get_highlight_passages(
    query: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Search high-quality passages for AI denoising calibration."""
    results = await book_service.search_highlight_passages(query=query, limit=limit)
    return {"query": query, "total": len(results), "passages": results}


@mcp.tool()
async def corpus_get_style_profile(
    genre: str | None = None,
    mood: str | None = None,
    sample: int = 5,
) -> dict[str, Any]:
    """Return aggregated writing style profiles from completed corpus books."""
    return book_service.get_style_profile(genre=genre, mood=mood, sample=sample)


def main() -> None:
    """Run the streamable HTTP MCP server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger.info("Starting book-analyzer MCP server at http://%s:%s/mcp", settings.mcp_host, settings.mcp_port)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
