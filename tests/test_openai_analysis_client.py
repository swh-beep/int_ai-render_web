from types import SimpleNamespace

from PIL import Image

from infrastructure.ai.openai_client import (
    _build_openai_input,
    _extract_output_text,
    call_openai_analysis,
)


def test_build_openai_input_supports_text_and_image():
    img = Image.new("RGB", (4, 4), color="white")
    try:
        payload = _build_openai_input(["hello", img])
    finally:
        img.close()
    assert payload[0]["role"] == "user"
    assert payload[0]["content"][0] == {"type": "input_text", "text": "hello"}
    assert payload[0]["content"][1]["type"] == "input_image"
    assert payload[0]["content"][1]["image_url"].startswith("data:image/")


def test_extract_output_text_prefers_direct_field():
    assert _extract_output_text({"output_text": "direct"}) == "direct"


def test_extract_output_text_falls_back_to_nested_output():
    payload = {
        "output": [
            {"content": [{"type": "output_text", "text": "first"}]},
            {"content": [{"type": "text", "text": {"value": "second"}}]},
        ]
    }
    assert _extract_output_text(payload) == "first\nsecond"


def test_call_openai_analysis_returns_text(monkeypatch):
    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output_text": "{\"ok\": true}"}

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr("infrastructure.ai.openai_analysis_client.requests.post", fake_post)
    img = Image.new("RGB", (4, 4), color="white")
    try:
        response = call_openai_analysis(
            "gpt-5.4",
            ["analyze", img],
            {"timeout": 33},
            api_key="test-key",
            logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
            log_brief=True,
            system_instruction="return json",
            log_tag="Unit",
            reasoning_effort="xhigh",
        )
    finally:
        img.close()

    assert response.text == "{\"ok\": true}"
    assert captured["timeout"] == 33
    assert captured["json"]["model"] == "gpt-5.4"
    assert captured["json"]["instructions"] == "return json"
    assert captured["json"]["reasoning"] == {"effort": "xhigh"}


def test_call_openai_analysis_retries_then_succeeds(monkeypatch):
    attempts = {"count": 0}

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output_text": "ok"}

    def fake_post(url, headers=None, json=None, timeout=None):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RuntimeError("transient")
        return DummyResponse()

    monkeypatch.setattr("infrastructure.ai.openai_analysis_client.requests.post", fake_post)
    monkeypatch.setattr("infrastructure.ai.openai_analysis_client.time.sleep", lambda *_: None)

    response = call_openai_analysis(
        "gpt-5.4",
        ["analyze"],
        {"timeout": 20},
        api_key="test-key",
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
    )
    assert response.text == "ok"
    assert attempts["count"] == 2


def test_call_openai_analysis_requires_key():
    try:
        call_openai_analysis(
            "gpt-5.4",
            ["analyze"],
            {"timeout": 20},
            api_key="",
            logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
            log_brief=True,
        )
    except RuntimeError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_call_openai_analysis_falls_back_from_xhigh_to_high(monkeypatch):
    class DummyResponse:
        status_code = 400
        text = "Unsupported reasoning effort: xhigh"

        def raise_for_status(self):
            error = requests.HTTPError("unsupported reasoning")
            error.response = self
            raise error

        def json(self):
            return {"output_text": "ignored"}

    class SuccessResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output_text": "ok"}

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["reasoning"]["effort"])
        if len(calls) == 1:
            return DummyResponse()
        return SuccessResponse()

    import requests

    monkeypatch.setattr("infrastructure.ai.openai_analysis_client.requests.post", fake_post)
    monkeypatch.setattr("infrastructure.ai.openai_analysis_client.time.sleep", lambda *_: None)

    response = call_openai_analysis(
        "gpt-5.4",
        ["analyze"],
        {"timeout": 20},
        api_key="test-key",
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None, warning=lambda *a, **k: None),
        log_brief=True,
        reasoning_effort="xhigh",
    )

    assert response.text == "ok"
    assert calls == ["xhigh", "high"]


def test_call_openai_analysis_fallback_does_not_consume_attempt_budget(monkeypatch):
    class UnsupportedResponse:
        status_code = 400
        text = "Unsupported reasoning effort: xhigh"

        def raise_for_status(self):
            error = requests.HTTPError("unsupported reasoning")
            error.response = self
            raise error

    class SuccessResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output_text": "ok"}

    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json["reasoning"]["effort"])
        if len(calls) == 1:
            return UnsupportedResponse()
        return SuccessResponse()

    import requests

    monkeypatch.setattr("infrastructure.ai.openai_analysis_client.requests.post", fake_post)
    monkeypatch.setenv("OPENAI_ANALYSIS_MAX_ATTEMPTS", "1")
    monkeypatch.setattr("infrastructure.ai.openai_analysis_client.time.sleep", lambda *_: None)

    response = call_openai_analysis(
        "gpt-5.4",
        ["analyze"],
        {"timeout": 20},
        api_key="test-key",
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None, warning=lambda *a, **k: None),
        log_brief=True,
        reasoning_effort="xhigh",
    )

    assert response.text == "ok"
    assert calls == ["xhigh", "high"]


def test_call_openai_analysis_respects_timeout_cap_and_attempt_env(monkeypatch):
    calls = []

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"output_text": "ok"}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(timeout)
        return DummyResponse()

    monkeypatch.setattr("infrastructure.ai.openai_analysis_client.requests.post", fake_post)
    monkeypatch.setenv("OPENAI_ANALYSIS_TIMEOUT_CAP_SEC", "25")
    monkeypatch.setenv("OPENAI_ANALYSIS_MAX_ATTEMPTS", "1")

    response = call_openai_analysis(
        "gpt-5.4",
        ["analyze"],
        {"timeout": 90},
        api_key="test-key",
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
        reasoning_effort="xhigh",
    )

    assert response.text == "ok"
    assert calls == [25]
