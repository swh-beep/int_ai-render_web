import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from application.details import detail_generation_stage
from application.details.detail_generation_stage import (
    _box_to_pixels,
    _expand_bounds,
    _fit_bounds_to_ratio,
    generate_detail_view,
)


def _landscape_png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1600, 900), color=(245, 245, 245)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_generate_detail_view_initial_detail_prefers_editorial_model_generation_over_crop_extract(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1200, 1500), color=(245, 245, 245)).save(source_path, format="PNG")
    captured = {}

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        captured["model_name"] = model_name
        captured["prompt"] = content[0]
        captured["request_options"] = dict(request_options)
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_path.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Accent Chair",
            "target_key": "chair_01",
            "target_label": "Accent Chair",
            "ratio": "4:5",
            "prompt": "render an editorial portrait of the chair",
        },
        "unitcase",
        1,
        furniture_data=[
            {
                "label": "Accent Chair",
                "target_key": "chair_01",
                "category": "chair",
                "box_2d": [220, 310, 760, 610],
                "source_box_2d": [210, 300, 770, 620],
                "box_source": "detail_current_image_analysis",
                "placement_contract": {"zone": "left", "edge": "window"},
                "layout_envelope": {"wall": "north", "floor_contact": True},
                "crop_path": str(tmp_path / "stale-cutout.png"),
            }
        ],
        materialize_input=lambda path, prefix: path,
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert result["generation_mode"] == "model_regeneration"
        assert result["style_name"] == "Detail: Accent Chair"
        assert result["cutout_ref_count"] == 0
        assert captured["model_name"] == "gemini-3.1-flash-image"
        assert captured["request_options"]["aspect_ratio"] == "4:5"
        assert "Create a source-constrained editorial reframe of the exact same finished room." in captured["prompt"]
        assert "Do not create a new camera angle that reveals unseen sides of furniture." in captured["prompt"]
        assert "If a more dynamic view would require moving, rotating, replacing, or reinterpreting any object" in captured["prompt"]
        assert "Create a NEW editorial close-up" not in captured["prompt"]
        assert "Do NOT turn this into a simple digital crop" not in captured["prompt"]
        assert "real parallax" not in captured["prompt"]
        assert "<TARGET ANCHOR>" in captured["prompt"]
        assert "ORIGINAL CACHED TARGET BOX" in captured["prompt"]
        assert "BOX SOURCE: detail_current_image_analysis" in captured["prompt"]
        assert 'PLACEMENT CONTRACT: {"zone":"left","edge":"window"}' in captured["prompt"]
        assert 'LAYOUT ENVELOPE: {"wall":"north","floor_contact":true}' in captured["prompt"]
        assert "PRESERVE THE MAIN-SHOT LAYOUT" in captured["prompt"]
        assert output_path.exists()

        with Image.open(output_path) as rendered:
            ratio = rendered.size[0] / rendered.size[1]
            assert abs(ratio - (4.0 / 5.0)) < 0.02
            assert rendered.size[1] > rendered.size[0]
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_simple_scene_detail_uses_main_image_only(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1200, 1500), color=(245, 245, 245)).save(source_path, format="PNG")
    captured = {}

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        captured["content"] = list(content)
        captured["request_options"] = dict(request_options)
        captured["prompt"] = content[0]
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_path.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Floor Lamp",
            "target_key": "lamp_01",
            "target_label": "Floor Lamp",
            "ratio": "4:5",
            "simple_scene_detail": True,
            "prompt": "legacy prompt should not be used",
        },
        "unitcase",
        2,
        furniture_data=[{"label": "Floor Lamp", "crop_path": str(tmp_path / "cutout.png")}],
        materialize_input=lambda path, prefix: (_ for _ in ()).throw(AssertionError("cutout refs should not be materialized")),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert result["generation_mode"] == "simple_scene_detail"
        assert result["cutout_ref_count"] == 0
        assert captured["request_options"]["aspect_ratio"] == "4:5"
        assert len(captured["content"]) == 3
        assert "focused on the Floor Lamp area" in captured["prompt"]
        assert "legacy prompt should not be used" not in captured["prompt"]
        assert "<TARGET ANCHOR>" not in captured["prompt"]
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_uses_simple_scene_prompt_with_gemini_image_model(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1600, 2000), color=(245, 245, 245)).save(source_path, format="PNG")
    captured = {}

    def _call_gpt_image(model_name, content, request_options, safety_settings, **kwargs):
        captured["model_name"] = model_name
        captured["content"] = list(content)
        captured["prompt"] = content[0]
        captured["request_options"] = dict(request_options)
        captured["kwargs"] = dict(kwargs)
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_path.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Floor Lamp",
            "target_key": "lamp_01",
            "target_label": "Floor Lamp",
            "ratio": "4:5",
            "simple_scene_detail": True,
            "prompt": "legacy prompt should not be used",
        },
        "unitcase",
        22,
        furniture_data=[{"label": "Floor Lamp", "crop_path": str(tmp_path / "cutout.png")}],
        materialize_input=lambda path, prefix: (_ for _ in ()).throw(AssertionError("cutout refs should not be materialized")),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gpt_image,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert result["generation_mode"] == "simple_scene_detail"
        assert result["cutout_ref_count"] == 0
        assert captured["model_name"] == "gemini-3.1-flash-image"
        assert captured["request_options"]["aspect_ratio"] == "4:5"
        assert captured["request_options"]["timeout"] == 180.0
        assert captured["request_options"]["thinking_level"] == "high"
        assert "quality" not in captured["request_options"]
        assert len(captured["content"]) == 3
        assert "photorealistic editorial detail photograph focused on the Floor Lamp area" in captured["prompt"]
        assert "This is not a redesign task." in captured["prompt"]
        assert "Keep every furniture/decor item's shape, count, placement, scale, material, and color unchanged." in captured["prompt"]
        assert "Use a source-constrained crop/reframe from the main image camera." in captured["prompt"]
        assert "If a more dynamic view would require moving, rotating, replacing, or reinterpreting any object" in captured["prompt"]
        assert "legacy prompt should not be used" not in captured["prompt"]
        assert "no text, no watermark" in captured["prompt"]
        assert captured["kwargs"]["log_tag"] == "Detail.Generate.Simple"
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_uses_object_centered_prompt_for_small_decor(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1200, 1500), color=(245, 245, 245)).save(source_path, format="PNG")

    captured = {}

    def _call_gpt_image(model_name, content, request_options, safety_settings, **kwargs):
        captured["prompt"] = content[0]
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_path.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Framed Art",
            "target_key": "art_01",
            "target_label": "Framed Art",
            "target_category_canonical": "decor",
            "ratio": "4:5",
            "simple_scene_detail": True,
        },
        "unitcase",
        4,
        furniture_data=[],
        materialize_input=lambda path, prefix: None,
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gpt_image,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert "photorealistic editorial detail photograph focused on the Framed Art area" in captured["prompt"]
        assert "This is not a redesign task." in captured["prompt"]
        assert "Use a source-constrained crop/reframe from the main image camera." in captured["prompt"]
        assert "If a more dynamic view would require moving, rotating, replacing, or reinterpreting any object" in captured["prompt"]
        assert "no text, no watermark" in captured["prompt"]
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_passes_vertical_aspect_ratio_and_high_thinking_to_gemini(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1200, 1500), color=(245, 245, 245)).save(source_path, format="PNG")

    captured = {}

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        captured["model_name"] = model_name
        captured["request_options"] = dict(request_options)
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_path.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Accent Chair",
            "target_key": "chair_01",
            "target_label": "Accent Chair",
            "ratio": "4:5",
            "prompt": "render a detail crop",
        },
        "unitcase",
        2,
        furniture_data=[],
        materialize_input=lambda path, prefix: path,
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert captured["model_name"] == "gemini-3.1-flash-image"
        assert captured["request_options"]["aspect_ratio"] == "4:5"
        assert captured["request_options"]["thinking_level"] == "high"
        assert captured["request_options"]["include_thoughts"] is False
        assert output_path.exists()
        with Image.open(output_path) as rendered:
            assert abs((rendered.size[0] / rendered.size[1]) - (4.0 / 5.0)) < 0.02
        raw_output_path = Path(str(output_path).replace("_aspect.png", ".png"))
        if raw_output_path != output_path:
            assert not raw_output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_honors_style_ratio_for_overview_styles(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1200, 1500), color=(245, 245, 245)).save(source_path, format="PNG")

    captured = {}

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        captured["request_options"] = dict(request_options)
        captured["prompt"] = content[0]
        captured["content"] = list(content)
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_path.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "High Angle Overview",
            "prompt": "render a standing-height overview",
        },
        "unitcase",
        3,
        furniture_data=[],
        materialize_input=lambda path, prefix: path,
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert captured["request_options"]["aspect_ratio"] == "4:5"
        assert captured["request_options"]["thinking_level"] == "high"
        assert captured["request_options"]["include_thoughts"] is False
        assert "OUTPUT ASPECT RATIO: 4:5" in captured["prompt"]
        assert output_path.exists()
        with Image.open(output_path) as rendered:
            assert abs((rendered.size[0] / rendered.size[1]) - (4.0 / 5.0)) < 0.02
        raw_output_path = Path(str(output_path).replace("_aspect.png", ".png"))
        if raw_output_path != output_path:
            assert not raw_output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_honors_landscape_ratio_for_angle_styles(tmp_path, monkeypatch):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1600, 900), color=(245, 245, 245)).save(source_path, format="PNG")
    cutout_path = tmp_path / "cutout.png"
    Image.new("RGB", (300, 300), color=(180, 180, 180)).save(cutout_path, format="PNG")

    captured = {}
    correction_calls = []

    def _correct(path, **kwargs):
        correction_calls.append((path, kwargs))
        return type("Correction", (), {"path": path})()

    monkeypatch.setattr(detail_generation_stage, "apply_reference_relative_white_balance", _correct)

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        captured["request_options"] = dict(request_options)
        captured["prompt"] = content[0]
        captured["content"] = list(content)
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": _landscape_png_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "High Angle Overview",
            "ratio": "16:9",
            "target_box_2d": [100, 100, 400, 400],
            "prompt": "render a standing-height landscape overview",
        },
        "unitcase",
        33,
        furniture_data=[{"label": "Accent Chair", "crop_path": str(cutout_path)}],
        materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("angle cutout refs should not be materialized")
        ),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert result["generation_mode"] == "angle_generation"
        assert result["camera_mode"] == "overview_angle"
        assert result["cutout_ref_count"] == 0
        assert result["cutout_ref_labels"] == []
        assert result["aspect_ratio"] == "16:9"
        assert captured["request_options"]["aspect_ratio"] == "16:9"
        assert captured["request_options"]["image_size"] == "4K"
        assert "OUTPUT ASPECT RATIO: 16:9" in captured["prompt"]
        assert "<SCENE LOCK: SAME ROOM, REAL HIGH CAMERA MOVE>" in captured["prompt"]
        assert "REAL HIGH CAMERA MOVE REQUIRED" in captured["prompt"]
        assert "Raise the camera above the main viewpoint and pitch downward" in captured["prompt"]
        assert "real high-camera perspective with changed projected positions and top-plane visibility" in captured["prompt"]
        assert "WORLD-SPACE POSE LOCK" in captured["prompt"]
        assert "projected screen positions, visible faces, occlusions, and perspective must change naturally" in captured["prompt"]
        assert "Do not create a new camera angle" not in captured["prompt"]
        assert "SOURCE-CONSTRAINED REFRAME" not in captured["prompt"]
        assert "facing direction from the main render" not in captured["prompt"]
        assert "PRIMARY TARGET IN-ROOM CROP" not in captured["content"]
        assert "this is a room angle shot, not an object close-up" in captured["prompt"]
        assert "focus on the specified target area only" not in captured["prompt"]
        assert correction_calls == [
            (
                str(output_path),
                {"reference_path": str(source_path), "stage_name": "detail_high_angle"},
            )
        ]
        with Image.open(output_path) as rendered:
            assert abs((rendered.size[0] / rendered.size[1]) - (16.0 / 9.0)) < 0.02
            assert rendered.size[0] > rendered.size[1]
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_uses_side_camera_scene_lock_for_side_angles(tmp_path, monkeypatch):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1600, 900), color=(245, 245, 245)).save(source_path, format="PNG")
    empty_room_path = tmp_path / "empty-room.png"
    Image.new("RGB", (1600, 900), color=(232, 232, 228)).save(empty_room_path, format="PNG")
    cutout_path = tmp_path / "cutout.png"
    Image.new("RGB", (300, 300), color=(180, 180, 180)).save(cutout_path, format="PNG")

    captured = {}
    correction_calls = []

    def _correct(path, **kwargs):
        correction_calls.append((path, kwargs))
        return type("Correction", (), {"path": path})()

    monkeypatch.setattr(detail_generation_stage, "apply_reference_relative_white_balance", _correct)

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        captured["prompt"] = content[0]
        captured["request_options"] = dict(request_options)
        captured["content"] = list(content)
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": _landscape_png_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Side Composition (Focus Right)",
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "right",
            "empty_room_path": str(empty_room_path),
            "room_dims_contract": {"dims_mm_center": {"width_mm": 5000}},
            "geometry_contract": {"geometry_source": "explicit_dimensions"},
            "scene_contract": {"critical_item_keys": ["sofa_01"]},
            "placement_plan": {"anchor_item_key": "sofa_01"},
            "prompt": "render a materially different right-side angle",
        },
        "unitcase",
        34,
        furniture_data=[{"label": "Sofa", "crop_path": str(cutout_path)}],
        materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("angle cutout refs should not be materialized")
        ),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert result["generation_mode"] == "angle_generation"
        assert result["camera_mode"] == "side_angle"
        assert result["focus_side"] == "right"
        assert result["cutout_ref_count"] == 0
        assert result["cutout_ref_labels"] == []
        assert captured["request_options"]["aspect_ratio"] == "16:9"
        assert captured["request_options"]["image_size"] == "4K"
        assert "<SCENE LOCK: SAME ROOM, REAL SIDE CAMERA MOVE>" in captured["prompt"]
        assert "REAL SIDE CAMERA MOVE REQUIRED" in captured["prompt"]
        assert "real side-camera parallax" in captured["prompt"]
        assert "changed projected positions, side planes, and occlusions" in captured["prompt"]
        assert "This must be a new side camera viewpoint, not a crop, zoom, or source reframe." in captured["prompt"]
        assert "crop out or minimize the opposite side of the room" in captured["prompt"]
        assert "do NOT relocate objects to keep them visible" in captured["prompt"]
        assert "<CRITICAL: WORLD-SPACE SCENE LOCK" in captured["prompt"]
        assert "positions must remain EXACTLY the same as the input image" not in captured["prompt"]
        assert "WORLD-SPACE POSE LOCK" in captured["prompt"]
        assert "projected screen positions, visible faces, occlusions, and perspective must change naturally" in captured["prompt"]
        assert "<ANGLE SCENE CONTRACT>" in captured["prompt"]
        assert "ROOM DIMS CONTRACT" in captured["prompt"]
        assert "GEOMETRY CONTRACT" in captured["prompt"]
        assert "SCENE CONTRACT" in captured["prompt"]
        assert "PLACEMENT PLAN" in captured["prompt"]
        assert "The furnished main image is the sole truth for furniture" in captured["prompt"]
        assert "Never remove furniture because it is absent from the empty-room reference" in captured["prompt"]
        assert "Do not create a new camera angle" not in captured["prompt"]
        assert "SOURCE-CONSTRAINED REFRAME" not in captured["prompt"]
        assert "facing direction from the main render" not in captured["prompt"]
        assert "this is a room angle shot, not an object close-up" in captured["prompt"]
        assert "focus on the specified target area only" not in captured["prompt"]
        assert not any(
            isinstance(part, str) and "Side Focus Composition Mask" in part
            for part in captured["content"]
        )
        assert not any(
            isinstance(part, str) and "CUTOUT" in part
            for part in captured["content"]
        )
        assert "Empty Room Architecture Reference (same room topology before furnishing):" in captured["content"]
        assert len([part for part in captured["content"] if isinstance(part, Image.Image)]) == 2
        assert result["aspect_ratio"] == "16:9"
        assert correction_calls == [
            (
                str(output_path),
                {"reference_path": str(source_path), "stage_name": "detail_side_angle"},
            )
        ]
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_rejects_angle_same_frame_qc_candidates(tmp_path, monkeypatch):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1600, 900), color=(245, 245, 245)).save(source_path, format="PNG")
    monkeypatch.setattr(detail_generation_stage, "DETAIL_ANGLE_QC_MAX_ATTEMPTS", 2)

    def _write_corrected_sibling(path, **_kwargs):
        corrected_path = f"{path}.wb.jpg"
        Path(corrected_path).write_bytes(Path(path).read_bytes())
        return type("Correction", (), {"path": corrected_path})()

    monkeypatch.setattr(
        detail_generation_stage,
        "apply_reference_relative_white_balance",
        _write_corrected_sibling,
    )

    generation_calls = []
    analysis_calls = []

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        generation_calls.append({"request_options": dict(request_options), "prompt": content[0]})
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_path.read_bytes()})()})()],
            },
        )()

    def _call_analysis(model_name, content, request_options, safety_settings, **kwargs):
        analysis_calls.append({"model_name": model_name, "request_options": dict(request_options)})
        payload = {
            "same_frame_or_crop": False,
            "camera_direction_matches": True,
            "room_topology_preserved": True,
            "background_only_rotation": False,
            "furniture_projection_coherent": True,
            "large_artificial_panel": False,
            "severe_geometry_warp": False,
            "camera_motion_score": 0.9,
            "confidence": 0.9,
            "reasons": [],
        }
        return type("Resp", (), {"text": json.dumps(payload)})()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Side Composition (Focus Left)",
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "left",
            "prompt": "render a materially different left-side angle",
        },
        "same-frame-qc",
        35,
        furniture_data=[],
        materialize_input=lambda path, prefix: path,
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
        call_analysis_with_failover=_call_analysis,
        analysis_model_name="analysis-model",
        safe_json_from_model_text=json.loads,
    )

    leaked = list(Path("outputs").glob("detail_*_same-frame-qc_35_*"))
    try:
        assert result is None
        assert len(generation_calls) == 2
        assert generation_calls[0]["request_options"]["image_size"] == "4K"
        assert "<ANGLE QC RETRY FEEDBACK>" not in generation_calls[0]["prompt"]
        assert "<ANGLE QC RETRY FEEDBACK>" in generation_calls[1]["prompt"]
        assert "same_frame_or_crop" in generation_calls[1]["prompt"]
        assert len(analysis_calls) == 2
        assert analysis_calls[0]["request_options"]["response_mime_type"] == "application/json"
        assert leaked == []
    finally:
        for path in leaked:
            if path.exists():
                path.unlink()


