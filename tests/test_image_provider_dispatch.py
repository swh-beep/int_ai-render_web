from types import SimpleNamespace

from infrastructure.ai.image_provider_dispatch import build_image_provider_dispatch


def test_image_dispatch_routes_to_openai():
    calls = []

    def gemini_caller(*args, **kwargs):
        calls.append(("gemini", args, kwargs))
        return SimpleNamespace(text="gemini")

    def openai_caller(*args, **kwargs):
        calls.append(("openai", args, kwargs))
        return SimpleNamespace(text="openai")

    dispatch = build_image_provider_dispatch(
        provider="openai",
        gemini_caller=gemini_caller,
        openai_image_caller=openai_caller,
        openai_api_key="secret",
    )

    response = dispatch("gpt-image-2", ["prompt"], {"timeout": 20}, {}, log_tag="unit")
    assert response.text == "openai"
    assert calls[0][0] == "openai"


def test_image_dispatch_routes_to_gemini():
    calls = []

    def gemini_caller(*args, **kwargs):
        calls.append(("gemini", args, kwargs))
        return SimpleNamespace(text="gemini")

    def openai_caller(*args, **kwargs):
        calls.append(("openai", args, kwargs))
        return SimpleNamespace(text="openai")

    dispatch = build_image_provider_dispatch(
        provider="gemini",
        gemini_caller=gemini_caller,
        openai_image_caller=openai_caller,
        openai_api_key="secret",
    )

    response = dispatch("gemini-3.1-flash-image-preview", ["prompt"], {"timeout": 20}, {"safe": True}, log_tag="unit")
    assert response.text == "gemini"
    assert calls[0][0] == "gemini"


def test_image_dispatch_requires_key_for_openai():
    try:
        build_image_provider_dispatch(
            provider="openai",
            gemini_caller=lambda *a, **k: None,
            openai_image_caller=lambda *a, **k: None,
            openai_api_key="",
        )
    except RuntimeError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
