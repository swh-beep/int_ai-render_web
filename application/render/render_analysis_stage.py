import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from application.render.postprocess_support import category_match_family, resolve_item_canonical_category, resolve_item_family
from application.render.scale_plan_support import build_scale_plan


_CATEGORY_METADATA_FIELDS = (
    "category_path",
    "category_source",
    "main_category",
    "sub_category",
    "mainCategory",
    "subCategory",
    "product_type",
)


@dataclass
class RenderAnalysisStageResult:
    windows_present: bool | None = None
    room_analysis_text: str = ""
    room_planes: dict | None = None
    wall_span_norm: tuple[float, float] = (0.0, 1.0)
    estimated_room_dims: dict | None = None
    furniture_specs_text: str | None = None
    furniture_specs_json: dict | None = None
    full_analyzed_data: list[dict] | None = None
    primary_item: dict | None = None
    scale_guide_path: str | None = None
    size_hierarchy: Any = None
    strict_scale_requested: bool = False
    strict_scale_ready: bool = False
    scale_plan: dict | None = None


_MATERIAL_CUE_KEYWORDS = (
    "wood",
    "walnut",
    "oak",
    "marble",
    "stone",
    "glass",
    "metal",
    "chrome",
    "steel",
    "fabric",
    "linen",
    "boucle",
    "leather",
    "rattan",
    "mirror",
    "reflective",
    "wooden",
    "유리",
    "금속",
    "패브릭",
    "가죽",
    "원목",
    "거울",
)

_SHAPE_CUE_KEYWORDS = (
    "round",
    "circular",
    "oval",
    "square",
    "rectangular",
    "triangular",
    "arched",
    "curved",
    "modular",
    "low-profile",
    "floor-grazing",
    "wall-mounted",
    "teardrop",
    "biomorphic",
    "pedestal",
    "spindle",
    "flaring",
    "blocky",
    "slim",
    "thin",
    "라운드",
    "원형",
    "사각",
    "삼각",
    "곡선",
    "벽걸이",
)


def _extract_keyword_cues(text: str, keywords: tuple[str, ...], limit: int = 4) -> list[str]:
    normalized = str(text or "").lower()
    cues: list[str] = []
    for keyword in keywords:
        if keyword in normalized and keyword not in cues:
            cues.append(keyword)
        if len(cues) >= limit:
            break
    return cues


def _merge_unique_str_lists(*values: list[str], limit: int = 6) -> list[str]:
    merged: list[str] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for raw in value:
            text = str(raw or "").strip()
            if not text or text in merged:
                continue
            merged.append(text)
            if len(merged) >= limit:
                return merged
    return merged


def _options_reference_features(options: Any) -> dict:
    if not isinstance(options, dict):
        return {}
    features = options.get("reference_features")
    return dict(features) if isinstance(features, dict) else {}


def _merge_options_reference_features(reference_features: dict | None, options: Any) -> dict:
    provided = _options_reference_features(options)
    if not provided:
        return reference_features if isinstance(reference_features, dict) else {}
    merged = dict(reference_features or {})
    for key in ("silhouette_cues", "material_cues", "distinctive_parts", "preserve_rules", "color_cues"):
        merged[key] = _merge_unique_str_lists(provided.get(key) or [], merged.get(key) or [], limit=8)
    if provided.get("reflective_surface") is not None:
        merged["reflective_surface"] = provided.get("reflective_surface")
    elif "reflective_surface" not in merged:
        merged["reflective_surface"] = False
    merged["options_reference_features_applied"] = True
    return merged


def _expected_placement_family(family: str) -> str:
    normalized = str(family or "").strip().lower()
    if normalized in {"mirror", "wall_light"}:
        return "wall_attached"
    if normalized == "ceiling_light":
        return "ceiling_attached"
    if normalized == "rug":
        return "rug"
    if normalized in {"table_lamp", "decor"}:
        return "surface_placed"
    return "floor_placed"


