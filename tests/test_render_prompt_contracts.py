import io
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from application.render.furnished_generation_stage import generate_furnished_room
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
        captured["prompt"] = content[0]
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
                    "identity_profile": {"family": "chair"},
                    "product_identity": {"family": "chair"},
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
        assert "- Accent Chair: qty=2; family=chair; zone=adjacent_seating_band" in prompt
        assert "- Rug: qty=1; family=rug; zone=centered_rug_zone" in prompt
        assert "Do not duplicate rugs, accent chairs, or tables beyond the listed qty." in prompt
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
        captured["prompt"] = content[0]
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
        assert "keep sofas, storage, rugs, desks, and main tables axis-aligned" in prompt
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
        assert "<ITEM IDENTITY LOCKS (STRICT)>" in prompt
        assert "Collector Lounge Chair: qty=1; family=lounge_chair" in prompt
        assert "distinctive=exposed tubular steel frame, deep wraparound seat" in prompt
        assert "support=tubular steel sled frame" in prompt
        assert "avoid=generic club chair, boxy accent chair" in prompt
        assert "PRODUCT EXACTNESS FIRST" in prompt
        assert long_item_prose not in prompt
        assert "PRIMARY EXACTNESS ANCHOR" in content[3]
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
        assert "support=leaning floor mirror" in prompt
        assert "gaps=narrow reveal between frame and mirror edge" in prompt
        assert "pattern=plain uninterrupted reflective field" in prompt
        assert "reflection=reflect opposite wall only" in prompt
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
        assert "topology=rolled back crest rail" in prompt
        assert output_path.exists()
    finally:
        if output_path.exists():
            output_path.unlink()
