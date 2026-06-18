from application.render.placement_plan_stage import build_placement_plan
from application.render.render_contracts import RoomDimsContract, SceneContract
from application.render.two_pass_strategy_stage import apply_two_pass_strategy


def test_build_placement_plan_assigns_anchor_zones_and_small_item_clamps():
    analyzed_items = [
        {
            "target_key": "sofa-1",
            "label": "Main Sofa",
            "category": "sofa",
            "layout_envelope": {
                "room_width_ratio": 0.62,
                "room_depth_ratio": 0.24,
                "room_height_ratio": 0.3,
                "footprint_ratio": 0.15,
            },
            "product_identity": {"family": "sofa", "dims_mm": {"width_mm": 2500, "depth_mm": 950, "height_mm": 720}},
        },
        {
            "target_key": "rug-1",
            "label": "Round Rug",
            "category": "rug",
            "layout_envelope": {
                "room_width_ratio": 0.35,
                "room_depth_ratio": 0.35,
                "room_height_ratio": 0.01,
                "footprint_ratio": 0.12,
            },
            "product_identity": {"family": "rug", "dims_mm": {"width_mm": 1400, "depth_mm": 1400, "height_mm": 10}},
        },
        {
            "target_key": "lamp-1",
            "label": "Tiny Lamp",
            "category": "floor_lamp",
            "layout_envelope": {
                "room_width_ratio": 0.03,
                "room_depth_ratio": 0.03,
                "room_height_ratio": 0.04,
                "footprint_ratio": 0.001,
            },
            "product_identity": {"family": "floor_lamp", "dims_mm": {"width_mm": 100, "depth_mm": 100, "height_mm": 100}},
        },
    ]
    room_dims_contract = RoomDimsContract(
        source="explicit",
        confidence="high",
        dims_mm_center={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        dims_mm_range={
            "width_mm": {"min_mm": 4000, "max_mm": 4000},
            "depth_mm": {"min_mm": 4000, "max_mm": 4000},
            "height_mm": {"min_mm": 2400, "max_mm": 2400},
        },
        estimation_basis=["user_dimensions"],
        strict_scale_mode="strict_geometry_mode",
        room_dims_valid=True,
    )
    scene_contract = SceneContract(
        room_dims_contract=room_dims_contract,
        room="livingroom",
        audience="internal",
        anchor_item_key="sofa-1",
        geometry_targets={
            "sofa-1": {
                "room_width_ratio": 0.625,
                "room_depth_ratio": 0.2375,
                "room_height_ratio": 0.3,
                "footprint_ratio": 0.1484,
            },
            "rug-1": {
                "room_width_ratio": 0.35,
                "room_depth_ratio": 0.35,
                "room_height_ratio": 0.01,
                "footprint_ratio": 0.1225,
            },
            "lamp-1": {
                "room_width_ratio": 0.025,
                "room_depth_ratio": 0.025,
                "room_height_ratio": 0.0417,
                "footprint_ratio": 0.0006,
            },
        },
        critical_item_keys=["sofa-1", "rug-1"],
        critical_families=["sofa", "rug"],
        pairwise_ratio_contracts=[{"kind": "rug_vs_table", "source_key": "rug-1", "anchor_key": "sofa-1"}],
        geometry_source="explicit",
        geometry_confidence="high",
    )

    placement_plan, enriched = build_placement_plan(
        analyzed_items=analyzed_items,
        primary_item=analyzed_items[0],
        scene_contract=scene_contract,
        placement_instructions="Keep the rug centered under the seating group.",
    )

    assert placement_plan.anchor_item_key == "sofa-1"
    assert placement_plan.placement_zones["sofa-1"]["zone"] == "back_wall_anchor_band"
    assert placement_plan.placement_zones["rug-1"]["placement_family"] == "rug"
    assert placement_plan.placement_zones["lamp-1"]["placement_family"] == "small_free_object"
    assert placement_plan.small_item_absolute_clamps[0]["target_key"] == "lamp-1"
    assert enriched[0]["placement_contract"]["target_key"] == "sofa-1"


def test_build_placement_plan_preserves_two_pass_strategy_metadata():
    analyzed_items = [
        {
            "target_key": "sofa-1",
            "label": "Main Sofa",
            "category": "sofa",
            "layout_envelope": {
                "room_width_ratio": 0.62,
                "room_depth_ratio": 0.24,
                "room_height_ratio": 0.3,
                "footprint_ratio": 0.15,
                "placement_family": "floor_placed",
            },
            "product_identity": {"family": "sofa", "dims_mm": {"width_mm": 2500, "depth_mm": 950, "height_mm": 720}},
            "requested_dims_mm": {"width_mm": 2500, "depth_mm": 950, "height_mm": 720},
        },
        {
            "target_key": "mirror-1",
            "label": "Mirror",
            "category": "mirror",
            "layout_envelope": {
                "room_width_ratio": 0.15,
                "room_depth_ratio": 0.01,
                "room_height_ratio": 0.3,
                "footprint_ratio": 0.01,
                "placement_family": "wall_attached",
            },
            "product_identity": {"family": "mirror", "dims_mm": {"width_mm": 600, "depth_mm": 20, "height_mm": 900}},
            "requested_dims_mm": {"width_mm": 600, "depth_mm": 20, "height_mm": 900},
        },
    ]
    analyzed_items, _ = apply_two_pass_strategy(analyzed_items)
    scene_contract = SceneContract(
        room_dims_contract=RoomDimsContract(
            source="explicit",
            confidence="high",
            dims_mm_center={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
            dims_mm_range={},
            estimation_basis=["user_dimensions"],
            strict_scale_mode="strict_geometry_mode",
            room_dims_valid=True,
        ),
        room="livingroom",
        audience="internal",
        anchor_item_key="sofa-1",
        geometry_targets={},
    )

    _, enriched = build_placement_plan(
        analyzed_items=analyzed_items,
        primary_item=analyzed_items[0],
        scene_contract=scene_contract,
        placement_instructions="",
    )

    by_key = {row["target_key"]: row for row in enriched}
    assert by_key["sofa-1"]["pass_role"] == "pass1_anchor"
    assert by_key["sofa-1"]["anchor_eligible"] is True
    assert by_key["mirror-1"]["pass_role"] == "pass2_wall"


def test_build_placement_plan_routes_secondary_lounge_seating_to_adjacent_band():
    analyzed_items = [
        {
            "target_key": "sofa-1",
            "label": "Main Sofa",
            "category": "sofa",
            "layout_envelope": {
                "room_width_ratio": 0.34,
                "room_depth_ratio": 0.22,
                "room_height_ratio": 0.28,
                "footprint_ratio": 0.11,
            },
            "product_identity": {"family": "sofa", "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 760}},
        },
        {
            "target_key": "seat-1",
            "label": "Lounge Seat",
            "category": "lounge_seating",
            "layout_envelope": {
                "room_width_ratio": 0.18,
                "room_depth_ratio": 0.15,
                "room_height_ratio": 0.18,
                "footprint_ratio": 0.04,
            },
            "product_identity": {"family": "lounge_seating", "dims_mm": {"width_mm": 980, "depth_mm": 860, "height_mm": 780}},
        },
    ]

    scene_contract = SceneContract(
        room_dims_contract=RoomDimsContract(
            source="explicit",
            confidence="high",
            dims_mm_center={"width_mm": 5000, "depth_mm": 4500, "height_mm": 2600},
            dims_mm_range={},
            estimation_basis=["user_dimensions"],
            strict_scale_mode="strict_geometry_mode",
            room_dims_valid=True,
        ),
        room="livingroom",
        audience="internal",
        anchor_item_key="sofa-1",
        geometry_targets={},
    )

    placement_plan, _ = build_placement_plan(
        analyzed_items=analyzed_items,
        primary_item=analyzed_items[0],
        scene_contract=scene_contract,
        placement_instructions="",
    )

    assert placement_plan.placement_zones["seat-1"]["placement_family"] == "floor_placed"
    assert placement_plan.placement_zones["seat-1"]["zone"] == "adjacent_seating_band"


def test_build_placement_plan_promotes_decor_shelving_reference_to_floor_storage():
    analyzed_items = [
        {
            "target_key": "shelf-1",
            "label": "몬타나 프리 333000",
            "category": "decor",
            "category_path": "수납·선반장 > 일반수납장",
            "requested_dims_mm": {"width_mm": 2030, "depth_mm": 380, "height_mm": 1100},
            "reference_features": {
                "silhouette_cues": [
                    "Four-tier horizontal open shelving unit with three wide vertical bays",
                    "open grid shelf",
                ]
            },
        }
    ]
    scene_contract = SceneContract(
        room_dims_contract=RoomDimsContract(
            source="explicit",
            confidence="high",
            dims_mm_center={"width_mm": 6000, "depth_mm": 5000, "height_mm": 2800},
            dims_mm_range={},
            estimation_basis=["user_dimensions"],
            strict_scale_mode="strict_geometry_mode",
            room_dims_valid=True,
        ),
        room="livingroom",
        audience="external",
        anchor_item_key="shelf-1",
        geometry_targets={},
    )

    placement_plan, enriched = build_placement_plan(
        analyzed_items=analyzed_items,
        primary_item=analyzed_items[0],
        scene_contract=scene_contract,
        placement_instructions="",
    )

    zone = placement_plan.placement_zones["shelf-1"]
    assert zone["family"] == "storage"
    assert zone["placement_family"] == "floor_placed"
    assert zone["zone"] == "back_wall_anchor_band"
    assert enriched[0]["placement_contract"]["family"] == "storage"


def test_build_placement_plan_routes_ceiling_and_wall_fixtures_with_orientation_hints():
    analyzed_items = [
        {
            "target_key": "sofa-1",
            "label": "Main Sofa",
            "category": "sofa",
            "layout_envelope": {
                "room_width_ratio": 0.34,
                "room_depth_ratio": 0.22,
                "room_height_ratio": 0.28,
                "footprint_ratio": 0.11,
            },
            "product_identity": {"family": "sofa", "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 760}},
        },
        {
            "target_key": "ceiling-1",
            "label": "Pendant Light",
            "category": "ceiling_light",
            "layout_envelope": {
                "room_width_ratio": 0.14,
                "room_depth_ratio": 0.14,
                "room_height_ratio": 0.35,
                "footprint_ratio": 0.01,
            },
            "product_identity": {"family": "ceiling_light", "dims_mm": {"width_mm": 600, "depth_mm": 600, "height_mm": 1200}},
        },
        {
            "target_key": "wall-1",
            "label": "Wall Sconce",
            "category": "wall_light",
            "layout_envelope": {
                "room_width_ratio": 0.08,
                "room_depth_ratio": 0.04,
                "room_height_ratio": 0.16,
                "footprint_ratio": 0.004,
            },
            "product_identity": {"family": "wall_light", "dims_mm": {"width_mm": 240, "depth_mm": 120, "height_mm": 420}},
        },
    ]

    scene_contract = SceneContract(
        room_dims_contract=RoomDimsContract(
            source="explicit",
            confidence="high",
            dims_mm_center={"width_mm": 5000, "depth_mm": 4500, "height_mm": 2600},
            dims_mm_range={},
            estimation_basis=["user_dimensions"],
            strict_scale_mode="strict_geometry_mode",
            room_dims_valid=True,
        ),
        room="livingroom",
        audience="internal",
        anchor_item_key="sofa-1",
        geometry_targets={},
    )

    placement_plan, _ = build_placement_plan(
        analyzed_items=analyzed_items,
        primary_item=analyzed_items[0],
        scene_contract=scene_contract,
        placement_instructions="",
    )

    ceiling_zone = placement_plan.placement_zones["ceiling-1"]
    wall_zone = placement_plan.placement_zones["wall-1"]

    assert ceiling_zone["placement_family"] == "ceiling_attached"
    assert ceiling_zone["zone"] == "ceiling_anchor_band"
    assert "suspended from the ceiling plane" in str(ceiling_zone["orientation_hint"])
    assert wall_zone["placement_family"] == "wall_attached"
    assert wall_zone["zone"] == "wall_mid_band"
    assert "attached to the wall plane" in str(wall_zone["orientation_hint"])
