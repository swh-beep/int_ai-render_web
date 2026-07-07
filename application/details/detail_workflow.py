import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from application.details.detail_analysis_stage import prepare_detail_generation_items
from application.details.detail_result_stage import build_detail_generation_output
from application.details.detail_style_stage import with_internal_angle_styles


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default)) or default))
    except Exception:
        return max(minimum, int(default))


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default)) or default))
    except Exception:
        return max(minimum, float(default))


DETAIL_GENERATION_TIMEOUT_CAP_SEC = _env_int("DETAIL_GENERATION_TIMEOUT_SEC", 180, minimum=10)
DETAIL_GENERATION_BUDGETED_MAX_WORKERS = _env_int("DETAIL_GENERATION_BUDGETED_MAX_WORKERS", 10)
DETAIL_GENERATION_MAX_WORKERS = _env_int("DETAIL_GENERATION_MAX_WORKERS", 20)
EXTERNAL_DETAIL_STYLE_LIMIT = 6
DETAIL_SPATIAL_DIVERSITY_IOU_THRESHOLD = _env_float("DETAIL_SPATIAL_DIVERSITY_IOU_THRESHOLD", 0.42)
DETAIL_SPATIAL_DIVERSITY_CANVAS_WIDTH = _env_int("DETAIL_SPATIAL_DIVERSITY_CANVAS_WIDTH", 1376)
DETAIL_SPATIAL_DIVERSITY_CANVAS_HEIGHT = _env_int("DETAIL_SPATIAL_DIVERSITY_CANVAS_HEIGHT", 768)
DETAIL_CROP_MIN_SOURCE_WIDTH_PX = _env_int("DETAIL_CROP_MIN_SOURCE_WIDTH_PX", 400)
DETAIL_CROP_MIN_SOURCE_HEIGHT_PX = _env_int("DETAIL_CROP_MIN_SOURCE_HEIGHT_PX", 500)


def _is_product_backed_external_style(style: dict) -> bool:
    if not isinstance(style, dict):
        return False
    target_key = str(style.get("target_key") or "").strip().lower()
    detail_mode = str(style.get("detail_mode") or "").strip().lower()
    return bool(
        detail_mode == "product_identity_lock"
        or target_key.startswith("cart_")
        or target_key.startswith("cart_product")
        or str(style.get("target_crop_path") or "").strip()
    )


