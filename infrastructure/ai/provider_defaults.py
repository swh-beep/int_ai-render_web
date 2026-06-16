from dataclasses import dataclass
from typing import Mapping


def _env_flag(raw_value: str | None, *, default: bool) -> bool:
    if raw_value is None:
        return default
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class ProviderDefaults:
    analysis_provider: str
    main_image_provider: str
    repair_image_provider: str
    openai_image_model_name: str
    force_gemini_analysis_provider: bool
    force_gemini_image_providers: bool


def resolve_runtime_image_provider(provider: str, openai_api_key: str | None) -> str:
    normalized_provider = (provider or "gemini").strip().lower() or "gemini"
    if normalized_provider == "openai" and not (openai_api_key or "").strip():
        return "gemini"
    return normalized_provider


def _looks_like_gemini_model(model_name: str | None) -> bool:
    return str(model_name or "").strip().lower().startswith("gemini-")


def _looks_like_openai_model(model_name: str | None) -> bool:
    normalized = str(model_name or "").strip().lower()
    return normalized.startswith("gpt-") or normalized.startswith("dall-e-") or normalized.startswith("chatgpt-")


def resolve_runtime_model_name(
    *,
    provider: str,
    configured_model_name: str | None,
    default_openai_model_name: str,
    default_gemini_model_name: str,
) -> str:
    normalized_provider = (provider or "gemini").strip().lower() or "gemini"
    candidate = (configured_model_name or "").strip()

    if normalized_provider == "openai":
        if candidate and not _looks_like_gemini_model(candidate):
            return candidate
        return default_openai_model_name

    if candidate and not _looks_like_openai_model(candidate):
        return candidate
    return default_gemini_model_name


def resolve_provider_defaults(env: Mapping[str, str | None]) -> ProviderDefaults:
    legacy_force_raw = env.get("FORCE_GEMINI_PROVIDERS")
    legacy_force_all = _env_flag(legacy_force_raw, default=False)

    force_gemini_analysis_raw = env.get("FORCE_GEMINI_ANALYSIS_PROVIDER")
    force_gemini_analysis_provider = _env_flag(
        force_gemini_analysis_raw,
        default=legacy_force_all,
    )
    force_gemini_image_raw = env.get("FORCE_GEMINI_IMAGE_PROVIDERS")
    force_gemini_image_providers = _env_flag(
        force_gemini_image_raw,
        default=legacy_force_all,
    )

    configured_analysis_provider = (env.get("ANALYSIS_PROVIDER", "gemini") or "gemini").strip().lower() or "gemini"
    configured_main_image_provider = (env.get("MAIN_IMAGE_PROVIDER", "gemini") or "gemini").strip().lower() or "gemini"
    configured_repair_image_provider = (
        env.get("REPAIR_IMAGE_PROVIDER", "gemini") or "gemini"
    ).strip().lower() or "gemini"

    analysis_provider = "gemini" if (force_gemini_analysis_raw is not None or legacy_force_raw is not None) and force_gemini_analysis_provider else configured_analysis_provider
    main_image_provider = "gemini" if (force_gemini_image_raw is not None or legacy_force_raw is not None) and force_gemini_image_providers else configured_main_image_provider
    repair_image_provider = "gemini" if (force_gemini_image_raw is not None or legacy_force_raw is not None) and force_gemini_image_providers else configured_repair_image_provider
    openai_image_model_name = (env.get("OPENAI_IMAGE_MODEL_NAME", "gpt-image-2") or "gpt-image-2").strip() or "gpt-image-2"

    return ProviderDefaults(
        analysis_provider=analysis_provider,
        main_image_provider=main_image_provider,
        repair_image_provider=repair_image_provider,
        openai_image_model_name=openai_image_model_name,
        force_gemini_analysis_provider=force_gemini_analysis_provider,
        force_gemini_image_providers=force_gemini_image_providers,
    )
