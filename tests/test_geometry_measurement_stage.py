from application.render.geometry_measurement_stage import (
    build_measurement_specs,
    summarize_measurements,
    unresolved_measurement_targets,
)


def test_build_measurement_specs_prefers_geometry_contract_targets():
    rows = build_measurement_specs(
        item_key="rug-1",
        bbox_norm=(0.25, 0.55, 0.55, 0.70),
        primary_bbox_norm=(0.20, 0.40, 0.70, 0.80),
        room_dims={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        scale_plan_row={"room_width_ratio": 0.5, "relative_to_anchor": {"width_ratio": 0.9}},
        geometry_target={
            "room_width_ratio": 0.28,
            "room_height_ratio": 0.10,
            "anchor_width_ratio": 0.6,
            "anchor_height_ratio": 0.25,
        },
        wall_span_norm=(0.1, 0.9),
        room_planes={"y_top": 0.2, "y_bottom": 0.9},
    )

    assert {row["rule_id"] for row in rows} >= {
        "scale_plan_room_width_ratio",
        "scale_plan_room_height_ratio",
        "scale_plan_anchor_width_ratio",
        "scale_plan_anchor_height_ratio",
    }
    assert all(row["source"] == "geometry_contract" for row in rows)
    summary = summarize_measurements(rows)
    assert summary["measurement_count"] == 4
    assert summary["max_relative_error"] >= 0.0


def test_unresolved_measurement_targets_exposes_expected_contracts():
    unresolved = unresolved_measurement_targets(
        unmatched_items=[{"item_key": "lamp-1", "family": "floor_lamp"}],
        geometry_contract={
            "item_targets": [
                {
                    "target_key": "lamp-1",
                    "room_width_ratio": 0.04,
                    "room_height_ratio": 0.05,
                    "anchor_width_ratio": 0.08,
                    "anchor_height_ratio": 0.12,
                }
            ]
        },
    )

    assert unresolved == [
        {
            "item_key": "lamp-1",
            "family": "floor_lamp",
            "expected_room_width_ratio": 0.04,
            "expected_room_height_ratio": 0.05,
            "expected_anchor_width_ratio": 0.08,
            "expected_anchor_height_ratio": 0.12,
        }
    ]


def test_build_measurement_specs_uses_scale_plan_relative_to_anchor_fallback():
    rows = build_measurement_specs(
        item_key="lamp-1",
        bbox_norm=(0.60, 0.40, 0.70, 0.64),
        primary_bbox_norm=(0.20, 0.30, 0.60, 0.70),
        room_dims={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        scale_plan_row={
            "room_width_ratio": 0.08,
            "relative_to_anchor": {"width_ratio": 0.25, "height_ratio": 0.60},
        },
        geometry_target=None,
        wall_span_norm=None,
        room_planes={"y_top": 0.10, "y_bottom": 0.90},
    )

    rule_ids = {row["rule_id"] for row in rows}
    assert "scale_plan_anchor_width_ratio" in rule_ids
    assert "scale_plan_anchor_height_ratio" in rule_ids
    assert "scale_plan_room_width_ratio" not in rule_ids


def test_build_measurement_specs_skips_room_width_ratio_without_measured_wall_span():
    rows = build_measurement_specs(
        item_key="sofa-1",
        bbox_norm=(0.20, 0.30, 0.60, 0.70),
        primary_bbox_norm=(0.20, 0.30, 0.60, 0.70),
        room_dims={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        scale_plan_row=None,
        geometry_target={"room_width_ratio": 0.40, "room_height_ratio": 0.30},
        wall_span_norm=None,
        room_planes={"y_top": 0.10, "y_bottom": 0.90},
    )

    rule_ids = {row["rule_id"] for row in rows}
    assert "scale_plan_room_width_ratio" not in rule_ids
    assert "scale_plan_room_height_ratio" in rule_ids
