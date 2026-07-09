from __future__ import annotations

from typing import Any

from application.render.postprocess_support import (
    decor_prefers_surface_placement,
    resolve_item_canonical_category,
    resolve_item_family,
)
from application.render.render_contracts import PlacementPlan


_SURFACE_FAMILIES = {"table_lamp"}
_SMALL_FREE_FAMILIES = {"floor_lamp", "table_lamp", "stool"}
_ADJACENT_SEATING_FAMILIES = {"chair", "lounge_chair"}
_SECONDARY_ADJACENT_SEATING_FAMILIES = {"lounge_seating", "armchair", "loveseat"}
_CEILING_ATTACHED_FAMILIES = {"ceiling_light"}
_WALL_ATTACHED_FAMILIES = {"mirror", "wall_light"}
_TABLE_LAMP_SUPPORT_PRIORITY = ("storage", "side_table", "floor")


def _coerce_ratio(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    return round(parsed, 4) if parsed > 0 else None


def _item_family(item: dict) -> str:
    return resolve_item_family(item)


def _item_canonical_category(item: dict) -> str:
    return resolve_item_canonical_category(item)


def _room_width_ratio_hint(item: dict) -> float | None:
    envelope = (item.get("layout_envelope") or {}) if isinstance(item, dict) else {}
    return _coerce_ratio(envelope.get("room_width_ratio"))


def _item_width_mm(item: dict) -> int | None:
    dims = (item.get("product_identity") or {}).get("dims_mm") or item.get("requested_dims_mm") or {}
    try:
        width_mm = int(dims.get("width_mm") or 0)
    except Exception:
        return None
    return width_mm if width_mm > 0 else None


def _is_adjacent_seating(item: dict) -> bool:
    family = _item_family(item)
    if family in _ADJACENT_SEATING_FAMILIES:
        return True
    if family not in _SECONDARY_ADJACENT_SEATING_FAMILIES:
        return False
    width_mm = _item_width_mm(item) or 0
    room_width_ratio = _room_width_ratio_hint(item) or 0.0
    return width_mm <= 1600 or room_width_ratio <= 0.24


def _placement_family(item: dict) -> str:
    family = _item_family(item)
    if family in _WALL_ATTACHED_FAMILIES:
        return "wall_attached"
    if family in _CEILING_ATTACHED_FAMILIES:
        return "ceiling_attached"
    if family == "rug":
        return "rug"
    if family in _SURFACE_FAMILIES or (family == "decor" and decor_prefers_surface_placement(item)):
        return "surface_placed"
    if family in _SMALL_FREE_FAMILIES:
        return "small_free_object"
    return "floor_placed"


def _zone_name(item: dict) -> str:
    family = _item_family(item)
    placement_family = _placement_family(item)
    if family in {"sofa", "lounge_sofa", "storage"}:
        return "back_wall_anchor_band"
    if placement_family == "ceiling_attached":
        return "ceiling_anchor_band"
    if placement_family == "wall_attached":
        return "wall_mid_band"
    if _is_adjacent_seating(item):
        return "adjacent_seating_band"
    if family in {"table", "desk"}:
        return "center_floor_anchor"
    if family == "rug":
        return "under_anchor_band"
    if family == "table_lamp":
        return "table_lamp_support_priority_band"
    if placement_family == "surface_placed":
        return "surface_top_band"
    if placement_family == "small_free_object":
        return "edge_floor_band"
    return "general_floor_band"


def _orientation_hint(item: dict) -> str | None:
    family = _item_family(item)
    placement_family = _placement_family(item)
    if placement_family == "ceiling_attached":
        return "Keep this fixture suspended from the ceiling plane with a vertical drop. Do not pull it forward into the room."
    if placement_family == "wall_attached":
        return "Keep this object attached to the wall plane. Do not float it into room depth."
    if family in {"sofa", "lounge_sofa", "storage"}:
        return "Keep the back roughly parallel to the back wall and face the seating area toward the room center."
    if _is_adjacent_seating(item):
        return "Keep this seat oriented toward the primary seating group instead of rotating it diagonally."
    if family == "rug":
        return "Keep a single rug centered under the anchor seating group. Do not duplicate or offset it."
    if family == "table_lamp":
        return "Place this table lamp by priority: storage/cabinet top first, side table second, floor fallback only when neither support exists. Do not default to a sofa or coffee table."
    if family in {"table", "desk"}:
        return "Keep the tabletop aligned to the seating anchor geometry unless explicit placement instructions say otherwise."
    return None


def _support_target_type(item: dict) -> str | None:
    family = _item_family(item)
    canonical = _item_canonical_category(item)
    if family == "storage" or canonical == "storage_cabinet_shelf":
        return "storage"
    if canonical == "side_table":
        return "side_table"
    return None


def _table_lamp_support_priority(item: dict, items: list[dict]) -> dict[str, Any] | None:
    if _item_family(item) != "table_lamp":
        return None

    item_key = str(item.get("target_key") or item.get("label") or "")
    available_targets: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for support_type in _TABLE_LAMP_SUPPORT_PRIORITY:
        if support_type == "floor":
            continue
        for candidate in items:
            if not isinstance(candidate, dict):
                continue
            candidate_key = str(candidate.get("target_key") or candidate.get("label") or "")
            if not candidate_key or candidate_key == item_key or candidate_key in seen_keys:
                continue
            if _support_target_type(candidate) != support_type:
                continue
            seen_keys.add(candidate_key)
            available_targets.append(
                {
                    "target_key": candidate_key,
                    "label": str(candidate.get("label") or candidate_key),
                    "support_type": support_type,
                }
            )

    return {
        "order": list(_TABLE_LAMP_SUPPORT_PRIORITY),
        "available_targets": available_targets,
        "fallback": "floor",
        "rule": "Use storage/cabinet top first, side table second, and the floor only if neither support is present. Avoid sofa tables and coffee tables for table lamps.",
    }


def _anchor_relationship(item: dict, anchor_item_key: str | None) -> dict[str, Any]:
    relationship = {
        "anchor_to": anchor_item_key,
        "width_ratio": None,
        "height_ratio": None,
        "footprint_ratio": None,
    }
    return relationship


def _small_item_absolute_clamp(item: dict) -> dict[str, Any] | None:
    family = _item_family(item)
    if family not in _SMALL_FREE_FAMILIES:
        return None
    dims = (item.get("product_identity") or {}).get("dims_mm") or item.get("requested_dims_mm") or {}
    try:
        width_mm = int(dims.get("width_mm") or 0)
        depth_mm = int(dims.get("depth_mm") or 0)
        height_mm = int(dims.get("height_mm") or 0)
    except Exception:
        return None
    if width_mm <= 0 and depth_mm <= 0 and height_mm <= 0:
        return None
    return {
        "target_key": item.get("target_key"),
        "family": family,
        "max_width_mm": width_mm or None,
        "max_depth_mm": depth_mm or None,
        "max_height_mm": height_mm or None,
    }


def build_placement_plan(
    *,
    analyzed_items: list[dict] | None,
    primary_item: dict | None,
    scene_contract: Any,
    placement_instructions: str | None = None,
) -> tuple[PlacementPlan, list[dict]]:
    scene_contract_dict = scene_contract.as_dict() if hasattr(scene_contract, "as_dict") else dict(scene_contract or {})
    anchor_item_key = str(
        (primary_item or {}).get("target_key")
        or scene_contract_dict.get("anchor_item_key")
        or ((scene_contract_dict.get("critical_item_keys") or [None])[0] if isinstance(scene_contract_dict, dict) else None)
        or ""
    ) or None
    geometry_targets = dict(scene_contract_dict.get("geometry_targets") or {}) if isinstance(scene_contract_dict, dict) else {}
    pairwise_by_item = {
        str(row.get("item_key") or ""): row
        for row in (scene_contract_dict.get("pairwise_ratio_contracts") or [])
        if isinstance(row, dict) and str(row.get("item_key") or "")
    }

    placement_zones: dict[str, Any] = {}
    small_item_absolute_clamps: list[dict[str, Any]] = []
    enriched_items: list[dict] = []

    for row in analyzed_items or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item_key = str(item.get("target_key") or item.get("label") or "")
        geometry_target = geometry_targets.get(item_key) or {}
        anchor_pair = pairwise_by_item.get(item_key) or {}
        placement_contract = {
            "target_key": item_key,
            "family": _item_family(item),
            "placement_family": _placement_family(item),
            "zone": _zone_name(item),
            "orientation_hint": _orientation_hint(item),
            "anchor_relationship": {
                **_anchor_relationship(item, anchor_item_key),
                "width_ratio": anchor_pair.get("width_ratio"),
                "height_ratio": anchor_pair.get("height_ratio"),
                "footprint_ratio": anchor_pair.get("footprint_ratio"),
            },
            "room_ratio_targets": {
                "room_width_ratio": _coerce_ratio(geometry_target.get("room_width_ratio")),
                "room_depth_ratio": _coerce_ratio(geometry_target.get("room_depth_ratio")),
                "room_height_ratio": _coerce_ratio(geometry_target.get("room_height_ratio")),
                "footprint_ratio": _coerce_ratio(geometry_target.get("footprint_ratio")),
            },
        }
        support_priority = _table_lamp_support_priority(item, analyzed_items or [])
        if support_priority:
            placement_contract["support_priority"] = support_priority
        placement_zones[item_key] = placement_contract
        item["placement_contract"] = placement_contract
        clamp = _small_item_absolute_clamp(item)
        if clamp:
            small_item_absolute_clamps.append(clamp)
        enriched_items.append(item)

    placement_plan = PlacementPlan(
        anchor_item_key=anchor_item_key,
        placement_zones=placement_zones,
        pairwise_ratio_contracts=list((scene_contract_dict.get("pairwise_ratio_contracts") or []) if isinstance(scene_contract_dict, dict) else []),
        small_item_absolute_clamps=small_item_absolute_clamps,
    )

    placement_plan_dict = placement_plan.as_dict()
    if placement_instructions:
        placement_plan_dict["placement_instructions"] = str(placement_instructions).strip()
    return placement_plan, enriched_items