def _coerce_box_2d(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [float(row) for row in value]
    except Exception:
        return None
    if xmax <= xmin or ymax <= ymin:
        return None
    box = [
        max(0.0, min(1000.0, ymin)),
        max(0.0, min(1000.0, xmin)),
        max(0.0, min(1000.0, ymax)),
        max(0.0, min(1000.0, xmax)),
    ]
    if box[2] - box[0] >= 980.0 and box[3] - box[1] >= 980.0:
        return None
    return box


def _box_iou(box_a, box_b) -> float:
    a = _coerce_box_2d(box_a)
    b = _coerce_box_2d(box_b)
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
    return inter / union if union > 0.0 else 0.0


def _clamp_bounds(bounds: tuple[float, float, float, float], image_size: tuple[int, int]) -> tuple[float, float, float, float]:
    width, height = image_size
    left, top, right, bottom = bounds
    left = max(0.0, min(float(width - 1), left))
    top = max(0.0, min(float(height - 1), top))
    right = max(left + 1.0, min(float(width), right))
    bottom = max(top + 1.0, min(float(height), bottom))
    return left, top, right, bottom


def _style_family(style: dict) -> str:
    raw = (
        style.get("target_category_canonical")
        or style.get("target_category")
        or style.get("category_canonical")
        or style.get("category")
        or style.get("target_label")
        or style.get("name")
        or ""
    )
    return str(raw).strip().lower().replace("-", "_").replace(" ", "_")


def _expand_style_bounds(
    bounds: tuple[float, float, float, float],
    image_size: tuple[int, int],
    *,
    family: str,
) -> tuple[float, float, float, float]:
    left, top, right, bottom = bounds
    width = max(1.0, float(right - left))
    height = max(1.0, float(bottom - top))
    pad_left = width * 0.35
    pad_right = width * 0.35
    pad_top = height * 0.30
    pad_bottom = height * 0.35

    if any(token in family for token in ("ceiling_light", "wall_light", "pendant", "chandelier", "sconce")):
        pad_left = width * 0.55
        pad_right = width * 0.55
        pad_top = max(height * 0.55, image_size[1] * 0.08)
        pad_bottom = height * 1.60
    elif "rug" in family:
        pad_left = width * 0.18
        pad_right = width * 0.18
        pad_top = height * 0.80
        pad_bottom = height * 0.25
    elif any(token in family for token in ("sofa", "chair", "seating", "loveseat")):
        pad_left = width * 0.35
        pad_right = width * 0.35
        pad_top = height * 0.40
        pad_bottom = height * 0.45
    elif any(token in family for token in ("table", "desk", "storage")):
        pad_left = width * 0.32
        pad_right = width * 0.32
        pad_top = height * 0.35
        pad_bottom = height * 0.38

    return _clamp_bounds((left - pad_left, top - pad_top, right + pad_right, bottom + pad_bottom), image_size)


def _fit_bounds_to_detail_ratio(
    bounds: tuple[float, float, float, float],
    image_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    left, top, right, bottom = bounds
    img_w, img_h = image_size
    desired_ratio = 4.0 / 5.0
    crop_w = max(float(right - left), float(DETAIL_CROP_MIN_SOURCE_WIDTH_PX))
    crop_h = max(float(bottom - top), float(DETAIL_CROP_MIN_SOURCE_HEIGHT_PX))
    center_x = (float(left) + float(right)) / 2.0
    center_y = (float(top) + float(bottom)) / 2.0

    if crop_w / crop_h > desired_ratio:
        crop_h = crop_w / desired_ratio
    else:
        crop_w = crop_h * desired_ratio

    if crop_w > float(img_w):
        crop_w = float(img_w)
        crop_h = crop_w / desired_ratio
    if crop_h > float(img_h):
        crop_h = float(img_h)
        crop_w = crop_h * desired_ratio
    if crop_w > float(img_w):
        crop_w = float(img_w)
        crop_h = crop_w / desired_ratio

    return _clamp_bounds(
        (
            center_x - crop_w / 2.0,
            center_y - crop_h / 2.0,
            center_x + crop_w / 2.0,
            center_y + crop_h / 2.0,
        ),
        image_size,
    )


def _predicted_detail_crop_bounds(style: dict) -> tuple[float, float, float, float] | None:
    if not isinstance(style, dict):
        return None
    box_2d = _coerce_box_2d(style.get("target_box_2d") or style.get("box_2d"))
    if box_2d is None:
        return None

    width = DETAIL_SPATIAL_DIVERSITY_CANVAS_WIDTH
    height = DETAIL_SPATIAL_DIVERSITY_CANVAS_HEIGHT
    ymin, xmin, ymax, xmax = box_2d
    raw_bounds = (
        xmin / 1000.0 * width,
        ymin / 1000.0 * height,
        xmax / 1000.0 * width,
        ymax / 1000.0 * height,
    )
    expanded = _expand_style_bounds(raw_bounds, (width, height), family=_style_family(style))
    return _fit_bounds_to_detail_ratio(expanded, (width, height))


def _bounds_iou(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    l1, t1, r1, b1 = left
    l2, t2, r2, b2 = right
    inter_w = max(0.0, min(r1, r2) - max(l1, l2))
    inter_h = max(0.0, min(b1, b2) - max(t1, t2))
    inter = inter_w * inter_h
    if inter <= 0.0:
        return 0.0
    area_left = max(0.0, r1 - l1) * max(0.0, b1 - t1)
    area_right = max(0.0, r2 - l2) * max(0.0, b2 - t2)
    denom = area_left + area_right - inter
    return inter / denom if denom > 0.0 else 0.0


def _style_label(style: dict) -> str:
    raw = style.get("target_label") or ""
    if not raw:
        name = str(style.get("name") or "")
        raw = name.split("Detail:", 1)[1] if name.startswith("Detail:") else name
    return " ".join(str(raw or "").strip().lower().split())


def _is_duplicate_external_detail_style(style: dict, accepted_styles: list[dict]) -> bool:
    if not isinstance(style, dict):
        return False
    target_key = str(style.get("target_key") or "").strip().lower()
    label = _style_label(style)
    family = _style_family(style)
    box = _coerce_box_2d(style.get("target_box_2d") or style.get("box_2d"))
    bounds = _predicted_detail_crop_bounds(style)

    for accepted in accepted_styles or []:
        if not isinstance(accepted, dict):
            continue
        accepted_target_key = str(accepted.get("target_key") or "").strip().lower()
        if target_key and accepted_target_key and target_key == accepted_target_key:
            return True

        accepted_label = _style_label(accepted)
        accepted_family = _style_family(accepted)
        same_label = bool(label and accepted_label and label == accepted_label)
        same_family = bool(family and accepted_family and family == accepted_family)
        if not same_label and not same_family:
            continue

        accepted_box = _coerce_box_2d(accepted.get("target_box_2d") or accepted.get("box_2d"))
        if box is not None and accepted_box is not None and _box_iou(box, accepted_box) >= 0.45:
            return True

        accepted_bounds = _predicted_detail_crop_bounds(accepted)
        if bounds is not None and accepted_bounds is not None and _bounds_iou(bounds, accepted_bounds) >= 0.68:
            return True

    return False


def _dedupe_external_detail_styles(styles: list[dict]) -> list[dict]:
    accepted: list[dict] = []
    delayed_duplicates: list[dict] = []
    for style in styles or []:
        if _is_duplicate_external_detail_style(style, accepted):
            delayed_duplicates.append(style)
        else:
            accepted.append(style)
    return [*accepted, *delayed_duplicates]


def _prioritize_spatially_diverse_styles(styles: list[dict]) -> list[dict]:
    selected: list[dict] = []
    selected_bounds: list[tuple[float, float, float, float]] = []
    delayed: list[dict] = []

    for style in styles:
        bounds = _predicted_detail_crop_bounds(style)
        if bounds is None:
            selected.append(style)
            continue
        overlaps_existing = any(
            _bounds_iou(bounds, existing) >= DETAIL_SPATIAL_DIVERSITY_IOU_THRESHOLD
            for existing in selected_bounds
        )
        if overlaps_existing:
            delayed.append(style)
            continue
        selected.append(style)
        selected_bounds.append(bounds)

    return [*selected, *delayed]


def select_external_detail_styles(dynamic_styles: list[dict], limit: int = EXTERNAL_DETAIL_STYLE_LIMIT) -> list[dict]:
    styles = list(dynamic_styles or [])
    max_count = max(0, int(limit or 0))
    if max_count <= 0:
        return []

    product_backed = [style for style in styles if _is_product_backed_external_style(style)]
    fallback = [style for style in styles if not _is_product_backed_external_style(style)]
    product_backed = _prioritize_spatially_diverse_styles(product_backed)
    return _dedupe_external_detail_styles([*product_backed, *fallback])[:max_count]


def _should_prefer_crop_extract_for_detail(style: dict, *, audience: str) -> bool:
    if not isinstance(style, dict):
        return False
    if not str(style.get("name") or "").startswith("Detail:"):
        return False
    detail_mode = str(style.get("detail_mode") or "").strip().lower()
    box_source = str(style.get("target_box_source") or "").strip().lower()
    if detail_mode == "product_identity_lock" and box_source == "product_reference_localization":
        return True
    return str(audience or "").strip().lower() == "external" and _is_product_backed_external_style(style)


def run_generate_details_job(
    payload: dict,
    *,
    normalize_audience: Callable[[Optional[str]], str],
    build_s3_prefix: Callable[[str, str, str | None], str],
    persist_job_result: Callable[[dict, Optional[str]], None],
    materialize_input: Callable[[str | None, str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    log_section: Callable[[str], None],
    detect_furniture_boxes: Callable[[str], list],
    detect_item_bbox_norm: Callable[..., object] | None = None,
    canonical_category: Callable[[Optional[str]], str],
    build_item_target_key: Callable[..., str],
    max_concurrency_analysis: int,
    analyze_cropped_item: Callable[[str, dict], dict],
    attach_volume_ranks: Callable[[list], list],
    construct_dynamic_styles: Callable[[list], list],
    generate_detail_view: Callable[[str, dict, str, int, list | None], dict | str | None],
    normalize_label_for_match: Callable[[str], str],
    volume_ranking_snapshot: Callable[[list], list],
) -> dict:
    try:
        image_url = payload.get("image_url")
        moodboard_url = payload.get("moodboard_url")
        furniture_data = payload.get("furniture_data")
        audience = payload.get("audience")
        require_details = bool(payload.get("require_details"))
        try:
            requested_detail_target_count = int(payload.get("detail_target_count") or EXTERNAL_DETAIL_STYLE_LIMIT)
        except Exception:
            requested_detail_target_count = EXTERNAL_DETAIL_STYLE_LIMIT
        requested_detail_target_count = max(1, requested_detail_target_count)
        raw_absolute_deadline_ts = payload.get("absolute_deadline_ts")
        raw_minimum_budget_sec = payload.get("minimum_detail_budget_sec")
        try:
            absolute_deadline_ts = float(raw_absolute_deadline_ts) if raw_absolute_deadline_ts is not None else None
        except Exception:
            absolute_deadline_ts = None
        try:
            minimum_detail_budget_sec = max(1.0, float(raw_minimum_budget_sec or 5.0))
        except Exception:
            minimum_detail_budget_sec = 5.0
        budgeted_mode = absolute_deadline_ts is not None and not require_details

        aud = normalize_audience(audience)
        input_furniture_count = len(furniture_data) if isinstance(furniture_data, list) else 0
        print(
            ">> [Detail View] mode "
            f"audience={aud} require_details={require_details} "
            f"budgeted_mode={budgeted_mode} deadline_supplied={absolute_deadline_ts is not None} "
            f"furniture_data_count={input_furniture_count}",
            flush=True,
        )
        prefix_detail_user = build_s3_prefix(aud, "detailrendered", "user-photos")
        prefix_detail_rendered = build_s3_prefix(aud, "detailrendered", "rendered")

        def _remaining_deadline_sec() -> float | None:
            if absolute_deadline_ts is None:
                return None
            try:
                return max(0.0, float(absolute_deadline_ts) - float(time.time()))
            except Exception:
                return 0.0

        def _build_best_effort_output(message: str, analyzed_items: list | None = None) -> dict:
            furniture_boxes = []
            for item in analyzed_items or []:
                if not isinstance(item, dict):
                    continue
                furniture_boxes.append(
                    {
                        "label": item.get("label"),
                        "target_key": item.get("target_key"),
                        "source_index": item.get("source_index"),
                        "category": item.get("category"),
                        "category_canonical": item.get("category_canonical"),
                        "box_2d": item.get("box_2d"),
                        "source_box_2d": item.get("source_box_2d"),
                        "box_source": item.get("box_source"),
                        "crop_path": item.get("crop_path"),
                        "volume_rank": item.get("volume_rank"),
                        "volume_proxy": item.get("volume_proxy"),
                        "volume_rank_basis": item.get("volume_rank_basis"),
                        "category_score": item.get("category_score"),
                    }
                )
            return {
                "details": [],
                "furniture_boxes": furniture_boxes,
                "used_cutout_references": [],
                "volume_ranking": volume_ranking_snapshot(analyzed_items or []),
                "message": message,
            }

        def _budgeted_style_cap(style_count: int, remaining_budget_sec: float | None) -> int:
            if remaining_budget_sec is None:
                return style_count
            try:
                budget = max(0.0, float(remaining_budget_sec))
            except Exception:
                budget = 0.0
            if budget < minimum_detail_budget_sec:
                return 0
            cap = int(budget // 18.0)
            return max(1, min(style_count, max(1, min(DETAIL_GENERATION_BUDGETED_MAX_WORKERS, cap or 1))))

        def _ret(result: dict) -> dict:
            persist_job_result(result, audience=aud)
            return result

        if budgeted_mode:
            remaining_budget = _remaining_deadline_sec()
            if remaining_budget is not None and remaining_budget < minimum_detail_budget_sec:
                return _ret(_build_best_effort_output("Detail generation skipped due to deadline budget exhaustion"))

        local_path = materialize_input(image_url, "detail_src")
        if not local_path or not os.path.exists(local_path):
            return _ret({"error": "Original image not found"})
        resolve_image_url(local_path, prefix_detail_user)

        unique_id = uuid.uuid4().hex[:6]
        log_section(f"[Detail View] REQUEST START ({unique_id}) - Smart Analysis Mode")

        use_product_reference_localization = (
            aud in {"external", "internal"}
            and bool(furniture_data)
            and detect_item_bbox_norm is not None
        )
        analyzed_items = prepare_detail_generation_items(
            furniture_data=furniture_data,
            moodboard_url=moodboard_url,
            local_path=local_path,
            materialize_input=materialize_input,
            detect_furniture_boxes=detect_furniture_boxes,
            detect_item_bbox_norm=detect_item_bbox_norm,
            canonical_category=canonical_category,
            build_item_target_key=build_item_target_key,
            max_concurrency_analysis=max_concurrency_analysis,
            analyze_cropped_item=analyze_cropped_item,
            attach_volume_ranks=attach_volume_ranks,
            normalize_label_for_match=normalize_label_for_match,
            simple_generation_mode=not use_product_reference_localization,
        )
        if budgeted_mode:
            remaining_budget = _remaining_deadline_sec()
            if remaining_budget is not None and remaining_budget < minimum_detail_budget_sec:
                return _ret(
                    _build_best_effort_output(
                        "Detail generation skipped after main render because deadline budget is too low",
                        analyzed_items=analyzed_items,
                    )
                )

        detected_item_count = len(analyzed_items or [])
        dynamic_styles = construct_dynamic_styles(analyzed_items)
        raw_style_count = len(dynamic_styles or [])
        if aud == "internal":
            dynamic_styles = with_internal_angle_styles(dynamic_styles)
        elif aud == "external":
            dynamic_styles = select_external_detail_styles(dynamic_styles, limit=requested_detail_target_count)
        print(
            ">> [Detail View] target counts "
            f"analyzed_items={detected_item_count} raw_styles={raw_style_count} final_styles={len(dynamic_styles or [])}",
            flush=True,
        )
        if not dynamic_styles:
            if budgeted_mode:
                return _ret(_build_best_effort_output("No detail styles available within the remaining deadline budget", analyzed_items=analyzed_items))
            return _ret({"error": "No styles available"})

        generated_paths = []

        def _append_detail_result(index: int, style_payload: dict, result) -> None:
            if not result:
                return
            if isinstance(result, dict):
                generated_paths.append(
                    {
                        "index": index,
                        "path": result.get("path"),
                        "style_name": result.get("style_name") or style_payload.get("name"),
                        "style_ratio": result.get("aspect_ratio") or style_payload.get("ratio"),
                        "style_target_key": style_payload.get("target_key"),
                        "style_target_label": style_payload.get("target_label"),
                        "cutout_ref_count": int(result.get("cutout_ref_count") or 0),
                        "cutout_ref_labels": list(result.get("cutout_ref_labels") or []),
                    }
                )
                return
            generated_paths.append(
                {
                    "index": index,
                    "path": result,
                    "style_name": style_payload.get("name"),
                    "style_ratio": style_payload.get("ratio"),
                    "style_target_key": style_payload.get("target_key"),
                    "style_target_label": style_payload.get("target_label"),
                    "cutout_ref_count": 0,
                    "cutout_ref_labels": [],
                }
            )

        if budgeted_mode:
            dynamic_styles = dynamic_styles[: _budgeted_style_cap(len(dynamic_styles), _remaining_deadline_sec())]
            print(f"?? Generating {len(dynamic_styles)} Budgeted Dynamic Shots...", flush=True)
            if len(dynamic_styles) > 1:
                futures = []
                max_workers = min(DETAIL_GENERATION_BUDGETED_MAX_WORKERS, len(dynamic_styles))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    for index, style in enumerate(dynamic_styles):
                        remaining_budget = _remaining_deadline_sec()
                        if remaining_budget is not None and remaining_budget < minimum_detail_budget_sec:
                            break
                        style_payload = dict(style or {})
                        if remaining_budget is not None:
                            style_payload["timeout_sec"] = max(
                                1.0,
                                min(float(DETAIL_GENERATION_TIMEOUT_CAP_SEC), float(remaining_budget) - 1.0),
                            )
                        futures.append(
                            (
                                index,
                                style_payload,
                                executor.submit(
                                    generate_detail_view,
                                    local_path,
                                    style_payload,
                                    unique_id,
                                    index + 1,
                                    analyzed_items,
                                    prefer_crop_extract=_should_prefer_crop_extract_for_detail(style_payload, audience=aud),
                                ),
                            )
                        )
                    for index, style_payload, future in futures:
                        _append_detail_result(index, style_payload, future.result())
            else:
                for index, style in enumerate(dynamic_styles):
                    remaining_budget = _remaining_deadline_sec()
                    if remaining_budget is not None and remaining_budget < minimum_detail_budget_sec:
                        break
                    style_payload = dict(style or {})
                    if remaining_budget is not None:
                        style_payload["timeout_sec"] = max(
                            1.0,
                            min(float(DETAIL_GENERATION_TIMEOUT_CAP_SEC), float(remaining_budget) - 1.0),
                        )
                    result = generate_detail_view(
                        local_path,
                        style_payload,
                        unique_id,
                        index + 1,
                        analyzed_items,
                        prefer_crop_extract=_should_prefer_crop_extract_for_detail(style_payload, audience=aud),
                    )
                    _append_detail_result(index, style_payload, result)
        else:
            print(f"?? Generating {len(dynamic_styles)} Dynamic Shots...", flush=True)
            with ThreadPoolExecutor(max_workers=min(DETAIL_GENERATION_MAX_WORKERS, len(dynamic_styles))) as executor:
                futures = []
                for index, style in enumerate(dynamic_styles):
                    futures.append(
                        (
                            index,
                            executor.submit(
                                generate_detail_view,
                                local_path,
                                style,
                                unique_id,
                                index + 1,
                                analyzed_items,
                                prefer_crop_extract=_should_prefer_crop_extract_for_detail(style, audience=aud),
                            ),
                        )
                    )
                for index, future in futures:
                    result = future.result()
                    style_payload = dynamic_styles[index] if index < len(dynamic_styles) else {}
                    _append_detail_result(index, style_payload, result)

        print(f"=== [Detail View] complete: {len(generated_paths)} generated ===", flush=True)
        if not generated_paths:
            if budgeted_mode:
                return _ret(
                    _build_best_effort_output(
                        "Detail generation reached the deadline budget before any detail shot completed",
                        analyzed_items=analyzed_items,
                    )
                )
            return _ret({"error": "Failed to generate images"})

        output = build_detail_generation_output(
            analyzed_items=analyzed_items,
            generated_paths=generated_paths,
            materialize_input=materialize_input,
            resolve_image_url=resolve_image_url,
            prefix_detail_user=prefix_detail_user,
            prefix_detail_rendered=prefix_detail_rendered,
            normalize_label_for_match=normalize_label_for_match,
            volume_ranking_snapshot=volume_ranking_snapshot,
        )
        if not output.get("details"):
            if budgeted_mode:
                output = _build_best_effort_output(
                    "Detail generation completed without usable detail shots before the deadline",
                    analyzed_items=analyzed_items,
                )
                return _ret(output)
            return _ret({"error": "Failed to generate images"})

        return _ret(output)
    except Exception as exc:
        print(f"[Detail Error] {exc}", flush=True)
        aud = normalize_audience(payload.get("audience"))
        result = {"error": str(exc)}
        persist_job_result(result, audience=aud)
        return result
