import os
import uuid
from typing import Callable, Optional

from application.details.detail_analysis_stage import load_analyzed_items
from application.details.regenerate_detail_resolution import (
    attach_regenerated_target_metadata,
    resolve_regeneration_style,
)


def _normalize_box(box) -> list[float] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [float(v) for v in box]
    except Exception:
        return None
    if ymax <= ymin or xmax <= xmin:
        return None
    return [ymin, xmin, ymax, xmax]


def _box_iou(box_a, box_b) -> float:
    a = _normalize_box(box_a)
    b = _normalize_box(box_b)
    if not a or not b:
        return 0.0
    top = max(a[0], b[0])
    left = max(a[1], b[1])
    bottom = min(a[2], b[2])
    right = min(a[3], b[3])
    if bottom <= top or right <= left:
        return 0.0
    inter = (bottom - top) * (right - left)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def _align_target_hint_from_box(
    *,
    analyzed_items: list,
    req_target_key: str,
    req_target_label: str,
    requested_target_box,
    normalize_label_for_match: Callable[[str], str],
) -> tuple[str, str, str | None]:
    if not analyzed_items:
        return req_target_key, req_target_label, None

    normalized_target = normalize_label_for_match(req_target_label) if req_target_label else ""
    for item in analyzed_items:
        if not isinstance(item, dict):
            continue
        item_key = str(item.get("target_key") or "").strip()
        if req_target_key and item_key == req_target_key:
            return req_target_key, req_target_label, None
        item_label = str(item.get("label") or "").strip()
        normalized_item_label = normalize_label_for_match(item_label) if item_label else ""
        if normalized_target and normalized_item_label and (
            normalized_target == normalized_item_label
            or normalized_target in normalized_item_label
            or normalized_item_label in normalized_target
        ):
            return req_target_key, req_target_label, None

    best_item = None
    best_iou = 0.0
    requested_box = _normalize_box(requested_target_box)
    if not requested_box:
        return req_target_key, req_target_label, None

    for item in analyzed_items:
        if not isinstance(item, dict):
            continue
        for candidate_box in (item.get("source_box_2d"), item.get("box_2d")):
            iou = _box_iou(requested_box, candidate_box)
            if iou > best_iou:
                best_iou = iou
                best_item = item

    if not isinstance(best_item, dict) or best_iou < 0.10:
        return req_target_key, req_target_label, None

    return (
        str(best_item.get("target_key") or req_target_key or "").strip(),
        str(best_item.get("label") or req_target_label or "").strip(),
        "target_box_iou",
    )


def _find_matching_requested_item(
    *,
    analyzed_items: list,
    req_target_key: str,
    req_target_label: str,
    normalize_label_for_match: Callable[[str], str],
) -> dict | None:
    normalized_target = normalize_label_for_match(req_target_label) if req_target_label else ""

    for item in analyzed_items or []:
        if not isinstance(item, dict):
            continue
        if req_target_key and str(item.get("target_key") or "").strip() == req_target_key:
            return item

    if not normalized_target:
        return None

    label_matches = []
    for item in analyzed_items or []:
        if not isinstance(item, dict):
            continue
        normalized_item = normalize_label_for_match(str(item.get("label") or ""))
        if normalized_item and normalized_item == normalized_target:
            label_matches.append(item)
    if len(label_matches) == 1:
        return label_matches[0]
    return None


