import io
import json
import os
import glob
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from application.render import furnished_generation_stage
from application.render.furnished_generation_stage import generate_furnished_room
from application.render.reference_features_stage import extract_reference_features
from application.render.render_audience_stage import run_render_audience_stage
from application.render.render_bootstrap_stage import _build_summary
from application.render.render_analysis_stage import (
    _normalize_estimated_room_dims,
    run_render_analysis_stage,
)
from application.render.scale_plan_support import build_scale_plan
from application.render.scale_plan_support import select_scale_anchor
from application.render.empty_room_generation_stage import generate_empty_room
from application.render.render_room_workflow import _review_summary_from_scalecheck_diagnostics
from application.render.render_room_workflow import _sync_furniture_specs_contracts
from application.render.render_room_workflow import run_render_room_workflow
from application.render.render_variant_stage import _generate_one_variant, run_render_variant_stage
from application.render.room_analysis import analyze_room_structure
from application.render.render_workflow_contracts import (
    RenderWorkflowAnalysisServices,
    RenderWorkflowDependencies,
    RenderWorkflowGenerationServices,
    RenderWorkflowPostprocessServices,
    RenderWorkflowRequest,
    RenderWorkflowRuntime,
    RenderWorkflowStorageServices,
)


def test_internal_audience_must_enable_scale_check():
    result = run_render_audience_stage(
        audience=None,
        normalize_audience=lambda aud: "internal" if aud is None else aud,
        build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
    )

    assert result.enable_scale_check is True


def test_bootstrap_summary_keeps_scale_check_counters():
    summary = _build_summary()

    assert summary["scalecheck_fail"] == 0
    assert summary["scalecheck_retry"] == 0


def _make_png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _write_grid_image(path, width: int = 160, height: int = 90, *, line_step: int = 16):
    img = Image.new("RGB", (width, height), color=(245, 245, 245))
    pixels = img.load()
    for x in range(0, width, line_step):
        for y in range(height):
            pixels[x, y] = (245, 235, 40)
    for y in range(0, height, line_step):
        for x in range(width):
            pixels[x, y] = (245, 235, 40)
    img.save(path, format="PNG")


def _write_recolored_grid_image(path, width: int = 160, height: int = 90, *, line_step: int = 16):
    img = Image.new("RGB", (width, height), color=(245, 245, 245))
    pixels = img.load()
    grid_color = (198, 184, 150)
    for x in range(0, width, line_step):
        for y in range(height):
            pixels[x, y] = grid_color
    for y in range(0, height, line_step):
        for x in range(width):
            pixels[x, y] = grid_color
    img.save(path, format="PNG")


def test_generate_empty_room_retries_invalid_zero_byte_response(tmp_path):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    call_state = {"count": 0}
    original_time = furnished_generation_stage.time.time

    import application.render.empty_room_generation_stage as empty_stage
    empty_stage.time.time = lambda: 1001.0

    def _call_gemini(*args, **kwargs):
        call_state["count"] += 1
        if call_state["count"] == 1:
            return SimpleNamespace(candidates=[SimpleNamespace()], parts=[SimpleNamespace(inline_data=SimpleNamespace(data=b""))])
        return SimpleNamespace(
            candidates=[SimpleNamespace()],
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
        )

    result = generate_empty_room(
        str(room_path),
        "empty-retry",
        1000.0,
        stage_name="Stage 1",
        return_raw=False,
        total_timeout_limit=60,
        log_step=lambda *_args, **_kwargs: None,
        model_name="model",
        build_empty_room_prompt=lambda: "prompt",
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        match_aspect_to_target=lambda path, _room: path,
    )

    try:
        assert call_state["count"] == 2
        assert os.path.exists(result)
        with Image.open(result) as generated:
            assert generated.size == (160, 90)
    finally:
        empty_stage.time.time = original_time


def test_generate_empty_room_requests_landscape_ratio_and_high_thinking(tmp_path):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    captured = {}
    original_time = furnished_generation_stage.time.time

    import application.render.empty_room_generation_stage as empty_stage
    empty_stage.time.time = lambda: 1001.0

    def _call_gemini(model_name, content, request_options, *args, **kwargs):
        captured["model_name"] = model_name
        captured["request_options"] = dict(request_options)
        return SimpleNamespace(
            candidates=[SimpleNamespace()],
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
        )

    result = generate_empty_room(
        str(room_path),
        "empty-config",
        1000.0,
        stage_name="Stage 1",
        return_raw=False,
        total_timeout_limit=60,
        log_step=lambda *_args, **_kwargs: None,
        model_name="gemini-3.1-flash-image-preview",
        build_empty_room_prompt=lambda: "prompt",
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        match_aspect_to_target=lambda path, _room: path,
    )
    try:
        assert os.path.exists(result)
        assert captured["model_name"] == "gemini-3.1-flash-image-preview"
        assert captured["request_options"]["aspect_ratio"] == "16:9"
        assert captured["request_options"]["thinking_level"] == "high"
        assert captured["request_options"]["include_thoughts"] is False
    finally:
        empty_stage.time.time = original_time


def test_generate_empty_room_retries_when_landscape_crop_would_remove_too_much_scene(tmp_path):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    captured = {"calls": 0}

    import application.render.empty_room_generation_stage as empty_stage

    original_time = empty_stage.time.time
    empty_stage.time.time = lambda: 1001.0

    def _call_gemini(model_name, content, request_options, *args, **kwargs):
        captured["calls"] += 1
        content[1].tobytes()
        return SimpleNamespace(
            candidates=[SimpleNamespace()],
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(800, 1000)))],
        )

    try:
        result = generate_empty_room(
            str(room_path),
            "empty-config",
            1000.0,
            stage_name="Stage 1",
            return_raw=False,
            total_timeout_limit=60,
            log_step=lambda *_args, **_kwargs: None,
            model_name="gemini-3.1-flash-image-preview",
            build_empty_room_prompt=lambda: "prompt",
            allow_all_safety_settings=lambda: {},
            call_gemini_with_failover=_call_gemini,
            match_aspect_to_target=lambda path, _room: path,
        )
    finally:
        empty_stage.time.time = original_time

    assert captured["calls"] == 3
    assert result == str(room_path)


def test_generate_empty_room_uses_injected_postprocessor_when_it_returns_matching_landscape_canvas(tmp_path):
    room_path = tmp_path / "room.png"
    processed_path = tmp_path / "processed.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    processed_path.write_bytes(_make_png_bytes(1600, 900))
    captured = {"calls": 0}

    import application.render.empty_room_generation_stage as empty_stage

    original_time = empty_stage.time.time
    empty_stage.time.time = lambda: 1001.0

    def _call_gemini(model_name, content, request_options, *args, **kwargs):
        return SimpleNamespace(
            candidates=[SimpleNamespace()],
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(800, 1000)))],
        )

    def _postprocess(_candidate, _room):
        captured["calls"] += 1
        return str(processed_path)

    try:
        result = generate_empty_room(
            str(room_path),
            "empty-config",
            1000.0,
            stage_name="Stage 1",
            return_raw=False,
            total_timeout_limit=60,
            log_step=lambda *_args, **_kwargs: None,
            model_name="gemini-3.1-flash-image-preview",
            build_empty_room_prompt=lambda: "prompt",
            allow_all_safety_settings=lambda: {},
            call_gemini_with_failover=_call_gemini,
            match_aspect_to_target=_postprocess,
        )
    finally:
        empty_stage.time.time = original_time

    assert captured["calls"] == 1
    assert result == str(processed_path)


def test_generate_empty_room_removes_raw_sibling_after_aspect_normalization(tmp_path):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))

    import application.render.empty_room_generation_stage as empty_stage

    original_time = empty_stage.time.time
    empty_stage.time.time = lambda: 1001.0

    def _call_gemini(model_name, content, request_options, *args, **kwargs):
        return SimpleNamespace(
            candidates=[SimpleNamespace()],
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(1536, 1024)))],
        )

    try:
        result = generate_empty_room(
            str(room_path),
            "empty-cleanup",
            1000.0,
            stage_name="Stage 1",
            return_raw=False,
            total_timeout_limit=60,
            log_step=lambda *_args, **_kwargs: None,
            model_name="gemini-3.1-flash-image-preview",
            build_empty_room_prompt=lambda: "prompt",
            allow_all_safety_settings=lambda: {},
            call_gemini_with_failover=_call_gemini,
            match_aspect_to_target=lambda path, _room: path,
        )
    finally:
        empty_stage.time.time = original_time

    output_path = Path(result)
    raw_output_path = Path(str(output_path).replace("_aspect.png", ".png"))
    try:
        assert output_path.exists()
        assert output_path.name.endswith("_aspect.png")
        assert not raw_output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_one_variant_normalizes_legacy_string_results_to_structured_scale_metadata():
    result = _generate_one_variant(
        0,
        step1_img="step1.png",
        style_prompt="style",
        ref_input="ref.png",
        unique_id="job-1",
        furniture_specs_text=None,
        furniture_specs_json={},
        dimensions="",
        placement="",
        scale_guide_path=None,
        primary_item=None,
        room_dims_parsed={},
        wall_span_norm=(0.0, 1.0),
        size_hierarchy=[],
        start_time=1000.0,
        room_planes=None,
        windows_present=False,
        room_analysis_text="",
        enable_scale_check=True,
        generate_furnished_room=lambda *args, **kwargs: "outputs/result.png",
    )

    assert result["path"] == "outputs/result.png"
    assert result["scalecheck_fail_count"] == 0
    assert result["scalecheck_retry_count"] == 0
    assert result["scale_check_failed"] is False


