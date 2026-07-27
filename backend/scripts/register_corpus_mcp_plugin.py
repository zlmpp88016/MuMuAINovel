"""Register the default global book-analyzer corpus MCP plugin."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database import get_engine
from app.models.mcp_plugin import MCPPlugin
from app.services.corpus_bridge import CORPUS_PLUGIN_NAME, GLOBAL_CORPUS_USER_ID


async def register(user_id: str, server_url: str) -> None:
    """Create or update a corpus MCP plugin row."""
    engine = await get_engine(user_id)
    async_session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session_factory() as session:
        result = await session.execute(
            select(MCPPlugin).where(
                MCPPlugin.user_id == user_id,
                MCPPlugin.plugin_name == CORPUS_PLUGIN_NAME,
            )
        )
        plugin = result.scalar_one_or_none()
        if plugin is None:
            plugin = MCPPlugin(
                user_id=user_id,
                plugin_name=CORPUS_PLUGIN_NAME,
                display_name="小说语料库",
                description="analyzer-book corpus MCP service",
                plugin_type="http",
                server_url=server_url,
                category="corpus",
                enabled=True,
                config={"timeout": 60.0},
            )
            session.add(plugin)
        else:
            plugin.display_name = plugin.display_name or "小说语料库"
            plugin.description = plugin.description or "analyzer-book corpus MCP service"
            plugin.plugin_type = "http"
            plugin.server_url = server_url
            plugin.category = "corpus"
            plugin.enabled = True
            plugin.config = plugin.config or {"timeout": 60.0}
        await session.commit()


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Register book-analyzer corpus MCP plugin.")
    parser.add_argument(
        "--user-id",
        default=GLOBAL_CORPUS_USER_ID,
        help="Target backend user id. Omit for global default.",
    )
    parser.add_argument("--server-url", default="http://localhost:8765/mcp")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    asyncio.run(register(args.user_id, args.server_url))
    scope = "全局默认" if args.user_id == GLOBAL_CORPUS_USER_ID else f"用户 {args.user_id}"
    print(f"已注册 book-analyzer corpus MCP 插件（{scope}）：{args.server_url}")


if __name__ == "__main__":
    main()
