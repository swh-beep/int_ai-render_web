from __future__ import annotations

from typing import Any

from application.render.postprocess_support import category_match_family
from application.render.render_contracts import PlacementPlan, RoomDimsContract, SceneContract


_CRITICAL_FAMILIES = {"sofa", "mirror", "rug", "table", "storage", "floor_lamp", "table_lamp", "ceiling_light", "wall_light", "lounge_seating", "chair", "lounge_chair"}


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _item_family(item: dict) -> str:
    identity = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    return str(
        identity.get("family")
        or category_match_family(item.get("category_canonical") or item.get("category") or item.get("label"))
        or item.get("category_canonical")
        or category_match_family(item.get("category") or item.get("label"))
        or ""
    ).strip().lower()


def _item_dims(item: dict) -> dict[str, int | None]:
    item = item if isinstance(item, dict) else {}
    dims = item.get("requested_dims_mm") or item.get("dims_mm") or {}
    return {
        "width_mm": _coerce_positive_int(dims.get("width_mm")),
        "depth_mm": _coerce_positive_int(dims.get("depth_mm")),
        "height_mm": _coerce_positive_int(dims.get("height_mm")),
    }


def _room_width(contract: RoomDimsContract) -> int | None:
    return _coerce_positive_int((contract.dims_mm_center or {}).get("width_mm"))


def _collect_pairwise_ratio_contracts(items: list[dict], primary_item: dict | None) -> list[dict[str, Any]]:
    rows = [row for row in (items or []) if isinstance(row, dict)]
    if not rows:
        return []
    primary_key = str((primary_item or {}).get("target_key") or "")
    anchor = None
    if primary_key:
        for row in rows:
            if str(row.get("target_key") or "") == primary_key:
                anchor = row
                break
    if anchor is None:
        for row in rows:
            family = _item_family(row)
            dims = _item_dims(row)
            if family != "rug" and all(dims.values()):
                anchor = row
                break
    if anchor is None:
        return []

    anchor_key = str(anchor.get("target_key") or "")
    anchor_dims = _item_dims(anchor)
    anchor_width = anchor_dims.get("width_mm") or 0
    anchor_depth = anchor_dims.get("depth_mm") or 0
    anchor_height = anchor_dims.get("height_mm") or 0
    if anchor_width <= 0 or anchor_depth <= 0 or anchor_height <= 0:
        return []

    rows_out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("target_key") or "") == anchor_key:
            continue
        dims = _item_dims(row)
        width = dims.get("width_mm") or 0
        depth = dims.get("depth_mm") or 0
        height = dims.get("height_mm") or 0
        if width <= 0 or depth <= 0 or height <= 0:
            continue
        rows_out.append(
            {
                "anchor_key": anchor_key,
                "item_key": row.get("target_key"),
                "family": _item_family(row),
                "width_ratio": round(width / anchor_width, 4),
                "depth_ratio": round(depth / anchor_depth, 4),
                "height_ratio": round(height / anchor_height, 4),
                "footprint_ratio": round((width * depth) / max(1, anchor_width * anchor_depth), 4),
            }
        )
    return rows_out


def _placement_family_for_family(family: str) -> str:
    if family in {"mirror", "wall_light"}:
        return "wall_attached"
    if family == "ceiling_light":
        return "ceiling_attached"
    if family == "rug":
        return "rug"
    if family in {"table_lamp", "decor"}:
        return "surface_placed"
    return "floor_placed"


def _collect_geometry_targets(items: list[dict], room_dims_contract: RoomDimsContract) -> dict[str, dict[str, Any]]:
    room_center = dict((room_dims_contract.dims_mm_center or {}) if isinstance(room_dims_contract, RoomDimsContract) else {})
    room_width = _coerce_positive_int(room_center.get("width_mm")) or 0
    room_depth = _coerce_positive_int(room_center.get("depth_mm")) or 0
    room_height = _coerce_positive_int(room_center.get("height_mm")) or 0
    targets: dict[str, dict[str, Any]] = {}
    for row in items or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("target_key") or "")
        if not key:
            continue
        dims = _item_dims(row)
        family = _item_family(row)
        width_mm = dims.get("width_mm") or 0
        depth_mm = dims.get("depth_mm") or 0
        height_mm = dims.get("height_mm") or 0
        targets[key] = {
            "target_key": key,
            "label": row.get("label"),
            "family": family,
            "placement_family": _placement_family_for_family(family),
            "room_width_ratio": round(width_mm / room_width, 4) if room_width > 0 and width_mm > 0 else None,
            "room_depth_ratio": round(depth_mm / room_depth, 4) if room_depth > 0 and depth_mm > 0 else None,
            "room_height_ratio": round(height_mm / room_height, 4) if room_height > 0 and height_mm > 0 else None,
            "footprint_ratio": round((width_mm * depth_mm) / max(1, room_width * room_depth), 4)
            if room_width > 0 and room_depth > 0 and width_mm > 0 and depth_mm > 0
            else None,
            "dims_mm": dims,
            "dims_complete": all((dims.get("width_mm"), dims.get("depth_mm"), dims.get("height_mm"))),
        }
    return targets


