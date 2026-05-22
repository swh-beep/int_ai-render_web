import io
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from application.render.furnished_generation_stage import generate_furnished_room
from application.render.postprocess_support import rank_best_variant_flash
from application.render.render_room_workflow import _resolve_style_prompt


def _make_png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _response(width: int = 160, height: int = 90):
    return SimpleNamespace(
        candidates=[SimpleNamespace()],
        parts=[SimpleNamespace(inline_data=SimpleNamespace(data=_make_png_bytes(width, height)))],
    )


def _logger():
    return SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)


def _summary_ref():
    return SimpleNamespace(get=lambda: {"dims_warn": 0, "primary_bbox_miss": 0})


def test_rank_best_variant_flash_includes_product_cutout_references(tmp_path):
    candidate_1 = tmp_path / "candidate_1.png"
    candidate_2 = tmp_path / "candidate_2.png"
    cutout = tmp_path / "chair_ref.png"
    candidate_1.write_bytes(_make_png_bytes(160, 90))
    candidate_2.write_bytes(_make_png_bytes(160, 90))
    cutout.write_bytes(_make_png_bytes(80, 80))

    captured = {}

    def fake_rank_call(model_name, content, request_options, *args, **kwargs):
        captured["content"] = content
        captured["prompt"] = content[0]
        captured["request_options"] = dict(request_options)
        return SimpleNamespace(text='{"best_index": 2, "reason": "Candidate 2 preserves the reference chair."}')

    best_idx = rank_best_variant_flash(
        [str(candidate_1), str(candidate_2)],
        [
            {
                "label": "Reference Chair",
                "category": "chair",
                "target_key": "chair-1",
                "crop_path": str(cutout),
                "dims_mm": {"width_mm": 620, "depth_mm": 700, "height_mm": 820},
            }
        ],
        call_gemini_with_failover=fake_rank_call,
        rank_model_name="ranker",
        safe_json_from_model_text=lambda text: __import__("json").loads(text),
    )

    assert best_idx == 1
    assert "PRODUCT REFERENCE CUTOUTS" in captured["prompt"]
    assert "product identity errors before photographic polish" in captured["prompt"]
    assert captured["request_options"]["timeout"] == 60
    assert captured["request_options"]["max_attempts"] == 3
    assert "Reference Product #1: Reference Chair" in captured["content"]


def test_resolve_style_prompt_matches_lowercase_preset_style_keys():
    style_prompt = _resolve_style_prompt(
        {
            "Scandinavian": {"prompt": "legacy title key"},
            "natural": {"prompt": "lowercase key"},
        },
        "scandinavian",
    )

    assert style_prompt == {"prompt": "legacy title key"}


