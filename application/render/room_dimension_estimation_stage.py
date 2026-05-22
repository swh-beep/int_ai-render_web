from __future__ import annotations

from typing import Any

from application.render.postprocess_support import category_match_family
from application.render.render_contracts import (
    RoomDimsContract,
    build_explicit_room_dims_contract,
    build_unknown_room_dims_contract,
)

_ANCHOR_ROOM_WIDTH_RATIO_HINTS: dict[str, float] = {
    "sofa": 0.46,
    "lounge_sofa": 0.3,
    "lounge_seating": 0.2,
    "chair": 0.16,
    "lounge_chair": 0.18,
    "table": 0.22,
    "desk": 0.28,
    "storage": 0.34,
    "bed": 0.5,
}

_TINY_FAMILIES = {"floor_lamp", "table_lamp", "decor"}


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _extract_room_analysis_dims(room_analysis: dict | None) -> dict[str, int | None]:
    room_analysis = room_analysis if isinstance(room_analysis, dict) else {}
    raw_dims = room_analysis.get("estimated_dimensions_mm") if isinstance(room_analysis.get("estimated_dimensions_mm"), dict) else {}
    return {
        "width_mm": _coerce_positive_int(raw_dims.get("width_mm")),
        "depth_mm": _coerce_positive_int(raw_dims.get("depth_mm")),
        "height_mm": _coerce_positive_int(raw_dims.get("height_mm")),
    }


def _has_any_dims(dims: dict[str, int | None]) -> bool:
    return any(_coerce_positive_int(value) is not None for value in (dims or {}).values())


def _has_complete_dims(dims: dict[str, int | None]) -> bool:
    return all(_coerce_positive_int(dims.get(key)) is not None for key in ("width_mm", "depth_mm", "height_mm"))


def _ratio_range(center_value: int, *, percent: float) -> dict[str, int]:
    bounded_percent = max(0.0, float(percent or 0.0))
    delta = 0 if bounded_percent == 0.0 else max(1, int(round(center_value * bounded_percent)))
    return {"min_mm": max(1, center_value - delta), "max_mm": center_value + delta}


def _build_dims_range(center: dict[str, int | None], *, percent: float) -> dict[str, dict[str, int | None]]:
    result: dict[str, dict[str, int | None]] = {}
    for key, value in center.items():
        positive_value = _coerce_positive_int(value)
        if positive_value is None:
            result[key] = {"min_mm": None, "max_mm": None}
            continue
        result[key] = _ratio_range(positive_value, percent=percent)
    return result


def _family_for_item(item: dict | None) -> str:
    item = item if isinstance(item, dict) else {}
    identity = item.get("identity_profile") or {}
    return str(
        identity.get("family")
        or category_match_family(item.get("category_canonical") or item.get("category") or item.get("label"))
        or item.get("category_canonical")
        or category_match_family(item.get("category") or item.get("label"))
        or ""
    ).strip().lower()


def _dims_for_item(item: dict | None) -> dict[str, int | None]:
    item = item if isinstance(item, dict) else {}
    dims = item.get("requested_dims_mm") or item.get("dims_mm") or {}
    return {
        "width_mm": _coerce_positive_int(dims.get("width_mm")),
        "depth_mm": _coerce_positive_int(dims.get("depth_mm")),
        "height_mm": _coerce_positive_int(dims.get("height_mm")),
    }


def _select_anchor_item(primary_item: dict | None, analyzed_items: list[dict] | None) -> dict | None:
    rows = [row for row in (analyzed_items or []) if isinstance(row, dict)]
    if isinstance(primary_item, dict):
        primary_key = str(primary_item.get("target_key") or "")
        if primary_key:
            for row in rows:
                if str(row.get("target_key") or "") == primary_key:
                    return row
    candidates = []
    for row in rows:
        family = _family_for_item(row)
        dims = _dims_for_item(row)
        width_mm = dims.get("width_mm") or 0
        depth_mm = dims.get("depth_mm") or 0
        height_mm = dims.get("height_mm") or 0
        if family in _TINY_FAMILIES:
            continue
        if width_mm <= 0 or depth_mm <= 0 or height_mm <= 0:
            continue
        volume = width_mm * depth_mm * height_mm
        candidates.append((volume, width_mm, row))
    if not candidates:
        return None
    candidates.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return candidates[0][2]


