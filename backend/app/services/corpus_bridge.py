"""Lightweight bridge to the book-analyzer corpus MCP plugin."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logger import get_logger


logger = get_logger(__name__)

CORPUS_PLUGIN_NAME = "book-analyzer-corpus"
GLOBAL_CORPUS_USER_ID = "__global__"


class CorpusBridge:
    """Call analyzer-book corpus tools through user override or global MCP plugin."""

    def __init__(self, user_id: str, db_session: AsyncSession) -> None:
        self.user_id = user_id
        self.db_session = db_session

    async def get_reference_for_chapter(
        self,
        chapter_outline: str,
        scene_type: str | None = None,
        mood: str | None = None,
        genre: str | None = None,
        limit: int = 5,
    ) -> str:
        """Return formatted corpus references for chapter generation."""
        query = chapter_outline.strip()
        if not query:
            return ""
        passages = await self._call_tool(
            "corpus_search_reference_passages",
            {
                "query": query,
                "scene_type": scene_type,
                "mood": mood,
                "genre": genre,
                "limit": limit,
            },
        )
        style_profile = await self._call_tool(
            "corpus_get_style_profile",
            {"genre": genre, "mood": mood, "sample": 3},
        )
        return self._format_chapter_context(passages, style_profile)

    async def get_highlight_for_denoise(self, query: str, limit: int = 5) -> str:
        """Return formatted highlight passages for AI denoising."""
        if not query.strip():
            return ""
        payload = await self._call_tool(
            "corpus_get_highlight_passages",
            {"query": query[:1000], "limit": limit},
        )
        passages = self._extract_payload(payload).get("passages", [])
        if not passages:
            return ""
        lines = [
            "【参考范文 - 该类型真实作家的笔触】",
            "以下片段仅用于校准遣词节奏和细节密度，保留原文情节与设定，不复制范文内容。",
        ]
        for index, passage in enumerate(passages[:limit], 1):
            lines.append(
                f"{index}. 《{passage.get('book_title', '未知作品')}》"
                f"第{passage.get('chapter_no', '?')}章："
                f"{self._clip(passage.get('content', ''), 220)}"
            )
        return "\n".join(lines)

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call corpus MCP tool and degrade to empty payload on failure."""
        plugin = await self._get_enabled_plugin()
        if plugin is None:
            return {}
        try:
            from app.mcp.registry import mcp_registry

            runtime_user_id = self._plugin_runtime_user_id(plugin)
            if not mcp_registry.get_client(runtime_user_id, plugin.plugin_name):
                loaded = await mcp_registry.load_plugin(plugin)
                if not loaded:
                    logger.warning("语料库 MCP 插件加载失败: %s", plugin.plugin_name)
                    return {}
            result = await mcp_registry.call_tool(
                runtime_user_id,
                plugin.plugin_name,
                tool_name,
                {key: value for key, value in arguments.items() if value is not None},
            )
            return self._extract_payload(result)
        except Exception as exc:
            logger.warning("语料库 MCP 调用失败: %s.%s: %s", CORPUS_PLUGIN_NAME, tool_name, exc)
            return {}

    async def _get_enabled_plugin(self) -> Any | None:
        """Find user override first, then the global analyzer-book corpus plugin."""
        from app.models.mcp_plugin import MCPPlugin

        result = await self.db_session.execute(
            select(MCPPlugin).where(
                MCPPlugin.user_id == self.user_id,
                MCPPlugin.plugin_name == CORPUS_PLUGIN_NAME,
                MCPPlugin.enabled == True,  # noqa: E712
            )
        )
        plugin = result.scalar_one_or_none()
        if plugin is not None:
            return plugin

        result = await self.db_session.execute(
            select(MCPPlugin).where(
                MCPPlugin.user_id == GLOBAL_CORPUS_USER_ID,
                MCPPlugin.plugin_name == CORPUS_PLUGIN_NAME,
                MCPPlugin.enabled == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    def _plugin_runtime_user_id(self, plugin: Any) -> str:
        """Return the registry namespace used by this plugin row."""
        return getattr(plugin, "user_id", None) or self.user_id

    def _extract_payload(self, result: Any) -> dict[str, Any]:
        """Normalize MCP SDK tool results into a dict."""
        if isinstance(result, dict):
            return result
        content = getattr(result, "content", None)
        if content and isinstance(content, list):
            for item in content:
                data = getattr(item, "data", None)
                if isinstance(data, dict):
                    return data
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    try:
                        import json

                        payload = json.loads(text)
                        if isinstance(payload, dict):
                            return payload
                    except Exception:
                        continue
        if hasattr(result, "model_dump"):
            dumped = result.model_dump()
            if isinstance(dumped, dict):
                return dumped
        return {}

    def _format_chapter_context(
        self,
        passages_payload: dict[str, Any],
        style_payload: dict[str, Any],
    ) -> str:
        """Format corpus references for prompt injection."""
        passages = passages_payload.get("passages", [])
        profiles = style_payload.get("profiles", [])
        if not passages and not profiles:
            return ""

        lines = [
            "【语料库 - 写作参考（非情节来源，仅供借鉴笔法与质感）】",
        ]
        if profiles:
            lines.append("风格画像：")
            for item in profiles[:3]:
                profile = item.get("profile", {})
                techniques = profile.get("signature_techniques") or []
                lines.append(
                    f"- 《{item.get('title', '未知作品')}》："
                    f"句式{profile.get('sentence_rhythm', '未知')}，"
                    f"对话密度{profile.get('dialogue_density', '未知')}，"
                    f"描写密度{profile.get('description_density', '未知')}，"
                    f"技法：{'、'.join(techniques[:4]) if techniques else '无'}"
                )

        if passages:
            lines.append("参考范文（学习句式节奏与细节肌理，勿抄情节/人名/设定）：")
            for index, passage in enumerate(passages[:5], 1):
                lines.append(
                    f"{index}. 《{passage.get('book_title', '未知作品')}》"
                    f"第{passage.get('chapter_no', '?')}章 "
                    f"{passage.get('chapter_title', '')}："
                    f"{self._clip(passage.get('content', ''), 260)}"
                )
            lines.append("重要：范文仅用于校准写作手感，不得搬运其情节、人名、设定到本书。")
        return "\n".join(lines)

    def _clip(self, text: str, limit: int) -> str:
        """Clip text for prompt-size control."""
        compact = " ".join(str(text).split())
        if len(compact) <= limit:
            return compact
        return compact[:limit].rstrip() + "..."