def _inject_requested_target_fallback(
    *,
    analyzed_items: list,
    req_target_key: str,
    req_target_label: str,
    requested_target_box,
    normalize_label_for_match: Callable[[str], str],
    canonical_category: Callable[[Optional[str]], str],
    build_item_target_key: Callable[..., str],
) -> tuple[list, str | None]:
    if not any((str(req_target_key or "").strip(), str(req_target_label or "").strip(), requested_target_box)):
        return analyzed_items, None

    matched_item = _find_matching_requested_item(
        analyzed_items=analyzed_items,
        req_target_key=req_target_key,
        req_target_label=req_target_label,
        normalize_label_for_match=normalize_label_for_match,
    )

    normalized_box = _normalize_box(requested_target_box)
    if matched_item is not None:
        hydrated = dict(matched_item)
        if req_target_label:
            hydrated["label"] = str(req_target_label).strip() or hydrated.get("label")
        if req_target_key:
            hydrated["target_key"] = str(req_target_key).strip()
        if normalized_box is not None:
            hydrated["box_2d"] = list(normalized_box)
            hydrated["source_box_2d"] = list(normalized_box)
            hydrated["box_source"] = "requested_target_box"
        next_items = [hydrated]
        replaced = False
        for item in analyzed_items or []:
            if not replaced and item is matched_item:
                replaced = True
                continue
            next_items.append(item)
        return next_items, "requested_target_fallback"

    best_item = None
    best_iou = 0.0
    if normalized_box is not None:
        for item in analyzed_items or []:
            if not isinstance(item, dict):
                continue
            for candidate_box in (item.get("source_box_2d"), item.get("box_2d")):
                iou = _box_iou(normalized_box, candidate_box)
                if iou > best_iou:
                    best_iou = iou
                    best_item = item

    hydrated = dict(best_item) if isinstance(best_item, dict) else {}
    label = str(req_target_label or hydrated.get("label") or "Requested Target").strip() or "Requested Target"
    hydrated["label"] = label
    hydrated["source_index"] = int(hydrated.get("source_index") or 1)
    hydrated["category_canonical"] = canonical_category(label)
    hydrated["target_key"] = (
        str(req_target_key).strip()
        if str(req_target_key or "").strip()
        else build_item_target_key(
            "detail",
            int(hydrated["source_index"]),
            label=label,
            category=hydrated.get("category_canonical"),
        )
    )

    if normalized_box is not None:
        hydrated["box_2d"] = list(normalized_box)
        hydrated["source_box_2d"] = list(normalized_box)
        hydrated["box_source"] = "requested_target_box"

    filtered_items = []
    for item in analyzed_items or []:
        if best_item is not None and item is best_item:
            continue
        filtered_items.append(item)
    return [hydrated, *filtered_items], "requested_target_fallback"