def test_generate_detail_view_retries_angle_then_returns_only_qc_passed_candidate(tmp_path, monkeypatch):
    source_path = tmp_path / "room.png"

    def _room_bytes(*, shifted: bool) -> bytes:
        image = Image.new("RGB", (1600, 900), color=(232, 228, 220))
        draw = ImageDraw.Draw(image)
        vanishing_x = 900 if shifted else 800
        draw.line((0, 0, vanishing_x, 420), fill=(55, 55, 55), width=10)
        draw.line((1599, 0, vanishing_x, 420), fill=(55, 55, 55), width=10)
        draw.line((0, 899, vanishing_x, 420), fill=(80, 80, 80), width=10)
        draw.line((1599, 899, vanishing_x, 420), fill=(80, 80, 80), width=10)
        sofa_left = 360 if shifted else 500
        draw.rectangle((sofa_left, 500, sofa_left + 650, 760), fill=(145, 105, 80))
        draw.rectangle((sofa_left + 80, 400, sofa_left + 560, 590), fill=(160, 120, 90))
        draw.rectangle((1130 if shifted else 1080, 130, 1450, 530), outline=(45, 75, 115), width=18)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image.close()
        return buffer.getvalue()

    source_bytes = _room_bytes(shifted=False)
    shifted_bytes = _room_bytes(shifted=True)
    source_path.write_bytes(source_bytes)
    monkeypatch.setattr(detail_generation_stage, "DETAIL_ANGLE_QC_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(
        detail_generation_stage,
        "apply_reference_relative_white_balance",
        lambda path, **_kwargs: type("Correction", (), {"path": path})(),
    )

    generation_prompts = []

    def _call_gemini(_model_name, content, _request_options, _safety_settings, **_kwargs):
        generation_prompts.append(content[0])
        payload = source_bytes if len(generation_prompts) == 1 else shifted_bytes
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": payload})()})()],
            },
        )()

    passing_payload = {
        "same_frame_or_crop": False,
        "camera_direction_matches": True,
        "room_topology_preserved": True,
        "background_only_rotation": False,
        "furniture_projection_coherent": True,
        "large_artificial_panel": False,
        "severe_geometry_warp": False,
        "camera_motion_score": 0.86,
        "confidence": 0.92,
        "reasons": [],
    }

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Side Composition (Focus Right)",
            "ratio": "16:9",
            "focus_side": "right",
            "prompt": "render a coherent right-side camera move",
        },
        "retry-pass-qc",
        36,
        furniture_data=[],
        materialize_input=lambda path, prefix: path,
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
        call_analysis_with_failover=lambda *_args, **_kwargs: type(
            "Resp",
            (),
            {"text": json.dumps(passing_payload)},
        )(),
        analysis_model_name="analysis-model",
        safe_json_from_model_text=json.loads,
    )

    output_path = Path(result["path"])
    artifacts = list(Path("outputs").glob("detail_*_retry-pass-qc_36_*"))
    try:
        assert len(generation_prompts) == 2
        assert "<ANGLE QC RETRY FEEDBACK>" not in generation_prompts[0]
        assert "<ANGLE QC RETRY FEEDBACK>" in generation_prompts[1]
        assert result["generation_mode"] == "angle_generation"
        assert result["camera_mode"] == "side_angle"
        assert result["focus_side"] == "right"
        assert result["angle_qc_attempts"] == 2
        assert result["angle_qc"]["passed"] is True
        assert result["angle_qc"]["model_checked"] is True
        assert artifacts == [output_path]
    finally:
        for path in artifacts:
            if path.exists():
                path.unlink()


