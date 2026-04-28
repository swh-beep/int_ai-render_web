from application.render.geometry_contract_stage import build_geometry_contract
from application.render.placement_plan_stage import build_placement_plan
from application.render.render_contracts import build_explicit_room_dims_contract
from application.render.scene_contract_stage import build_scene_contract


def _base_items():
    sofa = {
        "target_key": "sofa-1",
        "label": "Main Sofa",
        "category": "sofa",
        "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800},
        "identity_profile": {
            "family": "sofa",
            "layout_envelope": {
                "placement_family": "floor_placed",
                "room_width_ratio": 0.6,
                "room_depth_ratio": 0.275,
                "room_height_ratio": 0.3333,
                "footprint_ratio": 0.165,
            },
        },
        "layout_envelope": {
            "placement_family": "floor_placed",
            "room_width_ratio": 0.6,
            "room_depth_ratio": 0.275,
            "room_height_ratio": 0.3333,
            "footprint_ratio": 0.165,
        },
        "product_identity": {"family": "sofa", "dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800}},
    }
    rug = {
        "target_key": "rug-1",
        "label": "Round Rug",
        "category": "rug",
        "requested_dims_mm": {"width_mm": 1100, "depth_mm": 1100, "height_mm": 12},
        "identity_profile": {
            "family": "rug",
            "layout_envelope": {
                "placement_family": "rug",
                "room_width_ratio": 0.275,
                "room_depth_ratio": 0.275,
                "room_height_ratio": 0.005,
                "footprint_ratio": 0.0756,
            },
        },
        "layout_envelope": {
            "placement_family": "rug",
            "room_width_ratio": 0.275,
            "room_depth_ratio": 0.275,
            "room_height_ratio": 0.005,
            "footprint_ratio": 0.0756,
        },
        "product_identity": {"family": "rug", "dims_mm": {"width_mm": 1100, "depth_mm": 1100, "height_mm": 12}},
    }
    return sofa, rug


def test_build_geometry_contract_is_strict_ready_for_complete_internal_explicit_dims():
    sofa, rug = _base_items()
    room_dims_contract = build_explicit_room_dims_contract(
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        strict_scale_mode="strict_geometry_mode",
    )
    scene_contract = build_scene_contract(
        room="livingroom",
        audience="internal",
        room_dims_contract=room_dims_contract,
        room_analysis_text="bright room",
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        wall_span_norm=(0.15, 0.85),
        windows_present=True,
        analyzed_items=[sofa, rug],
        primary_item=sofa,
    )
    placement_plan, analyzed = build_placement_plan(
        analyzed_items=[sofa, rug],
        primary_item=sofa,
        scene_contract=scene_contract,
        placement_instructions="Keep centered.",
    )

    contract = build_geometry_contract(
        room_dims_contract=room_dims_contract,
        scene_contract=scene_contract,
        placement_plan=placement_plan,
        analyzed_items=analyzed,
        primary_item=sofa,
        strict_scale_requested=True,
    )

    data = contract.as_dict()
    assert data["strict_scale_ready"] is True
    assert data["anchor_item_key"] == "sofa-1"
    assert data["geometry_source"] == "explicit"
    rug_target = next(row for row in data["item_targets"] if row["target_key"] == "rug-1")
    assert rug_target["anchor_width_ratio"] is not None
    assert rug_target["zone"] == "under_anchor_band"


def test_build_geometry_contract_marks_missing_item_dims_as_not_ready():
    sofa, rug = _base_items()
    rug["requested_dims_mm"] = {"width_mm": 1100, "depth_mm": None, "height_mm": 12}
    room_dims_contract = build_explicit_room_dims_contract(
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        strict_scale_mode="strict_geometry_mode",
    )
    scene_contract = build_scene_contract(
        room="livingroom",
        audience="internal",
        room_dims_contract=room_dims_contract,
        room_analysis_text="bright room",
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        wall_span_norm=(0.15, 0.85),
        windows_present=True,
        analyzed_items=[sofa, rug],
        primary_item=sofa,
    )
    placement_plan, analyzed = build_placement_plan(
        analyzed_items=[sofa, rug],
        primary_item=sofa,
        scene_contract=scene_contract,
    )

    contract = build_geometry_contract(
        room_dims_contract=room_dims_contract,
        scene_contract=scene_contract,
        placement_plan=placement_plan,
        analyzed_items=analyzed,
        primary_item=sofa,
        strict_scale_requested=True,
    )

    assert contract.strict_scale_ready is False
    assert "item_dims_incomplete:rug-1" in contract.missing_requirements
