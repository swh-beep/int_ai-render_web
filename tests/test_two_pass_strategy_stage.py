import json
from pathlib import Path

from application.render.two_pass_strategy_stage import (
    apply_two_pass_strategy,
    is_anchor_eligible,
    select_anchor_candidate,
)


def test_apply_two_pass_strategy_partitions_anchor_support_and_detail_items():
    items = [
        {
            "target_key": "sofa-1",
            "label": "Main Sofa",
            "category": "sofa",
            "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1000, "height_mm": 800},
            "identity_profile": {"family": "sofa"},
            "layout_envelope": {"placement_family": "floor_placed", "room_width_ratio": 0.6, "footprint_ratio": 0.15},
        },
        {
            "target_key": "rug-1",
            "label": "Rug",
            "category": "rug",
            "requested_dims_mm": {"width_mm": 1400, "depth_mm": 1400, "height_mm": 12},
            "identity_profile": {"family": "rug"},
            "layout_envelope": {"placement_family": "rug", "room_width_ratio": 0.35, "footprint_ratio": 0.12},
        },
        {
            "target_key": "mirror-1",
            "label": "Mirror",
            "category": "mirror",
            "requested_dims_mm": {"width_mm": 600, "depth_mm": 20, "height_mm": 900},
            "identity_profile": {"family": "mirror"},
            "layout_envelope": {"placement_family": "wall_attached", "room_width_ratio": 0.15, "footprint_ratio": 0.01},
        },
        {
            "target_key": "lamp-1",
            "label": "Floor Lamp",
            "category": "floor_lamp",
            "requested_dims_mm": {"width_mm": 100, "depth_mm": 100, "height_mm": 1500},
            "identity_profile": {"family": "floor_lamp"},
            "layout_envelope": {"placement_family": "small_free_object", "room_width_ratio": 0.025, "footprint_ratio": 0.001},
        },
    ]

    enriched, summary = apply_two_pass_strategy(items)

    by_key = {row["target_key"]: row for row in enriched}
    assert by_key["sofa-1"]["pass_role"] == "pass1_anchor"
    assert by_key["sofa-1"]["anchor_eligible"] is True
    assert by_key["rug-1"]["pass_role"] == "pass1_footprint"
    assert by_key["rug-1"]["anchor_eligible"] is False
    assert by_key["mirror-1"]["pass_role"] == "pass2_wall"
    assert by_key["lamp-1"]["pass_role"] == "pass2_small"
    assert summary["recommended_anchor_key"] == "sofa-1"
    assert summary["pass1_primary_keys"] == ["sofa-1"]
    assert "rug-1" in summary["pass1_support_keys"]
    assert "mirror-1" in summary["pass2_detail_keys"]
    assert "lamp-1" in summary["pass2_detail_keys"]


def test_anchor_eligibility_excludes_rug_mirror_floor_lamp_and_tiny_objects():
    assert is_anchor_eligible(
        {
            "category": "rug",
            "requested_dims_mm": {"width_mm": 1200, "depth_mm": 1200, "height_mm": 12},
            "identity_profile": {"family": "rug"},
            "layout_envelope": {"placement_family": "rug", "footprint_ratio": 0.1},
        }
    ) is False
    assert is_anchor_eligible(
        {
            "category": "mirror",
            "requested_dims_mm": {"width_mm": 500, "depth_mm": 20, "height_mm": 800},
            "identity_profile": {"family": "mirror"},
            "layout_envelope": {"placement_family": "wall_attached", "footprint_ratio": 0.01},
        }
    ) is False
    assert is_anchor_eligible(
        {
            "category": "floor_lamp",
            "requested_dims_mm": {"width_mm": 100, "depth_mm": 100, "height_mm": 1500},
            "identity_profile": {"family": "floor_lamp"},
            "layout_envelope": {"placement_family": "small_free_object", "footprint_ratio": 0.001},
        }
    ) is False
    assert is_anchor_eligible(
        {
            "category": "decor",
            "requested_dims_mm": {"width_mm": 120, "depth_mm": 120, "height_mm": 120},
            "identity_profile": {"family": "decor"},
            "layout_envelope": {"placement_family": "surface_placed", "footprint_ratio": 0.001},
        }
    ) is False


def test_select_anchor_candidate_prefers_large_floor_anchor_archetype():
    items = [
        {
            "target_key": "lamp-1",
            "label": "Floor Lamp",
            "category": "floor_lamp",
            "source_index": 1,
            "requested_dims_mm": {"width_mm": 100, "depth_mm": 100, "height_mm": 2400},
            "identity_profile": {"family": "floor_lamp"},
            "layout_envelope": {"placement_family": "small_free_object", "room_width_ratio": 0.03, "footprint_ratio": 0.001},
        },
        {
            "target_key": "storage-1",
            "label": "Cabinet",
            "category": "storage",
            "source_index": 2,
            "requested_dims_mm": {"width_mm": 1600, "depth_mm": 500, "height_mm": 700},
            "identity_profile": {"family": "storage"},
            "layout_envelope": {"placement_family": "floor_placed", "room_width_ratio": 0.4, "footprint_ratio": 0.05},
        },
        {
            "target_key": "rug-1",
            "label": "Rug",
            "category": "rug",
            "source_index": 3,
            "requested_dims_mm": {"width_mm": 2000, "depth_mm": 2000, "height_mm": 12},
            "identity_profile": {"family": "rug"},
            "layout_envelope": {"placement_family": "rug", "room_width_ratio": 0.5, "footprint_ratio": 0.25},
        },
    ]

    anchor = select_anchor_candidate(items)
    assert anchor is not None
    assert anchor["target_key"] == "storage-1"