def test_generate_detail_view_sanitizes_invalid_ratio_to_vertical_canvas(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1200, 1500), color=(245, 245, 245)).save(source_path, format="PNG")

    captured = {}

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        captured["request_options"] = dict(request_options)
        captured["prompt"] = content[0]
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_path.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "High Angle Overview",
            "ratio": "portrait",
            "prompt": "render a standing-height overview",
        },
        "unitcase",
        6,
        furniture_data=[],
        materialize_input=lambda path, prefix: path,
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert captured["request_options"]["aspect_ratio"] == "4:5"
        assert "OUTPUT ASPECT RATIO: 4:5" in captured["prompt"]
        with Image.open(output_path) as rendered:
            assert abs((rendered.size[0] / rendered.size[1]) - (4.0 / 5.0)) < 0.02
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_rejects_unsafe_ratio_crop_and_cleans_up_raw_attempt(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1200, 900), color=(245, 245, 245)).save(source_path, format="PNG")

    result = generate_detail_view(
        str(source_path),
        {
            "name": "High Angle Overview",
            "prompt": "render a standing-height overview",
        },
        "unsafe-crop-case",
        7,
        furniture_data=[],
        materialize_input=lambda path, prefix: path,
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": _landscape_png_bytes()})()})()],
            },
        )(),
        model_name="gemini-3.1-flash-image",
    )

    leaked = list(Path("outputs").glob("detail_*_unsafe-crop-case_7_*.png"))
    try:
        assert result is None
        assert leaked == []
    finally:
        for path in leaked:
            if path.exists():
                path.unlink()


