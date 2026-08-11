"""Shared helpers for resolving user LLM configuration selections."""

from __future__ import annotations

from typing import Optional


MODULE_KEYS = {
    "world_building",
    "outline",
    "chapter",
    "character",
    "organization",
    "polish",
}

MODULE_LABELS = {
    "world_building": "世界观",
    "outline": "小说大纲",
    "chapter": "章节编写",
    "character": "角色设定",
    "organization": "组织设定",
    "polish": "文章润色",
}


def module_key_from_path(path: str) -> Optional[str]:
    """Map a generation route to a stable module binding key."""

    normalized_path = path.rstrip("/").lower()
    if "/wizard-stream/world-building" in normalized_path:
        return "world_building"
    if "/wizard-stream/outline" in normalized_path or "/outlines/" in normalized_path:
        return "outline"
    if "/chapters/" in normalized_path:
        return "chapter"
    if "/characters/" in normalized_path or normalized_path.endswith("/characters"):
        return "character"
    if "/organizations/" in normalized_path or normalized_path.endswith("/organizations"):
        return "organization"
    if "/polish" in normalized_path:
        return "polish"
    return None


def choose_config_id(
    request_config_id: Optional[str],
    module_config_id: Optional[str],
    default_config_id: Optional[str],
) -> Optional[str]:
    """Apply the request > module > default precedence rule."""

    for config_id in (request_config_id, module_config_id, default_config_id):
        normalized = (config_id or "").strip()
        if normalized:
            return normalized
    return None


def mask_api_key(api_key: Optional[str]) -> str:
    """Return a stable display value without exposing the secret."""

    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:3]}...{api_key[-4:]}"
