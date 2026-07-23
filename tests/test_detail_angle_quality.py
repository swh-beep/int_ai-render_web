import json

from PIL import Image, ImageDraw

from application.details.detail_angle_quality import assess_angle_candidate


def _save_structured_room(path, *, shifted: bool = False, slab_side: str | None = None) -> None:
    image = Image.new("RGB", (640, 360), color=(232, 228, 220))
    draw = ImageDraw.Draw(image)

    vanishing_x = 360 if shifted else 320
    draw.line((0, 0, vanishing_x, 170), fill=(70, 70, 70), width=4)
    draw.line((639, 0, vanishing_x, 170), fill=(70, 70, 70), width=4)
    draw.line((0, 359, vanishing_x, 170), fill=(90, 90, 90), width=4)
    draw.line((639, 359, vanishing_x, 170), fill=(90, 90, 90), width=4)

    window_left = 445 if shifted else 430
    draw.rectangle((window_left, 55, window_left + 125, 210), outline=(45, 75, 115), width=8)
    for offset in range(20, 120, 20):
        draw.line(
            (window_left + offset, 60, window_left + offset, 205),
            fill=(105, 125, 150),
            width=2,
        )

    sofa_left = 170 if shifted else 205
    draw.rectangle((sofa_left, 215, sofa_left + 250, 300), fill=(145, 105, 80), outline=(55, 45, 40), width=5)
    draw.rectangle((sofa_left + 35, 185, sofa_left + 210, 245), fill=(160, 120, 90), outline=(55, 45, 40), width=5)
    draw.ellipse((280 if shifted else 250, 265, 410 if shifted else 380, 330), fill=(80, 70, 60))

    for x in range(20, 620, 25):
        draw.line((x, 330, vanishing_x, 170), fill=(180, 170, 155), width=1)

    if slab_side == "left":
        draw.rectangle((0, 0, 205, 359), fill=(126, 126, 126))
    elif slab_side == "right":
        draw.rectangle((435, 0, 639, 359), fill=(126, 126, 126))

    image.save(path, format="PNG")


def _passing_model_payload() -> dict:
    return {
        "same_frame_or_crop": False,
        "camera_direction_matches": True,
        "room_topology_preserved": True,
        "background_only_rotation": False,
        "furniture_projection_coherent": True,
        "large_artificial_panel": False,
        "severe_geometry_warp": False,
        "camera_motion_score": 0.82,
        "confidence": 0.91,
        "reasons": [],
    }


def _analysis_response(payload: dict):
    return type("Response", (), {"text": json.dumps(payload)})()


def test_angle_quality_rejects_same_frame_even_when_model_claims_pass(tmp_path):
    source_path = tmp_path / "source.png"
    _save_structured_room(source_path)

    result = assess_angle_candidate(
        str(source_path),
        str(source_path),
        camera_mode="side_angle",
        focus_side="left",
        call_analysis_with_failover=lambda *_args, **_kwargs: _analysis_response(_passing_model_payload()),
        analysis_model_name="analysis-model",
        safe_json_from_model_text=json.loads,
    )

    assert result["passed"] is False
    assert "same_frame_or_crop" in result["reject_reasons"]
    assert result["metrics"]["same_frame_score"] >= 0.99


def test_angle_quality_accepts_coherent_camera_move(tmp_path):
    source_path = tmp_path / "source.png"
    candidate_path = tmp_path / "candidate.png"
    _save_structured_room(source_path)
    _save_structured_room(candidate_path, shifted=True)
    for path in (source_path, candidate_path):
        with Image.open(path) as opened:
            opened.resize((1600, 900), Image.Resampling.LANCZOS).save(path, format="PNG")
    captured = {}

    def _call_analysis(model_name, content, request_options, safety_settings, **kwargs):
        captured["model_name"] = model_name
        captured["content"] = list(content)
        captured["request_options"] = dict(request_options)
        captured["kwargs"] = dict(kwargs)
        return _analysis_response(_passing_model_payload())

    result = assess_angle_candidate(
        str(source_path),
        str(candidate_path),
        camera_mode="side_angle",
        focus_side="right",
        call_analysis_with_failover=_call_analysis,
        analysis_model_name="analysis-model",
        safe_json_from_model_text=json.loads,
    )

    assert result["passed"] is True
    assert result["reject_reasons"] == []
    assert captured["model_name"] == "analysis-model"
    assert captured["request_options"]["response_mime_type"] == "application/json"
    assert captured["request_options"]["temperature"] == 0
    model_images = [part for part in captured["content"] if isinstance(part, Image.Image)]
    assert len(model_images) == 2
    assert [image.size for image in model_images] == [(1024, 576), (1024, 576)]
    assert "RIGHT side of the source viewpoint" in captured["content"][0]
    assert "yaw coherently back into the room" in captured["content"][0]
    assert "real lateral camera translation" in captured["content"][0]