def _build_layout_envelope(*, dims_mm: dict, room_dims_parsed: dict, family: str) -> dict | None:
    if not isinstance(dims_mm, dict):
        return None
    try:
        width_mm = int(dims_mm.get("width_mm") or 0)
        depth_mm = int(dims_mm.get("depth_mm") or 0)
        height_mm = int(dims_mm.get("height_mm") or 0)
    except Exception:
        return None

    if width_mm <= 0 or depth_mm <= 0 or height_mm <= 0:
        return None

    try:
        room_width_mm = int((room_dims_parsed or {}).get("width_mm") or 0)
        room_depth_mm = int((room_dims_parsed or {}).get("depth_mm") or 0)
        room_height_mm = int((room_dims_parsed or {}).get("height_mm") or 0)
    except Exception:
        room_width_mm = 0
        room_depth_mm = 0
        room_height_mm = 0

    return {
        "room_width_ratio": round(width_mm / room_width_mm, 4) if room_width_mm > 0 else None,
        "room_depth_ratio": round(depth_mm / room_depth_mm, 4) if room_depth_mm > 0 else None,
        "room_height_ratio": round(height_mm / room_height_mm, 4) if room_height_mm > 0 else None,
        "footprint_ratio": round((width_mm * depth_mm) / max(1, room_width_mm * room_depth_mm), 4)
        if room_width_mm > 0 and room_depth_mm > 0
        else None,
        "placement_family": _expected_placement_family(family),
    }


def _absolute_size_class(*, family: str, dims_mm: dict) -> str | None:
    if not isinstance(dims_mm, dict):
        return None
    try:
        width_mm = int(dims_mm.get("width_mm") or 0)
        depth_mm = int(dims_mm.get("depth_mm") or 0)
        height_mm = int(dims_mm.get("height_mm") or 0)
        radius_mm = int(dims_mm.get("radius_mm") or 0)
    except Exception:
        return None
    max_dim = max(width_mm, depth_mm, height_mm, radius_mm)
    footprint_max = max(width_mm, depth_mm)
    normalized_family = str(family or "").strip().lower()
    if max_dim <= 0:
        return None
    if normalized_family == "rug":
        if footprint_max <= 1200:
            return "small"
        if footprint_max <= 2000:
            return "medium"
        if footprint_max <= 3000:
            return "large"
        return "extra-large"
    if normalized_family in {"table_lamp", "decor"}:
        if max_dim <= 250:
            return "tiny"
        if max_dim <= 450:
            return "small"
        if max_dim <= 800:
            return "medium"
        return "large"
    if max_dim <= 250:
        return "tiny"
    if max_dim <= 700:
        return "small"
    if max_dim <= 1400:
        return "medium"
    if max_dim <= 2400:
        return "large"
    return "extra-large"


def _room_presence_class(layout_envelope: dict | None) -> str | None:
    if not isinstance(layout_envelope, dict):
        return None
    try:
        room_width_ratio = float(layout_envelope.get("room_width_ratio") or 0.0)
        room_depth_ratio = float(layout_envelope.get("room_depth_ratio") or 0.0)
        footprint_ratio = float(layout_envelope.get("footprint_ratio") or 0.0)
    except Exception:
        return None
    if max(room_width_ratio, room_depth_ratio, footprint_ratio) <= 0.0:
        return None
    if max(room_width_ratio, room_depth_ratio) <= 0.12 and footprint_ratio <= 0.02:
        return "tiny-room-presence"
    if max(room_width_ratio, room_depth_ratio) <= 0.22 and footprint_ratio <= 0.05:
        return "small-room-presence"
    if max(room_width_ratio, room_depth_ratio) <= 0.38 and footprint_ratio <= 0.14:
        return "medium-room-presence"
    if max(room_width_ratio, room_depth_ratio) <= 0.58 and footprint_ratio <= 0.3:
        return "large-room-presence"
    return "anchor-room-presence"


