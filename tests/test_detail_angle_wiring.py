from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_wires_angle_generation_and_quality_gate_to_separate_providers():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    detail_block = source.split("generate_detail_view=lambda", 1)[1].split(
        "volume_ranking_snapshot=_volume_ranking_snapshot",
        1,
    )[0]

    assert "call_gemini_with_failover=CALL_REPAIR_IMAGE_WITH_PROVIDER" in detail_block
    assert "model_name=REPAIR_IMAGE_MODEL_NAME" in detail_block
    assert "call_analysis_with_failover=call_gemini_with_failover" in detail_block
    assert "analysis_model_name=ANALYSIS_MODEL_NAME" in detail_block
    assert "safe_json_from_model_text=_safe_json_from_model_text" in detail_block
