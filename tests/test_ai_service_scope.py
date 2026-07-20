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
    ai_service_scope,
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


def test_legacy_external_credential_does_not_resolve_an_ai_service_scope():
    with pytest.raises(HTTPException) as exc:
        require_ai_service_scope(
            _FakeRequest({"x-api-key": "legacy-external-key"}),
            {"external"},
            False,
            {"internal-key"},
            {"legacy-external-key"},
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
        }
    )
    logger = MagicMock()

    assert provider_key_source("kr_ai_designer", "openai", keys, logger) == ("openai-designer", "scoped")
    assert provider_key_source("kr_ai_consultant", "openai", keys, logger) == ("openai-consultant", "scoped")
    assert gemini_pool_for_scope("kr_ai_designer", keys, logger) == (["gemini-designer"], "scoped")
    assert gemini_pool_for_scope("kr_ai_consultant", keys, logger) == (["gemini-consultant"], "scoped")


def test_provider_key_missing_scope_fails_closed():
    keys = ScopedProviderKeys(
        openai_by_scope={},
        gemini_by_scope={},
    )

    with pytest.raises(RuntimeError, match="kr_ai_designer"):
        provider_key_source("kr_ai_designer", "openai", keys, MagicMock())


def test_legacy_provider_envs_and_nanobanana_alias_are_ignored():
    keys = load_scoped_provider_keys(
        {
            "OPENAI_API_KEY": "legacy-openai",
            "NANOBANANA_API_KEY": "legacy-gemini",
            "NANOBANANA_API_KEY_KR_AI_DESIGNER": "legacy-scoped-alias",
        }
    )

    with pytest.raises(RuntimeError, match="kr_ai_designer"):
        provider_key_source("kr_ai_designer", "openai", keys, MagicMock())
    with pytest.raises(RuntimeError, match="kr_ai_designer"):
        gemini_pool_for_scope("kr_ai_designer", keys, MagicMock())


class _CaptureLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(message % args)

    def warning(self, message, *args):
        self.messages.append(message % args)


def test_brief_mode_still_logs_safe_gemini_key_attribution(monkeypatch):
    import main

    secret = "gemini-secret-value"
    fake_logger = _CaptureLogger()
    monkeypatch.setattr(main, "LOG_BRIEF", True)
    monkeypatch.setattr(main, "logger", fake_logger)
    monkeypatch.setattr(
        main,
        "SCOPED_PROVIDER_KEYS",
        ScopedProviderKeys(
            openai_by_scope={},
            gemini_by_scope={"kr_ai_designer": [secret]},
        ),
    )
    monkeypatch.setattr(
        main,
        "call_gemini_with_failover_impl",
        lambda *args, **kwargs: SimpleNamespace(text="ok"),
    )

    with ai_service_scope("kr_ai_designer"):
        response = main._call_gemini_generation("gemini-test-model", ["prompt"], {}, {})

    log_output = "\n".join(fake_logger.messages)
    assert response.text == "ok"
    assert "[AIKey] scope=kr_ai_designer provider=gemini model=gemini-test-model key_source=scoped" in log_output
    assert secret not in log_output
    assert "prompt" not in log_output


def test_brief_mode_still_logs_safe_openai_image_key_attribution(monkeypatch):
    import main

    secret = "openai-image-secret-value"
    fake_logger = _CaptureLogger()
    monkeypatch.setattr(main, "LOG_BRIEF", True)
    monkeypatch.setattr(main, "logger", fake_logger)
    monkeypatch.setattr(
        main,
        "SCOPED_PROVIDER_KEYS",
        ScopedProviderKeys(
            openai_by_scope={"kr_ai_designer": secret},
            gemini_by_scope={},
        ),
    )
    monkeypatch.setattr(
        main,
        "call_openai_image_impl",
        lambda *args, **kwargs: SimpleNamespace(text="ok"),
    )

    with ai_service_scope("kr_ai_designer"):
        response = main._call_openai_image_generation("gpt-image-test", ["prompt"], {}, {})

    log_output = "\n".join(fake_logger.messages)
    assert response.text == "ok"
    assert "[AIKey] scope=kr_ai_designer provider=openai model=gpt-image-test key_source=scoped" in log_output
    assert secret not in log_output
    assert "prompt" not in log_output


def test_brief_mode_still_logs_safe_openai_analysis_key_attribution(monkeypatch):
    import main

    secret = "openai-analysis-secret-value"
    captured = {}
    fake_logger = _CaptureLogger()
    monkeypatch.setattr(main, "LOG_BRIEF", True)
    monkeypatch.setattr(main, "logger", fake_logger)
    monkeypatch.setattr(
        main,
        "SCOPED_PROVIDER_KEYS",
        ScopedProviderKeys(
            openai_by_scope={"kr_ai_consultant": secret},
            gemini_by_scope={},
        ),
    )

    def fake_openai_analysis(*args, **kwargs):
        captured["api_key"] = kwargs["api_key"]
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(main, "call_openai_analysis_impl", fake_openai_analysis)

    with ai_service_scope("kr_ai_consultant"):
        response = main._call_openai_analysis_generation("gpt-analysis-test", ["prompt"], {})

    log_output = "\n".join(fake_logger.messages)
    assert response.text == "ok"
    assert captured["api_key"] == secret
    assert "[AIKey] scope=kr_ai_consultant provider=openai model=gpt-analysis-test key_source=scoped" in log_output
    assert secret not in log_output
    assert "prompt" not in log_output