def run_regenerate_single_detail_job(
    payload: dict,
    *,
    normalize_audience: Callable[[Optional[str]], str],
    build_s3_prefix: Callable[[str, str, str | None], str],
    materialize_input: Callable[[str | None, str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    detect_furniture_boxes: Callable[[str], list],
    canonical_category: Callable[[Optional[str]], str],
    build_item_target_key: Callable[..., str],
    max_concurrency_analysis: int,
    analyze_cropped_item: Callable[[str, dict], dict],
    attach_volume_ranks: Callable[[list], list],
    construct_dynamic_styles: Callable[[list], list],
    normalize_label_for_match: Callable[[str], str],
    generate_detail_view: Callable[[str, dict, str, int, list | None], dict | str | None],
    volume_ranking_snapshot: Callable[[list], list],
) -> dict:
    try:
        original_image_url = payload.get("original_image_url")
        raw_style_index = int(payload.get("style_index") or 1)
        original_req_target_key = str(payload.get("target_key") or "").strip()
        original_req_target_label = str(payload.get("target_label") or "").strip()
        req_target_key = original_req_target_key
        req_target_label = original_req_target_label
        style_index_mode = str(payload.get("style_index_mode") or "auto").strip().lower()
        if style_index_mode not in {"auto", "detail", "overall"}:
            style_index_mode = "auto"

        furniture_data = payload.get("furniture_data")
        audience = payload.get("audience")

        aud = normalize_audience(audience)
        prefix_detail_user = build_s3_prefix(aud, "detailrendered", "user-photos")
        prefix_detail_rendered = build_s3_prefix(aud, "detailrendered", "rendered")

        local_path = materialize_input(original_image_url, "detail_src")
        if not local_path or not os.path.exists(local_path):
            return {"error": "Original image not found"}
        resolve_image_url(local_path, s3_prefix_override=prefix_detail_user)

        if furniture_data and len(furniture_data) > 0:
            print(">> [Single Retry] Using cached furniture data!", flush=True)
            analyzed_items = furniture_data
        else:
            print(">> [Single Retry] No cached furniture data. Re-analyzing the source image...", flush=True)
            analyzed_items = load_analyzed_items(
                furniture_data=None,
                moodboard_url=payload.get("moodboard_url"),
                local_path=local_path,
                materialize_input=materialize_input,
                detect_furniture_boxes=detect_furniture_boxes,
                canonical_category=canonical_category,
                build_item_target_key=build_item_target_key,
                max_concurrency_analysis=max_concurrency_analysis,
                analyze_cropped_item=analyze_cropped_item,
                attach_volume_ranks=attach_volume_ranks,
            )

        try:
            analyzed_items = attach_volume_ranks(analyzed_items)
        except Exception:
            pass

        requested_target_box = payload.get("target_box_2d") or payload.get("target_source_box_2d")
        fallback_resolved_by = None
        analyzed_items, fallback_resolved_by = _inject_requested_target_fallback(
            analyzed_items=analyzed_items,
            req_target_key=req_target_key,
            req_target_label=req_target_label,
            requested_target_box=requested_target_box,
            normalize_label_for_match=normalize_label_for_match,
            canonical_category=canonical_category,
            build_item_target_key=build_item_target_key,
        )

        try:
            analyzed_items = attach_volume_ranks(analyzed_items)
        except Exception:
            pass

        box_hint_resolved_by = None
        req_target_key, req_target_label, box_hint_resolved_by = _align_target_hint_from_box(
            analyzed_items=analyzed_items,
            req_target_key=req_target_key,
            req_target_label=req_target_label,
            requested_target_box=requested_target_box,
            normalize_label_for_match=normalize_label_for_match,
        )

        dynamic_styles = construct_dynamic_styles(analyzed_items)
        if not dynamic_styles:
            return {"error": "No styles available"}

        style, resolved_by, resolved_style_index = resolve_regeneration_style(
            dynamic_styles=dynamic_styles,
            raw_style_index=raw_style_index,
            req_target_key=req_target_key,
            req_target_label=req_target_label,
            style_index_mode=style_index_mode,
            normalize_label_for_match=normalize_label_for_match,
        )
        if style is None:
            return {"error": "No matching style for regeneration"}

        unique_id = uuid.uuid4().hex[:6]
        result = generate_detail_view(local_path, style, unique_id, int(resolved_style_index or 1), analyzed_items)
        if not result:
            return {"error": "Generation failed"}

        path = result.get("path") if isinstance(result, dict) else result
        url = resolve_image_url(path, s3_prefix_override=prefix_detail_rendered) if path else None
        if not url:
            return {"error": "Generation failed"}

        output = {
            "url": url,
            "message": "Success",
            "furniture_data": [dict(item) for item in analyzed_items if isinstance(item, dict)],
            "volume_ranking": volume_ranking_snapshot(analyzed_items),
            "resolved_by": resolved_by,
            "resolved_style_index": int(resolved_style_index or 1),
            "requested_style_index": raw_style_index,
            "requested_target_key": original_req_target_key or None,
            "requested_target_label": original_req_target_label or None,
        }
        resolution_chain = str(output.get("resolved_by") or "")
        if box_hint_resolved_by:
            resolution_chain = f"{box_hint_resolved_by}->{resolution_chain}" if resolution_chain else box_hint_resolved_by
        if fallback_resolved_by:
            resolution_chain = f"{fallback_resolved_by}->{resolution_chain}" if resolution_chain else fallback_resolved_by
        output["resolved_by"] = resolution_chain or None
        if isinstance(result, dict):
            output["style_name"] = result.get("style_name") or style.get("name")
            output["cutout_ref_count"] = int(result.get("cutout_ref_count") or 0)
            labels = list(result.get("cutout_ref_labels") or [])
            if labels:
                output["cutout_ref_labels"] = labels

        return attach_regenerated_target_metadata(
            output,
            style=style,
            analyzed_items=analyzed_items,
            normalize_label_for_match=normalize_label_for_match,
        )
    except Exception as exc:
        return {"error": str(exc)}
