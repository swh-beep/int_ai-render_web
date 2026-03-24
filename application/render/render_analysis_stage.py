import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class RenderAnalysisStageResult:
    windows_present: bool | None = None
    room_analysis_text: str = ""
    furniture_specs_text: str | None = None
    furniture_specs_json: dict | None = None
    full_analyzed_data: list[dict] | None = None
    primary_item: dict | None = None
    scale_guide_path: str | None = None
    size_hierarchy: Any = None


def _build_item_metas(
    *,
    ref_paths: list[str],
    item_refs: list[dict[str, Any]],
    detect_furniture_boxes: Callable[[str], list],
    canonical_category: Callable[[str | None], str],
    build_item_target_key: Callable[..., str],
    log_brief: bool,
) -> list[dict]:
    item_metas = []
    if item_refs:
        if not log_brief:
            print(f">> [Item Analysis] Using direct item references: {len(item_refs)}", flush=True)
        for ridx, meta in enumerate(item_refs, start=1):
            try:
                src_path = meta.get("path")
                if not src_path or not os.path.exists(src_path):
                    continue
                try:
                    qty_val = int(meta.get("qty") or 1)
                except Exception:
                    qty_val = 1
                if qty_val < 1:
                    qty_val = 1

                label_val = meta.get("label") or "Item"
                category_val = meta.get("category")
                item_id_val = meta.get("item_id")
                source_index = int(meta.get("payload_index") or ridx)
                target_key = meta.get("target_key") or build_item_target_key(
                    "cart",
                    source_index,
                    label=label_val,
                    category=category_val,
                    item_id=item_id_val,
                )

                item_metas.append(
                    {
                        "label": label_val,
                        "box_2d": [0, 0, 1000, 1000],
                        "dims_mm": meta.get("dims_mm"),
                        "options": meta.get("options"),
                        "qty": qty_val,
                        "source_path": src_path,
                        "category": category_val,
                        "category_canonical": canonical_category(category_val or label_val),
                        "item_id": item_id_val,
                        "source_index": source_index,
                        "target_key": target_key,
                    }
                )
            except Exception:
                continue
        return item_metas

    detected = []
    for ref_path in ref_paths:
        detected.extend(detect_furniture_boxes(ref_path))
    if not log_brief:
        print(f">> [Item Analysis] Detected {len(detected)} items for split analysis", flush=True)

    for idx, item in enumerate(detected):
        try:
            ref_path = ref_paths[min(idx, len(ref_paths) - 1)]
            label_val = item.get("label") or "Item"
            source_index = idx + 1
            item_metas.append(
                {
                    "label": label_val,
                    "box_2d": item.get("box_2d"),
                    "dims_mm": None,
                    "options": None,
                    "qty": 1,
                    "source_path": ref_path,
                    "category": None,
                    "category_canonical": canonical_category(label_val),
                    "item_id": None,
                    "source_index": source_index,
                    "target_key": build_item_target_key("ref", source_index, label=label_val),
                }
            )
        except Exception:
            continue
    return item_metas


