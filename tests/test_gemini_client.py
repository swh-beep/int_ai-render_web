from types import SimpleNamespace

from infrastructure.ai.gemini_client import call_gemini_with_failover


def _install_fake_genai_client(monkeypatch, captured: dict):
    class DummyModels:
        def generate_content(self, *, model, contents, config=None):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = dict(config or {})
            return SimpleNamespace(parts=[], candidates=[SimpleNamespace()])

    class DummyClient:
        def __init__(self, *, api_key):
            captured["api_key"] = api_key
            self.models = DummyModels()

    monkeypatch.setattr("infrastructure.ai.gemini_client.genai.Client", DummyClient)


def test_call_gemini_with_failover_consumes_max_attempts_without_forwarding_it_to_sdk(monkeypatch):
    captured = {}
    _install_fake_genai_client(monkeypatch, captured)

    response = call_gemini_with_failover(
        "gemini-3.1-flash-image",
        ["prompt"],
        {"timeout": 12, "max_attempts": 1},
        {},
        api_key_pool=["test-key-1234"],
        quota_exceeded_keys=set(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
        log_tag="unit",
    )

    assert response is not None
    assert captured["api_key"] == "test-key-1234"
    assert captured["config"]["http_options"] == {"timeout": 12000}
    assert "max_attempts" not in captured["config"]


def test_call_gemini_with_failover_does_not_apply_2k_image_defaults_for_pro_image_model(monkeypatch):
    captured = {}
    _install_fake_genai_client(monkeypatch, captured)

    call_gemini_with_failover(
        "gemini-3-pro-image",
        ["prompt"],
        {"timeout": 12},
        {},
        api_key_pool=["test-key-1234"],
        quota_exceeded_keys=set(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
        log_tag="Stage2.Furnish",
    )

    assert captured["config"]["response_modalities"] == ["IMAGE"]
    assert "image_config" not in captured["config"]


def test_call_gemini_with_failover_forwards_explicit_image_ratio_and_thinking_config(monkeypatch):
    captured = {}
    _install_fake_genai_client(monkeypatch, captured)

    call_gemini_with_failover(
        "gemini-3.1-flash-image",
        ["prompt"],
        {
            "timeout": 12,
            "aspect_ratio": "4:5",
            "thinking_level": "high",
            "include_thoughts": False,
        },
        {},
        api_key_pool=["test-key-1234"],
        quota_exceeded_keys=set(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
        log_tag="Detail.Generate",
    )

    assert captured["config"]["response_modalities"] == ["IMAGE"]
    assert captured["config"]["image_config"]["aspect_ratio"] == "4:5"
    assert "image_size" not in captured["config"]["image_config"]
    assert captured["config"]["thinking_config"]["thinking_level"] == "high"
    assert captured["config"]["thinking_config"]["include_thoughts"] is False


def test_call_gemini_with_failover_uses_high_thinking_for_furniture_analysis(monkeypatch):
    captured = {}
    _install_fake_genai_client(monkeypatch, captured)

    call_gemini_with_failover(
        "gemini-3.5-flash",
        ["prompt"],
        {"timeout": 12},
        {},
        api_key_pool=["test-key-1234"],
        quota_exceeded_keys=set(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
        log_tag="Analysis.CropItem",
    )

    assert captured["config"]["thinking_config"]["thinking_level"] == "high"


def test_call_gemini_with_failover_uses_high_thinking_for_detect_furniture(monkeypatch):
    captured = {}
    _install_fake_genai_client(monkeypatch, captured)

    call_gemini_with_failover(
        "gemini-3.5-flash",
        ["prompt"],
        {"timeout": 12},
        {},
        api_key_pool=["test-key-1234"],
        quota_exceeded_keys=set(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
        log_tag="Analysis.DetectFurniture",
    )

    assert captured["config"]["thinking_config"]["thinking_level"] == "high"


def test_call_gemini_with_failover_logs_attempts_exhausted_on_final_failure(monkeypatch):
    messages = {"errors": [], "warnings": []}

    class FailingModels:
        def generate_content(self, *, model, contents, config=None):
            raise RuntimeError("504 DEADLINE_EXCEEDED")

    class FailingClient:
        def __init__(self, *, api_key):
            self.models = FailingModels()

    monkeypatch.setattr("infrastructure.ai.gemini_client.genai.Client", FailingClient)
    monkeypatch.setattr("infrastructure.ai.gemini_client.time.sleep", lambda *_args, **_kwargs: None)

    response = call_gemini_with_failover(
        "gemini-3.5-flash",
        ["prompt"],
        {"timeout": 1, "max_attempts": 1},
        {},
        api_key_pool=["test-key-1234"],
        quota_exceeded_keys=set(),
        logger=SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda message, *a, **k: messages["warnings"].append(str(message)),
            error=lambda message, *a, **k: messages["errors"].append(str(message)),
        ),
        log_brief=True,
        log_tag="RankBestVariant",
    )

    assert response is None
    assert any("attempts exhausted (1)" in message for message in messages["errors"])
    assert not any("all keys failed" in message for message in messages["errors"])


def test_call_gemini_with_failover_uses_medium_thinking_for_non_furniture_analysis(monkeypatch):
    captured = {}
    _install_fake_genai_client(monkeypatch, captured)

    call_gemini_with_failover(
        "gemini-3.5-flash",
        ["prompt"],
        {"timeout": 12},
        {},
        api_key_pool=["test-key-1234"],
        quota_exceeded_keys=set(),
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
        log_tag="Analysis.RoomOnly",
    )

    assert captured["config"]["thinking_config"]["thinking_level"] == "medium"
