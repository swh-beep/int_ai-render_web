import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

DETAIL_PRODUCT_LOCALIZATION_LIMIT = max(0, int(os.getenv("DETAIL_PRODUCT_LOCALIZATION_LIMIT", "10")))
DETAIL_PRODUCT_LOCALIZATION_WORKERS = max(1, int(os.getenv("DETAIL_PRODUCT_LOCALIZATION_WORKERS", "4")))


def _normalize_box(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [float(v) for v in value]
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


def _is_full_frame_box(value) -> bool:
    box = _normalize_box(value)
    if not box:
        return False
    ymin, xmin, ymax, xmax = box
    return ymin <= 1 and xmin <= 1 and ymax >= 999 and xmax >= 999


def _structured_items_available(items: list | None) -> bool:
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if _normalize_box(item.get("box_2d")) is not None:
            return True
    return False


def _label_counts(items: list, normalize_label_for_match: Callable[[str], str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        normalized = normalize_label_for_match(str(item.get("label") or ""))
        if not normalized:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1
    return counts


def _normalized_key(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _is_product_backed_detail_item(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    crop_path = str(item.get("crop_path") or "").strip()
    if not crop_path:
        return False
    target_key = _normalized_key(item.get("target_key"))
    item_id = _normalized_key(item.get("item_id"))
    return bool(
        target_key.startswith("cart_")
        or target_key.startswith("cart-product")
        or target_key.startswith("cart_product")
        or target_key.startswith("internal_")
        or item_id.startswith("product_")
        or item_id.startswith("cart_")
    )


def _box_from_bbox_norm(value) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        xmin, ymin, xmax, ymax = [float(v) for v in value]
    except Exception:
        return None
    xmin = max(0.0, min(1.0, xmin))
    ymin = max(0.0, min(1.0, ymin))
    xmax = max(0.0, min(1.0, xmax))
    ymax = max(0.0, min(1.0, ymax))
    if xmax <= xmin or ymax <= ymin:
        return None
    box = [int(round(ymin * 1000)), int(round(xmin * 1000)), int(round(ymax * 1000)), int(round(xmax * 1000))]
    if _is_full_frame_box(box):
        return None
    return box


def _mark_product_localization_unverified(item: dict, reason: str) -> dict:
    row = dict(item or {})
    current_box = _normalize_box(row.get("box_2d"))
    if current_box is not None:
        row["untrusted_box_2d"] = list(current_box)
        if row.get("source_box_2d") in (None, []):
            row["source_box_2d"] = list(current_box)
    row["box_2d"] = None
    row["box_source"] = "product_reference_unlocalized"
    row["detail_localization_status"] = "unverified"
    row["detail_skip_reason"] = reason
    return row


def _call_product_localizer(
    *,
    detect_item_bbox_norm: Callable[..., object] | None,
    staged_path: str,
    crop_path: str | None,
    item: dict,
) -> list[int] | None:
    if detect_item_bbox_norm is None:
        return None
    label = str(item.get("label") or "").strip() or None
    try:
        bbox = detect_item_bbox_norm(
            staged_path,
            crop_path,
            label,
            item_context=item,
            timeout_sec=20.0,
        )
    except TypeError:
        bbox = detect_item_bbox_norm(staged_path, crop_path, label)
    except Exception:
        return None
    return _box_from_bbox_norm(bbox)


def _localize_product_backed_items(
    *,
    cached_items: list,
    local_path: str,
    materialize_input: Callable[[str | None, str], str | None],
    detect_item_bbox_norm: Callable[..., object] | None,
) -> list:
    if not cached_items:
        return []

    rows = [dict(item) for item in cached_items if isinstance(item, dict)]
    if detect_item_bbox_norm is None or not local_path or not os.path.exists(local_path):
        return [
            _mark_product_localization_unverified(row, "product_reference_localization_unavailable")
            if _is_product_backed_detail_item(row)
            else row
            for row in rows
        ]

    candidate_indexes = [
        index
        for index, row in enumerate(rows)
        if _is_product_backed_detail_item(row)
    ]
    try:
        candidate_indexes.sort(key=lambda idx: int((rows[idx] or {}).get("volume_rank") or 10**9))
    except Exception:
        pass
    if DETAIL_PRODUCT_LOCALIZATION_LIMIT > 0:
        candidate_indexes = candidate_indexes[:DETAIL_PRODUCT_LOCALIZATION_LIMIT]
    candidate_index_set = set(candidate_indexes)

    def _localize(index: int) -> tuple[int, list[int] | None]:
        item = rows[index]
        crop_path = str(item.get("crop_path") or "").strip()
        local_crop_path = crop_path
        if crop_path:
            try:
                materialized = materialize_input(crop_path, f"detail_product_ref_{index + 1}")
            except Exception:
                materialized = None
            if materialized:
                local_crop_path = materialized
        box = _call_product_localizer(
            detect_item_bbox_norm=detect_item_bbox_norm,
            staged_path=local_path,
            crop_path=local_crop_path,
            item=item,
        )
        return index, box

    localized_boxes: dict[int, list[int] | None] = {}
    if candidate_indexes:
        max_workers = min(DETAIL_PRODUCT_LOCALIZATION_WORKERS, len(candidate_indexes))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for index, box in executor.map(_localize, candidate_indexes):
                localized_boxes[index] = box

    prepared: list[dict] = []
    for index, row in enumerate(rows):
        if not _is_product_backed_detail_item(row):
            prepared.append(row)
            continue
        box = localized_boxes.get(index)
        if index not in candidate_index_set or box is None:
            prepared.append(_mark_product_localization_unverified(row, "product_reference_localization_missing"))
            continue

        old_box = _normalize_box(row.get("box_2d"))
        if old_box is not None and row.get("source_box_2d") in (None, []):
            row["source_box_2d"] = list(old_box)
        row["box_2d"] = list(box)
        row["box_source"] = "product_reference_localization"
        row["detail_localization_status"] = "product_reference_verified"
        row.pop("detail_skip_reason", None)
        prepared.append(row)

    return prepared


def _prepare_localized_cached_items(
    *,
    furniture_data: list | None,
    local_path: str,
    materialize_input: Callable[[str | None, str], str | None],
    detect_item_bbox_norm: Callable[..., object] | None,
    attach_volume_ranks: Callable[[list], list],
) -> list:
    cached_items = [dict(item) for item in furniture_data or [] if isinstance(item, dict)]
    try:
        cached_items = attach_volume_ranks(cached_items)
    except Exception:
        pass
    localized_items = _localize_product_backed_items(
        cached_items=cached_items,
        local_path=local_path,
        materialize_input=materialize_input,
        detect_item_bbox_norm=detect_item_bbox_norm,
    )
    try:
        localized_items = attach_volume_ranks(localized_items)
    except Exception:
        pass
    return localized_items


def _merge_cached_identity_into_fresh_items(
    *,
    cached_items: list,
    fresh_items: list,
    normalize_label_for_match: Callable[[str], str],
    canonical_category: Callable[[str | None], str],
) -> list:
    if not cached_items:
        merged = []
        for item in fresh_items or []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["box_source"] = row.get("box_source") or "detail_current_image_analysis"
            merged.append(row)
        return merged

    available_fresh = list(range(len(fresh_items or [])))
    cached_to_fresh: dict[int, int] = {}
    matched_cached_indexes: set[int] = set()
    cached_label_counts = _label_counts(cached_items, normalize_label_for_match)
    fresh_label_counts = _label_counts(fresh_items, normalize_label_for_match)

    for cached_index, cached_item in enumerate(cached_items or []):
        if not isinstance(cached_item, dict):
            continue
        cached_key = str(cached_item.get("target_key") or "").strip()
        if not cached_key:
            continue
        exact_target_index = None
        for fresh_index in list(available_fresh):
            fresh_item = fresh_items[fresh_index] if fresh_index < len(fresh_items) else None
            if not isinstance(fresh_item, dict):
                continue
            if str(fresh_item.get("target_key") or "").strip() == cached_key:
                exact_target_index = fresh_index
                break
        if exact_target_index is None:
            continue
        cached_to_fresh[cached_index] = exact_target_index
        matched_cached_indexes.add(cached_index)
        available_fresh.remove(exact_target_index)

    for cached_index, cached_item in enumerate(cached_items or []):
        if cached_index in matched_cached_indexes:
            continue
        if not isinstance(cached_item, dict):
            continue
        best_score = 0.0
        best_fresh_index = None
        best_iou = 0.0
        best_label_exact = False
        best_label_partial = False
        best_category_exact = False
        cached_key = str(cached_item.get("target_key") or "").strip()
        cached_label_norm = normalize_label_for_match(str(cached_item.get("label") or ""))
        cached_category = str(
            cached_item.get("category_canonical")
            or canonical_category(cached_item.get("category") or cached_item.get("label"))
            or ""
        ).strip().lower()
        label_is_unique = bool(cached_label_norm) and cached_label_counts.get(cached_label_norm, 0) == 1 and fresh_label_counts.get(cached_label_norm, 0) == 1
        for fresh_index in list(available_fresh):
            fresh_item = fresh_items[fresh_index] if fresh_index < len(fresh_items) else None
            if not isinstance(fresh_item, dict):
                continue

            score = 0.0
            fresh_key = str(fresh_item.get("target_key") or "").strip()
            if cached_key and fresh_key and cached_key == fresh_key:
                score += 20.0

            fresh_label_norm = normalize_label_for_match(str(fresh_item.get("label") or ""))
            label_exact = False
            label_partial = False
            if cached_label_norm and fresh_label_norm:
                if cached_label_norm == fresh_label_norm:
                    score += 4.0
                    label_exact = True
                elif cached_label_norm in fresh_label_norm or fresh_label_norm in cached_label_norm:
                    score += 2.5
                    label_partial = True

            fresh_category = str(
                fresh_item.get("category_canonical")
                or canonical_category(fresh_item.get("category") or fresh_item.get("label"))
                or ""
            ).strip().lower()
            category_exact = False
            if cached_category and fresh_category and cached_category == fresh_category:
                score += 1.5
                category_exact = True

            iou = _box_iou(cached_item.get("box_2d"), fresh_item.get("box_2d"))
            score += iou * 6.0

            try:
                cached_rank = int(cached_item.get("volume_rank") or 0)
                fresh_rank = int(fresh_item.get("volume_rank") or 0)
            except Exception:
                cached_rank = 0
                fresh_rank = 0
            if cached_rank > 0 and fresh_rank > 0:
                rank_gap = abs(cached_rank - fresh_rank)
                if rank_gap == 0:
                    score += 0.35
                elif rank_gap <= 2:
                    score += 0.15

            if score > best_score:
                best_score = score
                best_fresh_index = fresh_index
                best_iou = iou
                best_label_exact = label_exact
                best_label_partial = label_partial
                best_category_exact = category_exact

        if best_fresh_index is None:
            continue
        if cached_key and str((fresh_items[best_fresh_index] or {}).get("target_key") or "").strip() == cached_key:
            pass
        elif best_iou < 0.18:
            if not label_is_unique:
                continue
            if not best_label_exact:
                continue
            if best_score < 4.0:
                continue
        elif best_score < 3.0:
            continue
        cached_to_fresh[cached_index] = best_fresh_index
        matched_cached_indexes.add(cached_index)
        available_fresh.remove(best_fresh_index)

    fresh_to_cached = {fresh_index: cached_index for cached_index, fresh_index in cached_to_fresh.items()}
    used_target_keys = set()

    merged: list[dict] = []
    for fresh_index, fresh_item in enumerate(fresh_items or []):
        if not isinstance(fresh_item, dict):
            continue
        row = dict(fresh_item)
        row["box_source"] = row.get("box_source") or "detail_current_image_analysis"
        cached_index = fresh_to_cached.get(fresh_index)
        if cached_index is None:
            continue

        cached_item = cached_items[cached_index]
        if not isinstance(cached_item, dict):
            continue

        row["label"] = str(cached_item.get("label") or row.get("label") or "").strip() or row.get("label")
        if cached_item.get("target_key"):
            row["target_key"] = cached_item.get("target_key")
        if cached_item.get("source_index") not in (None, ""):
            row["source_index"] = cached_item.get("source_index")
        if cached_item.get("category") not in (None, ""):
            row["category"] = cached_item.get("category")
        if cached_item.get("category_canonical") not in (None, ""):
            row["category_canonical"] = cached_item.get("category_canonical")
        if cached_item.get("description") not in (None, ""):
            row["description"] = cached_item.get("description")
        if cached_item.get("crop_path") not in (None, ""):
            row["crop_path"] = cached_item.get("crop_path")

        for field in (
            "dims_mm",
            "requested_dims_mm",
            "reference_features",
            "product_identity",
            "identity_profile",
            "layout_envelope",
            "placement_contract",
            "identity_confidence",
            "identity_strictness",
            "anchor_eligible",
            "pass_role",
            "strategy_priority",
            "archetype_strategy",
            "two_pass_strategy",
        ):
            if cached_item.get(field) not in (None, "", [], {}):
                row[field] = cached_item.get(field)

        cached_box = _normalize_box(cached_item.get("box_2d"))
        if cached_box is not None and row.get("source_box_2d") in (None, []):
            row["source_box_2d"] = list(cached_box)
        if row.get("target_key"):
            used_target_keys.add(str(row.get("target_key")))
        merged.append(row)

    for cached_index, cached_item in enumerate(cached_items or []):
        if cached_index in matched_cached_indexes:
            continue
        if not isinstance(cached_item, dict):
            continue
        cached_key = str(cached_item.get("target_key") or "").strip()
        if cached_key and cached_key in used_target_keys:
            continue
        if _normalize_box(cached_item.get("box_2d")) is None:
            continue
        row = dict(cached_item)
        row["box_source"] = row.get("box_source") or "cached_detail_snapshot"
        merged.append(row)

    return merged


def load_analyzed_items(
    *,
    furniture_data: list | None,
    moodboard_url: str | None,
    local_path: str,
    materialize_input: Callable[[str | None, str], str | None],
    detect_furniture_boxes: Callable[[str], list],
    canonical_category: Callable[[str | None], str],
    build_item_target_key: Callable[..., str],
    max_concurrency_analysis: int,
    analyze_cropped_item: Callable[[str, dict], dict],
    attach_volume_ranks: Callable[[list], list],
    simple_generation_mode: bool = False,
) -> list:
    analyzed_items = []
    if furniture_data and len(furniture_data) > 0:
        print(">> [Smart Cache] Using pre-analyzed furniture data!", flush=True)
        analyzed_items = furniture_data
    else:
        print(">> [Smart Cache] No cached data found. Starting Analysis...", flush=True)

        target_analysis_path = None
        if moodboard_url:
            if moodboard_url.startswith("/assets/"):
                rel_path = moodboard_url.lstrip("/")
                target_analysis_path = os.path.join(*rel_path.split("/"))
            else:
                target_analysis_path = materialize_input(moodboard_url, "mb")
        else:
            print(">> [Info] No Moodboard provided. Analyzing the Main Image itself.", flush=True)
            target_analysis_path = local_path

        if target_analysis_path and os.path.exists(target_analysis_path):
            try:
                detected_items = detect_furniture_boxes(target_analysis_path)
                print(f">> [Deep Analysis] Found {len(detected_items)} items in {target_analysis_path}...", flush=True)

                enriched_items = []
                for index, item in enumerate(detected_items or [], start=1):
                    if not isinstance(item, dict):
                        continue
                    label_val = item.get("label") or f"Item{index}"
                    row = dict(item)
                    row["source_index"] = index
                    row["category_canonical"] = canonical_category(label_val)
                    row["target_key"] = build_item_target_key("detail", index, label=label_val)
                    row["box_source"] = row.get("box_source") or "detail_current_image_analysis"
                    enriched_items.append(row)

                if simple_generation_mode:
                    analyzed_items = enriched_items
                else:
                    analysis_workers = min(max_concurrency_analysis, max(1, len(enriched_items)))
                    with ThreadPoolExecutor(max_workers=analysis_workers) as executor:
                        futures = [executor.submit(analyze_cropped_item, target_analysis_path, item) for item in enriched_items]
                        analyzed_items = [future.result() for future in futures]
            except Exception as exc:
                print(f"!! [Deep Analysis Failed] {exc}", flush=True)

    if not analyzed_items:
        analyzed_items = [{"label": "Main Furniture", "description": "High quality furniture matching the room style."}]

    try:
        analyzed_items = attach_volume_ranks(analyzed_items)
    except Exception:
        pass

    return analyzed_items


def prepare_detail_generation_items(
    *,
    furniture_data: list | None,
    moodboard_url: str | None,
    local_path: str,
    materialize_input: Callable[[str | None, str], str | None],
    detect_furniture_boxes: Callable[[str], list],
    canonical_category: Callable[[str | None], str],
    build_item_target_key: Callable[..., str],
    max_concurrency_analysis: int,
    analyze_cropped_item: Callable[[str, dict], dict],
    attach_volume_ranks: Callable[[list], list],
    normalize_label_for_match: Callable[[str], str],
    detect_item_bbox_norm: Callable[..., object] | None = None,
    simple_generation_mode: bool = False,
) -> list:
    if furniture_data and not simple_generation_mode:
        return _prepare_localized_cached_items(
            furniture_data=furniture_data,
            local_path=local_path,
            materialize_input=materialize_input,
            detect_item_bbox_norm=detect_item_bbox_norm,
            attach_volume_ranks=attach_volume_ranks,
        )

    fresh_items = load_analyzed_items(
        furniture_data=None,
        moodboard_url=None if furniture_data else moodboard_url,
        local_path=local_path,
        materialize_input=materialize_input,
        detect_furniture_boxes=detect_furniture_boxes,
        canonical_category=canonical_category,
        build_item_target_key=build_item_target_key,
        max_concurrency_analysis=max_concurrency_analysis,
        analyze_cropped_item=analyze_cropped_item,
        attach_volume_ranks=attach_volume_ranks,
        simple_generation_mode=simple_generation_mode,
    )

    if not _structured_items_available(fresh_items):
        if furniture_data:
            return _prepare_localized_cached_items(
                furniture_data=furniture_data,
                local_path=local_path,
                materialize_input=materialize_input,
                detect_item_bbox_norm=detect_item_bbox_norm,
                attach_volume_ranks=attach_volume_ranks,
            )
        return load_analyzed_items(
            furniture_data=furniture_data,
            moodboard_url=moodboard_url,
            local_path=local_path,
            materialize_input=materialize_input,
            detect_furniture_boxes=detect_furniture_boxes,
            canonical_category=canonical_category,
            build_item_target_key=build_item_target_key,
            max_concurrency_analysis=max_concurrency_analysis,
            analyze_cropped_item=analyze_cropped_item,
            attach_volume_ranks=attach_volume_ranks,
            simple_generation_mode=simple_generation_mode,
        )

    if not furniture_data:
        return fresh_items

    merged_items = _merge_cached_identity_into_fresh_items(
        cached_items=furniture_data,
        fresh_items=fresh_items,
        normalize_label_for_match=normalize_label_for_match,
        canonical_category=canonical_category,
    )
    try:
        merged_items = attach_volume_ranks(merged_items)
    except Exception:
        pass
    return merged_items
