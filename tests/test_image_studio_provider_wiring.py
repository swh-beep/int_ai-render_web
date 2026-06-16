from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from application.media.frontal_generation_stage import generate_frontal_room_from_photos


ROOT = Path(__file__).resolve().parents[1]


def test_main_wires_image_studio_edit_and_decor_to_repair_provider():
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    edit_block = source.split("process_image_edit_logic=lambda", 1)[1].split(
        "generate_frontal_room_from_photos=lambda", 1
    )[0]

    assert "call_gemini_with_failover=CALL_REPAIR_IMAGE_WITH_PROVIDER" in edit_block
    assert "model_name=REPAIR_IMAGE_MODEL_NAME" in edit_block
    assert "model_name=GEMINI_IMAGE_MODEL_NAME" not in edit_block


def test_main_wires_frontal_generation_to_repair_provider_but_keeps_analysis_provider():
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    frontal_block = source.split("generate_frontal_room_from_photos=lambda", 1)[1].split(
        "log_section=log_section", 1
    )[0]

    assert "call_gemini_with_failover=call_gemini_with_failover" in frontal_block
    assert "analysis_model_name=ANALYSIS_MODEL_NAME" in frontal_block
    assert "call_generation_with_failover=CALL_REPAIR_IMAGE_WITH_PROVIDER" in frontal_block
    assert "model_name=REPAIR_IMAGE_MODEL_NAME" in frontal_block
    assert "model_name=GEMINI_IMAGE_MODEL_NAME" not in frontal_block


def test_frontal_stage_uses_separate_generation_caller(tmp_path):
    source_path = tmp_path / "input.png"
    Image.new("RGB", (320, 180), color=(240, 240, 240)).save(source_path, format="PNG")
    output_bytes = source_path.read_bytes()
    captured = {"analysis": [], "generation": []}

    def _analysis_caller(model_name, content, request_options, safety_settings, **kwargs):
        captured["analysis"].append((model_name, list(content), dict(request_options), safety_settings, kwargs))
        return SimpleNamespace(text="A compact room blueprint.")

    def _generation_caller(model_name, content, request_options, safety_settings, **kwargs):
        captured["generation"].append((model_name, list(content), dict(request_options), safety_settings, kwargs))
        return SimpleNamespace(
            candidates=[object()],
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=output_bytes))],
        )

    result = generate_frontal_room_from_photos(
        [str(source_path)],
        "unitcase",
        7,
        build_frontal_analysis_prompt=lambda: "analyze room",
        build_frontal_generation_prompt=lambda blueprint: f"generate from {blueprint}",
        call_gemini_with_failover=_analysis_caller,
        analysis_model_name="gemini-analysis",
        model_name="gemini-3.1-flash-image",
        allow_all_safety_settings=lambda: {"safe": True},
        standardize_image=lambda path: path,
        call_generation_with_failover=_generation_caller,
    )

    output_path = Path(result)
    try:
        assert output_path.exists()
        assert captured["analysis"][0][0] == "gemini-analysis"
        assert captured["analysis"][0][4]["log_tag"] == "Frontal.Analysis"
        assert captured["generation"][0][0] == "gemini-3.1-flash-image"
        assert captured["generation"][0][2]["aspect_ratio"] == "16:9"
        assert captured["generation"][0][2]["max_attempts"] == 1
        assert captured["generation"][0][4]["log_tag"] == "Frontal.Generate"
    finally:
        if output_path.exists():
            output_path.unlink()
