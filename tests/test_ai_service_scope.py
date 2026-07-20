from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from api_models import PresetRenderRequest
from application import job_entrypoints
from application.http.queue_route_handlers import handle_api_external_render_preset
from infrastructure.ai.service_scope import (
    INTERNAL_SCOPE,
    ScopedProviderKeys,
    attach_ai_service_scope,
    current_ai_service_scope,
    gemini_pool_for_scope,
    load_external_scope_key_map,
    load_scoped_provider_keys,
    provider_key_source,
    validate_external_scope_keys,
)
from request_helpers import require_ai_service_scope


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_external_scope_keys_resolve_one_scope_per_credential():
    scope_keys = load_external_scope_key_map(
        {
            "KR_AI_DESIGNER_RENDER_CLIENT_KEYS": "designer-key",
            "KR_AI_CONSULTANT_RENDER_CLIENT_KEYS": "consultant-key",
            "GLOBAL_AI_DESIGNER_RENDER_CLIENT_KEYS": "global-designer-key",
            "GLOBAL_AI_CONSULTANT_RENDER_CLIENT_KEYS": "global-consultant-key",
        }
    )

    assert require_ai_service_scope(
        _FakeRequest({"x-api-key": "designer-key"}),
        {"external"},
        False,
        {"internal-key"},
        set(),
        scope_keys,
    ) == "kr_ai_designer"
    assert require_ai_service_scope(
        _FakeRequest({"Authorization": "Bearer global-consultant-key"}),
        {"external"},
        False,
        {"internal-key"},
        set(),
        scope_keys,
    ) == "global_ai_consultant"
    assert require_ai_service_scope(
        _FakeRequest({"x-api-key": "internal-key"}),
        {"internal"},
        False,
        {"internal-key"},
        set(),
        scope_keys,
    ) == INTERNAL_SCOPE


def test_duplicate_external_scope_key_fails_validation():
    scope_keys = load_external_scope_key_map(
        {
            "KR_AI_DESIGNER_RENDER_CLIENT_KEYS": "duplicate-key",
            "GLOBAL_AI_DESIGNER_RENDER_CLIENT_KEYS": "duplicate-key",
        }
    )

    with pytest.raises(RuntimeError, match="multiple AI service scopes"):
        validate_external_scope_keys(scope_keys)


def test_external_route_persists_resolved_scope_into_enqueued_payload():
    deps = MagicMock()
    deps.redis_url = "redis://example"
    deps.local_inline_queue_enabled = False
    deps.rq_queue_render = "render"
    deps.api_auth_disabled = False
    deps.internal_api_keys = {"internal-key"}
    deps.external_api_keys = set()
    deps.external_scope_keys = {"kr_ai_designer": {"designer-key"}}
    deps.require_ai_service_scope = require_ai_service_scope
    deps.load_preset_map.return_value = {"preset-1": {"room": "livingroom", "style": "modern", "variant": "1"}}
    deps.build_external_preset_job.return_value = (
        {"require_details": True, "render": {"audience": "external"}},
        {"room": "livingroom", "style": "modern", "variant": "1"},
    )
    deps.enqueue_job.return_value = (SimpleNamespace(id="job-1"), None)

    response = handle_api_external_render_preset(
        PresetRenderRequest(image_url="https://example.com/room.png", preset_id="preset-1"),
        _FakeRequest({"x-api-key": "designer-key"}),
        deps=deps,
    )

    assert response.status_code == 200
    enqueued_payload = deps.enqueue_job.call_args.args[1]
    assert enqueued_payload["ai_service_scope"] == "kr_ai_designer"
    assert enqueued_payload["render"]["ai_service_scope"] == "kr_ai_designer"


def test_missing_or_wrong_scoped_credential_fails_closed():
    with pytest.raises(HTTPException) as exc:
        require_ai_service_scope(
            _FakeRequest({"x-api-key": "unknown-key"}),
            {"external"},
            False,
            {"internal-key"},
            set(),
            {"kr_ai_designer": {"designer-key"}},
        )

    assert exc.value.status_code == 401


def test_worker_sets_scope_context_from_payload(monkeypatch):
    captured = {}

    def fake_run_render_job(payload, **kwargs):
        captured["scope"] = current_ai_service_scope()
        captured["payload"] = payload
        return {"result_url": "https://cdn.example/render.png"}

    monkeypatch.setattr(
        job_entrypoints,
        "_services",
        lambda: SimpleNamespace(
            materialize_input=lambda *args: None,
            normalize_audience=lambda audience: audience or "external",
            render_room=lambda **kwargs: None,
        ),
    )
    monkeypatch.setattr(job_entrypoints, "run_render_job", fake_run_render_job)

    result = job_entrypoints.job_render(
        attach_ai_service_scope({"audience": "external", "file_path": "https://example.com/room.png"}, "kr_ai_consultant"),
        persist_result=False,
    )

    assert result["result_url"] == "https://cdn.example/render.png"
    assert captured["scope"] == "kr_ai_consultant"


def test_scoped_provider_keys_prefer_selected_scope_and_do_not_cross_scopes():
    keys = load_scoped_provider_keys(
        {
            "OPENAI_API_KEY_KR_AI_DESIGNER": "openai-designer",
            "OPENAI_API_KEY_KR_AI_CONSULTANT": "openai-consultant",
            "GEMINI_API_KEY_KR_AI_DESIGNER": "gemini-designer",
            "GEMINI_API_KEY_KR_AI_CONSULTANT": "gemini-consultant",
            "OPENAI_API_KEY": "openai-legacy",
            "NANOBANANA_API_KEY": "gemini-legacy",
        }
    )
    keys = ScopedProviderKeys(
        openai_by_scope=keys.openai_by_scope,
        gemini_by_scope=keys.gemini_by_scope,
        legacy_openai_key=keys.legacy_openai_key,
        legacy_gemini_pool=["gemini-legacy"],
        legacy_fallback_enabled=True,
    )
    logger = MagicMock()

    assert provider_key_source("kr_ai_designer", "openai", keys, logger) == ("openai-designer", "scoped")
    assert provider_key_source("kr_ai_consultant", "openai", keys, logger) == ("openai-consultant", "scoped")
    assert gemini_pool_for_scope("kr_ai_designer", keys, logger) == (["gemini-designer"], "scoped")
    assert gemini_pool_for_scope("kr_ai_consultant", keys, logger) == (["gemini-consultant"], "scoped")


def test_provider_key_missing_scope_fails_after_fallback_disabled():
    keys = ScopedProviderKeys(
        openai_by_scope={},
        gemini_by_scope={},
        legacy_openai_key="openai-legacy",
        legacy_gemini_pool=["gemini-legacy"],
        legacy_fallback_enabled=False,
    )

    with pytest.raises(RuntimeError, match="kr_ai_designer"):
        provider_key_source("kr_ai_designer", "openai", keys, MagicMock())