def test_run_render_analysis_stage_exposes_room_geometry_when_room_analysis_returns_it(tmp_path):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))

    result = run_render_analysis_stage(
        ref_paths=[str(room_path)],
        item_refs=[],
        step1_img="step1.png",
        step1_raw="raw-step1.png",
        dimensions="8000x8000x3000",
        unique_id="job-geom-1",
        detect_furniture_boxes=lambda *_args, **_kwargs: [],
        canonical_category=lambda value: value or "unknown",
        build_item_target_key=lambda *args, **kwargs: "target",
        analyze_room_structure=lambda *args, **kwargs: {
            "room_text": "room analysis",
            "windows_present": True,
            "room_planes": {"y_top": 0.15, "y_bottom": 0.85},
            "wall_span_norm": (0.2, 0.8),
            "estimated_dimensions_mm": {"width_mm": 5200, "depth_mm": 4100, "height_mm": 2600},
        },
        analyze_cropped_item=lambda *args, **kwargs: {},
        normalize_dims_dict=lambda dims: dims,
        parse_object_dimensions_mm=lambda value: {},
        build_furniture_specs_json=lambda items: {"items": items},
        create_scale_guide_overlay_with_model=lambda *args, **kwargs: None,
        match_aspect_to_target=lambda path, room: path,
        enable_scale_guidance=False,
        strict_scale_requested=True,
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        summary=_build_summary(),
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None),
        log_brief=True,
        max_concurrency_analysis=1,
        cart_max_analysis_workers=1,
    )

    assert result.room_analysis_text == "room analysis"
    assert result.windows_present is True
    assert result.room_planes == {"y_top": 0.15, "y_bottom": 0.85}
    assert result.wall_span_norm == (0.2, 0.8)
    assert result.estimated_room_dims == {"width_mm": 5000, "depth_mm": 4000, "height_mm": 2600}
    assert result.scale_plan["strict_scale_requested"] is True
    assert result.scale_plan["strict_scale_ready"] is False
    assert "missing_anchor" in result.scale_plan["missing_requirements"]


def test_run_render_analysis_stage_builds_identity_profile_and_layout_envelope(tmp_path):
    room_path = tmp_path / "room.png"
    item_path = tmp_path / "item.png"
    crop_path = tmp_path / "crop.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    item_path.write_bytes(_make_png_bytes(64, 64))
    crop_path.write_bytes(_make_png_bytes(64, 64))

    result = run_render_analysis_stage(
        ref_paths=[],
        item_refs=[
            {
                "path": str(item_path),
                "label": "Round Mirror",
                "category": "mirror",
                "payload_index": 1,
                "dims_mm": {"width_mm": 600, "depth_mm": 20, "height_mm": 600},
            }
        ],
        step1_img=str(room_path),
        step1_raw=str(room_path),
        dimensions="4000x4000x2400",
        unique_id="job-identity-1",
        detect_furniture_boxes=lambda *_args, **_kwargs: [],
        canonical_category=lambda value: "mirror" if "mirror" in str(value).lower() else (value or "unknown"),
        build_item_target_key=lambda *args, **kwargs: "mirror_key",
        analyze_room_structure=lambda *args, **kwargs: {
            "room_text": "room analysis",
            "windows_present": False,
            "room_planes": {"y_top": 0.1, "y_bottom": 0.9},
            "wall_span_norm": (0.1, 0.9),
        },
        analyze_cropped_item=lambda *args, **kwargs: {
            "description": "Round mirror with wood frame and reflective glass.",
            "crop_path": str(crop_path),
            "reference_features": {
                "silhouette_cues": ["round", "thin frame"],
                "material_cues": ["wood", "mirror"],
                "distinctive_parts": ["thin circular frame"],
                "preserve_rules": ["keep circular mirror frame"],
                "reflective_surface": True,
            },
        },
        normalize_dims_dict=lambda dims: dims,
        parse_object_dimensions_mm=lambda value: {},
        build_furniture_specs_json=lambda items: {"items": items, "primary": items[0], "primary_scale": items[0]},
        create_scale_guide_overlay_with_model=lambda *args, **kwargs: None,
        match_aspect_to_target=lambda path, room: path,
        enable_scale_guidance=False,
        strict_scale_requested=True,
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        summary=_build_summary(),
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None),
        log_brief=True,
        max_concurrency_analysis=1,
        cart_max_analysis_workers=1,
    )

    item = result.full_analyzed_data[0]
    assert item["identity_profile"]["family"] == "mirror"
    assert item["identity_profile"]["wall_attached_expected"] is True
    assert item["layout_envelope"]["placement_family"] == "wall_attached"
    assert item["layout_envelope"]["room_width_ratio"] == 0.15
    assert item["identity_profile"]["distinctive_parts"] == ["thin circular frame"]
    assert "keep circular mirror frame" in item["identity_profile"]["preserve_rules"]
    assert item["reference_features"]["reflective_surface"] is True
    assert result.scale_plan["strict_scale_ready"] is False
    assert "missing_anchor" in result.scale_plan["missing_requirements"]
    assert result.scale_plan["anchor_item"] is None


def test_run_render_analysis_stage_coerces_string_false_reflective_surface(tmp_path):
    room_path = tmp_path / "room.png"
    item_path = tmp_path / "item.png"
    crop_path = tmp_path / "crop.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    item_path.write_bytes(_make_png_bytes(64, 64))
    crop_path.write_bytes(_make_png_bytes(64, 64))

    result = run_render_analysis_stage(
        ref_paths=[],
        item_refs=[
            {
                "path": str(item_path),
                "label": "Storage Cabinet",
                "category": "storage",
                "payload_index": 1,
                "dims_mm": {"width_mm": 1200, "depth_mm": 450, "height_mm": 900},
            }
        ],
        step1_img=str(room_path),
        step1_raw=str(room_path),
        dimensions="4000x4000x2400",
        unique_id="job-identity-2",
        detect_furniture_boxes=lambda *_args, **_kwargs: [],
        canonical_category=lambda value: "storage",
        build_item_target_key=lambda *args, **kwargs: "storage_key",
        analyze_room_structure=lambda *args, **kwargs: {
            "room_text": "room analysis",
            "windows_present": False,
            "room_planes": {"y_top": 0.1, "y_bottom": 0.9},
            "wall_span_norm": (0.1, 0.9),
        },
        analyze_cropped_item=lambda *args, **kwargs: {
            "description": "Wood cabinet.",
            "crop_path": str(crop_path),
            "reference_features": {
                "silhouette_cues": ["rectangular"],
                "material_cues": ["wood"],
                "distinctive_parts": ["four doors"],
                "preserve_rules": ["keep four front doors"],
                "reflective_surface": "false",
            },
        },
        normalize_dims_dict=lambda dims: dims,
        parse_object_dimensions_mm=lambda value: {},
        build_furniture_specs_json=lambda items: {"items": items, "primary": items[0], "primary_scale": items[0]},
        create_scale_guide_overlay_with_model=lambda *args, **kwargs: None,
        match_aspect_to_target=lambda path, room: path,
        enable_scale_guidance=False,
        strict_scale_requested=True,
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        summary=_build_summary(),
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None),
        log_brief=True,
        max_concurrency_analysis=1,
        cart_max_analysis_workers=1,
    )

    item = result.full_analyzed_data[0]
    assert item["reference_features"]["reflective_surface"] == "false"
    assert item["identity_profile"]["reflective_surface"] is False


def test_build_scale_plan_marks_missing_dims_and_relative_ratios():
    items = [
        {
            "label": "Sofa",
            "category": "sofa",
            "source_index": 1,
            "target_key": "sofa_001",
            "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800},
            "identity_profile": {"family": "sofa"},
            "layout_envelope": {"placement_family": "floor_placed"},
        },
        {
            "label": "Rug",
            "category": "rug",
            "source_index": 2,
            "target_key": "rug_002",
            "requested_dims_mm": {"width_mm": 1100, "depth_mm": 1100, "height_mm": 12},
            "identity_profile": {"family": "rug"},
            "layout_envelope": {"placement_family": "rug"},
        },
        {
            "label": "Lamp",
            "category": "floor_lamp",
            "source_index": 3,
            "target_key": "lamp_003",
            "requested_dims_mm": {"width_mm": 100, "depth_mm": 100, "height_mm": 0},
            "identity_profile": {"family": "floor_lamp"},
            "layout_envelope": {"placement_family": "floor_placed"},
        },
    ]

    scale_plan = build_scale_plan(
        items=items,
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        wall_span_norm=(0.1, 0.9),
        primary_item={"target_key": "sofa_001", "source_index": 1},
        strict_scale_requested=True,
    )

    assert scale_plan["strict_scale_requested"] is True
    assert scale_plan["strict_scale_ready"] is False
    assert "item_dims_incomplete" in scale_plan["missing_requirements"]
    assert scale_plan["anchor_item"]["target_key"] == "sofa_001"
    rug_entry = next(row for row in scale_plan["items"] if row["target_key"] == "rug_002")
    assert rug_entry["relative_to_anchor"]["width_ratio"] == 0.4583
    lamp_entry = next(row for row in scale_plan["items"] if row["target_key"] == "lamp_003")
    assert lamp_entry["dims_complete"] is False


