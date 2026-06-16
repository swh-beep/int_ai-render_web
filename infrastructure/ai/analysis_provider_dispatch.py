from typing import Any, Callable, Iterable


GEMINI_ANALYSIS_DEFAULT = "gemini-3.5-flash"


def default_analysis_model_name(provider: str, openai_model_name: str) -> str:
    return openai_model_name if str(provider or "").strip().lower() == "openai" else GEMINI_ANALYSIS_DEFAULT


def build_analysis_model_set(*model_names: Any) -> set[str]:
    flattened: list[str] = []
    for raw in model_names:
        if isinstance(raw, (list, tuple, set)):
            flattened.extend([str(item or "").strip() for item in raw if str(item or "").strip()])
            continue
        text = str(raw or "").strip()
        if text:
            flattened.append(text)
    return set(flattened)


def should_route_analysis_to_openai(provider: str, model_name: str, analysis_model_set: set[str]) -> bool:
    if str(provider or "").strip().lower() != "openai":
        return False
    return str(model_name or "").strip() in set(analysis_model_set or set())


def build_analysis_provider_dispatch(
    *,
    provider: str,
    gemini_caller: Callable[..., Any],
    openai_caller: Callable[..., Any],
    openai_model_set: set[str],
    openai_api_key: str,
    openai_reasoning_effort: str,
    logger: Any,
    log_brief: bool,
):
    provider_normalized = str(provider or "").strip().lower()
    if provider_normalized == "openai" and not str(openai_api_key or "").strip():
        raise RuntimeError("OPENAI_API_KEY is required when ANALYSIS_PROVIDER=openai")

    def _dispatch(model_name, contents, request_options, safety_settings, system_instruction=None, log_tag=None):
        if should_route_analysis_to_openai(provider_normalized, model_name, openai_model_set):
            return openai_caller(
                model_name,
                contents,
                request_options,
                api_key=openai_api_key,
                logger=logger,
                log_brief=log_brief,
                system_instruction=system_instruction,
                log_tag=log_tag,
                reasoning_effort=openai_reasoning_effort,
            )
        return gemini_caller(
            model_name,
            contents,
            request_options,
            safety_settings,
            system_instruction=system_instruction,
            log_tag=log_tag,
        )

    return _dispatch
