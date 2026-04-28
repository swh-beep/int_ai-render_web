from application.render.repair_strategy_stage import build_repair_strategy_plan


def _item(
    *,
    target_key: str,
    family: str,
    render_strategy: str,
    repair_strategy: str,
    strictness: str = "standard",
    criticality: float = 1.0,
    width_mm: int = 1000,
    depth_mm: int = 1000,
    height_mm: int = 1000,
    category_score: int = 0,
    volume_proxy: int | None = None,
):
    return {
        "target_key": target_key,
        "label": target_key,
        "category": family,
        "category_score": category_score,
        "volume_proxy": volume_proxy if volume_proxy is not None else width_mm * depth_mm * max(height_mm, 1),
        "requested_dims_mm": {
            "width_mm": width_mm,
            "depth_mm": depth_mm,
            "height_mm": height_mm,
        },
        "archetype_strategy": {
            "family": family,
            "render_strategy": render_strategy,
            "repair_strategy": repair_strategy,
            "strictness": strictness,
            "criticality": criticality,
            "required_parts": ["signature-part"],
            "forbidden_substitutions": ["identity-swap"],
        },
    }


def test_build_repair_strategy_plan_uses_archetype_actions_without_product_name_hardcoding():
    furniture_specs_json = {
        "items": [
            _item(
                target_key="seat-1",
                family="sofa",
                render_strategy="topology_sensitive_seating",
                repair_strategy="topology_sensitive_repair",
                strictness="critical",
                criticality=2.2,
                width_mm=2400,
                depth_mm=1100,
                height_mm=800,
            ),
            _item(
                target_key="mirror-1",
                family="mirror",
                render_strategy="reflective_wall_object",
                repair_strategy="reflective_surface_repair",
                strictness="critical",
                criticality=1.8,
                width_mm=400,
                depth_mm=10,
                height_mm=800,
            ),
        ],
        "primary_scale": {"target_key": "seat-1"},
    }
    diagnostics = {
        "issue_records": [
            {"item_key": "seat-1", "rule_id": "reference_shape_drift", "severity": 1.2, "confidence": 0.9},
            {"item_key": "mirror-1", "rule_id": "mirror_reflection_drift", "severity": 1.1, "confidence": 0.8},
        ],
        "matched_items": {
            "seat-1": {"bbox_norm": [0.1, 0.1, 0.4, 0.5], "match_confidence": 0.88},
            "mirror-1": {"bbox_norm": [0.2, 0.7, 0.5, 0.85], "match_confidence": 0.77},
        },
        "failed_rules": ["reference_shape_drift", "mirror_reflection_drift"],
    }

    plan = build_repair_strategy_plan(diagnostics, furniture_specs_json)

    assert plan["target_count"] == 2
    by_key = {row["target_key"]: row for row in plan["repair_targets"]}
    assert by_key["seat-1"]["repair_actions"][0] == "topology_sensitive_repair"
    assert by_key["mirror-1"]["repair_actions"][0] == "reflective_surface_repair"
    assert by_key["seat-1"]["priority_score"] > by_key["mirror-1"]["priority_score"]


def test_build_repair_strategy_plan_prioritizes_unmatched_critical_items():
    furniture_specs_json = {
        "items": [
            _item(
                target_key="rug-1",
                family="rug",
                render_strategy="thin_floor_footprint_object",
                repair_strategy="footprint_rescale_repair",
                strictness="critical",
                criticality=1.7,
                width_mm=1100,
                depth_mm=1100,
                height_mm=12,
            ),
            _item(
                target_key="lamp-1",
                family="floor_lamp",
                render_strategy="tiny_absolute_scale_object",
                repair_strategy="tiny_absolute_scale_repair",
                strictness="critical",
                criticality=1.6,
                width_mm=100,
                depth_mm=100,
                height_mm=100,
            ),
        ]
    }
    diagnostics = {
        "issue_records": [
            {"item_key": "rug-1", "rule_id": "rug_vs_anchor_footprint", "severity": 1.0, "confidence": 0.7}
        ],
        "unmatched_items": [
            {"target_key": "lamp-1", "label": "Akari"}
        ],
        "failed_rules": ["rug_vs_anchor_footprint", "unmatched_source_items"],
    }

    plan = build_repair_strategy_plan(diagnostics, furniture_specs_json)

    assert plan["repair_targets"][0]["target_key"] == "lamp-1"
    assert plan["repair_targets"][0]["repair_actions"][0] == "tiny_absolute_scale_repair"
    assert plan["repair_targets"][1]["repair_actions"][0] == "footprint_rescale_repair"


def test_build_repair_strategy_plan_support_geometry_uses_support_repair():
    furniture_specs_json = {
        "items": [
            _item(
                target_key="table-1",
                family="table",
                render_strategy="support_geometry_object",
                repair_strategy="support_geometry_repair",
                strictness="critical",
                criticality=1.4,
                width_mm=500,
                depth_mm=500,
                height_mm=500,
                category_score=80,
            ),
        ]
    }
    diagnostics = {
        "issue_records": [
            {"item_key": "table-1", "rule_id": "reference_material_drift", "severity": 0.7, "confidence": 0.6},
        ],
        "matched_items": {
            "table-1": {"bbox_norm": [0.3, 0.3, 0.6, 0.6], "match_confidence": 0.74},
        },
        "failed_rules": ["reference_material_drift"],
    }

    plan = build_repair_strategy_plan(diagnostics, furniture_specs_json)

    target = plan["repair_targets"][0]
    assert target["repair_actions"][0] == "support_geometry_repair"
    assert target["bbox_norm"] == [0.3, 0.3, 0.6, 0.6]
    assert target["required_parts"] == ["signature-part"]


def test_build_repair_strategy_plan_uses_generic_local_repair_for_generic_objects():
    furniture_specs_json = {
        "items": [
            _item(
                target_key="decor-1",
                family="decor",
                render_strategy="generic_furniture_object",
                repair_strategy="generic_local_repair",
                strictness="standard",
                criticality=1.0,
                width_mm=300,
                depth_mm=300,
                height_mm=400,
            ),
        ]
    }
    diagnostics = {
        "issue_records": [
            {"item_key": "decor-1", "rule_id": "reference_material_drift", "severity": 0.5, "confidence": 0.5},
        ],
        "failed_rules": ["reference_material_drift"],
    }

    plan = build_repair_strategy_plan(diagnostics, furniture_specs_json)

    assert plan["repair_targets"][0]["repair_actions"] == ["generic_local_repair"]