def test_generate_detail_view_defaults_ratio_less_detail_crop_to_vertical_canvas(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1200, 900), color=(245, 245, 245)).save(source_path, format="PNG")

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Accent Chair",
            "target_key": "chair_01",
            "target_label": "Accent Chair",
            "prompt": "unused because crop-first path should win",
        },
        "unitcase",
        4,
        furniture_data=[
            {
                "label": "Accent Chair",
                "target_key": "chair_01",
                "category": "chair",
                "box_2d": [220, 310, 760, 610],
                "box_source": "main_render",
            }
        ],
        prefer_crop_extract=True,
        materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not materialize cutouts")
        ),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not request model safety settings")
        ),
        call_gemini_with_failover=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not call the generation model")
        ),
        model_name="unused",
    )

    output_path = Path(result["path"])
    try:
        with Image.open(output_path) as rendered:
            assert abs((rendered.size[0] / rendered.size[1]) - (4.0 / 5.0)) < 0.02
            assert rendered.size[1] > rendered.size[0]
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_crop_first_enforces_minimum_source_crop_area(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (5504, 3072), color=(245, 245, 245)).save(source_path, format="PNG")

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Small Side Table",
            "target_key": "side_table_01",
            "target_label": "Small Side Table",
            "ratio": "4:5",
            "prompt": "unused because crop-first path should win",
        },
        "min-crop-case",
        4,
        furniture_data=[
            {
                "label": "Small Side Table",
                "target_key": "side_table_01",
                "category": "side_table",
                "box_2d": [485, 630, 545, 712],
                "box_source": "product_reference_localization",
            }
        ],
        prefer_crop_extract=True,
        materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not materialize cutouts")
        ),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not request model safety settings")
        ),
        call_gemini_with_failover=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not call the generation model")
        ),
        model_name="unused",
    )

    output_path = Path(result["path"])
    try:
        left, top, right, bottom = result["crop_bounds_px"]
        assert right - left >= 1280
        assert bottom - top >= 1600
        with Image.open(output_path) as rendered:
            assert rendered.size == (1280, 1600)
            assert abs((rendered.size[0] / rendered.size[1]) - (4.0 / 5.0)) < 0.02
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_crop_first_rejects_full_image_box(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (2752, 1536), color=(245, 245, 245)).save(source_path, format="PNG")

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Accent Chair",
            "target_key": "chair_01",
            "target_label": "Accent Chair",
            "prompt": "unused because crop-first path should win",
        },
        "unitcase",
        44,
        furniture_data=[
            {
                "label": "Accent Chair",
                "target_key": "chair_01",
                "category": "chair",
                "box_2d": [0, 0, 1000, 1000],
            }
        ],
        prefer_crop_extract=True,
        materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not materialize cutouts")
        ),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not request model safety settings")
        ),
        call_gemini_with_failover=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not call the generation model")
        ),
        model_name="unused",
    )

    assert result is None


