from application.render.render_contracts import (
    build_explicit_room_dims_contract,
    build_unknown_room_dims_contract,
)


def test_build_explicit_room_dims_contract_marks_exact_geometry():
    contract = build_explicit_room_dims_contract(
        {"width_mm": 4000, "depth_mm": 3500, "height_mm": 2400},
        strict_scale_mode="strict_geometry_mode",
    )

    assert contract.source == "explicit"
    assert contract.confidence == "high"
    assert contract.room_dims_valid is True
    assert contract.strict_scale_mode == "strict_geometry_mode"
    assert contract.dims_mm_center["width_mm"] == 4000
    assert contract.dims_mm_range["width_mm"]["min_mm"] == 4000
    assert contract.dims_mm_range["width_mm"]["max_mm"] == 4000


def test_build_unknown_room_dims_contract_keeps_advisory_mode():
    contract = build_unknown_room_dims_contract(reason="missing_external_dimensions")

    assert contract.source == "unknown"
    assert contract.confidence == "none"
    assert contract.room_dims_valid is False
    assert contract.strict_scale_mode == "advisory_geometry_mode"
    assert contract.estimation_basis == ["missing_external_dimensions"]