def _normalize_windows_present(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in ("1", "true", "yes", "y")
    return False


def _append_requested_dimensions_if_missing(description: str, req_dims: dict) -> str:
    dim_pairs = []
    width_mm = req_dims.get("width_mm")
    depth_mm = req_dims.get("depth_mm")
    height_mm = req_dims.get("height_mm")
    radius_mm = req_dims.get("radius_mm")
    if (width_mm or 0) > 0:
        dim_pairs.append(f"W {width_mm}mm")
    if (depth_mm or 0) > 0:
        dim_pairs.append(f"D {depth_mm}mm")
    if (height_mm or 0) > 0:
        dim_pairs.append(f"H {height_mm}mm")
    if (radius_mm or 0) > 0:
        dim_pairs.append(f"R {radius_mm}mm")
    if not dim_pairs:
        return description

    desc_text = (description or "").strip()
    has_payload_numbers = all(
        re.search(rf"(?<!\d){re.escape(str(value))}(?!\d)", desc_text)
        for value in [x for x in [width_mm, depth_mm, height_mm, radius_mm] if (x or 0) > 0]
    )
    if has_payload_numbers:
        return description

    if desc_text and not desc_text.endswith((".", "!", "?")):
        desc_text += "."
    return (desc_text + f" It measures {', '.join(dim_pairs)}.").strip()


def _analyze_items(
    *,
    item_metas: list[dict],
    item_refs: list[dict[str, Any]],
    unique_id: str,
    analyze_cropped_item: Callable[..., dict],
    normalize_dims_dict: Callable[[dict], dict],
    canonical_category: Callable[[str | None], str],
    build_item_target_key: Callable[..., str],
    max_concurrency_analysis: int,
    cart_max_analysis_workers: int,
) -> list[dict]:
    full_analyzed_data: list[dict] = []
    if not item_metas:
        return full_analyzed_data

    is_cart_mode = bool(item_refs)
    ocr_text_read_enabled = not is_cart_mode
    if is_cart_mode:
        analysis_workers = min(
            max_concurrency_analysis,
            cart_max_analysis_workers,
            max(1, len(item_metas)),
        )
    else:
        analysis_workers = min(max_concurrency_analysis, max(1, len(item_metas)))

    results = [None] * len(item_metas)
    with ThreadPoolExecutor(max_workers=analysis_workers) as executor:
        futures = []
        for index, meta in enumerate(item_metas):
            item_data = {
                "label": meta.get("label"),
                "box_2d": meta.get("box_2d"),
                "target_key": meta.get("target_key"),
                "source_index": meta.get("source_index"),
                "category": meta.get("category"),
                "category_canonical": meta.get("category_canonical"),
                "item_id": meta.get("item_id"),
            }
            futures.append(
                (
                    index,
                    executor.submit(
                        analyze_cropped_item,
                        meta.get("source_path"),
                        item_data,
                        unique_id,
                        index + 1,
                        True,
                        ocr_text_read_enabled,
                        meta.get("dims_mm"),
                    ),
                )
            )
        for index, future in futures:
            try:
                results[index] = future.result()
            except Exception:
                results[index] = None

    for idx, meta in enumerate(item_metas):
        res_item = results[idx] if isinstance(results[idx], dict) else {}
        label = meta.get("label") or res_item.get("label") or f"Item{idx+1}"
        desc = (res_item.get("description") if isinstance(res_item, dict) else None) or f"A high quality {label}."
        req_dims = normalize_dims_dict(meta.get("dims_mm") or {})
        opts = meta.get("options")
        qty = meta.get("qty") or 1

        if is_cart_mode and req_dims:
            desc = _append_requested_dimensions_if_missing(desc, req_dims)

        extra_lines = []
        if qty and qty > 1:
            extra_lines.append(f"Quantity: {qty}")
        if req_dims and not is_cart_mode:
            extra_lines.append(
                f"Requested size: W={req_dims.get('width_mm') or 'null'} "
                f"D={req_dims.get('depth_mm') or 'null'} "
                f"H={req_dims.get('height_mm') or 'null'} mm."
            )
        if isinstance(opts, dict) and opts:
            try:
                extra_lines.append("Options: " + json.dumps(opts, ensure_ascii=False))
            except Exception:
                pass
        elif isinstance(opts, list) and opts:
            try:
                extra_lines.append("Options: " + json.dumps(opts, ensure_ascii=False))
            except Exception:
                pass
        elif isinstance(opts, str) and opts.strip():
            extra_lines.append("Options: " + opts.strip())

        full_desc = (desc + (" " + " ".join(extra_lines) if extra_lines else "")).strip()
        source_index = int(meta.get("source_index") or (idx + 1))
        category_val = meta.get("category") or res_item.get("category")
        category_canonical_val = (
            meta.get("category_canonical")
            or res_item.get("category_canonical")
            or canonical_category(category_val or label)
        )
        target_key = (
            meta.get("target_key")
            or res_item.get("target_key")
            or build_item_target_key(
                "item",
                source_index,
                label=label,
                category=category_val,
                item_id=(meta.get("item_id") or res_item.get("item_id")),
            )
        )

        full_analyzed_data.append(
            {
                "label": label,
                "description": full_desc,
                "box_2d": meta.get("box_2d") or res_item.get("box_2d") or [0, 0, 1000, 1000],
                "crop_path": res_item.get("crop_path"),
                "options": opts,
                "qty": qty,
                "requested_dims_mm": req_dims or None,
                "source_index": source_index,
                "target_key": target_key,
                "category": category_val,
                "category_canonical": category_canonical_val,
                "item_id": meta.get("item_id") or res_item.get("item_id"),
            }
        )

    return full_analyzed_data


def _log_analyzed_items(
    *,
    full_analyzed_data: list[dict],
    log_brief: bool,
    logger,
    parse_object_dimensions_mm: Callable[[str], dict],
) -> None:
    try:
        if full_analyzed_data and not log_brief:
            logger.info(f"[Analysis] items={len(full_analyzed_data)}")
            for index, item in enumerate(full_analyzed_data[:30]):
                dims = parse_object_dimensions_mm(item.get("description", ""))
                logger.info(
                    f"[Analysis] #{index} {item.get('label')} "
                    f"dims(mm) W={dims.get('width_mm')} D={dims.get('depth_mm')} H={dims.get('height_mm')} "
                    f"crop={item.get('crop_path')} "
                    f"desc={ (item.get('description','')[:120]).replace(chr(10),' ') }"
                )
    except Exception:
        logger.exception("[Analysis] logging failed")


def _build_specs_bundle(
    *,
    full_analyzed_data: list[dict],
    build_furniture_specs_json: Callable[[list], dict],
    enable_scale_guidance: bool,
    logger,
    room_dims_parsed: dict,
    create_scale_guide_overlay_with_model: Callable[..., str | None],
    match_aspect_to_target: Callable[[str, str], str | None],
    summary: dict,
    step1_raw: str | None,
    step1_img: str,
    unique_id: str,
) -> tuple[str | None, dict | None, dict | None, str | None, Any]:
    furniture_specs_text = None
    furniture_specs_json = None
    primary_item = None
    scale_guide_path = None
    size_hierarchy = None

    specs_list = []
    for index, item in enumerate(full_analyzed_data):
        qty = item.get("qty") or 1
        qty_text = f" (qty={qty})" if qty and qty > 1 else ""
        specs_list.append(f"{index+1}. {item['label']}{qty_text}: {item['description']}")
    furniture_specs_text = "\n".join(specs_list)

    try:
        furniture_specs_json = build_furniture_specs_json(full_analyzed_data)
        primary_item = (
            (furniture_specs_json or {}).get("primary_scale")
            or (furniture_specs_json or {}).get("primary")
        )
        size_hierarchy = (
            (furniture_specs_json or {}).get("size_hierarchy_scale")
            or (furniture_specs_json or {}).get("size_hierarchy")
        )

        if enable_scale_guidance:
            logger.info(f"[Scale] primary_item={ (primary_item or {}).get('label') }")
            logger.info(f"[Scale] room_dims_parsed={room_dims_parsed}")
            try:
                guide_path = os.path.join("outputs", f"scale_guide_{unique_id}.png")
                scale_guide_path = create_scale_guide_overlay_with_model(
                    step1_raw or step1_img,
                    guide_path,
                    room_dims=room_dims_parsed,
                )
                if scale_guide_path and step1_img:
                    scale_guide_path = match_aspect_to_target(scale_guide_path, step1_img)
                if not scale_guide_path:
                    summary["scale_guide_skipped"] = summary.get("scale_guide_skipped", 0) + 1
            except Exception as exc:
                logger.exception(f"[Scale] scale guide exception: {exc}")
                summary["scale_guide_skipped"] = summary.get("scale_guide_skipped", 0) + 1
        else:
            scale_guide_path = None
    except Exception as exc:
        logger.exception(f"[Scale] furniture JSON build failed: {exc}")
        furniture_specs_json = None
        primary_item = None
        scale_guide_path = None
        size_hierarchy = None

    return furniture_specs_text, furniture_specs_json, primary_item, scale_guide_path, size_hierarchy


def run_render_analysis_stage(
    *,
    ref_paths: list[str],
    item_refs: list[dict[str, Any]],
    step1_img: str,
    step1_raw: str | None,
    dimensions: str,
    unique_id: str,
    detect_furniture_boxes: Callable[[str], list],
    canonical_category: Callable[[str | None], str],
    build_item_target_key: Callable[..., str],
    analyze_room_structure: Callable[..., dict],
    analyze_cropped_item: Callable[..., dict],
    normalize_dims_dict: Callable[[dict], dict],
    parse_object_dimensions_mm: Callable[[str], dict],
    build_furniture_specs_json: Callable[[list], dict],
    create_scale_guide_overlay_with_model: Callable[..., str | None],
    match_aspect_to_target: Callable[[str, str], str | None],
    enable_scale_guidance: bool,
    room_dims_parsed: dict,
    summary: dict,
    logger,
    log_brief: bool,
    max_concurrency_analysis: int,
    cart_max_analysis_workers: int,
) -> RenderAnalysisStageResult:
    result = RenderAnalysisStageResult(full_analyzed_data=[])
    if not (ref_paths or item_refs):
        return result

    if not log_brief:
        print(">> [Split Analysis] Room + Items (separate calls)...", flush=True)

    try:
        item_metas = _build_item_metas(
            ref_paths=ref_paths,
            item_refs=item_refs,
            detect_furniture_boxes=detect_furniture_boxes,
            canonical_category=canonical_category,
            build_item_target_key=build_item_target_key,
            log_brief=log_brief,
        )

        room_result = analyze_room_structure(step1_img, room_dimensions=dimensions, timeout=120)
        result.room_analysis_text = (room_result.get("room_text") or "").strip()
        result.windows_present = _normalize_windows_present(room_result.get("windows_present"))

        result.full_analyzed_data = _analyze_items(
            item_metas=item_metas,
            item_refs=item_refs,
            unique_id=unique_id,
            analyze_cropped_item=analyze_cropped_item,
            normalize_dims_dict=normalize_dims_dict,
            canonical_category=canonical_category,
            build_item_target_key=build_item_target_key,
            max_concurrency_analysis=max_concurrency_analysis,
            cart_max_analysis_workers=cart_max_analysis_workers,
        )

        _log_analyzed_items(
            full_analyzed_data=result.full_analyzed_data,
            log_brief=log_brief,
            logger=logger,
            parse_object_dimensions_mm=parse_object_dimensions_mm,
        )

        (
            result.furniture_specs_text,
            result.furniture_specs_json,
            result.primary_item,
            result.scale_guide_path,
            result.size_hierarchy,
        ) = _build_specs_bundle(
            full_analyzed_data=result.full_analyzed_data,
            build_furniture_specs_json=build_furniture_specs_json,
            enable_scale_guidance=enable_scale_guidance,
            logger=logger,
            room_dims_parsed=room_dims_parsed,
            create_scale_guide_overlay_with_model=create_scale_guide_overlay_with_model,
            match_aspect_to_target=match_aspect_to_target,
            summary=summary,
            step1_raw=step1_raw,
            step1_img=step1_img,
            unique_id=unique_id,
        )

        print(">> [Split Analysis] Complete. Specs injected.", flush=True)
    except Exception as exc:
        print(f"!! [Split Analysis Failed] {exc}", flush=True)

    return result
