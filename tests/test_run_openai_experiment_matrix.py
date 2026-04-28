from tools.replay.run_openai_experiment_matrix import EXPERIMENTS, _experiment_runtime_metadata


def test_experiment_runtime_metadata_records_budget_and_models():
    experiment = next(row for row in EXPERIMENTS if row["key"] == "C")
    metadata = _experiment_runtime_metadata(experiment, base_env={"OPENAI_API_KEY": "test-key"})

    assert metadata["analysis_provider"] == "openai"
    assert metadata["analysis_model_name"] == "gpt-5.4"
    assert metadata["analysis_reasoning_effort"] == "xhigh"
    assert metadata["analysis_timeout_cap_sec"] == "25"
    assert metadata["analysis_max_attempts"] == "1"
    assert metadata["main_image_provider"] == "openai"
    assert metadata["main_image_model_name"] == "gpt-image-2"
    assert metadata["repair_image_provider"] == "openai"
    assert metadata["repair_image_model_name"] == "gpt-image-2"
    assert metadata["total_timeout_limit"] == "1800"


def test_experiment_runtime_metadata_respects_legacy_force_gemini_override():
    experiment = next(row for row in EXPERIMENTS if row["key"] == "C")
    metadata = _experiment_runtime_metadata(
        experiment,
        base_env={
            "OPENAI_API_KEY": "",
            "FORCE_GEMINI_PROVIDERS": "1",
        },
    )

    assert metadata["analysis_provider"] == "gemini"
    assert metadata["analysis_model_name"] == "gemini-3.1-pro-preview"
    assert metadata["main_image_provider"] == "gemini"
    assert metadata["main_image_model_name"] == "gemini-3.1-flash-image-preview"
    assert metadata["repair_image_provider"] == "gemini"
    assert metadata["repair_image_model_name"] == "gemini-3.1-flash-image-preview"