def test_generate_detail_view_crop_first_rejects_cached_snapshot_box(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1600, 1200), color=(245, 245, 245)).save(source_path, format="PNG")

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Accent Chair",
            "target_key": "chair_01",
            "target_label": "Accent Chair",
            "prompt": "unused because crop-first path should reject cached snapshot boxes",
        },
        "unitcase",
        45,
        furniture_data=[
            {
                "label": "Accent Chair",
                "target_key": "chair_01",
                "category": "chair",
                "box_2d": [120, 180, 820, 640],
                "box_source": "cached_detail_snapshot",
            }
        ],
        prefer_crop_extract=True,
        materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not materialize cutouts")
        ),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not request model safety settings")
        ),
        call_gemini_with_failover=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not call the generation model")
        ),
        model_name="unused",
    )

    assert result is None


def test_generate_detail_view_crop_first_rejects_source_reference_box(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1600, 1200), color=(245, 245, 245)).save(source_path, format="PNG")

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Accent Chair",
            "target_key": "chair_01",
            "target_label": "Accent Chair",
            "prompt": "unused because crop-first path should reject non-localized source boxes",
        },
        "unitcase",
        46,
        furniture_data=[
            {
                "label": "Accent Chair",
                "target_key": "chair_01",
                "category": "chair",
                "box_2d": [120, 180, 820, 640],
                "box_source": "source_reference",
            }
        ],
        prefer_crop_extract=True,
        materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not materialize cutouts")
        ),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not request model safety settings")
        ),
        call_gemini_with_failover=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not call the generation model")
        ),
        model_name="unused",
    )

    assert result is None


