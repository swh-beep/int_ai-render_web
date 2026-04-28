from __future__ import annotations

from typing import Any


def _coerce_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _coerce_bbox(bbox_norm: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(bbox_norm, (list, tuple)) or len(bbox_norm) != 4:
        return None
    try:
        xmin, ymin, xmax, ymax = [float(v) for v in bbox_norm]
    except Exception:
        return None
    if xmax <= xmin or ymax <= ymin:
        return None
    return (xmin, ymin, xmax, ymax)


def _metric_row(
    *,
    item_key: str,
    rule_id: str,
    metric: str,
    observed: float | None,
    expected: float | None,
    tolerance: float | None,
    source: str,
) -> dict | None:
    if observed is None or expected is None or tolerance is None:
        return None
    delta = abs(float(observed) - float(expected))
    relative_error = delta / max(1e-6, abs(float(expected)))
    return {
        "item_key": item_key,
        "rule_id": rule_id,
        "metric": metric,
        "observed": round(float(observed), 4),
        "expected": round(float(expected), 4),
        "delta": round(delta, 4),
        "relative_error": round(relative_error, 4),
        "tolerance": round(float(tolerance), 4),
        "source": source,
    }


def _geometry_target_by_key(geometry_contract: dict | None) -> dict[str, dict]:
    if not isinstance(geometry_contract, dict):
        return {}
    targets: dict[str, dict] = {}
    for row in geometry_contract.get("item_targets") or []:
        if not isinstance(row, dict):
            continue
        target_key = str(row.get("target_key") or "").strip()
        if target_key:
            targets[target_key] = row
    return targets


def build_measurement_specs(
    *,
    item_key: str,
    bbox_norm: Any,
    primary_bbox_norm: Any,
    room_dims: dict | None,
    scale_plan_row: dict | None,
    geometry_target: dict | None,
    wall_span_norm: Any,
    room_planes: dict | None,
) -> list[dict]:
    bbox = _coerce_bbox(bbox_norm)
    primary_bbox = _coerce_bbox(primary_bbox_norm)
    if bbox is None:
        return []

    specs: list[dict] = []
    bbox_width = bbox[2] - bbox[0]
    bbox_height = bbox[3] - bbox[1]

    wall_span_width = 1.0
    wall_span_measured = False
    if isinstance(wall_span_norm, (list, tuple)) and len(wall_span_norm) == 2:
        try:
            wall_span_width = max(1e-6, float(wall_span_norm[1]) - float(wall_span_norm[0]))
            wall_span_measured = wall_span_width > 0
        except Exception:
            wall_span_width = 1.0

    wall_height_norm = None
    if isinstance(room_planes, dict):
        try:
            y_top = float(room_planes.get("y_top", 0.0))
            y_bottom = float(room_planes.get("y_bottom", 1.0))
            if y_bottom > y_top:
                wall_height_norm = max(1e-6, y_bottom - y_top)
        except Exception:
            wall_height_norm = None

    plan_source = geometry_target if isinstance(geometry_target, dict) and geometry_target else scale_plan_row
    source_label = "geometry_contract" if isinstance(geometry_target, dict) and geometry_target else "scale_plan"

    if isinstance(plan_source, dict):
        room_width_ratio = _coerce_float(plan_source.get("room_width_ratio"))
        room_height_ratio = _coerce_float(plan_source.get("room_height_ratio"))
        anchor_width_ratio = _coerce_float(plan_source.get("anchor_width_ratio"))
        anchor_height_ratio = _coerce_float(plan_source.get("anchor_height_ratio"))
        relative_to_anchor = plan_source.get("relative_to_anchor") if isinstance(plan_source.get("relative_to_anchor"), dict) else {}
        if anchor_width_ratio is None:
            anchor_width_ratio = _coerce_float(relative_to_anchor.get("width_ratio"))
        if anchor_height_ratio is None:
            anchor_height_ratio = _coerce_float(relative_to_anchor.get("height_ratio"))
        can_emit_room_width_ratio = room_width_ratio and wall_span_measured
        can_emit_room_height_ratio = room_height_ratio and wall_height_norm is not None
        if can_emit_room_width_ratio and wall_span_width:
            row = _metric_row(
                item_key=item_key,
                rule_id="scale_plan_room_width_ratio",
                metric="room_width_ratio",
                observed=bbox_width / wall_span_width,
                expected=room_width_ratio,
                tolerance=max(0.08, room_width_ratio * 0.18),
                source=source_label,
            )
            if row:
                specs.append(row)
        if can_emit_room_height_ratio and wall_height_norm:
            row = _metric_row(
                item_key=item_key,
                rule_id="scale_plan_room_height_ratio",
                metric="room_height_ratio",
                observed=bbox_height / wall_height_norm,
                expected=room_height_ratio,
                tolerance=max(0.08, room_height_ratio * 0.20),
                source=source_label,
            )
            if row:
                specs.append(row)
        if primary_bbox is not None:
            primary_width = primary_bbox[2] - primary_bbox[0]
            primary_height = primary_bbox[3] - primary_bbox[1]
            if anchor_width_ratio and primary_width > 0:
                row = _metric_row(
                    item_key=item_key,
                    rule_id="scale_plan_anchor_width_ratio",
                    metric="anchor_width_ratio",
                    observed=bbox_width / primary_width,
                    expected=anchor_width_ratio,
                    tolerance=max(0.12, anchor_width_ratio * 0.18),
                    source=source_label,
                )
                if row:
                    specs.append(row)
            if anchor_height_ratio and primary_height > 0:
                row = _metric_row(
                    item_key=item_key,
                    rule_id="scale_plan_anchor_height_ratio",
                    metric="anchor_height_ratio",
                    observed=bbox_height / primary_height,
                    expected=anchor_height_ratio,
                    tolerance=max(0.12, anchor_height_ratio * 0.20),
                    source=source_label,
                )
                if row:
                    specs.append(row)
    return specs


def summarize_measurements(rows: list[dict] | None) -> dict:
    valid_rows = [row for row in (rows or []) if isinstance(row, dict)]
    if not valid_rows:
        return {
            "measurement_count": 0,
            "critical_ratio_fail_count": 0,
            "mean_relative_error": 0.0,
            "max_relative_error": 0.0,
        }
    relative_errors = [float(row.get("relative_error") or 0.0) for row in valid_rows]
    critical_fail_count = sum(
        1 for row in valid_rows if float(row.get("delta") or 0.0) > float(row.get("tolerance") or 0.0)
    )
    return {
        "measurement_count": len(valid_rows),
        "critical_ratio_fail_count": critical_fail_count,
        "mean_relative_error": round(sum(relative_errors) / max(1, len(relative_errors)), 4),
        "max_relative_error": round(max(relative_errors), 4),
    }


def unresolved_measurement_targets(
    *,
    unmatched_items: list[dict] | None,
    geometry_contract: dict | None,
) -> list[dict]:
    targets = _geometry_target_by_key(geometry_contract)
    unresolved: list[dict] = []
    for row in unmatched_items or []:
        if not isinstance(row, dict):
            continue
        item_key = str(row.get("item_key") or row.get("target_key") or "").strip()
        target = targets.get(item_key) or {}
        unresolved.append(
            {
                "item_key": item_key,
                "family": row.get("family"),
                "expected_room_width_ratio": target.get("room_width_ratio"),
                "expected_room_height_ratio": target.get("room_height_ratio"),
                "expected_anchor_width_ratio": target.get("anchor_width_ratio"),
                "expected_anchor_height_ratio": target.get("anchor_height_ratio"),
            }
        )
    return unresolved
