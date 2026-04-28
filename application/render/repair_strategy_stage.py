from __future__ import annotations

from typing import Any


_RULE_ACTION_OVERRIDES = {
    "rug_vs_anchor_footprint": "footprint_rescale_repair",
    "tiny_item_vs_anchor_height": "tiny_absolute_scale_repair",
    "mirror_reflection_drift": "reflective_surface_repair",
    "reference_shape_drift": "topology_sensitive_repair",
    "reference_material_drift": "generic_local_repair",
    "primary_width_vs_room_width": "footprint_rescale_repair",
    "scale_plan_room_width_ratio": "footprint_rescale_repair",
    "scale_plan_anchor_width_ratio": "footprint_rescale_repair",
    "scale_plan_anchor_height_ratio": "footprint_rescale_repair",
}

_RULE_SEVERITY = {
    "unmatched_source_items": 1.15,
    "primary_anchor_unmatched": 1.25,
    "no_matched_items": 1.25,
    "validation_exception": 1.35,
    "reference_shape_drift": 1.2,
    "reference_material_drift": 0.85,
    "mirror_reflection_drift": 1.2,
    "rug_vs_anchor_footprint": 1.15,
    "tiny_item_vs_anchor_height": 1.2,
    "primary_width_vs_room_width": 1.1,
    "scale_plan_room_width_ratio": 0.95,
    "scale_plan_room_height_ratio": 0.95,
    "scale_plan_anchor_width_ratio": 1.0,
    "scale_plan_anchor_height_ratio": 1.0,
}

