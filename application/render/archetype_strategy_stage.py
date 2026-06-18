from __future__ import annotations

from typing import Any

from application.render.postprocess_support import resolve_item_family
from application.render.render_contracts import ArchetypeStrategy


_SMALL_ABSOLUTE_MAX_MM = 250
_THIN_FOOTPRINT_MAX_MM = 40
_SEATING_FAMILIES = {"sofa", "lounge_sofa", "lounge_chair", "chair", "lounge_seating"}
_SUPPORT_FAMILIES = {"table", "desk", "stool"}


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _resolve_family(item: dict) -> str:
    return resolve_item_family(item)


def _resolve_dims(item: dict) -> dict[str, int | None]:
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


def _required_parts(item: dict) -> list[str]:
    profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    identity = (item.get("product_identity") or {}) if isinstance(item, dict) else {}
    parts: list[str] = []
    for value in list(identity.get("support_geometry") or []) + list(identity.get("opening_or_gap_features") or []) + list(profile.get("distinctive_parts") or []):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)
    return parts[:6]


def _forbidden_substitutions(strategy: str, family: str) -> list[str]:
    if strategy == "reflective_wall_object":
        return ["remove_reflective_face", "detach_from_wall", "replace_frame_outline"]
    if strategy == "thin_floor_footprint_object":
        return ["replace_floor_footprint", "change_outline_shape", "oversize_relative_to_anchor"]
    if strategy == "tiny_absolute_scale_object":
        return ["oversize_relative_to_room", "replace_micro_scale_object", "merge_into_large_fixture"]
    if strategy == "topology_sensitive_seating":
        return ["change_seat_topology", "remove_open_gap", "change_support_geometry"]
    if strategy == "support_geometry_object":
        return ["replace_support_layout", "swap_leg_geometry", "change_top_shape"]
    if strategy == "block_storage_object":
        return ["change_door_count", "change_block_silhouette", "replace_case_geometry"]
    if family == "mirror":
        return ["detach_from_wall", "change_reflection_plane"]
    return ["change_object_identity"]


def _allowed_variation(strategy: str) -> list[str]:
    if strategy == "reflective_wall_object":
        return ["minor_lighting_change", "minor_camera_perspective"]
    if strategy == "tiny_absolute_scale_object":
        return ["minor_shadow_change", "minor_glow_change"]
    return ["minor_material_highlight_change", "minor_camera_perspective"]


def _classify_strategy(item: dict) -> tuple[str, list[str]]:
    family = _resolve_family(item)
    identity = (item.get("product_identity") or {}) if isinstance(item, dict) else {}
    profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    dims = _resolve_dims(item)
    max_dim = max([value or 0 for value in dims.values()])
    support_geometry = list(identity.get("support_geometry") or [])
    opening_features = list(identity.get("opening_or_gap_features") or [])
    reflection_constraints = list(identity.get("reflection_constraints") or [])
    wall_attached = bool(profile.get("wall_attached_expected"))
    floor_contact = bool(profile.get("floor_contact_expected"))
    height_mm = dims.get("height_mm") or 0

    if family == "mirror" or (wall_attached and reflection_constraints):
        return "reflective_wall_object", ["reflection_consistency", "wall_attachment", "outline_preservation"]
    if family == "rug" or (floor_contact and height_mm > 0 and height_mm <= _THIN_FOOTPRINT_MAX_MM):
        return "thin_floor_footprint_object", ["footprint_ratio", "floor_contact", "outline_preservation"]
    if max_dim > 0 and max_dim <= _SMALL_ABSOLUTE_MAX_MM:
        return "tiny_absolute_scale_object", ["absolute_scale", "anchor_ratio", "presence_without_oversizing"]
    if family in _SEATING_FAMILIES:
        return "topology_sensitive_seating", ["topology_preservation", "support_geometry", "material_identity"]
    if family in _SUPPORT_FAMILIES or support_geometry:
        return "support_geometry_object", ["support_geometry", "top_shape", "anchor_ratio"]
    if family == "storage":
        return "block_storage_object", ["block_silhouette", "door_front_identity", "wall_span_limit"]
    return "generic_furniture_object", ["room_ratio", "anchor_ratio", "material_identity"]


def _strictness_for_strategy(strategy: str) -> str:
    if strategy in {
        "reflective_wall_object",
        "thin_floor_footprint_object",
        "tiny_absolute_scale_object",
        "topology_sensitive_seating",
        "support_geometry_object",
    }:
        return "critical"
    return "standard"


def _criticality(item: dict, *, strategy: str, is_primary: bool) -> float:
    dims = _resolve_dims(item)
    width = dims.get("width_mm") or 0
    depth = dims.get("depth_mm") or 0
    height = dims.get("height_mm") or 0
    footprint = width * depth
    score = 1.0
    if footprint > 0:
        score += min(0.8, footprint / 4_000_000.0)
    if height > 0:
        score += min(0.4, height / 3000.0)
    if strategy in {"reflective_wall_object", "thin_floor_footprint_object", "tiny_absolute_scale_object", "topology_sensitive_seating"}:
        score += 0.45
    if is_primary:
        score += 0.8
    return round(score, 3)


def build_archetype_strategies(
    analyzed_items: list[dict] | None,
    *,
    primary_item: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    primary_key = str((primary_item or {}).get("target_key") or "").strip()
    enriched: list[dict] = []
    strategies: list[dict] = []

    for row in analyzed_items or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        target_key = str(item.get("target_key") or item.get("label") or "").strip()
        family = _resolve_family(item)
        strategy_name, qc_strategy = _classify_strategy(item)
        strategy = ArchetypeStrategy(
            target_key=target_key,
            family=family,
            structural_archetype=strategy_name,
            render_strategy=strategy_name,
            repair_strategy=f"{strategy_name}_repair",
            qc_strategy=qc_strategy,
            strictness=_strictness_for_strategy(strategy_name),
            criticality=_criticality(item, strategy=strategy_name, is_primary=(target_key == primary_key)),
            forbidden_substitutions=_forbidden_substitutions(strategy_name, family),
            required_parts=_required_parts(item),
            allowed_variation=_allowed_variation(strategy_name),
        )
        item["archetype_strategy"] = strategy.as_dict()
        enriched.append(item)
        strategies.append(strategy.as_dict())

    return enriched, strategies