def test_select_anchor_candidate_sparse_fallback_excludes_small_free_floor_lamp():
    items = [
        {
            "target_key": "lamp-1",
            "label": "Large Floor Lamp",
            "category": "floor_lamp",
            "source_index": 1,
            "requested_dims_mm": {"width_mm": 450, "depth_mm": 450, "height_mm": 1900},
            "identity_profile": {"family": "floor_lamp"},
            "layout_envelope": {"placement_family": "small_free_object", "room_width_ratio": 0.11, "footprint_ratio": 0.01},
        },
        {
            "target_key": "chair-1",
            "label": "Accent Chair",
            "category": "chair",
            "source_index": 2,
            "requested_dims_mm": {"width_mm": 780, "depth_mm": 780, "height_mm": 900},
            "identity_profile": {"family": "chair"},
            "layout_envelope": {"placement_family": "floor_placed", "room_width_ratio": 0.195, "footprint_ratio": 0.038},
        },
    ]

    anchor = select_anchor_candidate(items)

    assert anchor is not None
    assert anchor["target_key"] == "chair-1"


def test_apply_two_pass_strategy_routes_large_decor_shelving_as_pass1_storage():
    items = [
        {
            "target_key": "cart_product-39555_montana-free_001",
            "label": "몬타나 프리 333000",
            "category": "decor",
            "category_path": "수납·선반장 > 일반수납장",
            "requested_dims_mm": {"width_mm": 2030, "depth_mm": 380, "height_mm": 1100},
            "reference_features": {
                "silhouette_cues": [
                    "Four-tier horizontal open shelving unit with three wide vertical bays",
                    "rounded-corner open grid shelf",
                ],
                "distinctive_parts": ["Slender vertical connection columns"],
            },
            "layout_envelope": {
                "room_width_ratio": 0.2256,
                "room_depth_ratio": 0.0633,
                "room_height_ratio": 0.3929,
                "footprint_ratio": 0.0143,
            },
        }
    ]

    enriched, summary = apply_two_pass_strategy(items)

    assert enriched[0]["two_pass_strategy"]["family"] == "storage"
    assert enriched[0]["anchor_eligible"] is True
    assert enriched[0]["pass_role"] == "pass1_anchor"
    assert summary["recommended_anchor_key"] == "cart_product-39555_montana-free_001"
    assert summary["pass1_primary_keys"] == ["cart_product-39555_montana-free_001"]
    assert summary["pass2_detail_keys"] == []


def test_apply_two_pass_strategy_marks_identity_rich_pass2_items_for_validation():
    items = [
        {
            "target_key": "sofa-1",
            "label": "Main Sofa",
            "category": "sofa",
            "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1000, "height_mm": 800},
            "identity_profile": {"family": "sofa"},
            "layout_envelope": {"placement_family": "floor_placed", "room_width_ratio": 0.6, "footprint_ratio": 0.15},
        },
        {
            "target_key": "cart_product-39553_bowler_003",
            "label": "HAY Bowler Table Red",
            "category": "table",
            "requested_dims_mm": {"width_mm": 360, "depth_mm": 360, "height_mm": 705},
            "reference_features": {
                "silhouette_cues": [
                    "Asymmetric silhouette with a single off-center vertical support stem",
                    "Looping inverted U-shaped handle",
                ],
                "distinctive_parts": ["Off-center single support column", "hemispherical stone base"],
            },
            "layout_envelope": {"placement_family": "floor_placed", "room_width_ratio": 0.04, "footprint_ratio": 0.0024},
        },
        {
            "target_key": "cart_product-38173_taccia-small_011",
            "label": "Taccia Small Table Lamp",
            "category": "table_lamp",
            "requested_dims_mm": {"width_mm": 373, "depth_mm": 373, "height_mm": 485},
            "reference_features": {
                "silhouette_cues": ["Deep diagonal-cut glass bowl diffuser", "fluted cylindrical column base"],
                "distinctive_parts": ["Angled open-top glass bowl", "Inner white reflector disc"],
            },
            "layout_envelope": {"placement_family": "surface_placed", "room_width_ratio": 0.05, "footprint_ratio": 0.002},
        },
    ]

    enriched, summary = apply_two_pass_strategy(items)
    by_key = {row["target_key"]: row for row in enriched}

    assert by_key["cart_product-39553_bowler_003"]["pass_role"] == "pass2_floor_secondary"
    assert by_key["cart_product-39553_bowler_003"]["two_pass_strategy"]["requires_identity_validation"] is True
    assert by_key["cart_product-38173_taccia-small_011"]["pass_role"] == "pass2_support_sensitive"
    assert by_key["cart_product-38173_taccia-small_011"]["two_pass_strategy"]["requires_identity_validation"] is True
    assert set(summary["identity_validation_required_keys"]) == {
        "cart_product-39553_bowler_003",
        "cart_product-38173_taccia-small_011",
    }


def test_result590_fixture_preserves_product_identity_contracts():
    fixture_path = Path(__file__).parent / "fixtures" / "result590_identity_contract.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    enriched, summary = apply_two_pass_strategy(fixture["items"])
    by_key = {row["target_key"]: row for row in enriched}

    for target_key, expected in fixture["expected"].items():
        strategy = by_key[target_key]["two_pass_strategy"]
        if "family" in expected:
            assert strategy["family"] == expected["family"]
        assert strategy["pass_role"] == expected["pass_role"]
        if expected.get("requires_identity_validation"):
            assert strategy["requires_identity_validation"] is True

    assert "cart_product-39555_montana-free_001" in summary["pass1_support_keys"]
    assert set(summary["identity_validation_required_keys"]) >= {
        "cart_product-39553_bowler_003",
        "cart_product-38173_taccia-small_011",
        "cart_product-41002_layer-lamp_012",
    }
