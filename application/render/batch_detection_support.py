from __future__ import annotations

import time
from typing import Any, Callable


def _coerce_detected_row(raw_row: dict, *, canonical_category: Callable[[str | None], str], category_match_family: Callable[[str | None], str]) -> dict | None:
    if not isinstance(raw_row, dict):
        return None
    box_2d = raw_row.get("box_2d")
    if not isinstance(box_2d, list) or len(box_2d) != 4:
        return None
    try:
        ymin = max(0.0, min(1000.0, float(box_2d[0])))
        xmin = max(0.0, min(1000.0, float(box_2d[1])))
        ymax = max(0.0, min(1000.0, float(box_2d[2])))
        xmax = max(0.0, min(1000.0, float(box_2d[3])))
    except Exception:
        return None
    if xmax <= xmin or ymax <= ymin:
        return None
    label = raw_row.get("label")
    category = raw_row.get("category") or label
    category_canonical = raw_row.get("category_canonical") or canonical_category(category)
    family = raw_row.get("family") or category_match_family(category or category_canonical or label)
    return {
        "label": label,
        "category": category,
        "category_canonical": category_canonical,
        "family": family,
        "box_2d": [ymin, xmin, ymax, xmax],
        "bbox_norm": (xmin / 1000.0, ymin / 1000.0, xmax / 1000.0, ymax / 1000.0),
    }


def detect_rows_from_render(
    render_path: str,
    *,
    detect_furniture_boxes: Callable[..., list],
    model_name: str,
    timeout_sec: int,
    retry: int,
    max_attempts: int | None = None,
    canonical_category: Callable[[str | None], str],
    category_match_family: Callable[[str | None], str],
) -> list[dict]:
    detected_rows: list[dict] = []
    configured_attempts = max_attempts if max_attempts is not None else (int(retry or 0) + 1)
    total_attempts = max(1, int(configured_attempts or 1))
    for attempt in range(total_attempts):
        try:
            raw_detected = detect_furniture_boxes(
                render_path,
                model_name=model_name,
                timeout_sec=timeout_sec,
                max_attempts=max_attempts,
            )
        except Exception:
            raw_detected = []
        detected_rows = []
        for raw_row in raw_detected or []:
            normalized = _coerce_detected_row(
                raw_row,
                canonical_category=canonical_category,
                category_match_family=category_match_family,
            )
            if normalized:
                detected_rows.append(normalized)
        if detected_rows:
            break
        if attempt + 1 < total_attempts:
            try:
                time.sleep(0.35 * (attempt + 1))
            except Exception:
                pass
    return detected_rows


