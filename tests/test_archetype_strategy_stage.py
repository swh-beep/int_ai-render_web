from application.render.archetype_strategy_stage import build_archetype_strategies


def _item(
    *,
    target_key: str,
    family: str,
    width_mm: int,
    depth_mm: int,
    height_mm: int,
    support_geometry=None,
    openings=None,
    reflective=False,
):
    support_geometry = support_geometry or []
    openings = openings or []
    return {
        "target_key": target_key,
        "category": family,
        "identity_profile": {
            "family": family,
            "wall_attached_expected": family == "mirror",
            "floor_contact_expected": family != "mirror",
            "distinctive_parts": support_geometry + openings,
            "reflective_surface": reflective,
        },
        "product_identity": {
            "family": family,
            "dims_mm": {"width_mm": width_mm, "depth_mm": depth_mm, "height_mm": height_mm},
            "support_geometry": support_geometry,
            "opening_or_gap_features": openings,
            "reflection_constraints": ["reflective_surface"] if reflective else [],
        },
        "requested_dims_mm": {"width_mm": width_mm, "depth_mm": depth_mm, "height_mm": height_mm},
    }


def test_build_archetype_strategies_classifies_generalized_objects():
    items = [
        _item(target_key="mirror-1", family="mirror", width_mm=400, depth_mm=10, height_mm=800, reflective=True),
        _item(target_key="rug-1", family="rug", width_mm=1100, depth_mm=1100, height_mm=12),
        _item(target_key="lamp-1", family="floor_lamp", width_mm=100, depth_mm=100, height_mm=100),
        _item(target_key="table-1", family="table", width_mm=500, depth_mm=500, height_mm=500, support_geometry=["cantilever frame"]),
        _item(target_key="sofa-1", family="sofa", width_mm=2400, depth_mm=1100, height_mm=800, openings=["center gap"], support_geometry=["low base"]),
    ]

    enriched, strategies = build_archetype_strategies(items, primary_item={"target_key": "sofa-1"})
    by_key = {row["target_key"]: row["archetype_strategy"] for row in enriched}

    assert len(strategies) == 5
    assert by_key["mirror-1"]["render_strategy"] == "reflective_wall_object"
    assert by_key["rug-1"]["render_strategy"] == "thin_floor_footprint_object"
    assert by_key["lamp-1"]["render_strategy"] == "tiny_absolute_scale_object"
    assert by_key["table-1"]["render_strategy"] == "support_geometry_object"
    assert by_key["sofa-1"]["render_strategy"] == "topology_sensitive_seating"
    assert by_key["sofa-1"]["strictness"] == "critical"
    assert by_key["sofa-1"]["criticality"] > by_key["table-1"]["criticality"]
