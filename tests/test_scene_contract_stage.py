from application.render.render_contracts import build_explicit_room_dims_contract
from application.render.scene_contract_stage import build_scene_contract


def test_build_scene_contract_tracks_critical_items_and_pairwise_ratios():
    room_dims_contract = build_explicit_room_dims_contract(
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        strict_scale_mode="strict_geometry_mode",
    )
    sofa = {
        "target_key": "sofa-1",
        "category": "sofa",
        "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800},
        "identity_profile": {"family": "sofa", "layout_envelope": {"placement_family": "floor_placed"}},
    }
    rug = {
        "target_key": "rug-1",
        "category": "rug",
        "requested_dims_mm": {"width_mm": 1100, "depth_mm": 1100, "height_mm": 12},
        "identity_profile": {"family": "rug", "layout_envelope": {"placement_family": "rug"}},
    }
    mirror = {
        "target_key": "mirror-1",
        "category": "mirror",
        "requested_dims_mm": {"width_mm": 400, "depth_mm": 10, "height_mm": 800},
        "identity_profile": {"family": "mirror", "layout_envelope": {"placement_family": "wall_attached"}},
    }

    contract = build_scene_contract(
        room="livingroom",
        audience="internal",
        room_dims_contract=room_dims_contract,
        room_analysis_text="bright living room",
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        wall_span_norm=(0.15, 0.85),
        windows_present=True,
        analyzed_items=[sofa, rug, mirror],
        primary_item=sofa,
    )

    data = contract.as_dict()
    assert data["geometry_source"] == "explicit"
    assert "sofa-1" in data["critical_item_keys"]
    assert "rug-1" in data["critical_item_keys"]
    assert "mirror-1" in data["critical_item_keys"]
    assert data["placement_zones"]["wall_attached"] == ["mirror-1"]
    assert data["placement_zones"]["rug"] == ["rug-1"]
    assert data["pairwise_ratio_contracts"][0]["anchor_key"] == "sofa-1"
