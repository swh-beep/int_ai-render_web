from pathlib import Path
import importlib


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
    assert "refurnish_locked_angle=_generate_locked_angle_furnishing" in detail_block


def test_locked_angle_stage2_uses_detail_timeout_budget(monkeypatch):
    main = importlib.import_module("main")
    captured = {}

    monkeypatch.setattr(
        main,
        "build_furniture_specs_json",
        lambda inventory: {"items": list(inventory)},
    )
    atlas_paths = []

    def _build_atlas(_furnished_path, _empty_path, output_path):
        atlas_path = Path(output_path)
        atlas_path.parent.mkdir(parents=True, exist_ok=True)
        atlas_path.write_bytes(b"atlas")
        atlas_paths.append(atlas_path)
        return str(atlas_path)

    monkeypatch.setattr(
        main,
        "build_furniture_only_reference_atlas",
        _build_atlas,
    )

    def _generate_furnished_room(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"path": "outputs/locked-stage2.png"}

    monkeypatch.setattr(main, "generate_furnished_room", _generate_furnished_room)

    result = main._generate_locked_angle_furnishing(
        guide_path="outputs/validated-guide.png",
        furnished_main_path="outputs/furnished-main.png",
        empty_room_path="outputs/original-empty-room.png",
        style_prompt="Keep the guide camera locked.",
        unique_id="timeout-budget",
        furniture_data=[{"target_key": "sofa-1", "label": "Sofa"}],
        geometry_contract={
            "item_targets": [
                {"target_key": "sofa-1", "family": "sofa", "qty": 1},
                {"target_key": "lamp-1", "family": "floor_lamp", "qty": 1},
            ]
        },
        timeout_sec=42.5,
    )

    assert result == {
        "path": "outputs/locked-stage2.png",
        "inventory_reference_mode": "furniture_only_atlas",
    }
    assert captured["args"][0] == "outputs/validated-guide.png"
    assert captured["kwargs"]["furnished_scene_reference_path"] is None
    assert captured["kwargs"]["furniture_atlas_reference_path"] == str(atlas_paths[0])
    assert result["inventory_reference_mode"] == "furniture_only_atlas"
    assert not atlas_paths[0].exists()
    assert captured["kwargs"]["max_generation_attempts"] == 1
    assert captured["kwargs"]["total_timeout_limit_override"] == 42.5
    assert [
        item["target_key"]
        for item in captured["kwargs"]["furniture_specs_json"]["items"]
    ] == ["sofa-1", "lamp-1"]


def test_locked_angle_stage2_fails_closed_when_furniture_atlas_is_unavailable(
    monkeypatch,
):
    main = importlib.import_module("main")
    monkeypatch.setattr(
        main,
        "build_furniture_only_reference_atlas",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        main,
        "generate_furnished_room",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Stage2 must not run without the camera-neutral atlas")
        ),
    )

    result = main._generate_locked_angle_furnishing(
        guide_path="outputs/validated-guide.png",
        furnished_main_path="outputs/furnished-main.png",
        empty_room_path="outputs/original-empty-room.png",
        style_prompt="Keep the guide camera locked.",
        unique_id="missing-atlas",
        furniture_data=[{"target_key": "sofa-1", "label": "Sofa"}],
        geometry_contract={"item_targets": [{"target_key": "sofa-1"}]},
        timeout_sec=42.5,
    )

    assert result == {
        "path": None,
        "inventory_reference_mode": "furniture_only_atlas_unavailable",
    }