def test_analysis_dispatch_scoped_marker_resolves_secret_and_logs_once(monkeypatch):
    import main
    from infrastructure.ai.analysis_provider_dispatch import build_analysis_provider_dispatch

    secret = "openai-dispatch-secret-value"
    captured = {}
    fake_logger = _CaptureLogger()
    monkeypatch.setattr(main, "LOG_BRIEF", True)
    monkeypatch.setattr(main, "logger", fake_logger)
    monkeypatch.setattr(
        main,
        "SCOPED_PROVIDER_KEYS",
        ScopedProviderKeys(
            openai_by_scope={"kr_ai_consultant": secret},
            gemini_by_scope={},
        ),
    )

    def fake_openai_analysis(*args, **kwargs):
        captured["api_key"] = kwargs["api_key"]
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(main, "call_openai_analysis_impl", fake_openai_analysis)
    dispatch = build_analysis_provider_dispatch(
        provider="openai",
        gemini_caller=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("gemini should not be called")),
        openai_caller=main._call_openai_analysis_generation,
        openai_model_set={"gpt-analysis-test"},
        openai_api_key="scoped",
        openai_reasoning_effort="xhigh",
        logger=fake_logger,
        log_brief=True,
    )

    with ai_service_scope("kr_ai_consultant"):
        response = dispatch("gpt-analysis-test", ["prompt"], {}, {}, log_tag="unit")

    log_output = "\n".join(fake_logger.messages)
    attribution_logs = [message for message in fake_logger.messages if message.startswith("[AIKey]")]
    assert response.text == "ok"
    assert captured["api_key"] == secret
    assert captured["api_key"] != "scoped"
    assert attribution_logs == [
        "[AIKey] scope=kr_ai_consultant provider=openai model=gpt-analysis-test key_source=scoped"
    ]
    assert secret not in log_output
    assert "prompt" not in log_output


def test_openai_analysis_rejects_explicit_key_override(monkeypatch):
    import main

    scoped_secret = "openai-scoped-secret-value"
    explicit_secret = "openai-explicit-secret-value"
    captured = {}
    fake_logger = _CaptureLogger()
    monkeypatch.setattr(main, "LOG_BRIEF", True)
    monkeypatch.setattr(main, "logger", fake_logger)
    monkeypatch.setattr(
        main,
        "SCOPED_PROVIDER_KEYS",
        ScopedProviderKeys(
            openai_by_scope={"kr_ai_consultant": scoped_secret},
            gemini_by_scope={},
        ),
    )

    def fake_openai_analysis(*args, **kwargs):
        captured["api_key"] = kwargs["api_key"]
        return SimpleNamespace(text="ok")

    monkeypatch.setattr(main, "call_openai_analysis_impl", fake_openai_analysis)

    with ai_service_scope("kr_ai_consultant"), pytest.raises(RuntimeError, match="Explicit OpenAI API keys are disabled"):
        main._call_openai_analysis_generation(
            "gpt-analysis-test",
            ["prompt"],
            {},
            api_key=explicit_secret,
        )

    log_output = "\n".join(fake_logger.messages)
    assert captured == {}
    assert scoped_secret not in log_output
    assert explicit_secret not in log_output
    assert "prompt" not in log_output


def test_materialize_input_downloads_configured_s3_bucket_url_with_authenticated_client(monkeypatch, tmp_path):
    import main

    captured = {}

    class FakeS3Client:
        def download_file(self, bucket, key, local_path):
            captured["bucket"] = bucket
            captured["key"] = key
            captured["local_path"] = local_path
            with open(local_path, "wb") as handle:
                handle.write(b"private-image")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "S3_BUCKET", "configured-bucket")
    monkeypatch.setattr(main, "AWS_REGION", "ap-northeast-2")
    monkeypatch.setattr(main, "_get_s3_client", lambda: FakeS3Client())
    monkeypatch.setattr(
        main,
        "_download_to_path",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("same-bucket URL should use S3 client")),
    )

    local_path = main._materialize_input(
        "https://configured-bucket.s3.ap-northeast-2.amazonaws.com/internal/uploads/source.png?X-Amz-Signature=hidden",
        "room",
    )

    assert local_path is not None
    assert captured["bucket"] == "configured-bucket"
    assert captured["key"] == "internal/uploads/source.png"
    assert (tmp_path / local_path).read_bytes() == b"private-image"


def test_materialize_input_keeps_http_download_for_external_urls(monkeypatch, tmp_path):
    import main

    captured = {}

    def fake_download(url, local_path):
        captured["url"] = url
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"external-image")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, "S3_BUCKET", "configured-bucket")
    monkeypatch.setattr(main, "_get_s3_client", lambda: (_ for _ in ()).throw(AssertionError("external URL should not use S3 client")))
    monkeypatch.setattr(main, "_download_to_path", fake_download)

    local_path = main._materialize_input("https://cdn.example.com/source.png", "room")

    assert local_path is not None
    assert captured["url"] == "https://cdn.example.com/source.png"
    assert (tmp_path / local_path).read_bytes() == b"external-image"
