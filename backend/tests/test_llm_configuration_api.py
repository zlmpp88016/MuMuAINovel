"""Focused tests for LLM configuration resolution and safe responses."""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import settings as settings_api


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _RoutingDB:
    def __init__(self, config=None, binding=None, default_config=None, legacy_settings=None):
        self.config = config
        self.binding = binding
        self.default_config = default_config
        self.legacy_settings = legacy_settings

    async def execute(self, statement):
        sql = str(statement)
        if "llm_module_bindings" in sql:
            return _Result(self.binding)
        if "llm_configurations" in sql:
            if "LIMIT" in sql.upper():
                return _Result(self.default_config)
            return _Result(self.config)
        if "settings" in sql:
            return _Result(self.legacy_settings)
        raise AssertionError(f"Unexpected query: {sql}")

    async def commit(self):
        return None

    async def refresh(self, value):
        return None

    def add(self, value):
        return None


def _request(path="/api/outlines/generate", payload=None):
    body = json.dumps(payload or {}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
    )


def _config(config_id="config-1", **overrides):
    values = {
        "id": config_id,
        "user_id": "user-1",
        "name": "Test configuration",
        "api_provider": "openai",
        "api_key": "secret-key",
        "api_base_url": "https://example.test/v1",
        "llm_model": "model-a",
        "temperature": 0.7,
        "max_tokens": 2000,
        "enabled": True,
        "is_default": False,
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_configuration_response_does_not_expose_plaintext_key():
    response = settings_api._configuration_response(_config())

    assert response.api_key_masked == "sec...-key"
    assert response.api_key_configured is True
    assert "api_key" not in response.__class__.model_fields


def test_get_user_ai_service_prefers_request_configuration(monkeypatch):
    captured = {}

    def fake_factory(**kwargs):
        captured.update(kwargs)
        return "service"

    monkeypatch.setattr(settings_api, "create_user_ai_service", fake_factory)
    result = asyncio.run(
        settings_api.get_user_ai_service(
            request=_request("/api/chapters/chapter-1/generate-stream", {"llm_config_id": "config-1"}),
            user=SimpleNamespace(user_id="user-1"),
            db=_RoutingDB(config=_config()),
        )
    )

    assert result == "service"
    assert captured["model_name"] == "model-a"
    assert captured["api_key"] == "secret-key"


def test_get_user_ai_service_uses_module_binding_when_request_has_no_override(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        settings_api,
        "create_user_ai_service",
        lambda **kwargs: captured.update(kwargs) or "service",
    )
    result = asyncio.run(
        settings_api.get_user_ai_service(
            request=_request("/api/wizard-stream/world-building", {}),
            user=SimpleNamespace(user_id="user-1"),
            db=_RoutingDB(
                config=_config("world-config", llm_model="world-model"),
                binding=SimpleNamespace(llm_config_id="world-config"),
            ),
        )
    )

    assert result == "service"
    assert captured["model_name"] == "world-model"


def test_get_user_ai_service_uses_user_default_before_legacy_settings(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        settings_api,
        "create_user_ai_service",
        lambda **kwargs: captured.update(kwargs) or "service",
    )

    result = asyncio.run(
        settings_api.get_user_ai_service(
            request=_request("/api/projects/project-1/generate", {}),
            user=SimpleNamespace(user_id="user-1"),
            db=_RoutingDB(
                default_config=_config("default-config", llm_model="default-model"),
                legacy_settings=SimpleNamespace(
                    api_provider="openai",
                    api_key="legacy-key",
                    api_base_url="https://legacy.test/v1",
                    llm_model="legacy-model",
                    temperature=0.7,
                    max_tokens=1000,
                ),
            ),
        )
    )

    assert result == "service"
    assert captured["model_name"] == "default-model"


def test_get_user_ai_service_falls_back_to_legacy_settings(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        settings_api,
        "create_user_ai_service",
        lambda **kwargs: captured.update(kwargs) or "service",
    )

    result = asyncio.run(
        settings_api.get_user_ai_service(
            request=_request("/api/projects/project-1/generate", {}),
            user=SimpleNamespace(user_id="user-1"),
            db=_RoutingDB(
                legacy_settings=SimpleNamespace(
                    api_provider="openai",
                    api_key="legacy-key",
                    api_base_url="https://legacy.test/v1",
                    llm_model="legacy-model",
                    temperature=0.7,
                    max_tokens=1000,
                ),
            ),
        )
    )

    assert result == "service"
    assert captured["api_key"] == "legacy-key"
    assert captured["model_name"] == "legacy-model"


def test_invalid_explicit_configuration_is_rejected():
    with pytest.raises(HTTPException) as error:
        asyncio.run(
            settings_api.get_user_ai_service(
                request=_request("/api/outlines/generate", {"llm_config_id": "missing"}),
                user=SimpleNamespace(user_id="user-1"),
                db=_RoutingDB(config=None),
            )
        )

    assert error.value.status_code == 404


def test_legacy_settings_response_masks_key_and_preserves_it_on_update():
    legacy = SimpleNamespace(
        api_provider="openai",
        api_key="secret-key",
        api_base_url="https://example.test/v1",
        llm_model="model-a",
        temperature=0.7,
        max_tokens=2000,
        preferences=None,
        id="settings-1",
        user_id="user-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    settings_api._preserve_masked_legacy_key(
        legacy,
        {"api_key": settings_api.mask_api_key(legacy.api_key)},
    )

    assert settings_api._legacy_settings_response(legacy).api_key == "sec...-key"