def test_select_scale_anchor_excludes_floor_lamp_rug_and_mirror_when_sofa_exists():
    items = [
        {
            "label": "Floor Lamp",
            "category": "floor_lamp",
            "source_index": 1,
            "target_key": "lamp_001",
            "requested_dims_mm": {"width_mm": 100, "depth_mm": 100, "height_mm": 2400},
            "identity_profile": {"family": "floor_lamp"},
            "layout_envelope": {"placement_family": "small_free_object", "room_width_ratio": 0.025, "footprint_ratio": 0.001},
        },
        {
            "label": "Round Rug",
            "category": "rug",
            "source_index": 2,
            "target_key": "rug_002",
            "requested_dims_mm": {"width_mm": 1100, "depth_mm": 1100, "height_mm": 12},
            "identity_profile": {"family": "rug"},
            "layout_envelope": {"placement_family": "rug", "room_width_ratio": 0.275, "footprint_ratio": 0.076},
        },
        {
            "label": "Wall Mirror",
            "category": "mirror",
            "source_index": 3,
            "target_key": "mirror_003",
            "requested_dims_mm": {"width_mm": 600, "depth_mm": 20, "height_mm": 900},
            "identity_profile": {"family": "mirror"},
            "layout_envelope": {"placement_family": "wall_attached", "room_width_ratio": 0.15, "footprint_ratio": 0.01},
        },
        {
            "label": "Main Sofa",
            "category": "sofa",
            "source_index": 4,
            "target_key": "sofa_004",
            "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800},
            "identity_profile": {"family": "sofa"},
            "layout_envelope": {"placement_family": "floor_placed", "room_width_ratio": 0.6, "footprint_ratio": 0.165},
        },
    ]

    anchor = select_scale_anchor(items)

    assert anchor is not None
    assert anchor["target_key"] == "sofa_004"


def test_sync_furniture_specs_contracts_rewrites_primary_scale_to_preferred_anchor():
    furniture_specs_json = {
        "items": [
            {"target_key": "lamp_001", "label": "Floor Lamp", "dims_mm": {"width_mm": 100, "depth_mm": 100, "height_mm": 2400}},
            {"target_key": "sofa_004", "label": "Main Sofa", "dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800}},
        ],
        "primary": {"target_key": "lamp_001"},
        "primary_scale": {"target_key": "lamp_001"},
    }
    analyzed_items = [
        {
            "target_key": "lamp_001",
            "label": "Floor Lamp",
            "category": "floor_lamp",
            "dims_mm": {"width_mm": 100, "depth_mm": 100, "height_mm": 2400},
            "identity_profile": {"family": "floor_lamp"},
            "layout_envelope": {"placement_family": "small_free_object", "room_width_ratio": 0.025},
        },
        {
            "target_key": "sofa_004",
            "label": "Main Sofa",
            "category": "sofa",
            "dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800},
            "identity_profile": {"family": "sofa"},
            "layout_envelope": {"placement_family": "floor_placed", "room_width_ratio": 0.6},
        },
    ]

    synced = _sync_furniture_specs_contracts(furniture_specs_json, analyzed_items, placement_plan=None)

    assert synced is not None
    assert synced["primary_scale"]["target_key"] == "sofa_004"
    assert synced["two_pass_strategy"]["recommended_anchor_key"] == "sofa_004"


def test_extract_reference_features_falls_back_on_malformed_payload(tmp_path):
    crop_path = tmp_path / "crop.png"
    crop_path.write_bytes(_make_png_bytes(64, 64))

    result = extract_reference_features(
        crop_path=str(crop_path),
        label="Mirror",
        category="mirror",
        description="Round mirror with wood frame.",
        dims_mm={"width_mm": 600, "depth_mm": 20, "height_mm": 600},
        call_gemini_with_failover=lambda *args, **kwargs: SimpleNamespace(text='{"reflective_surface":"false","material_cues":"bad"}'),
        analysis_model_name="model",
        safe_json_from_model_text=lambda text: json.loads(text),
        log_brief=True,
    )

    assert isinstance(result["silhouette_cues"], list)
    assert isinstance(result["material_cues"], list)
    assert isinstance(result["distinctive_parts"], list)
    assert isinstance(result["preserve_rules"], list)
    assert result["reflective_surface"] is False


def test_analyze_room_structure_requests_numeric_room_geometry_fields(tmp_path):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    captured = {}

    def fake_call_gemini_with_failover(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        captured["request_options"] = args[0]
        return SimpleNamespace(
            text='{"room_text":"room analysis","windows_present":true,"room_planes":{"y_top":0.1,"y_bottom":0.9},"wall_span_norm":[0.1,0.9],"estimated_dimensions_mm":{"width_mm":5200,"depth_mm":4100,"height_mm":2600}}'
        )

    result = analyze_room_structure(
        str(room_path),
        room_dimensions="8000x8000x3000",
        timeout=120,
        call_gemini_with_failover=fake_call_gemini_with_failover,
        model_name="model",
        safe_json_from_model_text=lambda text: json.loads(text),
    )

    assert '"room_planes": {"y_top": 0.08, "y_bottom": 0.92}' in captured["prompt"]
    assert '"room_planes": {"floor":' not in captured["prompt"]
    assert "wall_span_norm" in captured["prompt"]
    assert "estimated_dimensions_mm" in captured["prompt"]
    assert "Round width/depth to the nearest 500 mm" in captured["prompt"]
    assert "Round height to the nearest 100 mm" in captured["prompt"]
    assert captured["request_options"]["temperature"] == 0
    assert captured["request_options"]["seed"] == 7
    assert captured["request_options"]["response_mime_type"] == "application/json"
    assert result["room_planes"] == {"y_top": 0.1, "y_bottom": 0.9}
    assert result["wall_span_norm"] == [0.1, 0.9]
    assert result["estimated_dimensions_mm"] == {"width_mm": 5200, "depth_mm": 4100, "height_mm": 2600}


def test_normalize_estimated_room_dims_uses_coarse_architectural_rounding():
    assert _normalize_estimated_room_dims(
        {"width_mm": 6420, "depth_mm": 7680, "height_mm": 2835}
    ) == {
        "width_mm": 6500,
        "depth_mm": 7500,
        "height_mm": 2800,
    }


def test_generate_furnished_room_keeps_incomplete_dims_context_even_for_legacy_two_dim_labels(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    captured = {}

    gemini_response = SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
    )

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1010.0)

    def _call_gemini(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        return gemini_response

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-incomplete-prompt",
        furniture_specs_json={
            "items": [
                {
                    "label": "Mirror",
                    "dims_mm": {"width_mm": 600, "depth_mm": 25, "height_mm": 0},
                }
            ]
        },
        room_dimensions="8000x8000x3000",
        primary_item={"label": "Mirror"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1010.0,
        enable_scale_check=False,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: True,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, []),
    )

    assert result["path"] == os.path.join("outputs", "result_1010_job-incomplete-prompt.png")
    assert "<INCOMPLETE DIMENSIONS (DO NOT IGNORE)>" in captured["prompt"]
    assert "- Mirror: missing H" in captured["prompt"]


def test_generate_furnished_room_requests_landscape_ratio_and_high_thinking(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    captured = {}

    gemini_response = SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
    )

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1010.0)

    def _call_gemini(model_name, content, request_options, *args, **kwargs):
        captured["model_name"] = model_name
        captured["request_options"] = dict(request_options)
        return gemini_response

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-main-config",
        furniture_specs_json={"items": []},
        room_dimensions="8000x8000x3000",
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1010.0,
        enable_scale_check=False,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: True,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image-preview",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, []),
    )

    assert result["path"] == os.path.join("outputs", "result_1010_job-main-config.png")
    assert captured["model_name"] == "gemini-3.1-flash-image-preview"
    assert captured["request_options"]["aspect_ratio"] == "16:9"
    assert captured["request_options"]["thinking_level"] == "high"
    assert captured["request_options"]["include_thoughts"] is False


def test_generate_furnished_room_removes_raw_sibling_after_aspect_normalization(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))

    gemini_response = SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(1536, 1024)))],
    )

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1010.0)

    def _call_gemini(model_name, content, request_options, *args, **kwargs):
        return gemini_response

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-main-cleanup",
        furniture_specs_json={"items": []},
        room_dimensions="8000x8000x3000",
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1010.0,
        enable_scale_check=False,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: True,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="gemini-3.1-flash-image-preview",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, []),
    )

    output_path = Path(result["path"])
    raw_output_path = Path(str(output_path).replace("_aspect.png", ".png"))
    try:
        assert output_path.exists()
        assert output_path.name.endswith("_aspect.png")
        assert not raw_output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_does_not_send_scale_guide_image_to_model(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    guide_path = tmp_path / "guide.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    _write_grid_image(guide_path)
    captured = {}

    gemini_response = SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
    )

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1011.0)

    def _call_gemini(model_name, content, *args, **kwargs):
        captured["content"] = content
        return gemini_response

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-guide-prompt",
        furniture_specs_json={"items": []},
        room_dimensions="4000x4000x2400",
        scale_guide_path=str(guide_path),
        primary_item={"label": "Chair", "dims_mm": {"width_mm": 600, "depth_mm": 600, "height_mm": 900}},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1011.0,
        enable_scale_check=False,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, []),
    )

    assert result["path"] == os.path.join("outputs", "result_1011_job-guide-prompt.png")
    assert "SCALE GUIDE OVERLAY" not in captured["content"]
    assert len(captured["content"]) == 3