def test_angle_quality_rejects_new_uniform_side_slab(tmp_path):
    source_path = tmp_path / "source.png"
    candidate_path = tmp_path / "candidate.png"
    _save_structured_room(source_path)
    _save_structured_room(candidate_path, shifted=True, slab_side="right")

    result = assess_angle_candidate(
        str(source_path),
        str(candidate_path),
        camera_mode="side_angle",
        focus_side="left",
        call_analysis_with_failover=lambda *_args, **_kwargs: _analysis_response(_passing_model_payload()),
        analysis_model_name="analysis-model",
        safe_json_from_model_text=json.loads,
    )

    assert result["passed"] is False
    assert "large_artificial_panel" in result["reject_reasons"]
    assert result["metrics"]["uniform_side_panel_fraction"] >= 0.25


def test_angle_quality_rejects_background_only_rotation_from_model_qc(tmp_path):
    source_path = tmp_path / "source.png"
    candidate_path = tmp_path / "candidate.png"
    _save_structured_room(source_path)
    _save_structured_room(candidate_path, shifted=True)
    payload = _passing_model_payload()
    payload["background_only_rotation"] = True
    payload["furniture_projection_coherent"] = False
    payload["reasons"] = ["Furniture stayed frontal while the room changed."]

    result = assess_angle_candidate(
        str(source_path),
        str(candidate_path),
        camera_mode="side_angle",
        focus_side="right",
        call_analysis_with_failover=lambda *_args, **_kwargs: _analysis_response(payload),
        analysis_model_name="analysis-model",
        safe_json_from_model_text=json.loads,
    )

    assert result["passed"] is False
    assert "background_only_rotation" in result["reject_reasons"]
    assert "furniture_projection_incoherent" in result["reject_reasons"]


def test_angle_quality_rejects_when_required_model_qc_is_unavailable(tmp_path):
    source_path = tmp_path / "source.png"
    candidate_path = tmp_path / "candidate.png"
    _save_structured_room(source_path)
    _save_structured_room(candidate_path, shifted=True)

    result = assess_angle_candidate(
        str(source_path),
        str(candidate_path),
        camera_mode="overview_angle",
        call_analysis_with_failover=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider down")),
        analysis_model_name="analysis-model",
        safe_json_from_model_text=json.loads,
        require_model_qc=True,
    )

    assert result["passed"] is False
    assert "model_qc_unavailable" in result["reject_reasons"]
    assert result["model_checked"] is False


def test_angle_quality_rejects_incomplete_model_contract(tmp_path):
    source_path = tmp_path / "source.png"
    candidate_path = tmp_path / "candidate.png"
    _save_structured_room(source_path)
    _save_structured_room(candidate_path, shifted=True)

    result = assess_angle_candidate(
        str(source_path),
        str(candidate_path),
        camera_mode="side_angle",
        focus_side="left",
        call_analysis_with_failover=lambda *_args, **_kwargs: _analysis_response(
            {"same_frame_or_crop": False}
        ),
        analysis_model_name="analysis-model",
        safe_json_from_model_text=json.loads,
        require_model_qc=True,
    )

    assert result["passed"] is False
    assert "model_qc_incomplete" in result["reject_reasons"]
    assert "insufficient_camera_motion" in result["reject_reasons"]
    assert "model_qc_low_confidence" in result["reject_reasons"]