def test_generate_detail_view_crop_first_uses_exif_transposed_canvas(tmp_path):
    source_path = tmp_path / "room_oriented.jpg"
    base = Image.new("RGB", (900, 1600), color=(255, 0, 0))
    for y in range(800, 1600):
        for x in range(900):
            base.putpixel((x, y), (0, 0, 255))
    exif = Image.Exif()
    exif[274] = 6
    base.save(source_path, format="JPEG", exif=exif)

    style = {
        "name": "Detail: Accent Chair",
        "target_key": "chair_01",
        "target_label": "Accent Chair",
        "prompt": "unused because crop-first path should win",
    }
    furniture_item = {
        "label": "Accent Chair",
        "target_key": "chair_01",
        "category": "chair",
        "box_2d": [250, 50, 750, 350],
        "box_source": "main_render",
    }

    result = generate_detail_view(
        str(source_path),
        style,
        "unitcase",
        5,
        furniture_data=[furniture_item],
        prefer_crop_extract=True,
        materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not materialize cutouts")
        ),
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not request model safety settings")
        ),
        call_gemini_with_failover=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("crop-first detail extraction should not call the generation model")
        ),
        model_name="unused",
    )

    output_path = Path(result["path"])
    try:
        with Image.open(source_path) as raw_img:
            displayed = ImageOps.exif_transpose(raw_img).convert("RGB")
        bounds = _box_to_pixels(furniture_item["box_2d"], displayed.size)
        bounds = _expand_bounds(bounds, displayed.size, family="chair")
        bounds = _fit_bounds_to_ratio(bounds, displayed.size, target_ratio=(4, 5))
        expected_crop = displayed.crop(bounds).resize((40, 50))

        with Image.open(output_path) as rendered:
            rendered_probe = rendered.resize((40, 50))

        expected_pixel = expected_crop.resize((1, 1)).getpixel((0, 0))
        rendered_pixel = rendered_probe.resize((1, 1)).getpixel((0, 0))

        assert rendered_pixel[2] > rendered_pixel[0]
        assert abs(rendered_pixel[2] - expected_pixel[2]) < 35
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_detail_view_limits_detail_aux_cutouts_to_nearest_context(tmp_path):
    source_path = tmp_path / "room.png"
    Image.new("RGB", (1200, 1500), color=(245, 245, 245)).save(source_path, format="PNG")

    cutout_paths = {}
    for label in ("chair", "table", "lamp", "mirror", "rug"):
        path = tmp_path / f"{label}.png"
        Image.new("RGB", (240, 240), color=(220, 220, 220)).save(path, format="PNG")
        cutout_paths[label] = str(path)

    captured = {}

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        captured["content"] = list(content)
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_path.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source_path),
        {
            "name": "Detail: Accent Chair",
            "target_key": "chair_01",
            "target_label": "Accent Chair",
            "ratio": "4:5",
            "prompt": "render a focused editorial chair detail",
        },
        "unitcase",
        61,
        furniture_data=[
            {
                "label": "Accent Chair",
                "target_key": "chair_01",
                "source_index": 1,
                "category": "chair",
                "box_2d": [280, 260, 720, 540],
                "source_box_2d": [280, 260, 720, 540],
                "crop_path": cutout_paths["chair"],
            },
            {
                "label": "Side Table",
                "target_key": "table_01",
                "source_index": 2,
                "category": "table",
                "box_2d": [340, 560, 680, 760],
                "source_box_2d": [340, 560, 680, 760],
                "crop_path": cutout_paths["table"],
            },
            {
                "label": "Floor Lamp",
                "target_key": "lamp_01",
                "source_index": 3,
                "category": "light",
                "box_2d": [120, 520, 500, 700],
                "source_box_2d": [120, 520, 500, 700],
                "crop_path": cutout_paths["lamp"],
            },
            {
                "label": "Mirror",
                "target_key": "mirror_01",
                "source_index": 4,
                "category": "mirror",
                "box_2d": [40, 40, 180, 180],
                "source_box_2d": [40, 40, 180, 180],
                "crop_path": cutout_paths["mirror"],
            },
            {
                "label": "Rug",
                "target_key": "rug_01",
                "source_index": 5,
                "category": "rug",
                "box_2d": [780, 760, 990, 990],
                "source_box_2d": [780, 760, 990, 990],
                "crop_path": cutout_paths["rug"],
            },
        ],
        materialize_input=lambda path, prefix: path,
        normalize_label_for_match=lambda text: str(text or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image",
    )

    output_path = Path(result["path"])
    try:
        assert result["generation_mode"] == "model_regeneration"
        assert result["cutout_ref_count"] == 3
        assert result["cutout_ref_labels"] == ["Accent Chair", "Side Table", "Floor Lamp"]

        reference_lines = [
            part
            for part in captured["content"]
            if isinstance(part, str)
            and (
                part.startswith("PRIMARY TARGET CUTOUT")
                or part.startswith("Secondary Furniture Cutout Reference")
            )
        ]
        assert len(reference_lines) == 3
        assert "PRIMARY TARGET CUTOUT" in reference_lines[0]
        assert "Accent Chair" in reference_lines[0]
        assert all("Mirror" not in line for line in reference_lines)
        assert all("Rug" not in line for line in reference_lines)
    finally:
        if output_path.exists():
            output_path.unlink()