def test_generate_furnished_room_includes_strict_scale_plan_context(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    captured = {}

    gemini_response = SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
    )

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1012.0)

    def _call_gemini(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        return gemini_response

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-scale-plan-prompt",
        furniture_specs_json={"items": [{"label": "Sofa", "dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800}}]},
        room_dimensions="4000x4000x2400",
        primary_item={"label": "Sofa", "dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800}},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        scale_plan={
            "strict_scale_requested": True,
            "strict_scale_ready": True,
            "room_dims": {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
            "anchor_item": {"label": "Sofa", "layout_envelope": {"room_width_ratio": 0.6, "room_depth_ratio": 0.275, "room_height_ratio": 0.3333}},
            "items": [
                {
                    "label": "Sofa",
                    "placement_family": "floor_placed",
                    "room_width_ratio": 0.6,
                    "room_depth_ratio": 0.275,
                    "room_height_ratio": 0.3333,
                    "relative_to_anchor": {"width_ratio": 1.0, "height_ratio": 1.0, "footprint_ratio": 1.0},
                }
            ],
        },
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1012.0,
        enable_scale_check=False,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, []),
    )

    assert result["path"] == os.path.join("outputs", "result_1012_job-scale-plan-prompt.png")
    assert "<STRICT SCALE PLAN (HARD CONTRACT)>" in captured["prompt"]
    assert "Anchor: Sofa | roomW=0.6" in captured["prompt"]


def test_scale_guide_leak_detector_uses_guide_geometry_signature(tmp_path):
    guide_path = tmp_path / "guide.png"
    leaked_path = tmp_path / "leaked.png"
    clean_path = tmp_path / "clean.png"
    _write_grid_image(guide_path)
    _write_recolored_grid_image(leaked_path)
    clean_path.write_bytes(_make_png_bytes(160, 90))

    assert furnished_generation_stage._has_scale_guide_leak(str(leaked_path), str(guide_path)) is True
    assert furnished_generation_stage._has_scale_guide_leak(str(clean_path), str(guide_path)) is False


def test_scale_guide_leak_detector_matches_real_perspective_guide_artifact():
    guide_candidates = sorted(glob.glob(os.path.join("outputs", "scale_guide_*.png")))
    if not guide_candidates:
        pytest.skip("local scale guide artifact not available")
    guide_path = guide_candidates[-1]
    assert furnished_generation_stage._has_scale_guide_leak(guide_path, guide_path) is True


def test_scale_guide_leak_detector_does_not_flag_archived_clean_render():
    guide_path = os.path.join("outputs", "scale_debug", "scale_guide_url.png")
    clean_render_path = os.path.join("outputs", "scale_debug", "result_url.png")

    assert os.path.exists(guide_path)
    assert os.path.exists(clean_render_path)
    assert furnished_generation_stage._has_scale_guide_leak(clean_render_path, guide_path) is False


def test_generate_furnished_room_does_not_retry_on_scale_guide_signature_when_guide_is_not_attached(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    guide_path = tmp_path / "guide.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    _write_grid_image(guide_path)

    leaked_buffer = io.BytesIO()
    clean_buffer = io.BytesIO()
    leaked_path = tmp_path / "leaked.png"
    clean_path = tmp_path / "clean.png"
    _write_recolored_grid_image(leaked_path)
    Image.new("RGB", (160, 90), color=(255, 255, 255)).save(clean_path, format="PNG")
    leaked_buffer.write(leaked_path.read_bytes())
    clean_buffer.write(clean_path.read_bytes())
    call_state = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1012.0)

    def _call_gemini(model_name, content, *args, **kwargs):
        call_state["count"] += 1
        data = leaked_buffer.getvalue() if call_state["count"] == 1 else clean_buffer.getvalue()
        return SimpleNamespace(
            candidates=[SimpleNamespace()],
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=data))],
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-guide-retry",
        furniture_specs_json={"items": []},
        room_dimensions="4000x4000x2400",
        scale_guide_path=str(guide_path),
        primary_item={"label": "Chair", "dims_mm": {"width_mm": 600, "depth_mm": 600, "height_mm": 900}},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1012.0,
        enable_scale_check=False,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, []),
    )

    assert call_state["count"] == 1
    assert result == {
        "path": os.path.join("outputs", "result_1012_job-guide-retry.png"),
        "scalecheck_fail_count": 0,
        "scalecheck_retry_count": 0,
        "scale_check_failed": False,
        "scalecheck_issues": [],
        "scalecheck_failed_rules": [],
    }


def test_generate_furnished_room_keeps_path_after_exhausting_scale_retries(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1000_job-1.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    gemini_response = SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
    )
    validate_calls = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1000.0)

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-1",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1000.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: gemini_response,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (
            validate_calls.__setitem__("count", validate_calls["count"] + 1) or False,
            ["primary_width_vs_room_width"],
        ),
    )

    assert result == {
        "path": output_path,
        "scalecheck_fail_count": 3,
        "scalecheck_retry_count": 2,
        "scale_check_failed": True,
        "scalecheck_issues": ["primary_width_vs_room_width"],
        "scalecheck_failed_rules": ["primary_width_vs_room_width"],
    }
    assert validate_calls["count"] == 3


def test_generate_furnished_room_preserves_last_successful_path_when_later_retry_returns_none(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1004_job-path.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    responses = iter(
        [
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            None,
            None,
        ]
    )
    validate_calls = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1004.0)

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-path",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1004.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: next(responses),
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (
            validate_calls.__setitem__("count", validate_calls["count"] + 1) or False,
            ["rule_id:primary_width_vs_room_width"],
        ),
    )

    assert result == {
        "path": output_path,
        "scalecheck_fail_count": 1,
        "scalecheck_retry_count": 2,
        "scale_check_failed": True,
        "scalecheck_issues": ["rule_id:primary_width_vs_room_width"],
        "scalecheck_failed_rules": ["primary_width_vs_room_width"],
    }
    assert validate_calls["count"] == 1


def test_generate_furnished_room_keeps_structured_failed_rules_even_if_later_retries_are_free_form_only(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1005_job-rules.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    responses = iter(
        [
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
        ]
    )
    validate_calls = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1005.0)

    def _validate(*args, **kwargs):
        validate_calls["count"] += 1
        if validate_calls["count"] == 1:
            return False, ["rule_id:primary_width_vs_room_width", "primary width too large"]
        return False, ["room is too large"]

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-rules",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1005.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: next(responses),
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result == {
        "path": output_path,
        "scalecheck_fail_count": 3,
        "scalecheck_retry_count": 2,
        "scale_check_failed": True,
        "scalecheck_issues": ["room is too large"],
        "scalecheck_failed_rules": ["primary_width_vs_room_width"],
    }
    assert validate_calls["count"] == 3


def test_generate_furnished_room_extracts_rule_ids_from_production_colon_delimited_issues(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1006_job-prod.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    responses = iter(
        [
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
        ]
    )
    validate_calls = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1006.0)

    def _validate(*args, **kwargs):
        validate_calls["count"] += 1
        return False, [
            "primary_width_vs_room_width: primary width is too wide",
            "rug_vs_anchor_footprint: rug footprint does not match anchor",
        ]

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-prod",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1006.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: next(responses),
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result == {
        "path": output_path,
        "scalecheck_fail_count": 3,
        "scalecheck_retry_count": 2,
        "scale_check_failed": True,
        "scalecheck_issues": [
            "primary_width_vs_room_width: primary width is too wide",
            "rug_vs_anchor_footprint: rug footprint does not match anchor",
        ],
        "scalecheck_failed_rules": ["primary_width_vs_room_width", "rug_vs_anchor_footprint"],
    }
    assert validate_calls["count"] == 3


def test_generate_furnished_room_preserves_last_successful_path_when_later_retry_raises_exception(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1007_job-exc.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    responses = iter(
        [
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            RuntimeError("retry exploded"),
            RuntimeError("retry exploded again"),
        ]
    )
    validate_calls = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1007.0)

    def _call_gemini(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-exc",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1007.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (
            validate_calls.__setitem__("count", validate_calls["count"] + 1) or False,
            ["primary_width_vs_room_width"],
        ),
    )

    assert result == {
        "path": output_path,
        "scalecheck_fail_count": 1,
        "scalecheck_retry_count": 2,
        "scale_check_failed": True,
        "scalecheck_issues": ["primary_width_vs_room_width"],
        "scalecheck_failed_rules": ["primary_width_vs_room_width"],
    }
    assert validate_calls["count"] == 1


def test_generate_furnished_room_preserves_last_successful_path_when_later_validator_retry_raises_exception(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1009_job-valraise.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    responses = iter(
        [
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            None,
        ]
    )
    validate_calls = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1009.0)

    def _validate(*args, **kwargs):
        validate_calls["count"] += 1
        if validate_calls["count"] == 1:
            return False, ["rule_id:primary_width_vs_room_width"]
        raise RuntimeError("validator exploded")

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-valraise",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1009.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: next(responses),
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result == {
        "path": output_path,
        "scalecheck_fail_count": 2,
        "scalecheck_retry_count": 2,
        "scale_check_failed": True,
        "scalecheck_issues": ["validator exception: validator exploded"],
        "scalecheck_failed_rules": ["primary_width_vs_room_width"],
    }
    assert validate_calls["count"] == 2


def test_generate_furnished_room_invokes_scale_validation_without_room_planes(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1002_job-geo.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    gemini_response = SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
    )
    validate_calls = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1002.0)

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-geo",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes=None,
        start_time=1002.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: gemini_response,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (
            validate_calls.__setitem__("count", validate_calls["count"] + 1) or False,
            ["primary_width_vs_room_width"],
        ),
    )

    assert validate_calls["count"] == 3
    assert result["scalecheck_fail_count"] == 3


def test_generate_furnished_room_omits_free_form_issues_from_failed_rules(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1001_job-2.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    gemini_response = SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
    )

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1001.0)

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-2",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1001.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: gemini_response,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (
            False,
            ["chair is too large for the room"],
        ),
    )

    assert result == {
        "path": output_path,
        "scalecheck_fail_count": 3,
        "scalecheck_retry_count": 2,
        "scale_check_failed": True,
        "scalecheck_issues": ["chair is too large for the room"],
        "scalecheck_failed_rules": [],
    }