def _build_identity_profile(
    *,
    label: str,
    description: str,
    category: str | None,
    category_canonical: str,
    category_metadata: dict | None = None,
    dims_mm: dict,
    crop_path: str | None,
    target_key: str,
    source_index: int,
    room_dims_parsed: dict,
    reference_features: dict | None = None,
) -> dict:
    category_metadata = dict(category_metadata or {})
    family = resolve_item_family(
        {
            "label": label,
            "category": category,
            "category_canonical": category_canonical,
            "reference_features": reference_features,
            **category_metadata,
        }
    )
    category_canonical = resolve_item_canonical_category(
        {
            "label": label,
            "category": category,
            "category_canonical": category_canonical,
            "reference_features": reference_features,
            **category_metadata,
        },
        default=category_canonical,
    )
    text_blob = " ".join([str(label or ""), str(category or ""), str(description or "")]).strip()
    ref = reference_features if isinstance(reference_features, dict) else {}
    material_cues = _merge_unique_str_lists(
        ref.get("material_cues") or [],
        _extract_keyword_cues(text_blob, _MATERIAL_CUE_KEYWORDS),
    )
    shape_cues = _merge_unique_str_lists(
        ref.get("silhouette_cues") or [],
        _extract_keyword_cues(text_blob, _SHAPE_CUE_KEYWORDS),
    )
    distinctive_parts = _merge_unique_str_lists(ref.get("distinctive_parts") or [])
    preserve_rules = _merge_unique_str_lists(ref.get("preserve_rules") or [], distinctive_parts)
    reflective_surface = family == "mirror" or any(token in material_cues for token in ("mirror", "reflective", "glass", "chrome", "거울", "유리"))
    reflective_flag = ref.get("reflective_surface")
    if isinstance(reflective_flag, str):
        normalized_flag = reflective_flag.strip().lower()
        if normalized_flag in {"true", "1", "yes"}:
            reflective_flag = True
        elif normalized_flag in {"false", "0", "no"}:
            reflective_flag = False
        else:
            reflective_flag = False
    elif not isinstance(reflective_flag, bool):
        reflective_flag = False
    reflective_surface = bool(reflective_flag) or family == "mirror" or any(
        token in material_cues for token in ("mirror", "reflective", "glass", "chrome")
    )
    expected_placement_family = _expected_placement_family(family)
    wall_attached = expected_placement_family == "wall_attached"
    ceiling_attached = expected_placement_family == "ceiling_attached"
    floor_contact = expected_placement_family in {"floor_placed", "rug"}

    silhouette_summary = ", ".join(shape_cues[:3]) if shape_cues else (family or category_canonical or "generic")
    layout_envelope = _build_layout_envelope(
        dims_mm=dims_mm or {},
        room_dims_parsed=room_dims_parsed or {},
        family=family,
    )

    return {
        "target_key": target_key,
        "source_index": source_index,
        "name": label,
        "category": category,
        "category_canonical": category_canonical,
        "family": family,
        "dims_mm": dict(dims_mm or {}),
        "crop_path": crop_path,
        "shape_cues": shape_cues,
        "material_cues": material_cues,
        "distinctive_parts": distinctive_parts,
        "preserve_rules": preserve_rules,
        "silhouette_summary": silhouette_summary,
        "reflective_surface": reflective_surface,
        "wall_attached_expected": wall_attached,
        "ceiling_attached_expected": ceiling_attached,
        "floor_contact_expected": floor_contact,
        "layout_envelope": layout_envelope,
        "absolute_size_class": _absolute_size_class(family=family, dims_mm=dims_mm or {}),
        "room_presence_class": _room_presence_class(layout_envelope),
    }


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
                        **{
                            field: meta.get(field)
                            for field in _CATEGORY_METADATA_FIELDS
                            if meta.get(field) not in (None, "")
                        },
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


