import json

from PIL import Image, ImageDraw

from application.details.detail_angle_quality import assess_angle_camera_guide


def _save_architecture(path, *, shifted: bool = False) -> None:
    image = Image.new("RGB", (640, 360), color=(232, 228, 220))
    draw = ImageDraw.Draw(image)
    vanishing_x = 370 if shifted else 320
    draw.line((0, 0, vanishing_x, 170), fill=(70, 70, 70), width=4)
    draw.line((639, 0, vanishing_x, 170), fill=(70, 70, 70), width=4)
    draw.line((0, 359, vanishing_x, 170), fill=(90, 90, 90), width=4)
    draw.line((639, 359, vanishing_x, 170), fill=(90, 90, 90), width=4)
    window_left = 455 if shifted else 430
    draw.rectangle((window_left, 55, window_left + 125, 210), outline=(45, 75, 115), width=8)
    for x in range(20, 620, 25):
        draw.line((x, 330, vanishing_x, 170), fill=(180, 170, 155), width=1)
    image.save(path, format="PNG")


def _guide_payload(**overrides) -> dict:
    payload = {
        "same_frame_or_crop": False,
        "inferred_camera_translation": "right",
        "room_topology_preserved": True,
        "architecture_projection_coherent": True,
        "background_only_rotation": False,
        "lateral_parallax_visible": True,
        "downward_pitch_visible": False,
        "floor_or_top_plane_exposure_increased": False,
        "movable_furniture_present": False,
        "large_artificial_panel": False,
        "severe_geometry_warp": False,
        "camera_motion_score": 0.82,
        "confidence": 0.91,
        "reasons": [],
    }
    payload.update(overrides)
    return payload


def _analysis_response(payload: dict):
    return type("Response", (), {"text": json.dumps(payload)})()


def _assess(tmp_path, payload: dict, *, camera_mode="side_angle", focus_side="right", same_file=False):
    source_path = tmp_path / "source.png"
    guide_path = source_path if same_file else tmp_path / "guide.png"
    _save_architecture(source_path)
    if not same_file:
        _save_architecture(guide_path, shifted=True)
    return assess_angle_camera_guide(
        str(source_path),
        str(guide_path),
        camera_mode=camera_mode,
        focus_side=focus_side,
        call_analysis_with_failover=lambda *_args, **_kwargs: _analysis_response(payload),
        analysis_model_name="analysis-model",
        safe_json_from_model_text=json.loads,
    )


def test_angle_camera_guide_rejects_identical_source_architecture_frame(tmp_path):
    result = _assess(tmp_path, _guide_payload(), same_file=True)

    assert result["passed"] is False
    assert "same_frame_or_crop" in result["reject_reasons"]
    assert result["metrics"]["same_frame_score"] >= 0.99


def test_angle_camera_guide_incomplete_schema_fails_closed(tmp_path):
    result = _assess(tmp_path, {"same_frame_or_crop": False})

    assert result["passed"] is False
    assert "model_qc_incomplete" in result["reject_reasons"]
    assert "insufficient_camera_motion" in result["reject_reasons"]
    assert "model_qc_low_confidence" in result["reject_reasons"]


def test_angle_camera_guide_rejects_each_common_hard_failure(tmp_path):
    hard_failures = [
        ("same_frame_or_crop", True, "same_frame_or_crop"),
        ("room_topology_preserved", False, "room_topology_changed"),
        ("architecture_projection_coherent", False, "architecture_projection_incoherent"),
        ("background_only_rotation", True, "background_only_rotation"),
        ("movable_furniture_present", True, "movable_furniture_present"),
        ("large_artificial_panel", True, "large_artificial_panel"),
        ("severe_geometry_warp", True, "severe_geometry_warp"),
        ("camera_motion_score", 0.54, "insufficient_camera_motion"),
        ("confidence", 0.54, "model_qc_low_confidence"),
    ]

    for key, value, reason in hard_failures:
        result = _assess(tmp_path, _guide_payload(**{key: value}))
        assert result["passed"] is False
        assert reason in result["reject_reasons"]


def test_angle_camera_guide_wrong_side_is_physical_pass_but_slot_fail(tmp_path):
    result = _assess(tmp_path, _guide_payload(inferred_camera_translation="right"), focus_side="left")

    assert result["passed"] is True
    assert result["passed_for_requested_slot"] is False
    assert result["direction_only_mismatch"] is True
    assert result["reject_reasons"] == []
    assert result["warnings"] == ["direction_only_mismatch"]
    assert result["camera_direction_matches"] is False


def test_angle_camera_guide_side_requires_lateral_physical_indicators(tmp_path):
    result = _assess(tmp_path, _guide_payload(lateral_parallax_visible=False))

    assert result["passed"] is False
    assert "lateral_parallax_not_visible" in result["reject_reasons"]


def test_angle_camera_guide_overview_accepts_up_with_downward_pitch_and_more_planes(tmp_path):
    result = _assess(
        tmp_path,
        _guide_payload(
            inferred_camera_translation="up",
            lateral_parallax_visible=False,
            downward_pitch_visible=True,
            floor_or_top_plane_exposure_increased=True,
        ),
        camera_mode="overview_angle",
        focus_side=None,
    )

    assert result["passed"] is True
    assert result["passed_for_requested_slot"] is True
    assert result["inferred_camera_translation"] == "up"


def test_angle_camera_guide_overview_requires_downward_pitch_and_more_planes(tmp_path):
    result = _assess(
        tmp_path,
        _guide_payload(inferred_camera_translation="up"),
        camera_mode="overview_angle",
        focus_side=None,
    )

    assert result["passed"] is False
    assert "downward_pitch_not_visible" in result["reject_reasons"]
    assert "floor_or_top_plane_exposure_not_increased" in result["reject_reasons"]