def test_generate_furnished_room_clears_stale_failure_diagnostics_after_later_success(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1008_job-success.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    responses = iter(
        [
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
        ]
    )
    validate_calls = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1008.0)

    def _validate(*args, **kwargs):
        validate_calls["count"] += 1
        if validate_calls["count"] == 1:
            return False, ["primary_width_vs_room_width: initial failure"]
        return True, ["primary_width_vs_room_width: stale message should clear"]

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-success",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1008.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: next(responses),
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result == {
        "path": output_path,
        "scalecheck_fail_count": 1,
        "scalecheck_retry_count": 1,
        "scale_check_failed": False,
        "scalecheck_issues": [],
        "scalecheck_failed_rules": [],
    }
    assert validate_calls["count"] == 2


def test_generate_furnished_room_keeps_structured_failed_rules_even_if_later_retries_are_free_form_only(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    output_path = os.path.join("outputs", "result_1005_job-rules.png")
    if os.path.exists(output_path):
        os.remove(output_path)

    responses = iter(
        [
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
            SimpleNamespace(
                candidates=[SimpleNamespace()],
                parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
            ),
        ]
    )
    validate_calls = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1005.0)

    def _validate(*args, **kwargs):
        validate_calls["count"] += 1
        if validate_calls["count"] == 1:
            return False, ["rule_id:primary_width_vs_room_width", "primary width too large"]
        return False, ["room is too large"]

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-rules",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500}}]},
        room_dimensions="8000x8000",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1005.0,
        enable_scale_check=True,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=lambda *args, **kwargs: next(responses),
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result == {
        "path": output_path,
        "scalecheck_fail_count": 3,
        "scalecheck_retry_count": 2,
        "scale_check_failed": True,
        "scalecheck_issues": ["room is too large"],
        "scalecheck_failed_rules": ["primary_width_vs_room_width"],
    }
    assert validate_calls["count"] == 3


def test_run_render_variant_stage_returns_structured_rows_for_mixed_variant_results():
    def fake_generate_furnished_room(*args, **kwargs):
        unique_id = args[3]
        if unique_id.endswith("_v1"):
            return "outputs/variant-1.png"
        return {
            "path": "outputs/variant-2.png",
            "scalecheck_fail_count": 2,
            "scalecheck_retry_count": 1,
            "scale_check_failed": True,
        }

    results = run_render_variant_stage(
        step1_img="step1.png",
        style_prompt="style",
        ref_input="ref.png",
        unique_id="job-1",
        furniture_specs_text=None,
        furniture_specs_json={},
        dimensions="",
        placement="",
        scale_guide_path=None,
        primary_item=None,
        room_dims_parsed={},
        wall_span_norm=(0.0, 1.0),
        size_hierarchy=[],
        start_time=0.0,
        room_planes=None,
        windows_present=False,
        room_analysis_text="",
        enable_scale_check=True,
        generate_furnished_room=fake_generate_furnished_room,
        max_variants=2,
        max_workers=1,
    )

    assert results == [
        {
            "path": "outputs/variant-1.png",
            "scalecheck_fail_count": 0,
            "scalecheck_retry_count": 0,
            "scale_check_failed": False,
            "scalecheck_issues": [],
            "scalecheck_failed_rules": [],
        },
        {
            "path": "outputs/variant-2.png",
            "scalecheck_fail_count": 2,
            "scalecheck_retry_count": 1,
            "scale_check_failed": True,
            "scalecheck_issues": [],
            "scalecheck_failed_rules": [],
        },
    ]


def test_run_render_variant_stage_coerces_scalar_scale_metadata():
    results = run_render_variant_stage(
        step1_img="step1.png",
        style_prompt="style",
        ref_input="ref.png",
        unique_id="job-2",
        furniture_specs_text=None,
        furniture_specs_json={},
        dimensions="",
        placement="",
        scale_guide_path=None,
        primary_item=None,
        room_dims_parsed={},
        wall_span_norm=(0.0, 1.0),
        size_hierarchy=[],
        start_time=0.0,
        room_planes=None,
        windows_present=False,
        room_analysis_text="",
        enable_scale_check=True,
        generate_furnished_room=lambda *args, **kwargs: {
            "path": "outputs/variant-3.png",
            "scalecheck_fail_count": 1,
            "scalecheck_retry_count": 0,
            "scale_check_failed": True,
            "scalecheck_issues": "free form issue",
            "scalecheck_failed_rules": "primary_width_vs_room_width",
        },
        max_variants=1,
        max_workers=1,
    )

    assert results == [
        {
            "path": "outputs/variant-3.png",
            "scalecheck_fail_count": 1,
            "scalecheck_retry_count": 0,
            "scale_check_failed": True,
            "scalecheck_issues": ["free form issue"],
            "scalecheck_failed_rules": ["primary_width_vs_room_width"],
        }
    ]


def test_run_render_variant_stage_uses_start_index_for_variant_suffixes():
    seen_ids: list[str] = []

    def fake_generate_furnished_room(*args, **kwargs):
        unique_id = args[3]
        seen_ids.append(unique_id)
        return {"path": f"outputs/{unique_id}.png"}

    results = run_render_variant_stage(
        step1_img="step1.png",
        style_prompt="style",
        ref_input="ref.png",
        unique_id="job-3",
        furniture_specs_text=None,
        furniture_specs_json={},
        dimensions="",
        placement="",
        scale_guide_path=None,
        primary_item=None,
        room_dims_parsed={},
        wall_span_norm=(0.0, 1.0),
        size_hierarchy=[],
        start_time=0.0,
        room_planes=None,
        windows_present=False,
        room_analysis_text="",
        enable_scale_check=True,
        generate_furnished_room=fake_generate_furnished_room,
        max_variants=1,
        max_workers=1,
        start_index=1,
    )

    assert seen_ids == ["job-3_v2"]
    assert results == [
        {
            "path": "outputs/job-3_v2.png",
            "scalecheck_fail_count": 0,
            "scalecheck_retry_count": 0,
            "scale_check_failed": False,
            "scalecheck_issues": [],
            "scalecheck_failed_rules": [],
        }
    ]


