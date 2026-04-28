from types import SimpleNamespace

from infrastructure.ai.analysis_provider_dispatch import (
    build_analysis_model_set,
    build_analysis_provider_dispatch,
)


def test_build_analysis_model_set_trims_and_deduplicates():
    assert build_analysis_model_set([" gpt-5.4 ", "", None, "gpt-5.4", "gemini-3.1-pro-preview"]) == {
        "gpt-5.4",
        "gemini-3.1-pro-preview",
    }


def test_dispatch_routes_analysis_models_to_openai():
    calls = []

    def gemini_caller(*args, **kwargs):
        calls.append(("gemini", args, kwargs))
        return SimpleNamespace(text="gemini")

    def openai_caller(*args, **kwargs):
        calls.append(("openai", args, kwargs))
        return SimpleNamespace(text="openai")

    dispatch = build_analysis_provider_dispatch(
        provider="openai",
        gemini_caller=gemini_caller,
        openai_caller=openai_caller,
        openai_model_set={"gpt-5.4"},
        openai_api_key="secret",
        openai_reasoning_effort="xhigh",
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
    )

    response = dispatch("gpt-5.4", ["prompt"], {"timeout": 20}, {}, log_tag="unit")
    assert response.text == "openai"
    assert calls[0][0] == "openai"
    assert calls[0][2]["api_key"] == "secret"
    assert calls[0][2]["reasoning_effort"] == "xhigh"


def test_dispatch_routes_generation_models_to_gemini():
    calls = []

    def gemini_caller(*args, **kwargs):
        calls.append(("gemini", args, kwargs))
        return SimpleNamespace(text="gemini")

    def openai_caller(*args, **kwargs):
        calls.append(("openai", args, kwargs))
        return SimpleNamespace(text="openai")

    dispatch = build_analysis_provider_dispatch(
        provider="openai",
        gemini_caller=gemini_caller,
        openai_caller=openai_caller,
        openai_model_set={"gpt-5.4"},
        openai_api_key="secret",
        openai_reasoning_effort="xhigh",
        logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
        log_brief=True,
    )

    response = dispatch("gemini-3.1-flash-image-preview", ["prompt"], {"timeout": 20}, {"safe": True}, log_tag="unit")
    assert response.text == "gemini"
    assert calls[0][0] == "gemini"
    assert calls[0][1][3] == {"safe": True}


def test_dispatch_requires_openai_key_when_provider_is_openai():
    try:
        build_analysis_provider_dispatch(
            provider="openai",
            gemini_caller=lambda *a, **k: None,
            openai_caller=lambda *a, **k: None,
            openai_model_set={"gpt-5.4"},
            openai_api_key="",
            openai_reasoning_effort="xhigh",
            logger=SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None),
            log_brief=True,
        )
    except RuntimeError as exc:
        assert "OPENAI_API_KEY" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
