import json
from pathlib import Path

import pytest

from application.render.render_response_stage import build_render_response_payload
from application.render.scale_validation_support import validate_scale_from_detection_map
from tools.replay.internal_render_replay import build_replay_job_payload, load_case_manifest


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "internal_live_case_99b7e7db.json"
REPLAY_MANIFEST_PATH = Path(__file__).parent / "replay_cases" / "9ffde1c0" / "manifest.json"


def _load_fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _items_by_key(fixture: dict) -> dict:
    return {item["target_key"]: item for item in fixture["items"]}


def _box_width(box: list[int]) -> int:
    return int(box[2]) - int(box[0])


def _box_height(box: list[int]) -> int:
    return int(box[3]) - int(box[1])


def _fixture_detected_rows(fixture: dict) -> list[dict]:
    width_px = 1365.0
    height_px = 768.0
    rows = []
    for index, item in enumerate(fixture["items"]):
        ymin, xmin, ymax, xmax = item["observed_box_2d"]
        rows.append(
            {
                "label": item["label"],
                "target_key": item["target_key"],
                "source_index": index,
                "bbox_norm": [xmin / width_px, ymin / height_px, xmax / width_px, ymax / height_px],
            }
        )
    return rows


def test_live_fixture_stays_logic_only_without_replay_artifact_catalog():
    fixture = _load_fixture()

    assert "selected_result_url" not in fixture
    assert "selected_result_sha256" not in fixture
    assert "scale_guide_url" not in fixture
    assert "bad_output_paths" not in fixture
    assert all("source_path" not in item for item in fixture["items"])


def test_replay_manifest_loads_case_specific_inputs_separately():
    case = load_case_manifest(REPLAY_MANIFEST_PATH)

    assert case.mode == "internal_itemized_job_render"
    assert case.entrypoint == "/async/render"
    assert case.form_data["dimensions"] == "4000*4000*2400"
    assert len(case.items_json) == 10
    assert Path(case.room_file["path"]).exists()
    assert Path(case.item_files["item-3"]).name.endswith("de-sede-ds-676.png")


def test_replay_manifest_builds_direct_job_payload_without_route_side_effects():
    case = load_case_manifest(REPLAY_MANIFEST_PATH)
    payload = build_replay_job_payload(case)

    assert payload["file_path"].endswith(".png")
    assert payload["dimensions"] == "4000*4000*2400"
    assert len(payload["moodboard_items"]) == 10
    assert payload["moodboard_items"][2]["item_id"] == "item-3"
    assert payload["moodboard_items"][2]["path"].endswith("de-sede-ds-676.png")