def test_generate_furnished_room_includes_style_direction_and_inventory_for_complete_items(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 2000.0)

    captured = {}

    def fake_generation(model_name, content, *args, **kwargs):
        captured.setdefault("prompt", content[0])
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the room Scandinavian in tone with pale materials and quiet warmth."},
        str(ref_path),
        "prompt-contract",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Accent Chair",
                    "category": "chair",
                    "qty": 2,
                    "dims_mm": {"width_mm": 700, "depth_mm": 760, "height_mm": 820},
                    "requested_dims_mm": {"width_mm": 700, "depth_mm": 760, "height_mm": 820},
                    "crop_path": str(ref_path),
                    "reference_features": {"material_cues": ["boucle"], "distinctive_parts": ["curved open back"]},
                    "identity_profile": {"family": "chair", "material_cues": ["boucle"], "distinctive_parts": ["curved open back"]},
                    "product_identity": {
                        "family": "chair",
                        "topology_cues": ["curved open back"],
                        "support_geometry": ["four slim legs"],
                        "preserve_rules": ["copy curved open back"],
                    },
                    "archetype_strategy": {
                        "render_strategy": "topology_sensitive_seating",
                        "forbidden_substitutions": ["generic dining chair"],
                    },
                    "placement_contract": {"zone": "adjacent_seating_band"},
                },
                {
                    "target_key": "rug-1",
                    "label": "Rug",
                    "category": "rug",
                    "qty": 1,
                    "dims_mm": {"width_mm": 1800, "depth_mm": 1800, "height_mm": 10},
                    "requested_dims_mm": {"width_mm": 1800, "depth_mm": 1800, "height_mm": 10},
                    "crop_path": str(ref_path),
                    "identity_profile": {"family": "rug"},
                    "product_identity": {"family": "rug"},
                    "placement_contract": {"zone": "centered_rug_zone"},
                },
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Accent Chair"},
        },
        room_dimensions="4000x4000x2400",
        primary_item={"target_key": "chair-1", "label": "Accent Chair"},
        room_dims_parsed={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=2000.0,
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
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        assert "<STYLE DIRECTION>" in prompt
        assert "Keep the room Scandinavian in tone" in prompt
        assert "<ITEM INVENTORY (MUST RENDER ALL ITEMS)>" in prompt
        assert "Distinct items: 2 | Total requested quantity: 3" in prompt
        assert "- Accent Chair: qty=2; category=chair" in prompt
        assert "materials=boucle" in prompt
        assert "topology=curved open back" in prompt
        assert "support=four slim legs" in prompt
        assert "forbid=generic dining chair" in prompt
        assert "- Rug: qty=1; category=rug" in prompt
        assert "Do not duplicate rugs, accent chairs, or tables beyond the listed qty." in prompt
        assert "Preserve real material texture and tactile surface detail" in prompt
        assert "leather grain, fabric weave, wood grain, glass reflections, and metal highlights" in prompt
        assert "Avoid clay-like, waxy, plastic, CGI, overly smooth, or over-airbrushed furniture surfaces." in prompt
        assert "Avoid excessive yellow/orange cast, but preserve realistic sunlight warmth and material color." in prompt
        assert "**NO warm/yellow cast.**" not in prompt
        assert "OPENING LOCK" in prompt
        assert "AXIS ALIGNMENT" in prompt
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_includes_small_item_guardrails_and_external_room_inference(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 3000.0)

    captured = {}

    def fake_generation(model_name, content, *args, **kwargs):
        captured.setdefault("prompt", content[0])
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the scene restrained and architectural."},
        str(ref_path),
        "prompt-contract-small-items",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "sofa-1",
                    "label": "Lounge Sofa",
                    "category": "sofa",
                    "qty": 1,
                    "dims_mm": {"width_mm": 2400, "depth_mm": 980, "height_mm": 780},
                    "requested_dims_mm": {"width_mm": 2400, "depth_mm": 980, "height_mm": 780},
                    "crop_path": str(ref_path),
                    "identity_profile": {"family": "sofa", "absolute_size_class": "large", "room_presence_class": "anchor-room-presence"},
                    "product_identity": {"family": "sofa"},
                    "placement_contract": {"zone": "back_wall_anchor_band"},
                    "layout_envelope": {"room_width_ratio": 0.48, "room_depth_ratio": 0.196, "room_height_ratio": 0.289},
                },
                {
                    "target_key": "lamp-1",
                    "label": "Mini Table Lamp",
                    "category": "table_lamp",
                    "qty": 1,
                    "dims_mm": {"width_mm": 110, "depth_mm": 110, "height_mm": 130},
                    "requested_dims_mm": {"width_mm": 110, "depth_mm": 110, "height_mm": 130},
                    "crop_path": str(ref_path),
                    "identity_profile": {"family": "table_lamp", "absolute_size_class": "tiny", "room_presence_class": "tiny-room-presence"},
                    "product_identity": {"family": "table_lamp"},
                    "placement_contract": {"zone": "surface_top_band"},
                    "layout_envelope": {"room_width_ratio": 0.022, "room_depth_ratio": 0.022, "room_height_ratio": 0.048},
                },
                {
                    "target_key": "rug-1",
                    "label": "Round Rug",
                    "category": "rug",
                    "qty": 1,
                    "dims_mm": {"width_mm": 1200, "depth_mm": 1200, "height_mm": 10},
                    "requested_dims_mm": {"width_mm": 1200, "depth_mm": 1200, "height_mm": 10},
                    "crop_path": str(ref_path),
                    "identity_profile": {"family": "rug", "absolute_size_class": "small", "room_presence_class": "small-room-presence"},
                    "product_identity": {"family": "rug"},
                    "placement_contract": {"zone": "under_anchor_band"},
                    "layout_envelope": {"room_width_ratio": 0.24, "room_depth_ratio": 0.24, "room_height_ratio": 0.004},
                },
            ],
            "primary_scale": {"target_key": "sofa-1", "label": "Lounge Sofa"},
        },
        primary_item={"target_key": "sofa-1", "label": "Lounge Sofa"},
        room_dims_parsed={"width_mm": 5000, "depth_mm": 5000, "height_mm": 2700},
        room_analysis_text="Rectilinear room with straight window mullions and a fixed camera view.",
        placement_plan={
            "placement_zones": {
                "sofa-1": {
                    "placement_family": "floor_placed",
                    "zone": "back_wall_anchor_band",
                    "room_ratio_targets": {"room_width_ratio": 0.48, "room_height_ratio": 0.289, "footprint_ratio": 0.094},
                    "anchor_relationship": {"width_ratio": 1.0, "height_ratio": 1.0, "footprint_ratio": 1.0},
                    "orientation_hint": "Keep the back parallel to the dominant wall/window line.",
                }
            }
        },
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=3000.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: True,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2700},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        assert "<SMALL ITEM SCALE GUARDRAILS>" in prompt
        assert "Mini Table Lamp" in prompt
        assert "surface-scale object" in prompt
        assert "Round Rug" in prompt
        assert "not wall-to-wall" in prompt
        assert "<ROOM-SCALE INFERENCE RULES>" in prompt
        assert "<PLACEMENT PLAN (BINDING)>" not in prompt
        assert "placement_zones" not in prompt
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_includes_strict_estimated_scale_contract_context(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 3100.0)
    captured = {}

    def fake_generation(model_name, content, *args, **kwargs):
        captured.setdefault("prompt", content[0])
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the scene restrained and architectural."},
        str(ref_path),
        "prompt-contract-strict-estimated-scale",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Dining Chair",
                    "category": "chair",
                    "qty": 1,
                    "dims_mm": {"width_mm": 470, "depth_mm": 500, "height_mm": 810},
                    "requested_dims_mm": {"width_mm": 470, "depth_mm": 500, "height_mm": 810},
                    "crop_path": str(ref_path),
                    "identity_profile": {"family": "chair", "room_presence_class": "medium-room-presence"},
                    "product_identity": {"family": "chair"},
                    "placement_contract": {"zone": "around_table"},
                    "layout_envelope": {"room_width_ratio": 0.094, "room_depth_ratio": 0.125, "room_height_ratio": 0.3},
                }
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Dining Chair"},
        },
        room_dimensions="W 5000mm x D 4000mm x H 2700mm",
        primary_item={"target_key": "chair-1", "label": "Dining Chair"},
        room_dims_parsed={"width_mm": 5000, "depth_mm": 4000, "height_mm": 2700},
        room_analysis_text="ANALYSIS-DERIVED ROOM DIMENSIONS: W 5000mm, D 4000mm, H 2700mm.",
        placement_plan={
            "anchor_item_key": "chair-1",
            "placement_zones": {
                "chair-1": {
                    "placement_family": "floor_placed",
                    "zone": "around_table",
                    "room_ratio_targets": {"room_width_ratio": 0.094, "room_depth_ratio": 0.125, "room_height_ratio": 0.3},
                }
            },
        },
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={
            "strict_scale_requested": True,
            "strict_scale_ready": True,
            "room_dims": {"width_mm": 5000, "depth_mm": 4000, "height_mm": 2700},
            "room_dims_source": "estimated",
            "room_dims_confidence": "high",
            "anchor_item": {"target_key": "chair-1", "label": "Dining Chair"},
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Dining Chair",
                    "dims_mm": {"width_mm": 470, "depth_mm": 500, "height_mm": 810},
                    "room_width_ratio": 0.094,
                    "room_depth_ratio": 0.125,
                    "room_height_ratio": 0.3,
                    "placement_family": "floor_placed",
                }
            ],
        },
        geometry_contract={
            "strict_scale_requested": True,
            "strict_scale_ready": True,
            "geometry_source": "estimated",
            "geometry_confidence": "high",
            "strict_scale_mode": "strict_geometry_mode",
            "anchor_item_key": "chair-1",
            "item_targets": [
                {
                    "target_key": "chair-1",
                    "label": "Dining Chair",
                    "room_width_ratio": 0.094,
                    "room_depth_ratio": 0.125,
                    "room_height_ratio": 0.3,
                    "placement_family": "floor_placed",
                    "zone": "around_table",
                }
            ],
        },
        start_time=3100.0,
        enable_scale_check=True,
        total_timeout_limit=60,
        detect_windows_present=lambda path: True,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 5000, "depth_mm": 4000, "height_mm": 2700},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {"chair-1": {}}, "unmatched_items": []}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        assert "<SCALE PLAN (BINDING)>" in prompt
        assert "strict_scale_requested=True" in prompt
        assert "room_dims_source=estimated confidence=high" in prompt
        assert "<GEOMETRY CONTRACT (BINDING)>" in prompt
        assert "strict_scale_mode=strict_geometry_mode" in prompt
        assert "<PLACEMENT PLAN (BINDING)>" in prompt
        assert "Dining Chair" in prompt
        assert "room_width_ratio=0.094" in prompt
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_uses_compact_identity_cards_not_long_item_prose(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 4000.0)

    captured = {}
    long_item_prose = (
        "This lounge chair has a dramatic sculptural presence with an expressive silhouette, generous padding, "
        "luxury upholstery, a visually light frame, and a premium editorial attitude that should feel iconic, "
        "refined, collectible, and sophisticated from every viewing angle in the staged room."
    )

    def fake_generation(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        captured["content"] = content
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the scene calm and highly architectural."},
        str(ref_path),
        "prompt-contract-identity-cards",
        furniture_specs=long_item_prose,
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Collector Lounge Chair",
                    "category": "lounge_chair",
                    "category_canonical": "lounge_chair",
                    "qty": 1,
                    "dims_mm": {"width_mm": 820, "depth_mm": 880, "height_mm": 760},
                    "requested_dims_mm": {"width_mm": 820, "depth_mm": 880, "height_mm": 760},
                    "description": long_item_prose,
                    "crop_path": str(ref_path),
                    "identity_profile": {
                        "family": "lounge_chair",
                        "silhouette_summary": "low rounded lounge chair with exposed tubular frame",
                        "material_cues": ["camel leather", "polished steel"],
                        "distinctive_parts": ["exposed tubular steel frame", "deep wraparound seat"],
                        "preserve_rules": ["retain low sling profile", "keep steel frame visible"],
                    },
                    "product_identity": {
                        "family": "lounge_chair",
                        "support_geometry": ["tubular steel sled frame"],
                        "preserve_rules": ["retain low sling profile", "keep steel frame visible"],
                    },
                    "placement_contract": {"zone": "adjacent_seating_band"},
                    "archetype_strategy": {
                        "forbidden_substitutions": ["generic club chair", "boxy accent chair"],
                    },
                }
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Collector Lounge Chair"},
        },
        room_dimensions="4200x3600x2500",
        primary_item={"target_key": "chair-1", "label": "Collector Lounge Chair"},
        room_dims_parsed={"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=4000.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        content = captured["content"]
        assert "<ITEM EXACTNESS CARDS>" in prompt
        assert "<ITEM IDENTITY LOCKS (STRICT)>" not in prompt
        assert "Collector Lounge Chair: reference_image=authoritative_cutout; qty=1; category=lounge_chair" in prompt
        assert "same_family_substitute=invalid" in prompt
        assert "avoid=generic club chair, boxy accent chair" not in prompt
        assert "preserve_rules=" not in prompt
        assert "PRODUCT EXACTNESS FIRST" in prompt
        assert long_item_prose not in prompt
        assert "PRIMARY EXACTNESS ANCHOR" in content[3]
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_disambiguates_duplicate_product_labels(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    red_art_ref = tmp_path / "red_art.png"
    red_art_ref.write_bytes(_make_png_bytes(80, 80))
    gray_art_ref = tmp_path / "gray_art.png"
    gray_art_ref.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 4400.0)

    captured = {}

    def fake_generation(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        captured["content"] = content
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the dining room calm and precise."},
        str(red_art_ref),
        "prompt-contract-duplicate-labels",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "cart_product-39067_red-art_004",
                    "item_id": "product_39067",
                    "source_index": 4,
                    "label": "AI 디자인용 이미지입니다",
                    "category": "decor",
                    "qty": 1,
                    "dims_mm": {"width_mm": 900, "depth_mm": 50, "height_mm": 1200},
                    "requested_dims_mm": {"width_mm": 900, "depth_mm": 50, "height_mm": 1200},
                    "crop_path": str(red_art_ref),
                    "identity_profile": {"family": "decor"},
                    "product_identity": {"family": "decor"},
                },
                {
                    "target_key": "cart_product-39065_gray-art_005",
                    "item_id": "product_39065",
                    "source_index": 5,
                    "label": "AI 디자인용 이미지입니다",
                    "category": "decor",
                    "qty": 1,
                    "dims_mm": {"width_mm": 400, "depth_mm": 50, "height_mm": 600},
                    "requested_dims_mm": {"width_mm": 400, "depth_mm": 50, "height_mm": 600},
                    "crop_path": str(gray_art_ref),
                    "identity_profile": {"family": "decor"},
                    "product_identity": {"family": "decor"},
                },
            ],
            "primary_scale": {"target_key": "cart_product-39067_red-art_004", "label": "AI 디자인용 이미지입니다"},
        },
        room_dimensions="5000x4000x2600",
        primary_item={"target_key": "cart_product-39067_red-art_004", "label": "AI 디자인용 이미지입니다"},
        room_dims_parsed={"width_mm": 5000, "depth_mm": 4000, "height_mm": 2600},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=4400.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 5000, "depth_mm": 4000, "height_mm": 2600},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        content = captured["content"]
        assert "item_id=product_39067" in prompt
        assert "item_id=product_39065" in prompt
        assert "source_index=4" in prompt
        assert "source_index=5" in prompt
        headers = [part for part in content if isinstance(part, str) and "Furniture Cutout Reference" in part]
        assert any("ItemID=product_39067" in header and "SourceIndex=4" in header for header in headers)
        assert any("ItemID=product_39065" in header and "SourceIndex=5" in header for header in headers)
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_splits_primary_locks_from_secondary_items_and_orders_cutout_headers(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    sofa_ref = tmp_path / "sofa.png"
    sofa_ref.write_bytes(_make_png_bytes(80, 80))
    cabinet_ref = tmp_path / "cabinet.png"
    cabinet_ref.write_bytes(_make_png_bytes(80, 80))
    rug_ref = tmp_path / "rug.png"
    rug_ref.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 4500.0)

    captured = {}

    def fake_generation(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        captured["content"] = content
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the room restrained and architectural."},
        str(sofa_ref),
        "prompt-contract-primary-locks",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "sofa-1",
                    "label": "Hero Sofa",
                    "category": "main_sofa",
                    "category_canonical": "main_sofa",
                    "qty": 1,
                    "dims_mm": {"width_mm": 2600, "depth_mm": 1000, "height_mm": 760},
                    "requested_dims_mm": {"width_mm": 2600, "depth_mm": 1000, "height_mm": 760},
                    "crop_path": str(sofa_ref),
                    "identity_profile": {
                        "family": "main_sofa",
                        "silhouette_summary": "low deep sofa with broad seat modules",
                        "distinctive_parts": ["broad seat modules"],
                        "preserve_rules": ["keep low continuous seat"],
                    },
                    "product_identity": {
                        "family": "main_sofa",
                        "support_geometry": ["deep low sofa plinth"],
                        "preserve_rules": ["keep low continuous seat"],
                    },
                    "placement_contract": {"zone": "back_wall_anchor_band"},
                },
                {
                    "target_key": "cabinet-1",
                    "label": "Tall Cabinet",
                    "category": "storage_cabinet_shelf",
                    "category_canonical": "storage_cabinet_shelf",
                    "qty": 1,
                    "dims_mm": {"width_mm": 1800, "depth_mm": 450, "height_mm": 980},
                    "requested_dims_mm": {"width_mm": 1800, "depth_mm": 450, "height_mm": 980},
                    "crop_path": str(cabinet_ref),
                    "identity_profile": {
                        "family": "storage_cabinet_shelf",
                        "room_presence_class": "large-room-presence",
                        "silhouette_summary": "long low cabinet with four doors",
                        "distinctive_parts": ["four equal door fronts"],
                        "preserve_rules": ["keep long low cabinet proportion"],
                    },
                    "product_identity": {
                        "family": "storage_cabinet_shelf",
                        "support_geometry": ["box cabinet on plinth"],
                        "preserve_rules": ["keep long low cabinet proportion"],
                    },
                    "placement_contract": {"zone": "back_wall_support_band"},
                },
                {
                    "target_key": "rug-1",
                    "label": "Round Rug",
                    "category": "rug",
                    "category_canonical": "rug",
                    "qty": 1,
                    "dims_mm": {"width_mm": 1800, "depth_mm": 1800, "height_mm": 10},
                    "requested_dims_mm": {"width_mm": 1800, "depth_mm": 1800, "height_mm": 10},
                    "crop_path": str(rug_ref),
                    "identity_profile": {
                        "family": "rug",
                        "silhouette_summary": "round rug",
                        "preserve_rules": ["keep circular footprint"],
                    },
                    "product_identity": {
                        "family": "rug",
                        "preserve_rules": ["keep circular footprint"],
                    },
                    "placement_contract": {"zone": "centered_rug_zone"},
                },
            ],
            "primary_scale": {"target_key": "sofa-1", "label": "Hero Sofa"},
        },
        room_dimensions="5200x4200x2600",
        primary_item={"target_key": "sofa-1", "label": "Hero Sofa"},
        room_dims_parsed={"width_mm": 5200, "depth_mm": 4200, "height_mm": 2600},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=4500.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 5200, "depth_mm": 4200, "height_mm": 2600},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        content = captured["content"]
        assert "<PRIMARY PRODUCT LOCKS>" in prompt
        assert "<SECONDARY SUPPORTING ITEMS>" in prompt
        assert "PRIMARY LOCK ORDER" in prompt
        assert "Hero Sofa" in prompt
        assert "Tall Cabinet" in prompt
        assert "Round Rug" in prompt
        assert prompt.index("<PRIMARY PRODUCT LOCKS>") < prompt.index("- Hero Sofa:")
        assert prompt.index("- Tall Cabinet:") < prompt.index("<SECONDARY SUPPORTING ITEMS>")
        assert prompt.index("<SECONDARY SUPPORTING ITEMS>") < prompt.index("- Round Rug:")
        assert "Hero Sofa, Tall Cabinet" in prompt
        headers = [part for part in content if isinstance(part, str) and "Furniture Cutout Reference" in part]
        assert "PRIMARY PRODUCT LOCK" in headers[0]
        assert "PRIMARY PRODUCT LOCK" in headers[1]
        assert "SECONDARY SUPPORT ITEM" in headers[2]
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_excludes_support_and_pass2_items_from_primary_locks(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    sofa_ref = tmp_path / "sofa.png"
    sofa_ref.write_bytes(_make_png_bytes(80, 80))
    table_ref = tmp_path / "table.png"
    table_ref.write_bytes(_make_png_bytes(80, 80))
    chair_ref = tmp_path / "chair.png"
    chair_ref.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 4600.0)

    captured = {}

    def fake_generation(model_name, content, *args, **kwargs):
        captured.setdefault("prompt", content[0])
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the room restrained and architectural."},
        str(sofa_ref),
        "prompt-contract-primary-locks-two-pass",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "sofa-1",
                    "label": "Hero Sofa",
                    "category": "sofa",
                    "qty": 1,
                    "dims_mm": {"width_mm": 2400, "depth_mm": 1000, "height_mm": 760},
                    "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1000, "height_mm": 760},
                    "crop_path": str(sofa_ref),
                    "identity_profile": {"family": "sofa", "room_presence_class": "anchor-room-presence"},
                    "product_identity": {"family": "sofa"},
                },
                {
                    "target_key": "table-1",
                    "label": "Support Table",
                    "category": "table",
                    "qty": 1,
                    "dims_mm": {"width_mm": 900, "depth_mm": 900, "height_mm": 420},
                    "requested_dims_mm": {"width_mm": 900, "depth_mm": 900, "height_mm": 420},
                    "crop_path": str(table_ref),
                    "identity_profile": {"family": "table", "room_presence_class": "medium-room-presence"},
                    "product_identity": {"family": "table"},
                },
                {
                    "target_key": "chair-1",
                    "label": "Pass2 Chair",
                    "category": "chair",
                    "qty": 1,
                    "dims_mm": {"width_mm": 640, "depth_mm": 680, "height_mm": 760},
                    "requested_dims_mm": {"width_mm": 640, "depth_mm": 680, "height_mm": 760},
                    "crop_path": str(chair_ref),
                    "identity_profile": {"family": "chair", "room_presence_class": "medium-room-presence"},
                    "product_identity": {"family": "chair"},
                },
            ],
            "primary_scale": {"target_key": "sofa-1", "label": "Hero Sofa"},
            "two_pass_strategy": {
                "pass1_primary_keys": ["sofa-1"],
                "pass1_support_keys": ["table-1"],
                "pass2_detail_keys": ["chair-1"],
            },
        },
        room_dimensions="5200x4200x2600",
        primary_item={"target_key": "sofa-1", "label": "Hero Sofa"},
        room_dims_parsed={"width_mm": 5200, "depth_mm": 4200, "height_mm": 2600},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=4600.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 5200, "depth_mm": 4200, "height_mm": 2600},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        primary_section = prompt.split("<PRIMARY PRODUCT LOCKS>", 1)[1].split("<SECONDARY SUPPORTING ITEMS>", 1)[0]
        assert "Hero Sofa" in primary_section
        assert "Support Table" not in primary_section
        assert "Pass2 Chair" not in primary_section
        assert "PRIMARY LOCK ORDER" in prompt
        assert "Hero Sofa" in prompt
        assert "Support Table" not in prompt.split("PRIMARY LOCK ORDER:", 1)[1].split("\\n", 1)[0]
        assert "Pass2 Chair" not in prompt.split("PRIMARY LOCK ORDER:", 1)[1].split("\\n", 1)[0]
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_falls_back_to_text_guidance_and_ref_images_when_json_missing(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 5000.0)

    captured = {}
    fallback_text = "1. Accent Chair - preserve brushed steel frame and low sling seat."

    def fake_generation(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        captured["content"] = content
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the room restrained."},
        str(ref_path),
        "prompt-contract-fallback-guidance",
        furniture_specs=fallback_text,
        furniture_specs_json=None,
        room_dimensions="4200x3600x2500",
        room_dims_parsed={"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=5000.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        content = captured["content"]
        assert "<FALLBACK ITEM GUIDANCE>" in prompt
        assert fallback_text in prompt
        assert "Fallback Furniture Reference Image 1" in content[3]
        assert len(content) == 5
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_falls_back_to_ref_images_when_json_items_are_unusable(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 5500.0)

    captured = {}
    fallback_text = "1. Mirror - preserve lean angle and thin black frame."

    def fake_generation(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        captured["content"] = content
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the room restrained."},
        str(ref_path),
        "prompt-contract-fallback-guidance-broken-json",
        furniture_specs=fallback_text,
        furniture_specs_json={"items": [{"label": "Mirror", "crop_path": str(tmp_path / "missing.png")}]},
        room_dimensions="4200x3600x2500",
        room_dims_parsed={"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=5500.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        content = captured["content"]
        assert "<ITEM EXACTNESS CARDS>" in prompt
        assert "Mirror: qty=1" in prompt
        assert "Fallback Furniture Reference Image 1" in content[3]
        assert len(content) == 5
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_keeps_reflection_and_opening_cues_in_compact_cards(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 6000.0)

    captured = {}

    def fake_generation(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the room minimal."},
        str(ref_path),
        "prompt-contract-mirror-cues",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "mirror-1",
                    "label": "Lean Floor Mirror",
                    "category": "mirror",
                    "category_canonical": "mirror",
                    "qty": 1,
                    "dims_mm": {"width_mm": 500, "depth_mm": 40, "height_mm": 1800},
                    "requested_dims_mm": {"width_mm": 500, "depth_mm": 40, "height_mm": 1800},
                    "crop_path": str(ref_path),
                    "identity_profile": {
                        "family": "mirror",
                        "silhouette_summary": "tall rounded-rectangle lean mirror",
                        "material_cues": ["black metal"],
                        "distinctive_parts": ["thin black perimeter frame"],
                        "preserve_rules": ["keep lean-against-wall posture"],
                    },
                    "product_identity": {
                        "family": "mirror",
                        "support_geometry": ["leaning floor mirror"],
                        "opening_or_gap_features": ["narrow reveal between frame and mirror edge"],
                        "pattern_cues": ["plain uninterrupted reflective field"],
                        "reflection_constraints": ["reflect opposite wall only"],
                        "preserve_rules": ["keep lean-against-wall posture"],
                    },
                    "placement_contract": {"zone": "wall_edge_band"},
                }
            ],
            "primary_scale": {"target_key": "mirror-1", "label": "Lean Floor Mirror"},
        },
        room_dimensions="4200x3600x2500",
        primary_item={"target_key": "mirror-1", "label": "Lean Floor Mirror"},
        room_dims_parsed={"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=6000.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        assert "Lean Floor Mirror: reference_image=authoritative_cutout" in prompt
        assert "category=mirror" in prompt
        assert "category_rules=wall_attached_or_leaning_as_reference, preserve_reflective_face" in prompt
        assert "preserve_rules=keep lean-against-wall posture" not in prompt
        assert "same_family_substitute=invalid" in prompt
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_keeps_tiny_surface_items_out_of_primary_locks(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    sofa_ref = tmp_path / "sofa.png"
    sofa_ref.write_bytes(_make_png_bytes(80, 80))
    lamp_ref = tmp_path / "lamp.png"
    lamp_ref.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 6200.0)

    captured = {}

    def fake_generation(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the room minimal."},
        str(sofa_ref),
        "prompt-contract-tiny-secondary",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "sofa-1",
                    "label": "Lounge Sofa",
                    "category": "sofa",
                    "qty": 1,
                    "dims_mm": {"width_mm": 2400, "depth_mm": 980, "height_mm": 780},
                    "requested_dims_mm": {"width_mm": 2400, "depth_mm": 980, "height_mm": 780},
                    "crop_path": str(sofa_ref),
                    "identity_profile": {"family": "sofa", "room_presence_class": "anchor-room-presence"},
                    "product_identity": {"family": "sofa"},
                },
                {
                    "target_key": "lamp-1",
                    "label": "Mini Table Lamp",
                    "category": "table_lamp",
                    "qty": 1,
                    "dims_mm": {"width_mm": 110, "depth_mm": 110, "height_mm": 130},
                    "requested_dims_mm": {"width_mm": 110, "depth_mm": 110, "height_mm": 130},
                    "crop_path": str(lamp_ref),
                    "identity_profile": {
                        "family": "table_lamp",
                        "room_presence_class": "tiny-room-presence",
                        "absolute_size_class": "tiny",
                    },
                    "product_identity": {"family": "table_lamp"},
                },
            ],
            "primary_scale": {"target_key": "sofa-1", "label": "Lounge Sofa"},
        },
        room_dimensions="5000x5000x2700",
        primary_item={"target_key": "sofa-1", "label": "Lounge Sofa"},
        room_dims_parsed={"width_mm": 5000, "depth_mm": 5000, "height_mm": 2700},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=6200.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2700},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        primary_section = prompt.split("<PRIMARY PRODUCT LOCKS>", 1)[1].split("<SECONDARY SUPPORTING ITEMS>", 1)[0]
        assert "Lounge Sofa" in primary_section
        assert "Mini Table Lamp" not in primary_section
        assert "Lounge Sofa, Mini Table Lamp" not in prompt
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()