def _build_placement_zones(items: list[dict]) -> dict[str, list[str]]:
    zones = {
        "wall_attached": [],
        "ceiling_attached": [],
        "floor_placed": [],
        "rug": [],
        "surface_placed": [],
        "small_free_object": [],
    }
    for row in items or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("target_key") or "")
        if not key:
            continue
        envelope = (row.get("layout_envelope") or ((row.get("identity_profile") or {}).get("layout_envelope")) or {})
        placement_family = str(envelope.get("placement_family") or "").strip().lower()
        family = _item_family(row)
        if placement_family == "wall_attached" or family == "mirror":
            zones["wall_attached"].append(key)
        elif placement_family == "ceiling_attached" or family == "ceiling_light":
            zones["ceiling_attached"].append(key)
        elif placement_family == "rug" or family == "rug":
            zones["rug"].append(key)
        elif placement_family == "surface_placed":
            zones["surface_placed"].append(key)
        else:
            zones["floor_placed"].append(key)
            dims = _item_dims(row)
            if (dims.get("width_mm") or 0) <= 250 and (dims.get("height_mm") or 0) <= 250:
                zones["small_free_object"].append(key)
    return zones


def build_scene_contract(
    *,
    room: str,
    audience: str,
    room_dims_contract: RoomDimsContract,
    room_analysis_text: str,
    room_planes: dict | None,
    wall_span_norm,
    windows_present: bool | None,
    analyzed_items: list[dict] | None,
    primary_item: dict | None,
) -> SceneContract:
    items = [row for row in (analyzed_items or []) if isinstance(row, dict)]
    critical_item_keys: list[str] = []
    for row in items:
        key = str(row.get("target_key") or "")
        if not key:
            continue
        family = _item_family(row)
        if family in _CRITICAL_FAMILIES:
            critical_item_keys.append(key)
    primary_key = str((primary_item or {}).get("target_key") or "")
    if primary_key and primary_key not in critical_item_keys:
        critical_item_keys.insert(0, primary_key)

    placement_zones = _build_placement_zones(items)
    pairwise_ratio_contracts = _collect_pairwise_ratio_contracts(items, primary_item)
    geometry_targets = _collect_geometry_targets(items, room_dims_contract)
    anchor_item_key = primary_key or None
    placement_plan = PlacementPlan(
        anchor_item_key=anchor_item_key,
        placement_zones=placement_zones,
        pairwise_ratio_contracts=pairwise_ratio_contracts,
        small_item_absolute_clamps=[
            {
                "item_key": key,
                "max_height_ratio_vs_room": 0.12,
            }
            for key in placement_zones.get("small_free_object", [])
        ],
    )
    contract = SceneContract(
        room_dims_contract=room_dims_contract,
        room=room,
        audience=audience,
        room_planes=dict(room_planes or {}) if isinstance(room_planes, dict) else None,
        wall_span_norm=tuple(wall_span_norm) if isinstance(wall_span_norm, (tuple, list)) and len(wall_span_norm) == 2 else (0.0, 1.0),
        windows_present=windows_present,
        room_analysis_text=room_analysis_text or "",
        camera_estimate={
            "wall_span_norm": list(wall_span_norm) if isinstance(wall_span_norm, (tuple, list)) else [0.0, 1.0],
            "room_width_mm_center": _room_width(room_dims_contract),
        },
        placement_zones=placement_plan.placement_zones,
        anchor_item_key=anchor_item_key,
        geometry_targets=geometry_targets,
        critical_item_keys=critical_item_keys,
        critical_families=sorted(_CRITICAL_FAMILIES),
        pairwise_ratio_contracts=placement_plan.pairwise_ratio_contracts,
        geometry_source=room_dims_contract.source,
        geometry_confidence=room_dims_contract.confidence,
    )
    return contract
