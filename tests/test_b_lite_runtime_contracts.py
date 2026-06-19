import io
import os
from types import SimpleNamespace

from PIL import Image

from application.render.furnished_generation_stage import generate_furnished_room


def _make_png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _response(width: int = 160, height: int = 90):
    return SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(width, height)))],
    )


def _summary_ref():
    return SimpleNamespace(get=lambda: {"dims_warn": 0, "primary_bbox_miss": 0})


def _logger():
    return SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)


def test_strict_b_lite_runtime_keeps_first_failed_render_without_localized_repair(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    crop_path = tmp_path / "chair.png"
    crop_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 2000.0)

    validate_calls: list[tuple[list[str] | None, bool]] = []

    def _validate(*args, **kwargs):
        validate_calls.append((kwargs.get("focus_item_keys"), bool(kwargs.get("skip_reference_review"))))
        if len(validate_calls) == 1:
            return (
                False,
                ["reference_shape_drift"],
                {
                    "failed_rules": ["reference_shape_drift"],
                    "matched_items": {
                        "chair-1": {
                            "bbox_norm": [0.1, 0.2, 0.3, 0.5],
                            "match_confidence": 0.95,
                        }
                    },
                    "unmatched_items": [],
                    "issue_records": [
                        {
                            "item_key": "chair-1",
                            "rule_id": "reference_shape_drift",
                            "rule_kind": "reference_shape_drift",
                            "severity": 1.0,
                            "confidence": 0.95,
                        }
                    ],
                    "cheap_first_item_keys": ["chair-1"],
                    "rule_details": {},
                },
            )
        return (
            True,
            [],
            {
                "failed_rules": [],
                "matched_items": {
                    "chair-1": {
                        "bbox_norm": [0.11, 0.21, 0.31, 0.51],
                        "match_confidence": 0.96,
                    }
                },
                "unmatched_items": [],
                "rule_details": {},
            },
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "strict-b-lite",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Chair",
                    "category": "chair",
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "requested_dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "crop_path": str(crop_path),
                    "identity_profile": {"family": "chair", "distinctive_parts": ["rolled back"]},
                    "product_identity": {"family": "chair", "topology_cues": ["rolled back"]},
                    "archetype_strategy": {"render_strategy": "support_geometry_object", "strictness": "critical"},
                    "layout_envelope": {"room_width_ratio": 0.125, "room_depth_ratio": 0.125, "room_height_ratio": 0.333},
                    "placement_contract": {"zone": "front-left"},
                }
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Chair"},
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "chair-1", "label": "Chair"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        start_time=2000.0,
        enable_scale_check=True,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=lambda *args, **kwargs: _response(),
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result is not None
    assert result["scalecheck_fail_count"] == 1
    assert result["scalecheck_retry_count"] == 0
    assert "repair_applied" not in result
    assert "repair_attempt_count" not in result
    assert "repair_target_keys" not in result
    assert result["scale_check_failed"] is True
    assert result["scalecheck_failed_rules"] == ["reference_shape_drift"]
    assert result["scalecheck_diagnostics"]["matched_items"]["chair-1"]["bbox_norm"] == [0.1, 0.2, 0.3, 0.5]
    assert validate_calls == [(None, False)]