def estimate_room_dims_contract(
    *,
    room: str | None,
    explicit_room_dims: dict | None,
    room_dims_valid: bool,
    room_analysis: dict | None = None,
    analyzed_items: list[dict] | None = None,
    primary_item: dict | None = None,
    audience: str = "external",
) -> RoomDimsContract:
    if room_dims_valid:
        return build_explicit_room_dims_contract(explicit_room_dims, strict_scale_mode="strict_geometry_mode")

    basis: list[str] = []
    confidence = "none"
    strict_scale_mode = "advisory_geometry_mode"
    center: dict[str, int | None] = {"width_mm": None, "depth_mm": None, "height_mm": None}

    room_analysis = room_analysis if isinstance(room_analysis, dict) else {}
    room_planes = room_analysis.get("room_planes") if isinstance(room_analysis.get("room_planes"), dict) else {}
    wall_span_norm = room_analysis.get("wall_span_norm")
    windows_present = bool(room_analysis.get("windows_present"))
    analysis_dims = _extract_room_analysis_dims(room_analysis)
    calibration_metadata = {
        "camera_height_estimate": None,
        "horizon_band": None,
        "floor_contact_band": None,
        "wall_attachment_band": None,
        "wall_span_norm": list(wall_span_norm) if isinstance(wall_span_norm, (tuple, list)) and len(wall_span_norm) == 2 else None,
        "anchor_basis": None,
    }

    if _has_any_dims(analysis_dims):
        center.update(analysis_dims)
        basis.append("room_image_estimate")
        confidence = "medium"
        strict_scale_mode = "range_based_geometry_mode"
        calibration_metadata["estimated_dimensions_mm"] = dict(analysis_dims)

    if room_planes:
        basis.append("room_planes")
        try:
            y_top = float(room_planes.get("y_top", 0.0))
            y_bottom = float(room_planes.get("y_bottom", 1.0))
            calibration_metadata["floor_contact_band"] = [round(max(0.0, y_bottom - 0.06), 4), round(min(1.0, y_bottom + 0.02), 4)]
            calibration_metadata["wall_attachment_band"] = [round(max(0.0, y_top + 0.05), 4), round(min(1.0, y_bottom - 0.05), 4)]
        except Exception:
            pass

    anchor_item = _select_anchor_item(primary_item, analyzed_items)
    anchor_dims = _dims_for_item(anchor_item)
    anchor_family = _family_for_item(anchor_item)
    anchor_width = anchor_dims.get("width_mm") or 0
    if anchor_width > 0 and not _has_complete_dims(center):
        ratio_hint = _ANCHOR_ROOM_WIDTH_RATIO_HINTS.get(anchor_family) or 0.28
        estimated_width = max(2200, int(round(anchor_width / max(0.08, ratio_hint))))
        existing_width = _coerce_positive_int(center.get("width_mm"))
        if existing_width is None:
            center["width_mm"] = estimated_width
        else:
            center["width_mm"] = int(round((existing_width + estimated_width) / 2))
        basis.append("anchor_item")
        confidence = "medium"
        strict_scale_mode = "range_based_geometry_mode"
        calibration_metadata["anchor_basis"] = {
            "target_key": anchor_item.get("target_key"),
            "family": anchor_family,
            "width_mm": anchor_width,
            "ratio_hint": ratio_hint,
        }

    if windows_present:
        basis.append("windows_present")

    if not _has_any_dims(center):
        return build_unknown_room_dims_contract(reason="missing_room_dimensions_analysis")

    complete_estimate = _has_complete_dims(center)
    assume_exact_external = str(audience or "").strip().lower() == "external" and complete_estimate
    if assume_exact_external:
        confidence = "high"
        strict_scale_mode = "strict_geometry_mode"
        calibration_metadata["external_inferred_dimensions_assumed_exact"] = True
        basis.append("external_inferred_dimensions_assumed_exact")

    percent = 0.0 if assume_exact_external else (0.18 if confidence == "medium" else 0.28)
    return RoomDimsContract(
        source="estimated",
        confidence=confidence,
        dims_mm_center={
            "width_mm": _coerce_positive_int(center["width_mm"]),
            "depth_mm": _coerce_positive_int(center["depth_mm"]),
            "height_mm": _coerce_positive_int(center["height_mm"]),
        },
        dims_mm_range=_build_dims_range(center, percent=percent),
        estimation_basis=basis,
        calibration_metadata=calibration_metadata,
        strict_scale_mode=strict_scale_mode,
        room_dims_valid=bool(assume_exact_external),
    )