def test_replay_manifest_validation_fails_early_for_missing_item_file(tmp_path: Path):
    broken_manifest = tmp_path / "broken_manifest.json"
    broken_manifest.write_text(
        json.dumps(
            {
                "mode": "internal_itemized_job_render",
                "entrypoint": "/async/render",
                "form_data": {"room": "livingroom", "style": "Customize", "variant": "1"},
                "room_file": {"path": "tests/replay_cases/9ffde1c0_compare/assets/room.png"},
                "item_files": {"item-1": "tests/replay_cases/9ffde1c0/missing_item.png"},
                "items_json": [
                    {"client_id": "item-1", "name": "Chair", "category": "chair", "dims_mm": {"width_mm": 1, "depth_mm": 1, "height_mm": 1}}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Item file does not exist"):
        load_case_manifest(broken_manifest)


def test_replay_manifest_validation_fails_early_for_invalid_mode_type(tmp_path: Path):
    broken_manifest = tmp_path / "broken_mode_manifest.json"
    broken_manifest.write_text(
        json.dumps(
            {
                "mode": 123,
                "entrypoint": "/async/render",
                "form_data": {"room": "livingroom", "style": "Customize", "variant": "1"},
                "room_file": {"path": "tests/replay_cases/9ffde1c0/missing_room.png"},
                "item_files": {"item-1": "tests/replay_cases/9ffde1c0/missing_item.png"},
                "items_json": [
                    {"client_id": "item-1", "name": "Chair", "category": "chair", "dims_mm": {"width_mm": 1, "depth_mm": 1, "height_mm": 1}}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mode must be a non-empty string"):
        load_case_manifest(broken_manifest)


def test_replay_manifest_validation_fails_early_for_invalid_item_file_value(tmp_path: Path):
    broken_manifest = tmp_path / "broken_item_file_manifest.json"
    broken_manifest.write_text(
        json.dumps(
            {
                "mode": "internal_itemized_job_render",
                "entrypoint": "/async/render",
                "form_data": {"room": "livingroom", "style": "Customize", "variant": "1"},
                "room_file": {
                    "path": "outputs/job_9ffde1c0_artifacts/raw_1776063916_d6c594db_input_de0d4bc5_raw_1776063898_0e34b3e5_room.png"
                },
                "item_files": {"item-1": 123},
                "items_json": [
                    {"client_id": "item-1", "name": "Chair", "category": "chair", "dims_mm": {"width_mm": 1, "depth_mm": 1, "height_mm": 1}}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Item file path must be a non-empty string"):
        load_case_manifest(broken_manifest)


@pytest.mark.xfail(strict=True, reason="Live rug scale regression remains open until Task 4 remediation.")
def test_live_case_rug_should_not_read_close_to_sofa_scale():
    fixture = _load_fixture()
    items = _items_by_key(fixture)
    sofa = items["internal_item-1_sofa_001"]
    rug = items["internal_item-4_rug_004"]

    observed_ratio = _box_width(rug["observed_box_2d"]) / _box_width(sofa["observed_box_2d"])
    expected_ratio = rug["requested_dims_mm"]["width_mm"] / sofa["requested_dims_mm"]["width_mm"]

    assert abs(observed_ratio - expected_ratio) <= 0.18


@pytest.mark.xfail(strict=True, reason="Tiny lamp prominence regression remains open until Task 4 remediation.")
def test_live_case_tiny_floor_lamp_should_stay_visibly_tiny_against_sofa():
    fixture = _load_fixture()
    items = _items_by_key(fixture)
    sofa = items["internal_item-1_sofa_001"]
    tiny_lamp = items["internal_item-9_floor-lamp_009"]

    observed_ratio = _box_height(tiny_lamp["observed_box_2d"]) / _box_height(sofa["observed_box_2d"])
    expected_ratio = tiny_lamp["requested_dims_mm"]["height_mm"] / sofa["requested_dims_mm"]["height_mm"]

    assert observed_ratio <= expected_ratio + 0.10


def test_live_case_validator_flags_rug_and_tiny_lamp_pairwise_rules():
    fixture = _load_fixture()
    items = []
    for index, item in enumerate(fixture["items"]):
        items.append(
            {
                "label": item["label"],
                "category": item.get("category"),
                "target_key": item["target_key"],
                "source_index": index,
                "dims_mm": item["requested_dims_mm"],
            }
        )

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        detected_rows=_fixture_detected_rows(fixture),
        primary_target_key="internal_item-1_sofa_001",
    )

    assert ok is False
    assert issues
    assert "rug_vs_anchor_footprint" in diagnostics["failed_rules"]
    assert "tiny_item_vs_anchor_height" in diagnostics["failed_rules"]


def test_build_render_response_payload_keeps_replay_debug_metadata():
    payload = build_render_response_payload(
        std_path="outputs/std.png",
        step1_img="outputs/empty.png",
        scale_guide_path=None,
        generated_results=["outputs/variant_a.png", "outputs/variant_b.png"],
        moodboard_url=None,
        furniture_data=[{"label": "sofa", "target_key": "internal_item-1_sofa_001"}],
        volume_ranking=[{"rank": 1, "target_key": "internal_item-1_sofa_001"}],
        prefix_main_user="internal/mainrendered/user-photos/",
        prefix_main_empty="internal/mainrendered/empty/",
        prefix_main_rendered="internal/mainrendered/rendered/",
        resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{Path(path).name}",
        selected_result_index=0,
        selected_result_reason="review_pass_ranked",
        selected_variant_review={
            "review_pass": True,
            "matched_source_count": 4,
            "repair_applied": True,
            "repair_attempt_count": 1,
            "repair_target_keys": ["internal_item-1_sofa_001"],
            "scalecheck_diagnostics": {
                "matched_items": {"internal_item-1_sofa_001": {"target_key": "internal_item-1_sofa_001"}},
                "unmatched_items": [],
            },
        },
        variant_diagnostics=[
            {
                "path": "outputs/variant_a.png",
                "scalecheck_fail_count": 0,
                "scalecheck_retry_count": 0,
                "scale_check_failed": False,
                "scalecheck_issues": [],
                "scalecheck_failed_rules": [],
            },
            {
                "path": "outputs/variant_b.png",
                "scalecheck_fail_count": 1,
                "scalecheck_retry_count": 1,
                "scale_check_failed": True,
                "scalecheck_issues": ["rug_vs_anchor_footprint"],
                "scalecheck_failed_rules": ["rug_vs_anchor_footprint"],
            },
        ],
        include_replay_debug=True,
    )

    assert payload["selected_result_index"] == 0
    assert payload["selected_result_filename"] == "variant_a.png"
    assert payload["selected_result_reason"] == "review_pass_ranked"
    assert payload["selected_variant_review"]["matched_source_count"] == 4
    assert payload["selected_variant_review"]["repair_applied"] is True
    assert payload["selected_variant_review"]["repair_target_keys"] == ["internal_item-1_sofa_001"]
    assert payload["selected_item_review"][0]["status"] == "matched"
    assert payload["variant_diagnostics"][1]["scalecheck_failed_rules"] == ["rug_vs_anchor_footprint"]