def test_run_render_room_workflow_aggregates_scale_counts_after_variants_complete(monkeypatch):
    summary_ref = _SummaryRef()
    captured = {}

    def fake_bootstrap_stage(**kwargs):
        summary = _build_summary()
        summary_token = kwargs["summary_ref"].set(summary)
        return SimpleNamespace(unique_id="job-1", start_time=0.0, summary=summary, summary_token=summary_token)

    def fake_audience_stage(**kwargs):
        return SimpleNamespace(
            audience="internal",
            enable_scale_check=True,
            prefix_main_user="main/user",
            prefix_main_empty="main/empty",
            prefix_main_rendered="main/rendered",
            prefix_customize="customize",
        )

    def fake_input_stage(**kwargs):
        return SimpleNamespace(timestamp="ts-1", std_path="outputs/std.png")

    def fake_empty_stage(**kwargs):
        return SimpleNamespace(step1_img="outputs/step1.png", step1_raw="raw")

    def fake_scale_stage(**kwargs):
        return SimpleNamespace(
            room_dims_parsed={"width_mm": 8000},
            enable_scale_guidance=True,
            room_planes={"y_top": 0.1, "y_bottom": 0.9},
            wall_span_norm=(0.0, 1.0),
            windows_present=False,
            room_analysis_text="analysis",
            furniture_specs_text="specs",
            furniture_specs_json={"items": []},
            primary_item={"label": "Chair"},
            scale_guide_path=None,
            size_hierarchy=["Chair"],
            full_analyzed_data=[{"label": "Chair"}],
        )

    def fake_prepare_render_references(**kwargs):
        return SimpleNamespace(mb_url="moodboard.png", ref_paths=["outputs/ref.png"], item_refs=[])

    def fake_analysis_stage(**kwargs):
        return SimpleNamespace(
            windows_present=False,
            room_analysis_text="analysis",
            furniture_specs_text="specs",
            furniture_specs_json={"items": []},
            full_analyzed_data=[{"label": "Chair"}],
            primary_item={"label": "Chair"},
            scale_guide_path=None,
            size_hierarchy=["Chair"],
        )

    def fake_variant_stage(**kwargs):
        return [
            {
                "path": "outputs/variant-1.png",
                "scalecheck_fail_count": 0,
                "scalecheck_retry_count": 0,
                "scale_check_failed": False,
                "review_pass": True,
                "matched_source_count": 1,
                "unmatched_source_count": 0,
            },
            {
                "path": "outputs/variant-2.png",
                "scalecheck_fail_count": 2,
                "scalecheck_retry_count": 1,
                "scale_check_failed": True,
            },
        ]

    def fake_postprocess_stage(**kwargs):
        captured["generated_results"] = list(kwargs["generated_results"])
        captured["full_analyzed_data"] = list(kwargs["full_analyzed_data"])
        return SimpleNamespace(
            generated_results=list(kwargs["generated_results"]),
            full_analyzed_data=list(kwargs["full_analyzed_data"]),
            volume_ranking=[{"label": "Chair", "volume_rank": 1}],
        )

    def fake_log_render_summary(*args, **kwargs):
        return None

    monkeypatch.setattr("application.render.render_room_workflow.run_render_bootstrap_stage", fake_bootstrap_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_audience_stage", fake_audience_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_input_stage", fake_input_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_empty_stage", fake_empty_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_scale_stage", fake_scale_stage)
    monkeypatch.setattr("application.render.render_room_workflow.prepare_render_references", fake_prepare_render_references)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_analysis_stage", fake_analysis_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_variant_stage", fake_variant_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_postprocess_stage", fake_postprocess_stage)
    monkeypatch.setattr("application.render.render_room_workflow.log_render_summary", fake_log_render_summary)

    request = RenderWorkflowRequest(
        file=object(),
        room="room",
        style="style",
        variant="variant",
        dimensions="8000x8000",
        placement="center",
        audience="internal",
        moodboard_items=[],
    )
    deps = RenderWorkflowDependencies(
        runtime=RenderWorkflowRuntime(
            style_map={"style": "Style"},
            generate_unique_id=lambda: "job-1",
            time_now=lambda: 0.0,
            log_section=lambda *_args, **_kwargs: None,
            summary_ref=summary_ref,
            reset_summary_token=lambda *_args, **_kwargs: None,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None),
            log_brief=False,
            log_summary=False,
            use_s3_moodboard=False,
            max_concurrency_analysis=1,
            cart_max_analysis_workers=1,
            total_timeout_limit_sec=600.0,
        ),
        storage=RenderWorkflowStorageServices(
            normalize_audience=lambda aud: aud or "internal",
            build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
            standardize_image=lambda *args, **kwargs: "outputs/std.png",
            materialize_input=lambda *args, **kwargs: "outputs/std.png",
            resolve_image_url=lambda path, **kwargs: f"url://{path}",
            find_s3_moodboard_key=lambda *args, **kwargs: None,
            s3_public_url=lambda path: f"url://{path}",
        ),
        analysis=RenderWorkflowAnalysisServices(
            parse_room_dimensions_mm=lambda dimensions: {"width_mm": 8000},
            room_dims_valid_fn=lambda dims: True,
            build_item_target_key=lambda *args, **kwargs: "target",
            canonical_category=lambda value: value or "unknown",
            detect_furniture_boxes=lambda *args, **kwargs: [],
            analyze_room_structure=lambda *args, **kwargs: {},
            analyze_cropped_item=lambda *args, **kwargs: {},
            normalize_dims_dict=lambda dims: dims,
            parse_object_dimensions_mm=lambda value: {},
            build_furniture_specs_json=lambda *args, **kwargs: {"items": []},
            create_scale_guide_overlay_with_model=lambda *args, **kwargs: None,
            match_aspect_to_target=lambda *args, **kwargs: None,
        ),
        generation=RenderWorkflowGenerationServices(
            generate_empty_room=lambda *args, **kwargs: ("outputs/step1.png", None),
            generate_furnished_room=lambda *args, **kwargs: "outputs/variant.png",
        ),
        postprocess=RenderWorkflowPostprocessServices(
            rank_best_variant=lambda *args, **kwargs: None,
            refresh_item_boxes_from_main_render=lambda path, items: items,
            attach_volume_ranks=lambda items: items,
            volume_ranking_snapshot=lambda items: [],
        ),
    )

    result = run_render_room_workflow(request, deps)

    assert captured["generated_results"] == ["outputs/variant-1.png", "outputs/variant-2.png"]
    assert summary_ref.summary["scalecheck_fail"] == 2
    assert summary_ref.summary["scalecheck_retry"] == 1
    assert result["result_url"] == "url://outputs/variant-1.png"
    assert set(result.keys()) == {
        "candidate_result_urls",
        "original_url",
        "empty_room_url",
        "final_result_blocked",
        "result_url",
        "result_urls",
        "selected_result_index",
        "selected_result_filename",
        "selected_result_reason",
        "selected_variant_review",
        "selected_item_review",
        "variant_diagnostics",
        "scale_plan",
        "room_dims_contract",
        "geometry_contract",
        "scene_contract",
        "placement_plan",
        "moodboard_url",
        "scale_guide_url",
        "furniture_data",
        "volume_ranking",
        "message",
    }
    assert "scalecheck_fail" not in result
    assert "scalecheck_retry" not in result
    assert result["selected_result_index"] == 0
    assert result["selected_result_filename"] == "variant-1.png"
    assert len(result["variant_diagnostics"]) == 2


def test_run_render_room_workflow_sorts_variants_by_quality_before_postprocess(monkeypatch):
    summary_ref = _SummaryRef()
    captured = {}

    def fake_bootstrap_stage(**kwargs):
        summary = _build_summary()
        summary_token = kwargs["summary_ref"].set(summary)
        summary_ref.summary = summary
        return SimpleNamespace(
            unique_id="job-quality-sort",
            start_time=0.0,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
            log_brief=True,
            log_summary=False,
            summary=summary,
            summary_token=summary_token,
        )

    def fake_audience_stage(**kwargs):
        return SimpleNamespace(
            audience="internal",
            enable_scale_check=True,
            prefix_main_user="main/user",
            prefix_main_empty="main/empty",
            prefix_main_rendered="main/rendered",
            prefix_customize="customize",
        )

    def fake_input_stage(**kwargs):
        return SimpleNamespace(
            timestamp="ts-quality-sort",
            std_path="outputs/std.png",
        )

    def fake_empty_stage(**kwargs):
        return SimpleNamespace(step1_img="outputs/empty.png", step1_raw="raw")

    def fake_scale_stage(**kwargs):
        return SimpleNamespace(
            room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
            enable_scale_guidance=True,
            room_planes=None,
            wall_span_norm=(0.1, 0.9),
            windows_present=False,
            room_analysis_text="scale-analysis",
            furniture_specs_text="specs",
            furniture_specs_json={"items": []},
            primary_item=None,
            scale_guide_path=None,
            size_hierarchy=[],
            full_analyzed_data=[],
        )

    def fake_prepare_render_references(**kwargs):
        return SimpleNamespace(ref_paths=["outputs/ref.png"], mb_url=None, item_refs=[])

    def fake_analysis_stage(**kwargs):
        return SimpleNamespace(
            room_analysis_text="analysis",
            furniture_specs_text="specs",
            furniture_specs_json={"items": []},
            full_analyzed_data=[],
            primary_item=None,
            size_hierarchy=[],
            room_planes={"y_top": 0.1, "y_bottom": 0.9},
            wall_span_norm=(0.2, 0.8),
            windows_present=True,
            scale_guide_path=None,
        )

    def fake_variant_stage(**kwargs):
        return [
            {
                "path": "outputs/variant_c.png",
                "scalecheck_fail_count": 2,
                "scalecheck_retry_count": 2,
                "scale_check_failed": True,
                "scalecheck_issues": ["rule_a", "rule_b"],
                "scalecheck_failed_rules": ["rule_a", "rule_b"],
            },
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
                "scalecheck_issues": ["rule_x"],
                "scalecheck_failed_rules": ["rule_x"],
            },
        ]

    def fake_postprocess_stage(**kwargs):
        captured["generated_results"] = list(kwargs["generated_results"])
        return SimpleNamespace(
            generated_results=list(kwargs["generated_results"]),
            full_analyzed_data=[],
            volume_ranking=[],
        )

    monkeypatch.setattr("application.render.render_room_workflow.run_render_bootstrap_stage", fake_bootstrap_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_audience_stage", fake_audience_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_input_stage", fake_input_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_empty_stage", fake_empty_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_scale_stage", fake_scale_stage)
    monkeypatch.setattr("application.render.render_room_workflow.prepare_render_references", fake_prepare_render_references)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_analysis_stage", fake_analysis_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_variant_stage", fake_variant_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_postprocess_stage", fake_postprocess_stage)
    monkeypatch.setattr("application.render.render_room_workflow.log_render_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr("application.render.render_room_workflow._should_launch_budgeted_fallback_variant", lambda *args, **kwargs: False)

    request = RenderWorkflowRequest(
        file=object(),
        room="room",
        style="style",
        variant="variant",
        dimensions="4000*4000*2400",
        placement="",
    )
    deps = RenderWorkflowDependencies(
        runtime=RenderWorkflowRuntime(
            style_map={"style": "Style"},
            generate_unique_id=lambda: "job-quality-sort",
            time_now=lambda: 0.0,
            log_section=lambda *_args, **_kwargs: None,
            summary_ref=summary_ref,
            reset_summary_token=lambda *_args, **_kwargs: None,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None),
            log_brief=False,
            log_summary=False,
            use_s3_moodboard=False,
            max_concurrency_analysis=1,
            cart_max_analysis_workers=1,
            total_timeout_limit_sec=600.0,
        ),
        storage=RenderWorkflowStorageServices(
            normalize_audience=lambda aud: aud or "internal",
            build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
            standardize_image=lambda *args, **kwargs: "outputs/std.png",
            materialize_input=lambda *args, **kwargs: "outputs/std.png",
            resolve_image_url=lambda path, **kwargs: f"url://{path}",
            find_s3_moodboard_key=lambda *args, **kwargs: None,
            s3_public_url=lambda path: f"url://{path}",
        ),
        analysis=RenderWorkflowAnalysisServices(
            parse_room_dimensions_mm=lambda dimensions: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
            room_dims_valid_fn=lambda dims: True,
            build_item_target_key=lambda *args, **kwargs: "target",
            canonical_category=lambda value: value or "unknown",
            detect_furniture_boxes=lambda *args, **kwargs: [],
            analyze_room_structure=lambda *args, **kwargs: {},
            analyze_cropped_item=lambda *args, **kwargs: {},
            normalize_dims_dict=lambda dims: dims,
            parse_object_dimensions_mm=lambda value: {},
            build_furniture_specs_json=lambda *args, **kwargs: {"items": []},
            create_scale_guide_overlay_with_model=lambda *args, **kwargs: None,
            match_aspect_to_target=lambda *args, **kwargs: None,
        ),
        generation=RenderWorkflowGenerationServices(
            generate_empty_room=lambda *args, **kwargs: ("outputs/step1.png", None),
            generate_furnished_room=lambda *args, **kwargs: "outputs/variant.png",
        ),
        postprocess=RenderWorkflowPostprocessServices(
            rank_best_variant=lambda *args, **kwargs: None,
            refresh_item_boxes_from_main_render=lambda path, items: items,
            attach_volume_ranks=lambda items: items,
            volume_ranking_snapshot=lambda items: [],
        ),
    )

    run_render_room_workflow(request, deps)

    assert captured["generated_results"] == [
        "outputs/variant_a.png",
        "outputs/variant_b.png",
        "outputs/variant_c.png",
    ]