def test_strict_b_lite_runtime_does_not_run_full_scene_repair_sweep(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    crop_path = tmp_path / "chair.png"
    crop_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 2050.0)

    validate_calls: list[tuple[list[str] | None, bool]] = []

    def _validate(*args, **kwargs):
        validate_calls.append((kwargs.get("focus_item_keys"), bool(kwargs.get("skip_reference_review"))))
        if len(validate_calls) == 1:
            return (
                False,
                ["reference_shape_drift"],
                {
                    "failed_rules": ["reference_shape_drift"],
                    "matched_items": {
                        "chair-1": {
                            "bbox_norm": [0.1, 0.2, 0.3, 0.5],
                            "match_confidence": 0.95,
                        }
                    },
                    "unmatched_items": [],
                    "issue_records": [
                        {
                            "item_key": "chair-1",
                            "rule_id": "reference_shape_drift",
                            "rule_kind": "reference_shape_drift",
                            "severity": 1.0,
                            "confidence": 0.95,
                        }
                    ],
                    "cheap_first_item_keys": ["chair-1"],
                    "rule_details": {},
                },
            )
        if len(validate_calls) == 2:
            return (
                True,
                [],
                {
                    "failed_rules": [],
                    "matched_items": {
                        "chair-1": {
                            "bbox_norm": [0.11, 0.21, 0.31, 0.51],
                            "match_confidence": 0.96,
                        }
                    },
                    "unmatched_items": [],
                    "rule_details": {},
                },
            )
        return (
            False,
            ["primary_width_vs_room_width"],
            {
                "failed_rules": ["primary_width_vs_room_width"],
                "matched_items": {
                    "chair-1": {
                        "bbox_norm": [0.11, 0.21, 0.31, 0.51],
                        "match_confidence": 0.96,
                    }
                },
                "unmatched_items": [],
                "rule_details": {},
            },
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "strict-b-lite-fullsweep",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Chair",
                    "category": "chair",
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "requested_dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "crop_path": str(crop_path),
                    "identity_profile": {"family": "chair", "distinctive_parts": ["rolled back"]},
                    "product_identity": {"family": "chair", "topology_cues": ["rolled back"]},
                    "archetype_strategy": {"render_strategy": "support_geometry_object", "strictness": "critical"},
                    "layout_envelope": {"room_width_ratio": 0.125, "room_depth_ratio": 0.125, "room_height_ratio": 0.333},
                    "placement_contract": {"zone": "front-left"},
                }
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Chair"},
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "chair-1", "label": "Chair"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        start_time=2050.0,
        enable_scale_check=True,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=lambda *args, **kwargs: _response(),
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result is not None
    assert result["scale_check_failed"] is True
    assert result["scalecheck_failed_rules"] == ["reference_shape_drift"]
    assert validate_calls == [(None, False)]


