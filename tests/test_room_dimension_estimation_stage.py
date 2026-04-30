from application.render.room_dimension_estimation_stage import estimate_room_dims_contract


def test_estimate_room_dims_contract_uses_explicit_dimensions_when_available():
    contract = estimate_room_dims_contract(
        room="livingroom",
        explicit_room_dims={"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        room_dims_valid=True,
        audience="internal",
    )

    assert contract.source == "explicit"
    assert contract.confidence == "high"
    assert contract.strict_scale_mode == "strict_geometry_mode"
    assert contract.dims_mm_center == {
        "width_mm": 4000,
        "depth_mm": 4000,
        "height_mm": 2400,
    }


def test_estimate_room_dims_contract_uses_anchor_when_dimensions_missing():
    analyzed_items = [
        {
            "target_key": "sofa-1",
            "category": "sofa",
            "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800},
            "identity_profile": {"family": "sofa"},
        }
    ]

    contract = estimate_room_dims_contract(
        room="livingroom",
        explicit_room_dims={},
        room_dims_valid=False,
        room_analysis={
            "room_planes": {"y_top": 0.1, "y_bottom": 0.9},
            "wall_span_norm": (0.15, 0.85),
            "windows_present": True,
        },
        analyzed_items=analyzed_items,
        primary_item=analyzed_items[0],
        audience="external",
    )

    assert contract.source == "estimated"
    assert contract.confidence == "medium"
    assert contract.strict_scale_mode == "range_based_geometry_mode"
    assert contract.dims_mm_center["width_mm"] is not None
    assert contract.dims_mm_range["width_mm"]["min_mm"] < contract.dims_mm_center["width_mm"]
    assert "anchor_item" in contract.estimation_basis
    assert contract.calibration_metadata["anchor_basis"]["family"] == "sofa"
    assert contract.calibration_metadata["wall_span_norm"] == [0.15, 0.85]
    assert contract.calibration_metadata["floor_contact_band"] is not None


def test_estimate_room_dims_contract_prefers_room_analysis_estimated_dimensions():
    contract = estimate_room_dims_contract(
        room="bedroom",
        explicit_room_dims={},
        room_dims_valid=False,
        room_analysis={
            "estimated_dimensions_mm": {
                "width_mm": 5100,
                "depth_mm": 3900,
                "height_mm": 2550,
            }
        },
        analyzed_items=[],
        primary_item=None,
        audience="external",
    )

    assert contract.source == "estimated"
    assert contract.confidence == "medium"
    assert contract.strict_scale_mode == "range_based_geometry_mode"
    assert contract.dims_mm_center == {
        "width_mm": 5100,
        "depth_mm": 3900,
        "height_mm": 2550,
    }
    assert "room_image_estimate" in contract.estimation_basis
    assert "room_defaults" not in contract.estimation_basis


def test_estimate_room_dims_contract_returns_unknown_when_no_dimensions_can_be_estimated():
    contract = estimate_room_dims_contract(
        room="bedroom",
        explicit_room_dims={},
        room_dims_valid=False,
        room_analysis={},
        analyzed_items=[],
        primary_item=None,
        audience="external",
    )

    assert contract.source == "unknown"
    assert contract.confidence == "none"
    assert contract.strict_scale_mode == "advisory_geometry_mode"
    assert contract.dims_mm_center == {
        "width_mm": None,
        "depth_mm": None,
        "height_mm": None,
    }
    assert "room_defaults" not in contract.estimation_basis


def test_estimate_room_dims_contract_uses_lounge_sofa_anchor_ratio():
    analyzed_items = [
        {
            "target_key": "lounge-sofa-1",
            "category_canonical": "lounge_sofa",
            "requested_dims_mm": {"width_mm": 1800, "depth_mm": 950, "height_mm": 760},
        }
    ]

    contract = estimate_room_dims_contract(
        room="livingroom",
        explicit_room_dims={},
        room_dims_valid=False,
        room_analysis={},
        analyzed_items=analyzed_items,
        primary_item=analyzed_items[0],
        audience="external",
    )

    assert contract.source == "estimated"
    assert contract.calibration_metadata["anchor_basis"]["family"] == "lounge_sofa"
    assert contract.calibration_metadata["anchor_basis"]["ratio_hint"] == 0.3
    assert contract.dims_mm_center["width_mm"] == 6000


def test_estimate_room_dims_contract_uses_lounge_chair_anchor_ratio():
    analyzed_items = [
        {
            "target_key": "lounge-chair-1",
            "category_canonical": "lounge_chair",
            "requested_dims_mm": {"width_mm": 900, "depth_mm": 920, "height_mm": 760},
        }
    ]

    contract = estimate_room_dims_contract(
        room="livingroom",
        explicit_room_dims={},
        room_dims_valid=False,
        room_analysis={},
        analyzed_items=analyzed_items,
        primary_item=analyzed_items[0],
        audience="external",
    )

    assert contract.source == "estimated"
    assert contract.calibration_metadata["anchor_basis"]["family"] == "lounge_chair"
    assert contract.calibration_metadata["anchor_basis"]["ratio_hint"] == 0.18
    assert contract.dims_mm_center["width_mm"] == 5000