def test_generate_furnished_room_keeps_topology_cues_in_compact_cards(tmp_path, monkeypatch):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(_make_png_bytes(160, 90))
    ref_path = tmp_path / "ref.png"
    ref_path.write_bytes(_make_png_bytes(80, 80))

    import application.render.furnished_generation_stage as generation_stage

    monkeypatch.setattr(generation_stage.time, "time", lambda: 6500.0)

    captured = {}

    def fake_generation(model_name, content, *args, **kwargs):
        captured["prompt"] = content[0]
        return _response()

    result = generate_furnished_room(
        str(room_path),
        {"prompt": "Keep the room minimal."},
        str(ref_path),
        "prompt-contract-topology-cues",
        furniture_specs_json={
            "items": [
                {
                    "target_key": "chair-1",
                    "label": "Crest Rail Chair",
                    "category": "chair",
                    "category_canonical": "chair",
                    "qty": 1,
                    "dims_mm": {"width_mm": 480, "depth_mm": 520, "height_mm": 880},
                    "requested_dims_mm": {"width_mm": 480, "depth_mm": 520, "height_mm": 880},
                    "crop_path": str(ref_path),
                    "identity_profile": {
                        "family": "chair",
                        "silhouette_summary": "slender dining chair",
                    },
                    "product_identity": {
                        "family": "chair",
                        "topology_cues": ["rolled back crest rail"],
                    },
                    "placement_contract": {"zone": "table_edge_band"},
                }
            ],
            "primary_scale": {"target_key": "chair-1", "label": "Crest Rail Chair"},
        },
        room_dimensions="4200x3600x2500",
        primary_item={"target_key": "chair-1", "label": "Crest Rail Chair"},
        room_dims_parsed={"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        scale_plan={"strict_scale_requested": False},
        geometry_contract=None,
        start_time=6500.0,
        enable_scale_check=False,
        total_timeout_limit=60,
        detect_windows_present=lambda path: False,
        logger=_logger(),
        parse_room_dimensions_mm=lambda text: {"width_mm": 4200, "depth_mm": 3600, "height_mm": 2500},
        normalize_dims_dict=lambda dims: dims,
        is_two_dim_ok_label=lambda label: False,
        available_dim_axes=lambda dims: {"width_mm", "depth_mm", "height_mm"},
        summary_ref=_summary_ref(),
        log_brief=False,
        log_summary=False,
        allow_all_safety_settings=lambda: {},
        call_generation_with_failover=fake_generation,
        generation_model_name="model",
        call_repair_with_failover=lambda *args, **kwargs: _response(),
        repair_model_name="repair-model",
        match_aspect_to_target=lambda path, room: path,
        validate_furnished_scale=lambda *args, **kwargs: (True, [], {"failed_rules": [], "matched_items": {}, "unmatched_items": [], "rule_details": {}}),
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        assert "Crest Rail Chair: reference_image=authoritative_cutout" in prompt
        assert "same_family_substitute=invalid" in prompt
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()