def test_non_strict_runtime_keeps_legacy_retry_budget(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 2100.0)

    responses = iter([_response(), _response(), _response()])
    validate_calls = {"count": 0}

    def _validate(*args, **kwargs):
        validate_calls["count"] += 1
        if validate_calls["count"] < 3:
            return False, ["primary_width_vs_room_width"], {"failed_rules": ["primary_width_vs_room_width"], "matched_items": {}, "unmatched_items": [], "rule_details": {}}
        return True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "legacy-retry",
        furniture_specs_json={"items": [{"label": "Chair", "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800}}]},
        room_dimensions="4000x4000x2400",
        primary_item={"label": "Chair"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        start_time=2100.0,
        enable_scale_check=True,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=lambda *args, **kwargs: next(responses),
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result is not None
    assert result["scalecheck_fail_count"] == 2
    assert result["scalecheck_retry_count"] == 2
    assert validate_calls["count"] == 3


def test_strict_b_lite_runtime_keeps_original_render_without_repair_scoring(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    crop_path = tmp_path / "chair.png"
    crop_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 2200.0)

    generation_output = os.path.join("outputs", "result_2200_strict-reject.png")
    for path in (generation_output,):
        if os.path.exists(path):
            os.remove(path)

    validate_calls: list[list[str] | None] = []

    def _validate(*args, **kwargs):
        validate_calls.append(kwargs.get("focus_item_keys"))
        if len(validate_calls) == 1:
            return (
                False,
                ["reference_shape_drift"],
                {
                    "failed_rules": ["reference_shape_drift"],
                    "matched_items": {
                        "chair-1": {
                            "bbox_norm": [0.1, 0.2, 0.3, 0.5],
                            "match_confidence": 0.95,
                        }
                    },
                    "unmatched_items": [],
                    "issue_records": [
                        {
                            "item_key": "chair-1",
                            "rule_id": "reference_shape_drift",
                            "rule_kind": "reference_shape_drift",
                            "severity": 1.0,
                            "confidence": 0.95,
                        }
                    ],
                    "cheap_first_item_keys": ["chair-1"],
                    "rule_details": {},
                },
            )
        return (
            False,
            ["reference_shape_drift", "reference_material_drift"],
            {
                "failed_rules": ["reference_shape_drift", "reference_material_drift"],
                "matched_items": {
                    "chair-1": {
                        "bbox_norm": [0.12, 0.22, 0.32, 0.52],
                        "match_confidence": 0.95,
                    }
                },
                "unmatched_items": [],
                "issue_records": [
                    {
                        "item_key": "chair-1",
                        "rule_id": "reference_shape_drift",
                        "rule_kind": "reference_shape_drift",
                        "severity": 1.0,
                        "confidence": 0.95,
                        "weighted_score": 20.0,
                    },
                    {
                        "item_key": "chair-1",
                        "rule_id": "reference_material_drift",
                        "rule_kind": "reference_material_drift",
                        "severity": 1.0,
                        "confidence": 0.95,
                        "weighted_score": 15.0,
                    },
                ],
                "cheap_first_item_keys": ["chair-1"],
                "rule_details": {},
            },
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "strict-reject",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Chair",
                    "category": "chair",
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "requested_dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "crop_path": str(crop_path),
                    "identity_profile": {"family": "chair", "distinctive_parts": ["rolled back"]},
                    "product_identity": {"family": "chair", "topology_cues": ["rolled back"]},
                    "archetype_strategy": {"render_strategy": "support_geometry_object", "strictness": "critical"},
                    "layout_envelope": {"room_width_ratio": 0.125, "room_depth_ratio": 0.125, "room_height_ratio": 0.333},
                    "placement_contract": {"zone": "front-left"},
                }
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Chair"},
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "chair-1", "label": "Chair"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        start_time=2200.0,
        enable_scale_check=True,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=lambda *args, **kwargs: _response(),
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result is not None
    assert result["path"] == generation_output
    assert "repair_applied" not in result
    assert result["scale_check_failed"] is True
    assert result["scalecheck_failed_rules"] == ["reference_shape_drift"]
    assert validate_calls == [None]


def test_strict_b_lite_first_pass_includes_legacy_pass2_cutouts_without_repair(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    sofa_crop = tmp_path / "sofa.png"
    side_crop = tmp_path / "side.png"
    sofa_crop.write_bytes(_make_png_bytes(80, 80))
    side_crop.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 2300.0)

    generation_payloads: list[list] = []
    validate_calls: list[list[str] | None] = []

    def _generation(*args, **kwargs):
        generation_payloads.append(list(args[1]))
        return _response()

    def _validate(*args, **kwargs):
        validate_calls.append(kwargs.get("focus_item_keys"))
        if len(validate_calls) == 1:
            return (
                False,
                ["unmatched_source_items"],
                {
                    "failed_rules": ["unmatched_source_items"],
                    "matched_items": {
                        "sofa-1": {
                            "bbox_norm": [0.1, 0.2, 0.6, 0.7],
                            "match_confidence": 0.95,
                        }
                    },
                    "unmatched_items": [
                        {
                            "target_key": "side-2",
                            "label": "Side Table",
                        }
                    ],
                    "issue_records": [],
                    "cheap_first_item_keys": ["sofa-1"],
                    "rule_details": {},
                },
            )
        return (
            True,
            [],
            {
                "failed_rules": [],
                "matched_items": {
                    "sofa-1": {
                        "bbox_norm": [0.1, 0.2, 0.6, 0.7],
                        "match_confidence": 0.95,
                    },
                    "side-2": {
                        "bbox_norm": [0.72, 0.55, 0.86, 0.86],
                        "match_confidence": 0.94,
                    },
                },
                "unmatched_items": [],
                "rule_details": {},
            },
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "two-pass-pass2",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "sofa-1",
                    "label": "Sofa",
                    "category": "sofa",
                    "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
                    "requested_dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
                    "crop_path": str(sofa_crop),
                    "identity_profile": {"family": "sofa"},
                    "product_identity": {"family": "sofa"},
                    "archetype_strategy": {"render_strategy": "topology_sensitive_seating", "strictness": "critical"},
                    "layout_envelope": {"room_width_ratio": 0.55, "room_depth_ratio": 0.23, "room_height_ratio": 0.33},
                    "placement_contract": {"zone": "back-wall"},
                },
                {
                    "target_key": "side-2",
                    "label": "Side Table",
                    "category": "table",
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500},
                    "requested_dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500},
                    "crop_path": str(side_crop),
                    "identity_profile": {"family": "table"},
                    "product_identity": {"family": "table"},
                    "archetype_strategy": {"render_strategy": "support_geometry_object", "strictness": "critical"},
                    "layout_envelope": {"room_width_ratio": 0.125, "room_depth_ratio": 0.125, "room_height_ratio": 0.21},
                    "placement_contract": {"zone": "right-front"},
                },
            ],
            "primary_scale": {"target_key": "sofa-1", "label": "Sofa"},
            "two_pass_strategy": {
                "pass1_primary_keys": ["sofa-1"],
                "pass1_support_keys": [],
                "pass2_detail_keys": ["side-2"],
            },
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "sofa-1", "label": "Sofa"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        start_time=2300.0,
        enable_scale_check=True,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=_generation,
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result is not None
    assert generation_payloads
    generation_reference_lines = [part for part in generation_payloads[0] if isinstance(part, str) and part.startswith("Furniture Cutout Reference")]
    reserve_reference_lines = [part for part in generation_payloads[0] if isinstance(part, str) and part.startswith("Pass2 Detail Reserve Reference")]
    assert any("Sofa" in line for line in generation_reference_lines)
    assert any("Side Table" in line for line in generation_reference_lines)
    assert not reserve_reference_lines
    assert not any("Do NOT insert these pass2 detail items yet" in part for part in generation_payloads[0] if isinstance(part, str))
    assert validate_calls == [None]


def test_strict_b_lite_first_pass_keeps_primary_and_detail_refs_when_legacy_pass2_bucket_is_large(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 2400.0)

    generation_payloads: list[list] = []
    validate_calls: list[list[str] | None] = []

    items = []
    unmatched_rows = []
    for index in range(1, 7):
        crop_path = tmp_path / f"detail-{index}.png"
        crop_path.write_bytes(_make_png_bytes(64, 64))
        item_key = f"detail-{index}"
        items.append(
            {
                "target_key": item_key,
                "label": f"Detail {index}",
                "category": "decor",
                "dims_mm": {"width_mm": 200, "depth_mm": 200, "height_mm": 200},
                "requested_dims_mm": {"width_mm": 200, "depth_mm": 200, "height_mm": 200},
                "crop_path": str(crop_path),
                "identity_profile": {"family": "decor"},
                "product_identity": {"family": "decor"},
                "archetype_strategy": {"render_strategy": "tiny_absolute_scale_object", "strictness": "standard"},
                "two_pass_strategy": {"pass_role": "pass2_small"},
                "layout_envelope": {"room_width_ratio": 0.05, "room_depth_ratio": 0.05, "room_height_ratio": 0.08},
                "placement_contract": {"zone": "scatter"},
            }
        )
        unmatched_rows.append({"target_key": item_key, "label": f"Detail {index}"})

    primary_crop = tmp_path / "primary.png"
    primary_crop.write_bytes(_make_png_bytes(80, 80))
    items.insert(
        0,
        {
            "target_key": "sofa-1",
            "label": "Primary Sofa",
            "category": "sofa",
            "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
            "requested_dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
            "crop_path": str(primary_crop),
            "identity_profile": {"family": "sofa"},
            "product_identity": {"family": "sofa"},
            "archetype_strategy": {"render_strategy": "topology_sensitive_seating", "strictness": "critical"},
            "two_pass_strategy": {"pass_role": "pass1_anchor"},
            "layout_envelope": {"room_width_ratio": 0.55, "room_depth_ratio": 0.23, "room_height_ratio": 0.33},
            "placement_contract": {"zone": "back-wall"},
        },
    )
    support_crop = tmp_path / "support.png"
    support_crop.write_bytes(_make_png_bytes(72, 72))
    items.insert(
        1,
        {
            "target_key": "table-1",
            "label": "Support Table",
            "category": "table",
            "dims_mm": {"width_mm": 900, "depth_mm": 600, "height_mm": 450},
            "requested_dims_mm": {"width_mm": 900, "depth_mm": 600, "height_mm": 450},
            "crop_path": str(support_crop),
            "identity_profile": {"family": "table"},
            "product_identity": {"family": "table"},
            "archetype_strategy": {"render_strategy": "support_geometry_object", "strictness": "critical"},
            "two_pass_strategy": {"pass_role": "pass1_footprint"},
            "layout_envelope": {"room_width_ratio": 0.225, "room_depth_ratio": 0.15, "room_height_ratio": 0.19},
            "placement_contract": {"zone": "center"},
        },
    )

    def _generation(*args, **kwargs):
        generation_payloads.append(list(args[1]))
        return _response()


    def _validate(*args, **kwargs):
        validate_calls.append(kwargs.get("focus_item_keys"))
        if len(validate_calls) == 1:
            return (
                False,
                ["reference_shape_drift", "unmatched_source_items"],
                {
                    "failed_rules": ["reference_shape_drift", "unmatched_source_items"],
                    "matched_items": {
                        "sofa-1": {
                            "bbox_norm": [0.1, 0.2, 0.6, 0.7],
                            "match_confidence": 0.95,
                        },
                        "table-1": {
                            "bbox_norm": [0.45, 0.5, 0.7, 0.72],
                            "match_confidence": 0.93,
                        },
                    },
                    "unmatched_items": unmatched_rows,
                    "issue_records": [
                        {
                            "item_key": "sofa-1",
                            "rule_id": "reference_shape_drift",
                            "rule_kind": "reference_shape_drift",
                            "severity": 1.0,
                            "confidence": 0.95,
                        },
                        {
                            "item_key": "table-1",
                            "rule_id": "reference_shape_drift",
                            "rule_kind": "reference_shape_drift",
                            "severity": 1.0,
                            "confidence": 0.92,
                        },
                    ],
                    "cheap_first_item_keys": ["sofa-1", "table-1"],
                    "rule_details": {},
                },
            )
        return (
            True,
            [],
            {
                "failed_rules": [],
                "matched_items": {
                    "sofa-1": {"bbox_norm": [0.1, 0.2, 0.6, 0.7], "match_confidence": 0.95},
                    "table-1": {"bbox_norm": [0.45, 0.5, 0.7, 0.72], "match_confidence": 0.93},
                },
                "unmatched_items": [],
                "rule_details": {},
            },
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "two-pass-priority",
        furniture_specs_json={
            "items": items,
            "primary_scale": {"target_key": "sofa-1", "label": "Primary Sofa"},
            "two_pass_strategy": {
                "pass1_primary_keys": ["sofa-1"],
                "pass1_support_keys": ["table-1"],
                "pass2_detail_keys": [f"detail-{index}" for index in range(1, 7)],
            },
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "sofa-1", "label": "Primary Sofa"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        start_time=2400.0,
        enable_scale_check=True,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=_generation,
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result is not None
    reference_lines = [part for part in generation_payloads[0] if isinstance(part, str) and part.startswith("Furniture Cutout Reference")]
    assert any("Primary Sofa" in line for line in reference_lines)
    assert any("Support Table" in line for line in reference_lines)
    assert any("Detail " in line for line in reference_lines)
    assert not any(part.startswith("Pass2 Detail Reserve Reference") for part in generation_payloads[0] if isinstance(part, str))
    assert validate_calls == [None]


def test_strict_b_lite_first_pass_cap_does_not_evict_pass1_support_refs(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 2500.0)

    generation_payloads: list[list] = []

    items = []
    pass2_keys = []
    for index in range(1, 13):
        crop_path = tmp_path / f"reserve-{index}.png"
        crop_path.write_bytes(_make_png_bytes(64, 64))
        item_key = f"reserve-{index}"
        pass2_keys.append(item_key)
        items.append(
            {
                "target_key": item_key,
                "label": f"Reserve {index}",
                "category": "decor",
                "dims_mm": {"width_mm": 220, "depth_mm": 220, "height_mm": 220},
                "requested_dims_mm": {"width_mm": 220, "depth_mm": 220, "height_mm": 220},
                "crop_path": str(crop_path),
                "identity_profile": {"family": "decor"},
                "product_identity": {"family": "decor"},
                "archetype_strategy": {"render_strategy": "tiny_absolute_scale_object", "strictness": "standard"},
                "two_pass_strategy": {"pass_role": "pass2_small"},
                "layout_envelope": {"room_width_ratio": 0.05, "room_depth_ratio": 0.05, "room_height_ratio": 0.08},
                "placement_contract": {"zone": "scatter"},
                "category_score": 90 - index,
            }
        )

    for key, label in (("sofa-1", "Primary Sofa"), ("table-1", "Support Table"), ("desk-1", "Support Desk")):
        crop_path = tmp_path / f"{key}.png"
        crop_path.write_bytes(_make_png_bytes(80, 80))
        items.insert(
            0,
            {
                "target_key": key,
                "label": label,
                "category": "sofa" if key == "sofa-1" else "table",
                "dims_mm": {"width_mm": 2200 if key == "sofa-1" else 900, "depth_mm": 900 if key == "sofa-1" else 600, "height_mm": 800 if key == "sofa-1" else 450},
                "requested_dims_mm": {"width_mm": 2200 if key == "sofa-1" else 900, "depth_mm": 900 if key == "sofa-1" else 600, "height_mm": 800 if key == "sofa-1" else 450},
                "crop_path": str(crop_path),
                "identity_profile": {"family": "sofa" if key == "sofa-1" else "table"},
                "product_identity": {"family": "sofa" if key == "sofa-1" else "table"},
                "archetype_strategy": {"render_strategy": "topology_sensitive_seating" if key == "sofa-1" else "support_geometry_object", "strictness": "critical"},
                "two_pass_strategy": {"pass_role": "pass1_anchor" if key == "sofa-1" else "pass1_footprint"},
                "layout_envelope": {"room_width_ratio": 0.55 if key == "sofa-1" else 0.22, "room_depth_ratio": 0.23 if key == "sofa-1" else 0.15, "room_height_ratio": 0.33 if key == "sofa-1" else 0.19},
                "placement_contract": {"zone": "center"},
                "category_score": 100,
            },
        )

    def _generation(*args, **kwargs):
        generation_payloads.append(list(args[1]))
        return _response()

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "two-pass-cap",
        furniture_specs_json={
            "items": items,
            "primary_scale": {"target_key": "sofa-1", "label": "Primary Sofa"},
            "two_pass_strategy": {
                "pass1_primary_keys": ["sofa-1"],
                "pass1_support_keys": ["table-1", "desk-1"],
                "pass2_detail_keys": pass2_keys,
            },
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "sofa-1", "label": "Primary Sofa"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True, "two_pass_staging_runtime": True},
        start_time=2500.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=_generation,
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    assert result is not None
    generation_reference_lines = [part for part in generation_payloads[0] if isinstance(part, str) and part.startswith("Furniture Cutout Reference")]
    reserve_reference_lines = [part for part in generation_payloads[0] if isinstance(part, str) and part.startswith("Pass2 Detail Reserve Reference")]
    assert any("Primary Sofa" in line for line in generation_reference_lines)
    assert any("Support Table" in line for line in generation_reference_lines)
    assert any("Support Desk" in line for line in generation_reference_lines)
    assert len(reserve_reference_lines) <= 4


def test_strict_b_lite_runtime_does_not_schedule_repair_timeout_after_generation(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    crop_path = tmp_path / "chair.png"
    crop_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    current_time = {"value": 4000.0}
    monkeypatch.setattr(generation_stage.time, "time", lambda: current_time["value"])

    generation_timeouts: list[float] = []
    validate_calls: list[list[str] | None] = []

    def _generation(*args, **kwargs):
        generation_timeouts.append(float(args[2]["timeout"]))
        current_time["value"] = 4060.0
        return _response()

    def _validate(*args, **kwargs):
        validate_calls.append(kwargs.get("focus_item_keys"))
        if len(validate_calls) == 1:
            return (
                False,
                ["reference_shape_drift"],
                {
                    "failed_rules": ["reference_shape_drift"],
                    "matched_items": {"chair-1": {"bbox_norm": [0.1, 0.2, 0.3, 0.5]}},
                    "unmatched_items": [],
                    "issue_records": [
                        {
                            "item_key": "chair-1",
                            "rule_id": "reference_shape_drift",
                            "rule_kind": "reference_shape_drift",
                            "severity": 1.0,
                            "confidence": 0.95,
                        }
                    ],
                    "cheap_first_item_keys": ["chair-1"],
                    "rule_details": {},
                },
            )
        return (
            True,
            [],
            {
                "failed_rules": [],
                "matched_items": {"chair-1": {"bbox_norm": [0.11, 0.21, 0.31, 0.51]}},
                "unmatched_items": [],
                "rule_details": {},
            },
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "strict-timeout-refresh",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Chair",
                    "category": "chair",
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "requested_dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "crop_path": str(crop_path),
                    "identity_profile": {"family": "chair"},
                    "product_identity": {"family": "chair"},
                    "archetype_strategy": {"render_strategy": "support_geometry_object", "strictness": "critical"},
                    "layout_envelope": {"room_width_ratio": 0.125, "room_depth_ratio": 0.125, "room_height_ratio": 0.333},
                    "placement_contract": {"zone": "front-left"},
                }
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Chair"},
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "chair-1", "label": "Chair"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": True},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True},
        start_time=4000.0,
        enable_scale_check=True,
        total_timeout_limit=90,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=_generation,
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result is not None
    assert generation_timeouts == [90.0]
    assert validate_calls == [None]


def test_strict_b_lite_runtime_caps_stage2_generation_requests_without_repair_requests(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    crop_path = tmp_path / "chair.png"
    crop_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    current_time = {"value": 6000.0}
    monkeypatch.setattr(generation_stage.time, "time", lambda: current_time["value"])

    generation_requests: list[dict[str, float | int]] = []
    validate_calls: list[list[str] | None] = []

    def _generation(*args, **kwargs):
        generation_requests.append(dict(args[2]))
        current_time["value"] = 6200.0
        return _response()

    def _validate(*args, **kwargs):
        validate_calls.append(kwargs.get("focus_item_keys"))
        if len(validate_calls) == 1:
            return (
                False,
                ["reference_shape_drift"],
                {
                    "failed_rules": ["reference_shape_drift"],
                    "matched_items": {"chair-1": {"bbox_norm": [0.1, 0.2, 0.3, 0.5]}},
                    "unmatched_items": [],
                    "issue_records": [
                        {
                            "item_key": "chair-1",
                            "rule_id": "reference_shape_drift",
                            "rule_kind": "reference_shape_drift",
                            "severity": 1.0,
                            "confidence": 0.95,
                        }
                    ],
                    "cheap_first_item_keys": ["chair-1"],
                    "rule_details": {},
                },
            )
        return (
            True,
            [],
            {
                "failed_rules": [],
                "matched_items": {"chair-1": {"bbox_norm": [0.11, 0.21, 0.31, 0.51]}},
                "unmatched_items": [],
                "rule_details": {},
            },
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "strict-stage2-cap",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Chair",
                    "category": "chair",
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "requested_dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "crop_path": str(crop_path),
                    "identity_profile": {"family": "chair"},
                    "product_identity": {"family": "chair"},
                    "archetype_strategy": {"render_strategy": "support_geometry_object", "strictness": "critical"},
                    "layout_envelope": {"room_width_ratio": 0.125, "room_depth_ratio": 0.125, "room_height_ratio": 0.333},
                    "placement_contract": {"zone": "front-left"},
                }
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Chair"},
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "chair-1", "label": "Chair"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": True},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True},
        start_time=6000.0,
        enable_scale_check=True,
        total_timeout_limit=500,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=_generation,
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result is not None
    assert generation_requests == [
        {
            "timeout": 150.0,
            "aspect_ratio": "16:9",
            "thinking_level": "high",
            "include_thoughts": False,
            "max_attempts": 1,
        }
    ]
    assert validate_calls == [None]


def test_strict_b_lite_runtime_keeps_failure_when_budget_is_almost_gone_without_repair(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    crop_path = tmp_path / "chair.png"
    crop_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    current_time = {"value": 5000.0}
    monkeypatch.setattr(generation_stage.time, "time", lambda: current_time["value"])

    validate_calls: list[list[str] | None] = []

    def _generation(*args, **kwargs):
        current_time["value"] = 5055.0
        return _response()

    def _validate(*args, **kwargs):
        validate_calls.append(kwargs.get("focus_item_keys"))
        if len(validate_calls) == 1:
            return (
                False,
                ["reference_shape_drift"],
                {
                    "failed_rules": ["reference_shape_drift"],
                    "matched_items": {"chair-1": {"bbox_norm": [0.1, 0.2, 0.3, 0.5]}},
                    "unmatched_items": [],
                    "issue_records": [
                        {
                            "item_key": "chair-1",
                            "rule_id": "reference_shape_drift",
                            "rule_kind": "reference_shape_drift",
                            "severity": 1.0,
                            "confidence": 0.95,
                        }
                    ],
                    "cheap_first_item_keys": ["chair-1"],
                    "rule_details": {},
                },
            )
        current_time["value"] = 5059.5
        return (
            True,
            [],
            {
                "failed_rules": [],
                "matched_items": {"chair-1": {"bbox_norm": [0.11, 0.21, 0.31, 0.51]}},
                "unmatched_items": [],
                "rule_details": {},
            },
        )

    result = generate_furnished_room(
        str(room_path),
        "style",
        "ref.png",
        "strict-near-deadline",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Chair",
                    "category": "chair",
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "requested_dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 800},
                    "crop_path": str(crop_path),
                    "identity_profile": {"family": "chair"},
                    "product_identity": {"family": "chair"},
                    "archetype_strategy": {"render_strategy": "support_geometry_object", "strictness": "critical"},
                    "layout_envelope": {"room_width_ratio": 0.125, "room_depth_ratio": 0.125, "room_height_ratio": 0.333},
                    "placement_contract": {"zone": "front-left"},
                }
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Chair"},
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "chair-1", "label": "Chair"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": True},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True},
        start_time=5000.0,
        enable_scale_check=True,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=_generation,
        generation_model_name="model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=_validate,
    )

    assert result is not None
    assert "repair_applied" not in result
    assert "full_scene_revalidate_skipped_due_to_budget" not in result.get("scalecheck_diagnostics", {})
    assert result["scale_check_failed"] is True
    assert validate_calls == [None]
