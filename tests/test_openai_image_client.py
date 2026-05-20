from types import SimpleNamespace

from PIL import Image

from infrastructure.ai.openai_image_client import (
    _ordered_prompt_and_images,
    call_openai_image,
)


def test_ordered_prompt_and_images_preserves_text_and_image_markers():
    img = Image.new("RGB", (4, 4), color="white")
    try:
        prompt, images = _ordered_prompt_and_images(["first", img, "second"], system_instruction="system")
    finally:
        img.close()

    assert "system" in prompt
    assert "first" in prompt
    assert "[Image 1]" in prompt
    assert "second" in prompt
    assert len(images) == 1
    assert images[0][0] == "image_1.png"


def test_call_openai_image_returns_gemini_like_response_for_edits(monkeypatch):
    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6M2ioAAAAASUVORK5CYII="}
                ]
            }

    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None, json=None):
        captured["url"] = url
        captured["data"] = data
        captured["files"] = files
        captured["json"] = json
        return DummyResponse()

    monkeypatch.setattr("infrastructure.ai.openai_image_client.requests.post", fake_post)
    img = Image.new("RGB", (4, 4), color="white")
    try:
        response = call_openai_image(
            "gpt-image-2",
            ["edit", img],
            {"timeout": 20, "aspect_ratio": "4:5", "quality": "auto"},
            {},
            api_key="test-key",
            logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
            log_brief=True,
            system_instruction="system",
        )
    finally:
        img.close()

    assert response is not None
    assert response.candidates
    assert response.parts
    assert captured["url"].endswith("/v1/images/edits")
    assert captured["data"]["model"] == "gpt-image-2"
    assert captured["data"]["size"] == "1600x2000"
    assert "quality" not in captured["data"]
    assert captured["files"][0][0] == "image[]"


def test_call_openai_image_keeps_explicit_non_auto_quality(monkeypatch):
    class DummyResponse:
        status_code = 200

        def json(self):
            return {
                "data": [
                    {"b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6M2ioAAAAASUVORK5CYII="}
                ]
            }

    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None, json=None):
        captured["json"] = json
        return DummyResponse()

    monkeypatch.setattr("infrastructure.ai.openai_image_client.requests.post", fake_post)

    response = call_openai_image(
        "gpt-image-2",
        ["generate"],
        {"timeout": 20, "aspect_ratio": "16:9", "quality": "medium"},
        {},
        api_key="test-key",
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
    )

    assert response is not None
    assert captured["json"]["size"] == "2048x1152"
    assert captured["json"]["quality"] == "medium"


def test_call_openai_image_requires_key():
    response = call_openai_image(
        "gpt-image-2",
        ["generate"],
        {"timeout": 20},
        {},
        api_key="",
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
    )
    assert response is None


def test_call_openai_image_respects_request_max_attempts(monkeypatch):
    calls = {"count": 0}

    def fake_post(url, headers=None, data=None, files=None, timeout=None, json=None):
        calls["count"] += 1
        raise RuntimeError("504 upstream timeout")

    monkeypatch.setattr("infrastructure.ai.openai_image_client.requests.post", fake_post)

    response = call_openai_image(
        "gpt-image-2",
        ["generate"],
        {"timeout": 20, "max_attempts": 2},
        {},
        api_key="test-key",
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
    )

    assert response is None
    assert calls["count"] == 2


def test_call_openai_image_falls_back_when_gpt_image_2_requires_verified_org(monkeypatch):
    class VerificationBlockedResponse:
        status_code = 403

        def json(self):
            return {
                "error": {
                    "message": "Your organization must be verified to use the model `gpt-image-2`.",
                    "type": "invalid_request_error",
                    "code": None,
                }
            }

    class SuccessResponse:
        status_code = 200

        def json(self):
            return {
                "data": [
                    {"b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6M2ioAAAAASUVORK5CYII="}
                ]
            }

    calls = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None, json=None):
        model_name = (data or json or {}).get("model")
        calls.append(model_name)
        if len(calls) == 1:
            return VerificationBlockedResponse()
        return SuccessResponse()

    monkeypatch.setattr("infrastructure.ai.openai_image_client.requests.post", fake_post)

    response = call_openai_image(
        "gpt-image-2",
        ["generate"],
        {"timeout": 20},
        {},
        api_key="test-key",
        logger=SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        ),
        log_brief=True,
        fallback_model_name="gpt-image-1.5",
    )

    assert response is not None
    assert response.candidates
    assert calls == ["gpt-image-2", "gpt-image-1.5"]


def test_call_openai_image_verification_fallback_works_even_when_max_attempts_is_one(monkeypatch):
    class VerificationBlockedResponse:
        status_code = 403

        def json(self):
            return {
                "error": {
                    "message": "Your organization must be verified to use the model `gpt-image-2`.",
                    "type": "invalid_request_error",
                    "code": None,
                }
            }

    class SuccessResponse:
        status_code = 200

        def json(self):
            return {
                "data": [
                    {"b64_json": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6M2ioAAAAASUVORK5CYII="}
                ]
            }

    calls = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None, json=None):
        model_name = (data or json or {}).get("model")
        calls.append(model_name)
        if len(calls) == 1:
            return VerificationBlockedResponse()
        return SuccessResponse()

    monkeypatch.setattr("infrastructure.ai.openai_image_client.requests.post", fake_post)

    response = call_openai_image(
        "gpt-image-2",
        ["generate"],
        {"timeout": 20, "max_attempts": 1},
        {},
        api_key="test-key",
        logger=SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        ),
        log_brief=True,
        fallback_model_name="gpt-image-1.5",
    )

    assert response is not None
    assert calls == ["gpt-image-2", "gpt-image-1.5"]


def test_call_openai_image_supports_non_gpt_fallback_with_url_outputs(monkeypatch):
    class VerificationBlockedResponse:
        status_code = 403

        def json(self):
            return {
                "error": {
                    "message": "Your organization must be verified to use the model `gpt-image-2`.",
                    "type": "invalid_request_error",
                    "code": None,
                }
            }

    class DalleSuccessResponse:
        status_code = 200

        def json(self):
            return {
                "data": [
                    {"url": "https://example.com/fallback-image.png"}
                ]
            }

    class DownloadResponse:
        status_code = 200
        content = b"png-bytes"

    post_calls = []
    get_calls = []

    def fake_post(url, headers=None, data=None, files=None, timeout=None, json=None):
        payload = data or json or {}
        post_calls.append(payload)
        if len(post_calls) == 1:
            return VerificationBlockedResponse()
        return DalleSuccessResponse()

    def fake_get(url, timeout=None):
        get_calls.append((url, timeout))
        return DownloadResponse()

    monkeypatch.setattr("infrastructure.ai.openai_image_client.requests.post", fake_post)
    monkeypatch.setattr("infrastructure.ai.openai_image_client.requests.get", fake_get)

    response = call_openai_image(
        "gpt-image-2",
        ["generate"],
        {"timeout": 20, "max_attempts": 1},
        {},
        api_key="test-key",
        logger=SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        ),
        log_brief=True,
        fallback_model_name="dall-e-3",
    )

    assert response is not None
    assert response.parts
    assert post_calls[1]["model"] == "dall-e-3"
    assert post_calls[1]["response_format"] == "b64_json"
    assert get_calls == [("https://example.com/fallback-image.png", 20)]
