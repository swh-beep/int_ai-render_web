from typing import Any, Callable


def build_image_provider_dispatch(
    *,
    provider: str,
    gemini_caller: Callable[..., Any],
    openai_image_caller: Callable[..., Any],
    openai_api_key: str,
):
    normalized_provider = (provider or "gemini").strip().lower() or "gemini"
    has_openai_key = bool((openai_api_key or "").strip())
    if normalized_provider == "openai" and not has_openai_key:
        raise RuntimeError("OPENAI_API_KEY is required when MAIN_IMAGE_PROVIDER or REPAIR_IMAGE_PROVIDER is openai")

    def dispatch(model_name, contents, request_options, safety_settings, system_instruction=None, log_tag=None):
        if normalized_provider == "openai" and has_openai_key:
            return openai_image_caller(
                model_name,
                contents,
                request_options,
                safety_settings,
                system_instruction=system_instruction,
                log_tag=log_tag,
            )
        return gemini_caller(
            model_name,
            contents,
            request_options,
            safety_settings,
            system_instruction=system_instruction,
            log_tag=log_tag,
        )

    return dispatch
