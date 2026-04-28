from __future__ import annotations

from typing import Any

from application.render.render_contracts import GeometryContract, RoomDimsContract


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _dims_complete(dims: dict | None) -> bool:
    if not isinstance(dims, dict):
        return False
    return all(_coerce_positive_int(dims.get(key)) for key in ("width_mm", "depth_mm", "height_mm"))


def _resolve_item_dims(item: dict | None) -> dict[str, int | None]:
    item = item if isinstance(item, dict) else {}
    dims = (
        item.get("dims_mm")
        or item.get("requested_dims_mm")
        or ((item.get("product_identity") or {}).get("dims_mm") if isinstance(item.get("product_identity"), dict) else None)
        or {}
    )
    return {
        "width_mm": _coerce_positive_int(dims.get("width_mm")),
        "depth_mm": _coerce_positive_int(dims.get("depth_mm")),
        "height_mm": _coerce_positive_int(dims.get("height_mm")),
        "radius_mm": _coerce_positive_int(dims.get("radius_mm")),
    }


def build_geometry_contract(
    *,
    room_dims_contract: RoomDimsContract,
    scene_contract,
    placement_plan,
    analyzed_items: list[dict] | None,
    primary_item: dict | None,
    strict_scale_requested: bool,
) -> GeometryContract:
    scene_dict = scene_contract.as_dict() if hasattr(scene_contract, "as_dict") else dict(scene_contract or {})
    placement_dict = placement_plan.as_dict() if hasattr(placement_plan, "as_dict") else dict(placement_plan or {})
    room_dims_dict = room_dims_contract.as_dict() if hasattr(room_dims_contract, "as_dict") else dict(room_dims_contract or {})

    anchor_item_key = str(
        placement_dict.get("anchor_item_key")
        or (primary_item or {}).get("target_key")
        or ""
    ).strip() or None

    pairwise_by_item: dict[str, dict[str, Any]] = {}
    for row in scene_dict.get("pairwise_ratio_contracts") or []:
        if not isinstance(row, dict):
            continue
        item_key = str(row.get("item_key") or "").strip()
        if item_key:
            pairwise_by_item[item_key] = row

    item_targets: list[dict[str, Any]] = []
    missing_requirements: list[str] = []

    for item in analyzed_items or []:
        if not isinstance(item, dict):
            continue
        target_key = str(item.get("target_key") or item.get("label") or "").strip()
        if not target_key:
            continue
        placement_row = (placement_dict.get("placement_zones") or {}).get(target_key) or {}
        room_targets = dict(placement_row.get("room_ratio_targets") or {})
        anchor_relationship = dict(placement_row.get("anchor_relationship") or {})
        pairwise_row = pairwise_by_item.get(target_key) or {}
        dims_mm = _resolve_item_dims(item)
        item_target = {
            "target_key": target_key,
            "label": item.get("label"),
            "family": ((item.get("product_identity") or {}).get("family") if isinstance(item.get("product_identity"), dict) else None)
            or ((item.get("identity_profile") or {}).get("family") if isinstance(item.get("identity_profile"), dict) else None)
            or item.get("category_canonical")
            or item.get("category"),
            "dims_mm": dims_mm,
            "dims_complete": _dims_complete(dims_mm),
            "placement_family": placement_row.get("placement_family"),
            "zone": placement_row.get("zone"),
            "room_width_ratio": room_targets.get("room_width_ratio"),
            "room_depth_ratio": room_targets.get("room_depth_ratio"),
            "room_height_ratio": room_targets.get("room_height_ratio"),
            "footprint_ratio": room_targets.get("footprint_ratio"),
            "anchor_width_ratio": anchor_relationship.get("width_ratio") or pairwise_row.get("width_ratio"),
            "anchor_depth_ratio": anchor_relationship.get("depth_ratio") or pairwise_row.get("depth_ratio"),
            "anchor_height_ratio": anchor_relationship.get("height_ratio") or pairwise_row.get("height_ratio"),
            "anchor_footprint_ratio": anchor_relationship.get("footprint_ratio") or pairwise_row.get("footprint_ratio"),
        }
        if strict_scale_requested and not item_target["dims_complete"]:
            missing_requirements.append(f"item_dims_incomplete:{target_key}")
        item_targets.append(item_target)

    room_valid = bool(room_dims_dict.get("room_dims_valid"))
    strict_scale_mode = str(room_dims_dict.get("strict_scale_mode") or "advisory_geometry_mode")
    if strict_scale_requested and not room_valid:
        missing_requirements.append("room_dims_incomplete")
    if strict_scale_requested and not anchor_item_key:
        missing_requirements.append("missing_anchor")
    if strict_scale_requested and strict_scale_mode == "advisory_geometry_mode":
        missing_requirements.append("advisory_geometry_mode")

    return GeometryContract(
        strict_scale_requested=bool(strict_scale_requested),
        strict_scale_ready=bool(strict_scale_requested and not missing_requirements),
        missing_requirements=list(dict.fromkeys(missing_requirements)),
        geometry_source=str(scene_dict.get("geometry_source") or room_dims_dict.get("source") or "unknown"),
        geometry_confidence=str(scene_dict.get("geometry_confidence") or room_dims_dict.get("confidence") or "none"),
        strict_scale_mode=strict_scale_mode,
        anchor_item_key=anchor_item_key,
        room_dims_contract=room_dims_contract,
        room_planes=dict(scene_dict.get("room_planes") or {}) if isinstance(scene_dict.get("room_planes"), dict) else None,
        wall_span_norm=tuple(scene_dict.get("wall_span_norm") or (0.0, 1.0)),
        pairwise_ratio_contracts=list(scene_dict.get("pairwise_ratio_contracts") or []),
        item_targets=item_targets,
    )