def test_run_render_room_workflow_disables_failed_rerank_for_strict_internal(monkeypatch):
    summary_ref = _SummaryRef()
    captured = {}

    def fake_bootstrap_stage(**kwargs):
        summary = _build_summary()
        summary_token = kwargs["summary_ref"].set(summary)
        return SimpleNamespace(unique_id="job-strict-rerank", start_time=0.0, summary=summary, summary_token=summary_token)

    def fake_audience_stage(**kwargs):
        return SimpleNamespace(
            audience="internal",
            enable_scale_check=True,
            prefix_main_user="main/user",
            prefix_main_empty="main/empty",
            prefix_main_rendered="main/rendered",
            prefix_customize="customize",
        )

    def fake_input_stage(**kwargs):
        return SimpleNamespace(timestamp="ts-strict-rerank", std_path="outputs/std.png")

    def fake_empty_stage(**kwargs):
        return SimpleNamespace(step1_img="outputs/empty.png", step1_raw="raw")

    def fake_scale_stage(**kwargs):
        return SimpleNamespace(
            room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
            enable_scale_guidance=True,
            strict_scale_requested=True,
            room_planes=None,
            wall_span_norm=(0.1, 0.9),
            windows_present=False,
            room_analysis_text="analysis",
            furniture_specs_text="specs",
            furniture_specs_json={"items": []},
            primary_item=None,
            scale_guide_path=None,
            size_hierarchy=[],
            full_analyzed_data=[],
        )

    def fake_prepare_render_references(**kwargs):
        return SimpleNamespace(ref_paths=["outputs/ref.png"], mb_url=None, item_refs=[])

    def fake_analysis_stage(**kwargs):
        return SimpleNamespace(
            room_analysis_text="analysis",
            furniture_specs_text="specs",
            furniture_specs_json={"items": []},
            full_analyzed_data=[],
            primary_item=None,
            size_hierarchy=[],
            room_planes={"y_top": 0.1, "y_bottom": 0.9},
            wall_span_norm=(0.1, 0.9),
            windows_present=False,
            scale_guide_path=None,
            scale_plan={"strict_scale_requested": True, "strict_scale_ready": True, "items": []},
        )

    def fake_variant_stage(**kwargs):
        return [
            {
                "path": "outputs/variant_c.png",
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["rule_c"],
                "weighted_issue_score": 99.0,
                "review_pass": False,
                "scalecheck_diagnostics": {"issue_records": [{"weighted_score": 9.9}], "failed_rules": ["rule_c"], "matched_items": {}, "unmatched_items": []},
            },
            {
                "path": "outputs/variant_a.png",
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["rule_a"],
                "weighted_issue_score": 11.0,
                "review_pass": False,
                "scalecheck_diagnostics": {"issue_records": [{"weighted_score": 1.1}], "failed_rules": ["rule_a"], "matched_items": {}, "unmatched_items": []},
            },
            {
                "path": "outputs/variant_b.png",
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["rule_b"],
                "weighted_issue_score": 22.0,
                "review_pass": False,
                "scalecheck_diagnostics": {"issue_records": [{"weighted_score": 2.2}], "failed_rules": ["rule_b"], "matched_items": {}, "unmatched_items": []},
            },
        ]

    def fake_postprocess_stage(**kwargs):
        captured["generated_results"] = list(kwargs["generated_results"])
        captured["allow_failed_rerank"] = kwargs["allow_failed_rerank"]
        return SimpleNamespace(
            generated_results=list(kwargs["generated_results"]),
            full_analyzed_data=[],
            volume_ranking=[],
        )

    monkeypatch.setattr("application.render.render_room_workflow.run_render_bootstrap_stage", fake_bootstrap_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_audience_stage", fake_audience_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_input_stage", fake_input_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_empty_stage", fake_empty_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_scale_stage", fake_scale_stage)
    monkeypatch.setattr("application.render.render_room_workflow.prepare_render_references", fake_prepare_render_references)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_analysis_stage", fake_analysis_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_variant_stage", fake_variant_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_postprocess_stage", fake_postprocess_stage)
    monkeypatch.setattr("application.render.render_room_workflow.log_render_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr("application.render.render_room_workflow._should_launch_budgeted_fallback_variant", lambda *args, **kwargs: False)

    request = RenderWorkflowRequest(file=object(), room="room", style="style", variant="variant", dimensions="4000*4000*2400", placement="")
    deps = RenderWorkflowDependencies(
        runtime=RenderWorkflowRuntime(
            style_map={"style": "Style"},
            generate_unique_id=lambda: "job-strict-rerank",
            time_now=lambda: 0.0,
            log_section=lambda *_args, **_kwargs: None,
            summary_ref=summary_ref,
            reset_summary_token=lambda *_args, **_kwargs: None,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None),
            log_brief=False,
            log_summary=False,
            use_s3_moodboard=False,
            max_concurrency_analysis=1,
            cart_max_analysis_workers=1,
            total_timeout_limit_sec=600.0,
        ),
        storage=RenderWorkflowStorageServices(
            normalize_audience=lambda aud: aud or "internal",
            build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
            standardize_image=lambda *args, **kwargs: "outputs/std.png",
            materialize_input=lambda *args, **kwargs: "outputs/std.png",
            resolve_image_url=lambda path, **kwargs: f"url://{path}",
            find_s3_moodboard_key=lambda *args, **kwargs: None,
            s3_public_url=lambda path: f"url://{path}",
        ),
        analysis=RenderWorkflowAnalysisServices(
            parse_room_dimensions_mm=lambda dimensions: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
            room_dims_valid_fn=lambda dims: True,
            build_item_target_key=lambda *args, **kwargs: "target",
            canonical_category=lambda value: value or "unknown",
            detect_furniture_boxes=lambda *args, **kwargs: [],
            analyze_room_structure=lambda *args, **kwargs: {},
            analyze_cropped_item=lambda *args, **kwargs: {},
            normalize_dims_dict=lambda dims: dims,
            parse_object_dimensions_mm=lambda value: {},
            build_furniture_specs_json=lambda *args, **kwargs: {"items": []},
            create_scale_guide_overlay_with_model=lambda *args, **kwargs: None,
            match_aspect_to_target=lambda *args, **kwargs: None,
        ),
        generation=RenderWorkflowGenerationServices(
            generate_empty_room=lambda *args, **kwargs: ("outputs/step1.png", None),
            generate_furnished_room=lambda *args, **kwargs: "outputs/variant.png",
        ),
        postprocess=RenderWorkflowPostprocessServices(
            rank_best_variant=lambda *args, **kwargs: 2,
            refresh_item_boxes_from_main_render=lambda path, items: items,
            attach_volume_ranks=lambda items: items,
            volume_ranking_snapshot=lambda items: [],
        ),
    )

    run_render_room_workflow(request, deps)

    assert captured["allow_failed_rerank"] is False
    assert captured["generated_results"] == [
        "outputs/variant_a.png",
        "outputs/variant_b.png",
        "outputs/variant_c.png",
    ]


