from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _empty_dims() -> dict[str, int | None]:
    return {"width_mm": None, "depth_mm": None, "height_mm": None}


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _coerce_dims_dict(value: dict | None) -> dict[str, int | None]:
    value = value if isinstance(value, dict) else {}
    return {
        "width_mm": _coerce_positive_int(value.get("width_mm")),
        "depth_mm": _coerce_positive_int(value.get("depth_mm")),
        "height_mm": _coerce_positive_int(value.get("height_mm")),
    }


def _range_from_center(center: dict[str, int | None], percent: float) -> dict[str, dict[str, int | None]]:
    bounded_percent = max(0.0, min(float(percent or 0.0), 0.95))
    result: dict[str, dict[str, int | None]] = {}
    for key, raw_value in center.items():
        value = _coerce_positive_int(raw_value)
        if value is None:
            result[key] = {"min_mm": None, "max_mm": None}
            continue
        delta = 0 if bounded_percent == 0.0 else max(1, int(round(value * bounded_percent)))
        result[key] = {
            "min_mm": max(1, value - delta),
            "max_mm": value + delta,
        }
    return result


@dataclass
class RoomDimsContract:
    source: str = "unknown"
    confidence: str = "none"
    dims_mm_center: dict[str, int | None] = field(default_factory=_empty_dims)
    dims_mm_range: dict[str, dict[str, int | None]] = field(default_factory=lambda: _range_from_center(_empty_dims(), 0.0))
    estimation_basis: list[str] = field(default_factory=list)
    calibration_metadata: dict[str, Any] = field(default_factory=dict)
    strict_scale_mode: str = "advisory_geometry_mode"
    room_dims_valid: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedRenderRequest:
    audience: str
    room: str
    style: str
    variant: str
    dimensions: str = ""
    placement: str = ""
    moodboard_items: list[dict[str, Any]] = field(default_factory=list)
    room_dims_contract: RoomDimsContract = field(default_factory=RoomDimsContract)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["room_dims_contract"] = self.room_dims_contract.as_dict()
        return data


@dataclass
class ProductIdentity:
    target_key: str
    family: str
    dims_mm: dict[str, int | None] = field(default_factory=_empty_dims)
    topology_cues: list[str] = field(default_factory=list)
    support_geometry: list[str] = field(default_factory=list)
    opening_or_gap_features: list[str] = field(default_factory=list)
    pattern_cues: list[str] = field(default_factory=list)
    reflection_constraints: list[str] = field(default_factory=list)
    preserve_rules: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArchetypeStrategy:
    target_key: str
    family: str
    structural_archetype: str = "generic_furniture_object"
    render_strategy: str = "generic_furniture_object"
    repair_strategy: str = "generic_local_repair"
    qc_strategy: list[str] = field(default_factory=list)
    strictness: str = "standard"
    criticality: float = 1.0
    forbidden_substitutions: list[str] = field(default_factory=list)
    required_parts: list[str] = field(default_factory=list)
    allowed_variation: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlacementPlan:
    anchor_item_key: str | None = None
    placement_zones: dict[str, Any] = field(default_factory=dict)
    pairwise_ratio_contracts: list[dict[str, Any]] = field(default_factory=list)
    small_item_absolute_clamps: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GeometryContract:
    contract_version: str = "v1"
    strict_scale_requested: bool = False
    strict_scale_ready: bool = False
    missing_requirements: list[str] = field(default_factory=list)
    geometry_source: str = "unknown"
    geometry_confidence: str = "none"
    strict_scale_mode: str = "advisory_geometry_mode"
    anchor_item_key: str | None = None
    room_dims_contract: RoomDimsContract = field(default_factory=RoomDimsContract)
    room_planes: dict[str, Any] | None = None
    wall_span_norm: tuple[float, float] = (0.0, 1.0)
    pairwise_ratio_contracts: list[dict[str, Any]] = field(default_factory=list)
    item_targets: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["room_dims_contract"] = self.room_dims_contract.as_dict()
        return data


@dataclass
class SceneContract:
    contract_version: str = "v1"
    room_dims_contract: RoomDimsContract = field(default_factory=RoomDimsContract)
    room: str = ""
    audience: str = ""
    room_planes: dict[str, Any] | None = None
    wall_span_norm: tuple[float, float] = (0.0, 1.0)
    windows_present: bool | None = None
    room_analysis_text: str = ""
    camera_estimate: dict[str, Any] = field(default_factory=dict)
    placement_zones: dict[str, Any] = field(default_factory=dict)
    anchor_item_key: str | None = None
    geometry_targets: dict[str, Any] = field(default_factory=dict)
    critical_item_keys: list[str] = field(default_factory=list)
    critical_families: list[str] = field(default_factory=list)
    pairwise_ratio_contracts: list[dict[str, Any]] = field(default_factory=list)
    geometry_source: str = "unknown"
    geometry_confidence: str = "none"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["room_dims_contract"] = self.room_dims_contract.as_dict()
        return data


def build_explicit_room_dims_contract(room_dims_parsed: dict | None, *, strict_scale_mode: str = "strict_geometry_mode") -> RoomDimsContract:
    center = _coerce_dims_dict(room_dims_parsed)
    room_dims_valid = all(value is not None for value in center.values())
    return RoomDimsContract(
        source="explicit" if room_dims_valid else "unknown",
        confidence="high" if room_dims_valid else "none",
        dims_mm_center=center,
        dims_mm_range=_range_from_center(center, 0.0 if room_dims_valid else 0.35),
        estimation_basis=["user_dimensions"] if room_dims_valid else [],
        calibration_metadata={"source": "user_dimensions"} if room_dims_valid else {},
        strict_scale_mode=strict_scale_mode if room_dims_valid else "advisory_geometry_mode",
        room_dims_valid=room_dims_valid,
    )


def build_unknown_room_dims_contract(*, reason: str | None = None) -> RoomDimsContract:
    basis = [reason] if reason else []
    return RoomDimsContract(
        source="unknown",
        confidence="none",
        dims_mm_center=_empty_dims(),
        dims_mm_range=_range_from_center(_empty_dims(), 0.0),
        estimation_basis=basis,
        calibration_metadata={"reason": reason} if reason else {},
        strict_scale_mode="advisory_geometry_mode",
        room_dims_valid=False,
    )
