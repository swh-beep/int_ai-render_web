from typing import Any

from application.render.postprocess_support import category_match_family
from application.render.two_pass_strategy_stage import (
    apply_two_pass_strategy,
    compute_pass_role,
    compute_strategy_priority,
    is_anchor_eligible,
    select_anchor_candidate,
)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def dims_complete(dims: dict | None) -> bool:
    if not isinstance(dims, dict):
        return False
    return _safe_int(dims.get("width_mm")) > 0 and _safe_int(dims.get("depth_mm")) > 0 and _safe_int(dims.get("height_mm")) > 0


def room_dims_complete(room_dims: dict | None) -> bool:
    if not isinstance(room_dims, dict):
        return False
    return _safe_int(room_dims.get("width_mm")) > 0 and _safe_int(room_dims.get("depth_mm")) > 0 and _safe_int(room_dims.get("height_mm")) > 0


def _item_family(item: dict) -> str:
    identity = (item or {}).get("identity_profile") or {}
    return str(
        identity.get("family")
        or (item or {}).get("category_canonical")
        or category_match_family((item or {}).get("category") or (item or {}).get("label"))
        or ""
    )


def _item_dims(item: dict) -> dict:
    return dict((item or {}).get("requested_dims_mm") or (item or {}).get("dims_mm") or {})


def _item_volume(item: dict) -> int:
    dims = _item_dims(item)
    width_mm = _safe_int(dims.get("width_mm"))
    depth_mm = _safe_int(dims.get("depth_mm"))
    height_mm = _safe_int(dims.get("height_mm"))
    if width_mm > 0 and depth_mm > 0 and height_mm > 0:
        return width_mm * depth_mm * height_mm
    return 0


def select_scale_anchor(items: list[dict], primary_item: dict | None = None) -> dict | None:
    rows, _ = apply_two_pass_strategy(items or [], primary_item=primary_item)
    if not rows:
        return None

    anchor = select_anchor_candidate(rows, primary_item=primary_item)
    if anchor:
        return anchor

    candidates = [
        row
        for row in rows
        if bool((row.get("two_pass_strategy") or {}).get("fallback_anchor_candidate"))
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            compute_strategy_priority(row),
            _item_volume(row),
            _safe_int(_item_dims(row).get("width_mm")),
            -_safe_int(row.get("source_index")),
        ),
        reverse=True,
    )
    return candidates[0]