def test_run_render_room_workflow_passes_analysis_geometry_into_variants(monkeypatch):
    summary_ref = _SummaryRef()
    captured = {}

    def fake_bootstrap_stage(**kwargs):
        summary = _build_summary()
        summary_token = kwargs["summary_ref"].set(summary)
        return SimpleNamespace(unique_id="job-geom-2", start_time=0.0, summary=summary, summary_token=summary_token)

    def fake_audience_stage(**kwargs):
        return SimpleNamespace(
            audience="internal",
            enable_scale_check=True,
            prefix_main_user="main/user",
            prefix_main_empty="main/empty",
            prefix_main_rendered="main/rendered",
            prefix_customize="customize",
        )

    def fake_input_stage(**kwargs):
        return SimpleNamespace(timestamp="ts-geom", std_path="outputs/std.png")

    def fake_empty_stage(**kwargs):
        return SimpleNamespace(step1_img="outputs/step1.png", step1_raw="raw")

    def fake_scale_stage(**kwargs):
        return SimpleNamespace(
            room_dims_parsed={"width_mm": 0, "depth_mm": 0, "height_mm": 0},
            room_dims_valid=False,
            enable_scale_guidance=True,
            room_planes=None,
            wall_span_norm=(0.0, 1.0),
            windows_present=False,
            room_analysis_text="scale-stage-analysis",
            furniture_specs_text="specs",
            furniture_specs_json={"items": []},
            primary_item={"label": "Chair"},
            scale_guide_path=None,
            size_hierarchy=["Chair"],
            full_analyzed_data=[{"label": "Chair"}],
        )

    def fake_prepare_render_references(**kwargs):
        return SimpleNamespace(mb_url="moodboard.png", ref_paths=["outputs/ref.png"], item_refs=[])

    def fake_analysis_stage(**kwargs):
        return SimpleNamespace(
            windows_present=False,
            room_analysis_text="analysis-stage-analysis",
            room_planes={"floor": "plane"},
            wall_span_norm=(0.25, 0.75),
            furniture_specs_text="specs",
            furniture_specs_json={"items": []},
            full_analyzed_data=[{"label": "Chair"}],
            primary_item={"label": "Chair"},
            scale_guide_path=None,
            size_hierarchy=["Chair"],
        )

    def fake_variant_stage(**kwargs):
        captured["room_planes"] = kwargs["room_planes"]
        captured["wall_span_norm"] = kwargs["wall_span_norm"]
        captured["room_analysis_text"] = kwargs["room_analysis_text"]
        return [
            {
                "path": "outputs/variant-1.png",
                "scalecheck_fail_count": 0,
                "scalecheck_retry_count": 0,
                "scale_check_failed": False,
            }
        ]

    def fake_postprocess_stage(**kwargs):
        return SimpleNamespace(
            generated_results=list(kwargs["generated_results"]),
            full_analyzed_data=list(kwargs["full_analyzed_data"]),
            volume_ranking=[{"label": "Chair", "volume_rank": 1}],
        )

    monkeypatch.setattr("application.render.render_room_workflow.run_render_bootstrap_stage", fake_bootstrap_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_audience_stage", fake_audience_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_input_stage", fake_input_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_empty_stage", fake_empty_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_scale_stage", fake_scale_stage)
    monkeypatch.setattr("application.render.render_room_workflow.prepare_render_references", fake_prepare_render_references)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_analysis_stage", fake_analysis_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_variant_stage", fake_variant_stage)
    monkeypatch.setattr("application.render.render_room_workflow.run_render_postprocess_stage", fake_postprocess_stage)
    monkeypatch.setattr("application.render.render_room_workflow.log_render_summary", lambda *args, **kwargs: None)

    request = RenderWorkflowRequest(
        file=object(),
        room="room",
        style="style",
        variant="variant",
        dimensions="",
        placement="center",
        audience="internal",
        moodboard_items=[],
    )
    deps = RenderWorkflowDependencies(
        runtime=RenderWorkflowRuntime(
            style_map={"style": "Style"},
            generate_unique_id=lambda: "job-geom-2",
            time_now=lambda: 0.0,
            log_section=lambda *_args, **_kwargs: None,
            summary_ref=summary_ref,
            reset_summary_token=lambda *_args, **_kwargs: None,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None),
            log_brief=False,
            log_summary=False,
            use_s3_moodboard=False,
            max_concurrency_analysis=1,
            cart_max_analysis_workers=1,
            total_timeout_limit_sec=600.0,
        ),
        storage=RenderWorkflowStorageServices(
            normalize_audience=lambda aud: aud or "internal",
            build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
            standardize_image=lambda *args, **kwargs: "outputs/std.png",
            materialize_input=lambda *args, **kwargs: "outputs/std.png",
            resolve_image_url=lambda path, **kwargs: f"url://{path}",
            find_s3_moodboard_key=lambda *args, **kwargs: None,
            s3_public_url=lambda path: f"url://{path}",
        ),
        analysis=RenderWorkflowAnalysisServices(
            parse_room_dimensions_mm=lambda dimensions: {"width_mm": 0, "depth_mm": 0, "height_mm": 0},
            room_dims_valid_fn=lambda dims: False,
            build_item_target_key=lambda *args, **kwargs: "target",
            canonical_category=lambda value: value or "unknown",
            detect_furniture_boxes=lambda *args, **kwargs: [],
            analyze_room_structure=lambda *args, **kwargs: {},
            analyze_cropped_item=lambda *args, **kwargs: {},
            normalize_dims_dict=lambda dims: dims,
            parse_object_dimensions_mm=lambda value: {},
            build_furniture_specs_json=lambda *args, **kwargs: {"items": []},
            create_scale_guide_overlay_with_model=lambda *args, **kwargs: None,
            match_aspect_to_target=lambda *args, **kwargs: None,
            estimate_room_dims_contract=lambda **kwargs: SimpleNamespace(
                source="estimated",
                confidence="medium",
                strict_scale_mode="range_based_geometry_mode",
                dims_mm_center={"width_mm": 5200, "depth_mm": 4100, "height_mm": 2600},
                as_dict=lambda: {
                    "source": "estimated",
                    "confidence": "medium",
                    "strict_scale_mode": "range_based_geometry_mode",
                    "dims_mm_center": {"width_mm": 5200, "depth_mm": 4100, "height_mm": 2600},
                    "dims_mm_range": {
                        "width_mm": {"min_mm": 4264, "max_mm": 6136},
                        "depth_mm": {"min_mm": 3362, "max_mm": 4838},
                        "height_mm": {"min_mm": 2132, "max_mm": 3068},
                    },
                    "estimation_basis": ["room_image_estimate"],
                    "calibration_metadata": {},
                    "room_dims_valid": False,
                },
            ),
        ),
        generation=RenderWorkflowGenerationServices(
            generate_empty_room=lambda *args, **kwargs: ("outputs/step1.png", None),
            generate_furnished_room=lambda *args, **kwargs: "outputs/variant.png",
        ),
        postprocess=RenderWorkflowPostprocessServices(
            rank_best_variant=lambda *args, **kwargs: None,
            refresh_item_boxes_from_main_render=lambda path, items: items,
            attach_volume_ranks=lambda items: items,
            volume_ranking_snapshot=lambda items: [],
        ),
    )

    run_render_room_workflow(request, deps)

    assert captured["room_planes"] == {"floor": "plane"}
    assert captured["wall_span_norm"] == (0.25, 0.75)
    assert "ANALYSIS-DERIVED ROOM DIMENSIONS" in captured["room_analysis_text"]
    assert "W 5200mm" in captured["room_analysis_text"]


def test_generate_furnished_room_counts_retries_before_first_success(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    call_state = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1013.0)

    def _call_gemini(model_name, content, *args, **kwargs):
        call_state["count"] += 1
        if call_state["count"] < 3:
            return SimpleNamespace(candidates=[SimpleNamespace()], parts=[])
        return SimpleNamespace(
            candidates=[SimpleNamespace()],
            parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(160, 90)))],
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-retry-count",
        furniture_specs_json={"items": []},
        room_dimensions="4000x4000x2400",
        primary_item={"label": "Chair", "dims_mm": {"width_mm": 600, "depth_mm": 600, "height_mm": 900}},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1013.0,
        enable_scale_check=False,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, []),
    )

    assert call_state["count"] == 3
    assert result == {
        "path": os.path.join("outputs", "result_1013_job-retry-count.png"),
        "scalecheck_fail_count": 0,
        "scalecheck_retry_count": 2,
        "scale_check_failed": False,
        "scalecheck_issues": [],
        "scalecheck_failed_rules": [],
    }


def test_generate_furnished_room_respects_explicit_single_attempt_limit(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    call_state = {"count": 0}

    monkeypatch.setattr(furnished_generation_stage.time, "time", lambda: 1014.0)

    def _call_gemini(model_name, content, *args, **kwargs):
        call_state["count"] += 1
        return SimpleNamespace(candidates=[SimpleNamespace()], parts=[])

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "job-single-attempt",
        furniture_specs_json={"items": []},
        room_dimensions="4000x4000x2400",
        primary_item={"label": "Chair", "dims_mm": {"width_mm": 600, "depth_mm": 600, "height_mm": 900}},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=1014.0,
        enable_scale_check=False,
        total_timeout_limit=30,
        detect_windows_present=lambda path: False,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=SimpleNamespace(get=lambda: _build_summary()),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_gemini_with_failover=_call_gemini,
        model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, []),
        max_generation_attempts=1,
    )

    assert call_state["count"] == 1
    assert result is None


def test_collect_repair_targets_prefers_weighted_issue_score_over_family_priority():
    diagnostics = {
        "matched_items": {
            "chair_01": {"bbox_norm": [0.1, 0.4, 0.2, 0.8], "match_confidence": 0.95, "item_importance": 2.2},
            "mirror_01": {"bbox_norm": [0.7, 0.2, 0.8, 0.8], "match_confidence": 0.72, "item_importance": 1.1},
        },
        "unmatched_items": [
            {"item_key": "table_01", "label": "Table", "target_key": "table_01", "family": "table", "item_importance": 2.8}
        ],
        "issue_records": [
            {"item_key": "chair_01", "severity": 1.2, "confidence": 0.95, "item_importance": 2.2},
            {"item_key": "mirror_01", "severity": 0.5, "confidence": 0.72, "item_importance": 1.1},
        ],
    }
    furniture_specs_json = {
        "items": [
            {"target_key": "chair_01", "label": "Chair", "identity_profile": {"family": "chair"}, "layout_envelope": {"room_width_ratio": 0.12}},
            {"target_key": "mirror_01", "label": "Mirror", "identity_profile": {"family": "mirror"}, "layout_envelope": {"room_width_ratio": 0.08}},
            {"target_key": "table_01", "label": "Table", "identity_profile": {"family": "table"}, "layout_envelope": {"room_width_ratio": 0.15}},
        ],
        "primary_scale": {"target_key": "mirror_01"},
    }

    targets = furnished_generation_stage._collect_repair_targets(diagnostics, furniture_specs_json, limit=2)

    assert [row["item_key"] for row in targets] == ["table_01", "chair_01"]
    assert targets[0]["priority_score"] > targets[1]["priority_score"]


def test_review_summary_backfills_weight_for_failed_variant_without_issue_records():
    summary = _review_summary_from_scalecheck_diagnostics(
        {"failed_rules": ["scale_guide_leak_detected"], "matched_items": {}, "unmatched_items": []},
        scale_check_failed=True,
        failed_rules=["scale_guide_leak_detected"],
        issues=["scale_guide_leak_detected"],
    )

    assert summary["review_pass"] is False
    assert summary["weighted_issue_score"] > 0


class _SummaryRef:
    def __init__(self):
        self.summary = None

    def set(self, summary):
        self.summary = summary
        return "summary-token"

    def get(self):
        return self.summary
