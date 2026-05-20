from infrastructure.ai.provider_defaults import (
    resolve_provider_defaults,
    resolve_runtime_image_provider,
    resolve_runtime_model_name,
)


def test_default_provider_resolution_keeps_main_on_gemini_and_repair_on_openai():
    defaults = resolve_provider_defaults({})

    assert defaults.analysis_provider == "gemini"
    assert defaults.main_image_provider == "gemini"
    assert defaults.repair_image_provider == "openai"
    assert defaults.openai_image_model_name == "gpt-image-2"


def test_explicit_openai_analysis_provider_is_respected_when_force_flags_are_unset():
    defaults = resolve_provider_defaults({"ANALYSIS_PROVIDER": "openai"})

    assert defaults.analysis_provider == "openai"
    assert defaults.main_image_provider == "gemini"
    assert defaults.repair_image_provider == "openai"


def test_explicit_legacy_force_gemini_providers_still_forces_images_to_gemini():
    defaults = resolve_provider_defaults({"FORCE_GEMINI_PROVIDERS": "1"})

    assert defaults.analysis_provider == "gemini"
    assert defaults.main_image_provider == "gemini"
    assert defaults.repair_image_provider == "gemini"


def test_explicit_legacy_force_gemini_overrides_openai_provider_settings():
    defaults = resolve_provider_defaults(
        {
            "FORCE_GEMINI_PROVIDERS": "1",
            "ANALYSIS_PROVIDER": "openai",
            "MAIN_IMAGE_PROVIDER": "openai",
            "REPAIR_IMAGE_PROVIDER": "openai",
        }
    )

    assert defaults.analysis_provider == "gemini"
    assert defaults.main_image_provider == "gemini"
    assert defaults.repair_image_provider == "gemini"


def test_image_force_flag_overrides_legacy_force_all():
    defaults = resolve_provider_defaults(
        {
            "FORCE_GEMINI_PROVIDERS": "1",
            "FORCE_GEMINI_IMAGE_PROVIDERS": "0",
            "MAIN_IMAGE_PROVIDER": "openai",
            "REPAIR_IMAGE_PROVIDER": "openai",
        }
    )

    assert defaults.analysis_provider == "gemini"
    assert defaults.main_image_provider == "openai"
    assert defaults.repair_image_provider == "openai"


def test_runtime_image_provider_falls_back_to_gemini_when_openai_key_is_missing():
    assert resolve_runtime_image_provider("openai", "") == "gemini"
    assert resolve_runtime_image_provider("openai", None) == "gemini"
    assert resolve_runtime_image_provider("openai", "secret") == "openai"


def test_runtime_model_name_falls_back_to_provider_default_when_model_family_mismatches():
    assert (
        resolve_runtime_model_name(
            provider="gemini",
            configured_model_name="gpt-image-2",
            default_openai_model_name="gpt-image-2",
            default_gemini_model_name="gemini-3.1-flash-image-preview",
        )
        == "gemini-3.1-flash-image-preview"
    )
    assert (
        resolve_runtime_model_name(
            provider="openai",
            configured_model_name="gemini-3.1-flash-image-preview",
            default_openai_model_name="gpt-image-2",
            default_gemini_model_name="gemini-3.1-flash-image-preview",
        )
        == "gpt-image-2"
    )


def test_default_runtime_models_keep_main_gemini_and_repair_gpt_image_when_openai_key_exists():
    defaults = resolve_provider_defaults({})
    main_provider = resolve_runtime_image_provider(defaults.main_image_provider, "secret")
    repair_provider = resolve_runtime_image_provider(defaults.repair_image_provider, "secret")

    assert main_provider == "gemini"
    assert repair_provider == "openai"
    assert (
        resolve_runtime_model_name(
            provider=main_provider,
            configured_model_name=None,
            default_openai_model_name=defaults.openai_image_model_name,
            default_gemini_model_name="gemini-3-pro-image-preview",
        )
        == "gemini-3-pro-image-preview"
    )
    assert (
        resolve_runtime_model_name(
            provider=repair_provider,
            configured_model_name=None,
            default_openai_model_name=defaults.openai_image_model_name,
            default_gemini_model_name="gemini-3-pro-image-preview",
        )
        == "gpt-image-2"
    )
