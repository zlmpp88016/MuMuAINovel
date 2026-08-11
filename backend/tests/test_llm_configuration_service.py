"""Tests for LLM configuration routing helpers."""

import pytest

from app.services.llm_configuration_service import (
    choose_config_id,
    mask_api_key,
    module_key_from_path,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/wizard-stream/world-building", "world_building"),
        ("/api/wizard-stream/outline", "outline"),
        ("/api/outlines/project-1/generate-stream", "outline"),
        ("/api/chapters/chapter-1/generate-stream", "chapter"),
        ("/api/characters/generate", "character"),
        ("/api/organizations/generate-stream", "organization"),
        ("/api/polish", "polish"),
        ("/api/projects/project-1", None),
    ],
)
def test_module_key_from_path(path, expected):
    assert module_key_from_path(path) == expected


def test_choose_config_id_uses_request_then_module_then_default():
    assert choose_config_id(" request ", "module", "default") == "request"
    assert choose_config_id(None, " module ", "default") == "module"
    assert choose_config_id("", None, " default ") == "default"
    assert choose_config_id(" ", None, None) is None


@pytest.mark.parametrize(
    ("api_key", "expected"),
    [
        (None, ""),
        ("", ""),
        ("short", "***"),
        ("12345678", "***"),
        ("sk-test-secret", "sk-...cret"),
    ],
)
def test_mask_api_key_never_returns_plaintext(api_key, expected):
    masked = mask_api_key(api_key)
    assert masked == expected
    if api_key:
        assert masked != api_key