def build_scale_plan(
    *,
    items: list[dict] | None,
    room_dims_parsed: dict | None,
    room_dims_contract: dict | None = None,
    geometry_contract: dict | None = None,
    room_planes: dict | None,
    wall_span_norm,
    primary_item: dict | None,
    strict_scale_requested: bool,
) -> dict:
    strategy_rows, two_pass_summary = apply_two_pass_strategy(items or [], primary_item=primary_item)
    geometry = dict(geometry_contract or {}) if isinstance(geometry_contract, dict) else {}
    if geometry:
        geometry_room_contract = dict(geometry.get("room_dims_contract") or {})
        geometry_room_dims = dict(geometry_room_contract.get("dims_mm_center") or room_dims_parsed or {})
        geometry_items = []
        for row in geometry.get("item_targets") or []:
            if not isinstance(row, dict):
                continue
            geometry_items.append(
                {
                    "target_key": row.get("target_key"),
                    "label": row.get("label"),
                    "source_index": row.get("source_index"),
                    "category": row.get("family"),
                    "family": row.get("family"),
                    "dims_mm": dict(row.get("dims_mm") or {}),
                    "dims_complete": bool(row.get("dims_complete")),
                    "room_width_ratio": row.get("room_width_ratio"),
                    "room_depth_ratio": row.get("room_depth_ratio"),
                    "room_height_ratio": row.get("room_height_ratio"),
                    "room_footprint_ratio": row.get("footprint_ratio"),
                    "placement_family": row.get("placement_family"),
                    "pass_role": row.get("pass_role"),
                    "anchor_eligible": bool(row.get("anchor_eligible")),
                    "strategy_priority": row.get("strategy_priority"),
                    "two_pass_strategy": dict(row.get("two_pass_strategy") or {}),
                    "relative_to_anchor": {
                        "width_ratio": row.get("anchor_width_ratio"),
                        "depth_ratio": row.get("anchor_depth_ratio"),
                        "height_ratio": row.get("anchor_height_ratio"),
                        "footprint_ratio": row.get("anchor_footprint_ratio"),
                    },
                }
            )
        anchor_key = str(geometry.get("anchor_item_key") or "")
        anchor_payload = next((row for row in geometry_items if str(row.get("target_key") or "") == anchor_key), None)
        return {
            "strict_scale_requested": bool(geometry.get("strict_scale_requested", strict_scale_requested)),
            "strict_scale_ready": bool(geometry.get("strict_scale_ready")),
            "missing_requirements": list(geometry.get("missing_requirements") or []),
            "room_dims": geometry_room_dims,
            "room_dims_valid": bool(geometry_room_contract.get("room_dims_valid")),
            "room_dims_source": geometry.get("geometry_source") or geometry_room_contract.get("source") or "unknown",
            "room_dims_confidence": geometry.get("geometry_confidence") or geometry_room_contract.get("confidence") or "none",
            "room_dims_contract": geometry_room_contract,
            "room_planes": dict(geometry.get("room_planes") or {}) if isinstance(geometry.get("room_planes"), dict) else None,
            "wall_span_norm": list(geometry.get("wall_span_norm") or wall_span_norm or [0.0, 1.0]),
            "anchor_item": anchor_payload,
            "items": geometry_items,
            "missing_item_keys": [str(entry).split(":", 1)[1] for entry in (geometry.get("missing_requirements") or []) if str(entry).startswith("item_dims_incomplete:")],
            "geometry_contract": geometry,
            "two_pass_strategy": dict(geometry.get("two_pass_strategy") or two_pass_summary or {}),
        }

    contract = dict(room_dims_contract or {}) if isinstance(room_dims_contract, dict) else {}
    center_dims = contract.get("dims_mm_center") if isinstance(contract.get("dims_mm_center"), dict) else {}
    room_dims = dict(room_dims_parsed or center_dims or {})
    room_width_mm = _safe_int(room_dims.get("width_mm"))
    room_depth_mm = _safe_int(room_dims.get("depth_mm"))
    room_height_mm = _safe_int(room_dims.get("height_mm"))
    room_ok = room_dims_complete(room_dims)

    rows = [row for row in strategy_rows if isinstance(row, dict)]
    anchor_row = select_scale_anchor(rows, primary_item=primary_item)
    anchor_dims = _item_dims(anchor_row or {})
    anchor_width_mm = _safe_int(anchor_dims.get("width_mm"))
    anchor_depth_mm = _safe_int(anchor_dims.get("depth_mm"))
    anchor_height_mm = _safe_int(anchor_dims.get("height_mm"))

    missing_items: list[str] = []
    plan_items: list[dict] = []

    for row in rows:
        dims = _item_dims(row)
        complete = dims_complete(dims)
        target_key = str(row.get("target_key") or row.get("label") or "")
        if not complete:
            missing_items.append(target_key)
        width_mm = _safe_int(dims.get("width_mm"))
        depth_mm = _safe_int(dims.get("depth_mm"))
        height_mm = _safe_int(dims.get("height_mm"))
        family = _item_family(row)
        envelope = dict((row.get("layout_envelope") or ((row.get("identity_profile") or {}).get("layout_envelope")) or {}))

        rel_to_anchor = None
        if anchor_row and anchor_width_mm > 0 and anchor_depth_mm > 0 and anchor_height_mm > 0 and complete:
            rel_to_anchor = {
                "width_ratio": round(width_mm / anchor_width_mm, 4),
                "depth_ratio": round(depth_mm / anchor_depth_mm, 4),
                "height_ratio": round(height_mm / anchor_height_mm, 4),
                "footprint_ratio": round((width_mm * depth_mm) / max(1, anchor_width_mm * anchor_depth_mm), 4),
            }

        plan_items.append(
            {
                "target_key": row.get("target_key"),
                "label": row.get("label"),
                "source_index": row.get("source_index"),
                "category": row.get("category"),
                "family": family,
                "dims_mm": dims,
                "dims_complete": complete,
                "room_width_ratio": round(width_mm / room_width_mm, 4) if room_width_mm > 0 and width_mm > 0 else None,
                "room_depth_ratio": round(depth_mm / room_depth_mm, 4) if room_depth_mm > 0 and depth_mm > 0 else None,
                "room_height_ratio": round(height_mm / room_height_mm, 4) if room_height_mm > 0 and height_mm > 0 else None,
                "room_footprint_ratio": round((width_mm * depth_mm) / max(1, room_width_mm * room_depth_mm), 4)
                if room_width_mm > 0 and room_depth_mm > 0 and width_mm > 0 and depth_mm > 0
                else None,
                "placement_family": envelope.get("placement_family"),
                "pass_role": str(row.get("pass_role") or compute_pass_role(row)),
                "anchor_eligible": bool(row.get("anchor_eligible") or is_anchor_eligible(row)),
                "strategy_priority": int(row.get("strategy_priority") or compute_strategy_priority(row)),
                "two_pass_strategy": dict(row.get("two_pass_strategy") or {}),
                "relative_to_anchor": rel_to_anchor,
            }
        )

    missing_requirements: list[str] = []
    if strict_scale_requested and not room_ok:
        missing_requirements.append("room_dims_incomplete")
    if strict_scale_requested and not anchor_row:
        missing_requirements.append("missing_anchor")
    if strict_scale_requested and missing_items:
        missing_requirements.append("item_dims_incomplete")

    anchor_payload = None
    if anchor_row:
        anchor_payload = {
            "target_key": anchor_row.get("target_key"),
            "label": anchor_row.get("label"),
            "source_index": anchor_row.get("source_index"),
            "category": anchor_row.get("category"),
            "family": _item_family(anchor_row),
            "dims_mm": anchor_dims,
            "layout_envelope": dict((anchor_row.get("layout_envelope") or ((anchor_row.get("identity_profile") or {}).get("layout_envelope")) or {})),
            "pass_role": str(anchor_row.get("pass_role") or compute_pass_role(anchor_row)),
            "anchor_eligible": bool(anchor_row.get("anchor_eligible") or is_anchor_eligible(anchor_row)),
            "strategy_priority": int(anchor_row.get("strategy_priority") or compute_strategy_priority(anchor_row)),
            "two_pass_strategy": dict(anchor_row.get("two_pass_strategy") or {}),
        }

    return {
        "strict_scale_requested": bool(strict_scale_requested),
        "strict_scale_ready": bool(
            strict_scale_requested
            and room_ok
            and anchor_row
            and not missing_items
            and str(contract.get("strict_scale_mode") or "").strip().lower() != "advisory_geometry_mode"
        ),
        "missing_requirements": missing_requirements,
        "room_dims": room_dims,
        "room_dims_valid": room_ok,
        "room_dims_source": contract.get("source") or ("explicit" if room_ok else "unknown"),
        "room_dims_confidence": contract.get("confidence") or ("high" if room_ok else "none"),
        "room_dims_contract": contract,
        "room_planes": dict(room_planes or {}) if isinstance(room_planes, dict) else None,
        "wall_span_norm": list(wall_span_norm) if isinstance(wall_span_norm, (tuple, list)) else [0.0, 1.0],
        "anchor_item": anchor_payload,
        "items": plan_items,
        "missing_item_keys": missing_items,
        "two_pass_strategy": two_pass_summary,
    }