_ACTION_PRIORITY = {
    "topology_sensitive_repair": 0,
    "support_geometry_repair": 1,
    "reflective_surface_repair": 2,
    "footprint_rescale_repair": 3,
    "tiny_absolute_scale_repair": 4,
    "generic_local_repair": 5,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _target_key(item: dict[str, Any]) -> str:
    return str(item.get("target_key") or item.get("source_index") or item.get("label") or "").strip()


def _family(item: dict[str, Any]) -> str:
    archetype = item.get("archetype_strategy") or {}
    identity = item.get("product_identity") or {}
    profile = item.get("identity_profile") or {}
    return str(
        archetype.get("family")
        or identity.get("family")
        or profile.get("family")
        or item.get("category_canonical")
        or item.get("category")
        or ""
    ).strip().lower()


def _dims(item: dict[str, Any]) -> dict[str, int]:
    dims = (
        item.get("dims_mm")
        or item.get("requested_dims_mm")
        or ((item.get("product_identity") or {}).get("dims_mm") if isinstance(item.get("product_identity"), dict) else None)
        or {}
    )
    return {
        "width_mm": _safe_int(dims.get("width_mm")),
        "depth_mm": _safe_int(dims.get("depth_mm")),
        "height_mm": _safe_int(dims.get("height_mm")),
    }


def _volume_proxy(item: dict[str, Any]) -> float:
    explicit = _safe_float(item.get("volume_proxy"))
    if explicit > 0:
        return explicit
    dims = _dims(item)
    width = max(0, dims["width_mm"])
    depth = max(0, dims["depth_mm"])
    height = max(0, dims["height_mm"])
    if width and depth and height:
        return float(width * depth * height)
    if width and depth:
        return float(width * depth)
    return float(max(width, depth, height, 0))


def _item_importance(item: dict[str, Any], *, is_primary: bool = False) -> float:
    archetype = item.get("archetype_strategy") or {}
    strictness = str(archetype.get("strictness") or "").strip().lower()
    criticality = _safe_float(archetype.get("criticality"), 1.0)
    category_score = _safe_float(item.get("category_score"), 0.0)
    placement = item.get("placement_contract") or {}
    layout = item.get("layout_envelope") or {}
    ratio_target = max(
        _safe_float((placement.get("room_ratio_targets") or {}).get("room_width_ratio")),
        _safe_float(layout.get("room_width_ratio")),
        _safe_float(layout.get("footprint_ratio")),
    )
    score = 1.0
    score += min(1.2, _volume_proxy(item) / 4_000_000_000.0)
    score += min(0.6, category_score / 100.0)
    score += min(0.5, ratio_target * 2.0)
    score += min(1.0, criticality * 0.35)
    if strictness == "critical":
        score += 0.75
    if is_primary:
        score += 1.0
    return round(score, 4)


def _base_action_for_item(item: dict[str, Any]) -> str:
    archetype = item.get("archetype_strategy") or {}
    render_strategy = str(archetype.get("render_strategy") or "").strip().lower()
    repair_strategy = str(archetype.get("repair_strategy") or "").strip().lower()
    family = _family(item)

    if repair_strategy in {
        "topology_sensitive_repair",
        "support_geometry_repair",
        "reflective_surface_repair",
        "footprint_rescale_repair",
        "tiny_absolute_scale_repair",
        "generic_local_repair",
    }:
        return repair_strategy
    if render_strategy == "topology_sensitive_seating":
        return "topology_sensitive_repair"
    if render_strategy == "support_geometry_object":
        return "support_geometry_repair"
    if render_strategy == "reflective_wall_object":
        return "reflective_surface_repair"
    if render_strategy == "thin_floor_footprint_object":
        return "footprint_rescale_repair"
    if render_strategy == "tiny_absolute_scale_object":
        return "tiny_absolute_scale_repair"
    if family in {"mirror"}:
        return "reflective_surface_repair"
    return "generic_local_repair"


def _choose_actions(item: dict[str, Any], issue_rules: list[str]) -> list[str]:
    actions = [_base_action_for_item(item)]
    for rule in issue_rules:
        override = _RULE_ACTION_OVERRIDES.get(str(rule or "").strip())
        if override and override not in actions:
            actions.append(override)
    actions = sorted(actions, key=lambda action: (_ACTION_PRIORITY.get(action, 999), action))
    return actions


def _normalized_issue_rule(issue: dict[str, Any] | None) -> str:
    if not isinstance(issue, dict):
        return ""
    return str(issue.get("rule_id") or issue.get("rule") or issue.get("issue") or "").strip()


def _issue_severity(issue: dict[str, Any] | None, rule_id: str) -> float:
    if isinstance(issue, dict) and issue.get("severity") is not None:
        return max(0.1, _safe_float(issue.get("severity"), 0.0))
    return _RULE_SEVERITY.get(rule_id, 0.8)


def _issue_confidence(issue: dict[str, Any] | None, *, matched_row: dict[str, Any] | None, unmatched: bool = False) -> float:
    if unmatched:
        return 0.98
    if isinstance(issue, dict) and issue.get("confidence") is not None:
        return max(0.1, min(1.0, _safe_float(issue.get("confidence"), 0.0)))
    if isinstance(matched_row, dict):
        confidence = _safe_float(matched_row.get("match_confidence"), 0.0)
        if confidence > 0:
            return max(0.1, min(1.0, confidence))
    return 0.65


def _issue_records_by_key(diagnostics: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_key: dict[str, list[dict[str, Any]]] = {}
    for row in diagnostics.get("issue_records") or []:
        if not isinstance(row, dict):
            continue
        item_key = str(row.get("item_key") or "").strip()
        if not item_key:
            continue
        by_key.setdefault(item_key, []).append(row)
    return by_key


def _unmatched_rows_by_key(diagnostics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in diagnostics.get("unmatched_items") or []:
        if not isinstance(row, dict):
            continue
        item_key = str(row.get("target_key") or row.get("item_key") or row.get("label") or "").strip()
        if item_key and item_key not in by_key:
            by_key[item_key] = row
    return by_key


def _rule_list(diagnostics: dict[str, Any]) -> list[str]:
    return [str(rule or "").strip() for rule in (diagnostics.get("failed_rules") or []) if str(rule or "").strip()]


def _plan_entry(
    *,
    item: dict[str, Any],
    matched_row: dict[str, Any] | None,
    issue_rows: list[dict[str, Any]],
    unmatched_row: dict[str, Any] | None,
    fallback_rules: list[str],
    is_primary: bool,
) -> dict[str, Any]:
    issue_rules = [_normalized_issue_rule(row) for row in issue_rows if _normalized_issue_rule(row)]
    if unmatched_row:
        issue_rules.append("unmatched_source_items")
    if not issue_rules:
        issue_rules = list(fallback_rules)
    issue_rules = list(dict.fromkeys(issue_rules))

    severity = max([_issue_severity(row, _normalized_issue_rule(row)) for row in issue_rows] or [_RULE_SEVERITY.get("unmatched_source_items", 1.15) if unmatched_row else 0.8])
    confidence = max(
        [_issue_confidence(row, matched_row=matched_row) for row in issue_rows]
        or [_issue_confidence(None, matched_row=matched_row, unmatched=bool(unmatched_row))]
    )
    importance = _item_importance(item, is_primary=is_primary)
    archetype = item.get("archetype_strategy") or {}
    strictness = str(archetype.get("strictness") or "standard").strip().lower()
    strictness_multiplier = 1.35 if strictness == "critical" else 1.0
    priority_score = round(severity * confidence * importance * strictness_multiplier, 4)

    return {
        "target_key": _target_key(item),
        "label": str(item.get("label") or item.get("category") or _target_key(item)),
        "family": _family(item),
        "strictness": strictness or "standard",
        "criticality": _safe_float(archetype.get("criticality"), 1.0),
        "item_importance": importance,
        "severity": round(severity, 4),
        "confidence": round(confidence, 4),
        "priority_score": priority_score,
        "issue_rules": issue_rules,
        "repair_actions": _choose_actions(item, issue_rules),
        "bbox_norm": (matched_row or {}).get("bbox_norm"),
        "match_confidence": _safe_float((matched_row or {}).get("match_confidence"), 0.0),
        "unmatched": bool(unmatched_row),
        "required_parts": list((archetype.get("required_parts") or [])[:6]),
        "forbidden_substitutions": list((archetype.get("forbidden_substitutions") or [])[:6]),
    }


def build_repair_strategy_plan(
    diagnostics: dict[str, Any] | None,
    furniture_specs_json: dict[str, Any] | None,
    *,
    limit: int = 6,
) -> dict[str, Any]:
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    furniture_specs_json = furniture_specs_json if isinstance(furniture_specs_json, dict) else {}
    items = [row for row in (furniture_specs_json.get("items") or []) if isinstance(row, dict)]
    item_by_key = {_target_key(item): item for item in items if _target_key(item)}
    matched_items = diagnostics.get("matched_items") if isinstance(diagnostics.get("matched_items"), dict) else {}
    issue_records_by_key = _issue_records_by_key(diagnostics)
    unmatched_by_key = _unmatched_rows_by_key(diagnostics)
    fallback_rules = _rule_list(diagnostics)
    primary_key = str(
        ((furniture_specs_json.get("primary_scale") or {}) if isinstance(furniture_specs_json.get("primary_scale"), dict) else {}).get("target_key")
        or ((furniture_specs_json.get("primary") or {}) if isinstance(furniture_specs_json.get("primary"), dict) else {}).get("target_key")
        or ""
    ).strip()

    repair_targets: list[dict[str, Any]] = []
    for item_key, item in item_by_key.items():
        issue_rows = issue_records_by_key.get(item_key) or []
        unmatched_row = unmatched_by_key.get(item_key)
        matched_row = matched_items.get(item_key) if isinstance(matched_items, dict) else None
        if not issue_rows and not unmatched_row:
            continue
        repair_targets.append(
            _plan_entry(
                item=item,
                matched_row=matched_row if isinstance(matched_row, dict) else None,
                issue_rows=issue_rows,
                unmatched_row=unmatched_row,
                fallback_rules=fallback_rules,
                is_primary=(item_key == primary_key),
            )
        )

    repair_targets.sort(
        key=lambda row: (
            -_safe_float(row.get("priority_score"), 0.0),
            _ACTION_PRIORITY.get((row.get("repair_actions") or ["generic_local_repair"])[0], 999),
            str(row.get("target_key") or ""),
        )
    )
    repair_targets = repair_targets[: max(1, int(limit or 1))]

    action_counts: dict[str, int] = {}
    for row in repair_targets:
        primary_action = (row.get("repair_actions") or ["generic_local_repair"])[0]
        action_counts[primary_action] = action_counts.get(primary_action, 0) + 1

    return {
        "plan_version": "v1",
        "target_count": len(repair_targets),
        "repair_targets": repair_targets,
        "action_counts": action_counts,
        "strict_primary_present": bool(primary_key),
        "fallback_failed_rules": fallback_rules,
    }