def _normalize_room_dimension_axis(raw_value: Any, *, step_mm: int) -> int | None:
    try:
        value = int(raw_value or 0)
    except Exception:
        return None
    if value <= 0:
        return None
    return int(step_mm * ((value + (step_mm // 2)) // step_mm))


def _normalize_estimated_room_dims(raw_value: Any) -> dict | None:
    if not isinstance(raw_value, dict):
        return None
    normalized = {
        "width_mm": _normalize_room_dimension_axis(raw_value.get("width_mm"), step_mm=500),
        "depth_mm": _normalize_room_dimension_axis(raw_value.get("depth_mm"), step_mm=500),
        "height_mm": _normalize_room_dimension_axis(raw_value.get("height_mm"), step_mm=100),
    }
    if not any(normalized.values()):
        return None
    return normalized


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


def _description_is_generic(description: str, label: str) -> bool:
    normalized = str(description or "").strip().lower()
    label_normalized = str(label or "").strip().lower()
    if not normalized:
        return True
    if normalized.startswith("a high quality ") or normalized == label_normalized or normalized == f"{label_normalized}.":
        return True
    word_count = len(re.findall(r"[A-Za-z0-9가-힣]+", normalized))
    if word_count < 12:
        return True
    return False


def _requested_size_hint(req_dims: dict, category: str | None) -> str:
    dims = req_dims if isinstance(req_dims, dict) else {}
    try:
        max_dim = max(
            int(dims.get("width_mm") or 0),
            int(dims.get("depth_mm") or 0),
            int(dims.get("height_mm") or 0),
            int(dims.get("radius_mm") or 0),
        )
    except Exception:
        max_dim = 0
    family = category_match_family(category) or str(category or "").strip().lower()
    if family == "rug":
        if max_dim <= 1200:
            return "a compact rug accent"
        if max_dim <= 2000:
            return "a mid-scale rug"
        return "a large rug anchor"
    if family in {"table_lamp", "decor"}:
        if max_dim <= 250:
            return "a tiny surface object"
        if max_dim <= 450:
            return "a compact tabletop object"
        return "a substantial but still secondary tabletop object"
    if max_dim <= 250:
        return "a tiny accessory-scale piece"
    if max_dim <= 700:
        return "a compact accent piece"
    if max_dim <= 1400:
        return "a standard human-scale furniture piece"
    if max_dim <= 2400:
        return "a large anchor furniture piece"
    if max_dim > 0:
        return "an oversized anchor furniture piece"
    return "the original product scale"


def _stabilize_requested_item_description(*, label: str, category: str | None, description: str, req_dims: dict) -> str:
    base = str(description or "").strip()
    if _description_is_generic(base, label):
        size_hint = _requested_size_hint(req_dims, category)
        base = (
            f"{label} should preserve its original silhouette, material identity, and support geometry. "
            f"Treat it as {size_hint} rather than a generic substitute."
        )
    return _append_requested_dimensions_if_missing(base, req_dims)


def _description_is_generic(description: str, label: str) -> bool:
    normalized = str(description or "").strip().lower()
    label_normalized = str(label or "").strip().lower()
    if not normalized:
        return True
    if normalized.startswith("a high quality ") or normalized == label_normalized or normalized == f"{label_normalized}.":
        return True
    word_count = len(re.findall(r"[A-Za-z0-9_]+", normalized))
    return word_count < 12


def _analyze_items(
    *,
    item_metas: list[dict],
    item_refs: list[dict[str, Any]],
    unique_id: str,
    analyze_cropped_item: Callable[..., dict],
    normalize_dims_dict: Callable[[dict], dict],
    canonical_category: Callable[[str | None], str],
    build_item_target_key: Callable[..., str],
    room_dims_parsed: dict,
    max_concurrency_analysis: int,
    cart_max_analysis_workers: int,
    absolute_deadline_ts: float | None = None,
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
                **{
                    field: meta.get(field)
                    for field in _CATEGORY_METADATA_FIELDS
                    if meta.get(field) not in (None, "")
                },
            }
            futures.append(
                (
                    index,
                    executor.submit(
                        analyze_cropped_item,
                        meta.get("source_path"),
                        item_data,
                        unique_id=unique_id,
                        item_index=index + 1,
                        save_crop=True,
                        enable_text_read=ocr_text_read_enabled,
                        allow_reference_feature_model=is_cart_mode,
                        provided_dims_mm=meta.get("dims_mm"),
                        absolute_deadline_ts=absolute_deadline_ts,
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
        category_val = meta.get("category") or res_item.get("category")
        desc = (res_item.get("description") if isinstance(res_item, dict) else None) or f"{label} with its original identity preserved."
        req_dims = normalize_dims_dict(meta.get("dims_mm") or {})
        opts = meta.get("options")
        qty = meta.get("qty") or 1

        if req_dims:
            desc = _stabilize_requested_item_description(
                label=label,
                category=category_val,
                description=desc,
                req_dims=req_dims,
            )

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
        reference_features = res_item.get("reference_features")
        if not isinstance(reference_features, dict):
            reference_features = {}
        reference_features = _merge_options_reference_features(reference_features, opts)
        category_metadata = {
            field: (meta.get(field) if meta.get(field) not in (None, "") else res_item.get(field))
            for field in _CATEGORY_METADATA_FIELDS
            if (meta.get(field) if meta.get(field) not in (None, "") else res_item.get(field)) not in (None, "")
        }
        category_canonical_val = resolve_item_canonical_category(
            {
                "label": label,
                "category": category_val,
                "category_canonical": category_canonical_val,
                "reference_features": reference_features,
                **category_metadata,
            },
            default=category_canonical_val,
        )
        identity_profile = _build_identity_profile(
            label=label,
            description=full_desc,
            category=category_val,
            category_canonical=category_canonical_val,
            category_metadata=category_metadata,
            dims_mm=req_dims or {},
            crop_path=res_item.get("crop_path"),
            target_key=target_key,
            source_index=source_index,
            room_dims_parsed=room_dims_parsed or {},
            reference_features=reference_features,
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
                "reference_features": reference_features,
                "identity_profile": identity_profile,
                "layout_envelope": identity_profile.get("layout_envelope"),
                **category_metadata,
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
    strict_scale_requested: bool,
    room_dims_parsed: dict,
    summary: dict,
    logger,
    log_brief: bool,
    max_concurrency_analysis: int,
    cart_max_analysis_workers: int,
    absolute_deadline_ts: float | None = None,
) -> RenderAnalysisStageResult:
    result = RenderAnalysisStageResult(full_analyzed_data=[])
    if not (ref_paths or item_refs):
        return result

    if not log_brief:
        print(">> [Split Analysis] Room + Items (separate calls)...", flush=True)

    try:
        def _bounded_timeout(requested_sec: float, *, minimum_sec: float) -> int | None:
            if absolute_deadline_ts is None:
                return int(requested_sec)
            try:
                remaining = max(0.0, float(absolute_deadline_ts) - float(time.time()))
            except Exception:
                remaining = 0.0
            timeout_sec = min(float(requested_sec), remaining)
            if timeout_sec <= minimum_sec:
                return None
            return int(max(float(minimum_sec), timeout_sec))

        item_metas = _build_item_metas(
            ref_paths=ref_paths,
            item_refs=item_refs,
            detect_furniture_boxes=detect_furniture_boxes,
            canonical_category=canonical_category,
            build_item_target_key=build_item_target_key,
            log_brief=log_brief,
        )

        room_timeout = _bounded_timeout(45.0, minimum_sec=12.0)
        room_result = {}
        if room_timeout is not None:
            room_result = analyze_room_structure(
                step1_img,
                room_dimensions=dimensions,
                timeout=room_timeout,
                max_attempts=1,
            )
        result.room_analysis_text = (room_result.get("room_text") or "").strip()
        result.windows_present = _normalize_windows_present(room_result.get("windows_present"))
        result.estimated_room_dims = _normalize_estimated_room_dims(room_result.get("estimated_dimensions_mm"))
        room_planes = room_result.get("room_planes")
        if isinstance(room_planes, dict):
            result.room_planes = dict(room_planes)
        wall_span_norm = room_result.get("wall_span_norm")
        if isinstance(wall_span_norm, (tuple, list)) and len(wall_span_norm) >= 2:
            try:
                result.wall_span_norm = (float(wall_span_norm[0]), float(wall_span_norm[1]))
            except Exception:
                pass

        result.full_analyzed_data = _analyze_items(
            item_metas=item_metas,
            item_refs=item_refs,
            unique_id=unique_id,
            analyze_cropped_item=analyze_cropped_item,
            normalize_dims_dict=normalize_dims_dict,
            canonical_category=canonical_category,
            build_item_target_key=build_item_target_key,
            room_dims_parsed=room_dims_parsed,
            max_concurrency_analysis=max_concurrency_analysis,
            cart_max_analysis_workers=cart_max_analysis_workers,
            absolute_deadline_ts=absolute_deadline_ts,
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

        result.strict_scale_requested = bool(strict_scale_requested)
        result.scale_plan = build_scale_plan(
            items=result.full_analyzed_data,
            room_dims_parsed=room_dims_parsed,
            room_planes=result.room_planes,
            wall_span_norm=result.wall_span_norm,
            primary_item=result.primary_item,
            strict_scale_requested=bool(strict_scale_requested),
        )
        result.strict_scale_ready = bool((result.scale_plan or {}).get("strict_scale_ready"))

        print(">> [Split Analysis] Complete. Specs injected.", flush=True)
    except Exception as exc:
        print(f"!! [Split Analysis Failed] {exc}", flush=True)

    return result