def match_items_to_detected_rows(
    analyzed_items: list[dict],
    detected_rows: list[dict],
    *,
    remap_match_score: Callable[[dict, dict, int, int], float],
    category_match_family: Callable[[str | None], str],
    canonical_category: Callable[[str | None], str],
    sensitive_remap_families: set[str],
) -> list[dict]:
    remaining = list(range(len(detected_rows or [])))
    matches: list[dict] = []
    for src_idx, src_item in enumerate(analyzed_items or []):
        item = dict(src_item or {})
        best_idx = None
        best_score = 0.0
        for det_idx in remaining:
            det_item = detected_rows[det_idx] if det_idx < len(detected_rows) else {}
            score = remap_match_score(item, det_item, src_idx, det_idx)
            if score > best_score:
                best_score = score
                best_idx = det_idx

        picked_idx = None
        match_strategy = None
        src_cat = item.get("category_canonical") or canonical_category(item.get("category") or item.get("label") or "")
        src_family = category_match_family(item.get("category") or item.get("label") or "")
        sensitive_family = src_family in (sensitive_remap_families or set())
        if best_idx is not None:
            det_best = detected_rows[best_idx] if best_idx < len(detected_rows) else {}
            det_cat = (det_best or {}).get("category_canonical") or canonical_category((det_best or {}).get("category") or (det_best or {}).get("label") or "")
            det_family = category_match_family((det_best or {}).get("category") or (det_best or {}).get("label") or "")
            if best_score >= 0.52:
                picked_idx = best_idx
                match_strategy = "score_threshold"
            elif best_score >= 0.36 and src_family and det_family and src_family == det_family:
                picked_idx = best_idx
                match_strategy = "family_score_threshold"
            elif best_score >= 0.28 and src_cat and det_cat and src_cat == det_cat and not sensitive_family:
                picked_idx = best_idx
                match_strategy = "category_score_threshold"

        if picked_idx is None and remaining:
            family_candidates = []
            if src_family:
                family_candidates = [
                    det_idx
                    for det_idx in remaining
                    if category_match_family((detected_rows[det_idx] or {}).get("category") or (detected_rows[det_idx] or {}).get("label") or "") == src_family
                ]
            family_unique_threshold = 0.36 if sensitive_family else 0.18
            if len(family_candidates) == 1 and best_idx == family_candidates[0] and best_score >= family_unique_threshold:
                picked_idx = family_candidates[0]
                match_strategy = "family_unique"
            elif best_idx is not None and best_score >= 0.34 and not sensitive_family:
                picked_idx = best_idx
                match_strategy = "score_fallback"
            elif len(remaining) == 1 and len(analyzed_items or []) == 1 and not sensitive_family:
                picked_idx = remaining[0]
                match_strategy = "single_remaining"

        picked_row = detected_rows[picked_idx] if picked_idx is not None and picked_idx < len(detected_rows) else None
        if picked_idx is not None and picked_idx in remaining:
            remaining.remove(picked_idx)
        matches.append(
            {
                "item": item,
                "src_idx": src_idx,
                "picked_row": picked_row,
                "match_score": remap_match_score(item, picked_row or {}, src_idx, picked_idx or src_idx) if picked_row else 0.0,
                "match_strategy": match_strategy or "unmatched",
            }
        )
    return matches


def build_matched_items_from_rows(analyzed_items: list[dict], matches: list[dict]) -> list[dict]:
    remapped: list[dict] = []
    for src_item, match in zip(analyzed_items or [], matches or []):
        item = dict(src_item or {})
        old_box = item.get("box_2d")
        if old_box is not None and item.get("source_box_2d") is None:
            item["source_box_2d"] = old_box
        picked_row = (match or {}).get("picked_row") or {}
        if picked_row:
            box_2d = picked_row.get("box_2d")
            if isinstance(box_2d, list) and len(box_2d) == 4:
                item["box_2d"] = list(box_2d)
                item["box_source"] = "main_render"
                item["box_label_detected"] = picked_row.get("label")
                item["box_match_score"] = round(float((match or {}).get("match_score") or 0.0), 4)
                item["box_match_strategy"] = (match or {}).get("match_strategy")
            else:
                item["box_source"] = item.get("box_source") or "source_reference"
        else:
            item["box_source"] = item.get("box_source") or "source_reference"
        remapped.append(item)
    return remapped


def build_detection_rows_from_matches(matches: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for match in matches or []:
        item = (match or {}).get("item") or {}
        picked_row = (match or {}).get("picked_row") or {}
        bbox_norm = picked_row.get("bbox_norm")
        if not bbox_norm:
            continue
        rows.append(
            {
                "label": item.get("label") or picked_row.get("label"),
                "category": item.get("category") or picked_row.get("category"),
                "category_canonical": item.get("category_canonical") or picked_row.get("category_canonical"),
                "target_key": item.get("target_key"),
                "source_index": item.get("source_index"),
                "bbox_norm": bbox_norm,
                "box_2d": picked_row.get("box_2d"),
                "detected_label": picked_row.get("label"),
                "match_score": float((match or {}).get("match_score") or 0.0),
                "match_strategy": (match or {}).get("match_strategy") or "unmatched",
            }
        )
    return rows
