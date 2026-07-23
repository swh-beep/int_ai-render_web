import re
import json
import os
import time
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont, ImageOps
from application.render.placement_support import build_placement_prompt_block
from application.render.postprocess_support import decor_prefers_surface_placement
from shared.image_canvas import (
    get_image_size,
    image_matches_ratio,
    match_aspect_to_ratio,
    match_aspect_to_target as default_match_aspect_to_target,
)


_PLACEMENT_FAILED_RULE_IDS = {"wall_attached_floor_collision", "rug_floating_above_floor_zone", "floor_item_floating"}
_FIDELITY_FAILED_RULE_IDS = {"mirror_reflection_drift"}
_FIDELITY_RULE_KINDS = {
    "reference_shape_drift",
    "reference_material_drift",
    "reference_integration_drift",
    "reference_review_unresolved",
    "reflection_violation",
}
_DIRECT_CUTOUT_REFERENCE_LIMIT = 12
_GROUPED_SMALL_ITEM_SHEET_LIMIT = 8
_GROUPED_SMALL_ITEM_SHEET_SIZE = 2048
_GROUPED_SMALL_ITEM_CATEGORY_KEYWORDS = {
    "소품",
    "decor",
    "decoration",
    "decorative",
    "accessory",
    "object",
    "vase",
    "plant",
    "art",
    "frame",
    "wall_art",
    "wall_decor",
    "장식",
    "오브제",
    "화병",
    "식물",
    "액자",
    "아트",
}
_GEOMETRY_RULE_KINDS = {"scale_fit_violation", "validation_exception", "low_confidence_match"}


def _extract_failed_rule_ids(issues: list[str] | tuple | set | None) -> list[str]:
    rule_ids: list[str] = []
    for issue in issues or []:
        text = str(issue or "").strip()
        if not text:
            continue
        if text.startswith("rule_id:"):
            candidate = text[len("rule_id:") :].strip().split()[0].strip(",;:")
            if candidate:
                rule_ids.append(candidate)
            continue
        if ":" in text:
            candidate = text.split(":", 1)[0].strip()
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", candidate):
                rule_ids.append(candidate)
                continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", text):
            rule_ids.append(text)
    return rule_ids


def _merge_rule_ids(existing: list[str], new_rule_ids: list[str]) -> list[str]:
    merged = list(existing or [])
    for rule_id in new_rule_ids or []:
        if rule_id and rule_id not in merged:
            merged.append(rule_id)
    return merged


def _review_bucket_counts(failed_rules: list[str] | None) -> dict[str, int]:
    rules = [str(rule or "").strip() for rule in (failed_rules or []) if str(rule or "").strip()]
    return {
        "fidelity_fail_count": sum(1 for rule in rules if rule.startswith("reference_") or rule in _FIDELITY_FAILED_RULE_IDS),
        "placement_fail_count": sum(1 for rule in rules if rule in _PLACEMENT_FAILED_RULE_IDS),
        "geometry_fail_count": sum(
            1
            for rule in rules
            if not rule.startswith("reference_")
            and rule not in _FIDELITY_FAILED_RULE_IDS
            and rule not in _PLACEMENT_FAILED_RULE_IDS
        ),
    }


def _weighted_issue_score(issue_records: list[dict] | None) -> float:
    total = 0.0
    for row in issue_records or []:
        if not isinstance(row, dict):
            continue
        try:
            total += float(row.get("weighted_score") or 0.0)
        except Exception:
            continue
    return round(total, 4)


def _normalize_render_candidate_aspect(
    image_path: str,
    room_path: str,
    *,
    expected_ratio: float,
    ratio_tol: float,
    match_aspect_to_target: Callable[[str, str], str | None],
    log_brief: bool,
    max_crop_fraction: float = 0.20,
) -> str | None:
    try:
        if image_matches_ratio(image_path, expected_ratio, ratio_tol):
            return image_path

        width, height = get_image_size(image_path, exif_safe=True)
        if height <= 0:
            return None
        normalized_path = image_path
        current_ratio = width / height
        if match_aspect_to_target is not None and match_aspect_to_target is not default_match_aspect_to_target:
            normalized_path = match_aspect_to_target(image_path, room_path)
            if image_matches_ratio(normalized_path, expected_ratio, ratio_tol):
                return normalized_path
        if abs(current_ratio - expected_ratio) > ratio_tol:
            if current_ratio > expected_ratio:
                retained_fraction = expected_ratio / current_ratio if current_ratio > 0 else 0.0
            else:
                retained_fraction = current_ratio / expected_ratio if expected_ratio > 0 else 0.0
            crop_fraction = max(0.0, 1.0 - retained_fraction)
            if crop_fraction > max_crop_fraction:
                if log_brief:
                    print(
                        f"[RatioCheck] FAIL {width}x{height} (expected ~{expected_ratio:.4f}, crop={crop_fraction:.3f})",
                        flush=True,
                    )
                return None
            normalized_path = match_aspect_to_ratio(image_path, expected_ratio)
            if not normalized_path:
                if log_brief:
                    print(f"[RatioCheck] FAIL {width}x{height} (expected ~{expected_ratio:.4f})", flush=True)
                return None
        with Image.open(normalized_path) as normalized_img:
            normalized_width, normalized_height = normalized_img.size
        if normalized_height <= 0:
            return None
        normalized_ratio = normalized_width / normalized_height
        if abs(normalized_ratio - expected_ratio) > ratio_tol:
            if log_brief:
                print(
                    f"[RatioCheck] FAIL {normalized_width}x{normalized_height} (expected ~{expected_ratio:.4f})",
                    flush=True,
                )
            return None
        return normalized_path
    except Exception:
        return None


def _format_identity_dims(dims: dict | None) -> str:
    if not isinstance(dims, dict):
        return ""
    parts: list[str] = []
    width_mm = dims.get("width_mm")
    depth_mm = dims.get("depth_mm")
    height_mm = dims.get("height_mm")
    if width_mm is not None:
        parts.append(f"W={width_mm}mm")
    if depth_mm is not None:
        parts.append(f"D={depth_mm}mm")
    if height_mm is not None:
        parts.append(f"H={height_mm}mm")
    return " ".join(parts)


def _item_category_for_prompt(item: dict | None) -> str:
    if not isinstance(item, dict):
        return "unknown"
    category = (
        item.get("category_canonical")
        or item.get("category")
        or item.get("type")
        or ""
    )
    text = str(category or "").strip()
    return text or "unknown"


def _category_prompt_guardrails(category: str) -> list[str]:
    text = str(category or "").strip().lower()
    rules: list[str] = []
    if any(token in text for token in ("wall_art", "wall decor", "poster", "painting", "artwork", "framed print")):
        rules.extend(
            [
                "solid_wall_first",
                "floor_leaning_against_wall_second",
                "never_on_window_surface",
                "never_directly_in_front_of_window",
            ]
        )
    if "mirror" in text:
        rules.extend(["wall_attached_or_leaning_as_reference", "preserve_reflective_face"])
    if "rug" in text or "carpet" in text:
        rules.extend(["floor_flat", "keep_footprint_shape", "not_wall_to_wall_unless_dimensions_require"])
    if any(token in text for token in ("lamp", "light", "pendant", "chandelier", "sconce")):
        rules.extend(["preserve_light_fixture_scale", "do_not_convert_into_furniture"])
    if "table_lamp" in text:
        rules.extend(
            [
                "exact_lampshade_shape",
                "exact_base_and_stem_geometry",
                "no_generic_lamp_substitution",
                "support_priority_storage_then_side_table_then_floor",
                "avoid_sofa_table_or_coffee_table_default",
            ]
        )
    if "chair" in text:
        rules.extend(["exact_back_seat_leg_geometry", "no_generic_chair_substitution"])
    if any(token in text for token in ("decor", "art", "frame")):
        rules.extend(["copy_artwork_image_content", "no_generic_wall_art_substitution"])
    if any(token in text for token in ("table_lamp", "vase", "object", "accessory")):
        rules.append("surface_or_compact_object_scale")
    return rules


def _ultra_detailed_item_analysis_enabled() -> bool:
    return str(os.getenv("AI_RENDER_ULTRA_DETAILED_ITEM_ANALYSIS", "")).strip().lower() in {"1", "true", "yes", "on"}


def _prompt_cue_list(values, *, limit: int = 3) -> str:
    if limit == 3 and _ultra_detailed_item_analysis_enabled():
        limit = 6
    if not isinstance(values, list):
        return ""
    cues = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in cues:
            continue
        cues.append(text)
        if len(cues) >= limit:
            break
    return ", ".join(cues)


def _is_generic_product_label(label: str | None) -> bool:
    text = str(label or "").strip().lower()
    if not text:
        return False
    compact = re.sub(r"[\s_\-]+", "", text)
    if "ai" in compact and (("design" in compact and "image" in compact) or ("디자인" in compact and "이미지" in compact)):
        return True
    if text.startswith("ai ") and "?" in text:
        return True
    if "판매 제외 상품" in text and ("ai" in compact or "디자인" in compact or "이미지" in compact):
        return True
    return False


def _visual_alias_for_prompt(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    profile = item.get("identity_profile") if isinstance(item.get("identity_profile"), dict) else {}
    product_identity = item.get("product_identity") if isinstance(item.get("product_identity"), dict) else {}
    reference_features = item.get("reference_features") if isinstance(item.get("reference_features"), dict) else {}
    alias = (
        _prompt_cue_list(reference_features.get("distinctive_parts"), limit=1)
        or _prompt_cue_list(product_identity.get("topology_cues"), limit=1)
        or _prompt_cue_list(reference_features.get("silhouette_cues"), limit=1)
        or _prompt_cue_list(profile.get("distinctive_parts"), limit=1)
        or _prompt_cue_list(profile.get("topology_cues") or profile.get("shape_cues"), limit=1)
        or _prompt_cue_list(product_identity.get("material_cues"), limit=1)
        or _prompt_cue_list(reference_features.get("material_cues"), limit=1)
    )
    return alias[:120].strip()


def _item_display_label_for_prompt(item: dict | None) -> str:
    if not isinstance(item, dict):
        return "Unknown Item"
    raw_label = str(item.get("label") or item.get("category") or "Unknown Item").strip() or "Unknown Item"
    product_name = str(item.get("product_name") or "").strip()
    if product_name:
        return product_name
    alias = _visual_alias_for_prompt(item)
    if alias and _is_generic_product_label(raw_label):
        item_id = str(item.get("item_id") or item.get("source_index") or item.get("target_key") or "").strip()
        suffix = f" [{item_id}]" if item_id else ""
        return f"{alias}{suffix}"
    return raw_label


def _format_contract_dims(dims: dict | None) -> str:
    if not isinstance(dims, dict):
        return ""
    width = dims.get("width_mm")
    depth = dims.get("depth_mm")
    height = dims.get("height_mm")
    parts = []
    if width is not None:
        parts.append(f"W={width}mm")
    if depth is not None:
        parts.append(f"D={depth}mm")
    if height is not None:
        parts.append(f"H={height}mm")
    return " ".join(parts)


def _ratio_bits(row: dict | None) -> list[str]:
    row = row if isinstance(row, dict) else {}
    bits: list[str] = []
    for key in ("room_width_ratio", "room_depth_ratio", "room_height_ratio", "room_footprint_ratio", "footprint_ratio"):
        value = row.get(key)
        if value is not None:
            bits.append(f"{key}={value}")
    return bits


def _build_scale_plan_context(scale_plan: dict | None, item_labels_by_key: dict[str, str] | None) -> str:
    if not isinstance(scale_plan, dict) or not scale_plan:
        return ""
    room_dims = _format_contract_dims(scale_plan.get("room_dims"))
    source = scale_plan.get("room_dims_source") or ((scale_plan.get("room_dims_contract") or {}).get("source") if isinstance(scale_plan.get("room_dims_contract"), dict) else None)
    confidence = scale_plan.get("room_dims_confidence") or ((scale_plan.get("room_dims_contract") or {}).get("confidence") if isinstance(scale_plan.get("room_dims_contract"), dict) else None)
    lines = [
        "\n<SCALE PLAN (BINDING)>\n",
        f"strict_scale_requested={bool(scale_plan.get('strict_scale_requested'))} strict_scale_ready={bool(scale_plan.get('strict_scale_ready'))}\n",
    ]
    if room_dims:
        lines.append(f"room_dims={room_dims}\n")
    if source or confidence:
        lines.append(f"room_dims_source={source or 'unknown'} confidence={confidence or 'none'}\n")
    anchor = scale_plan.get("anchor_item") if isinstance(scale_plan.get("anchor_item"), dict) else {}
    if anchor:
        anchor_key = str(anchor.get("target_key") or "").strip()
        anchor_label = str(anchor.get("label") or (item_labels_by_key or {}).get(anchor_key) or anchor_key).strip()
        anchor_dims = _format_contract_dims(anchor.get("dims_mm"))
        lines.append(f"anchor={anchor_label or anchor_key}" + (f" {anchor_dims}" if anchor_dims else "") + "\n")
    item_rows = []
    for row in (scale_plan.get("items") or [])[:10]:
        if not isinstance(row, dict):
            continue
        item_key = str(row.get("target_key") or row.get("source_index") or "").strip()
        label = str(row.get("label") or (item_labels_by_key or {}).get(item_key) or item_key or "Item").strip()
        dims = _format_contract_dims(row.get("dims_mm"))
        bits = [bit for bit in [dims, f"placement={row.get('placement_family')}" if row.get("placement_family") else ""] if bit]
        bits.extend(_ratio_bits(row))
        if bits:
            item_rows.append(f"- {label}: " + "; ".join(bits))
    if item_rows:
        lines.append("item_scale_targets:\n" + "\n".join(item_rows) + "\n")
    lines.append("Treat these dimensions and ratios as hard generation targets; do not resize items for composition.\n")
    lines.append("--------------------------------------------------\n")
    return "".join(lines)


def _build_geometry_contract_context(geometry_contract: dict | None, item_labels_by_key: dict[str, str] | None) -> str:
    if not isinstance(geometry_contract, dict) or not geometry_contract:
        return ""
    lines = [
        "\n<GEOMETRY CONTRACT (BINDING)>\n",
        f"strict_scale_requested={bool(geometry_contract.get('strict_scale_requested'))} strict_scale_ready={bool(geometry_contract.get('strict_scale_ready'))}\n",
        f"strict_scale_mode={geometry_contract.get('strict_scale_mode') or 'unknown'} source={geometry_contract.get('geometry_source') or 'unknown'} confidence={geometry_contract.get('geometry_confidence') or 'none'}\n",
    ]
    if geometry_contract.get("anchor_item_key"):
        lines.append(f"anchor_item_key={geometry_contract.get('anchor_item_key')}\n")
    missing = [str(value) for value in (geometry_contract.get("missing_requirements") or []) if str(value).strip()]
    if missing:
        lines.append("missing_requirements=" + ", ".join(missing[:8]) + "\n")
    target_rows = []
    for row in (geometry_contract.get("item_targets") or [])[:10]:
        if not isinstance(row, dict):
            continue
        item_key = str(row.get("target_key") or "").strip()
        label = str(row.get("label") or (item_labels_by_key or {}).get(item_key) or item_key or "Item").strip()
        bits = [bit for bit in [f"zone={row.get('zone')}" if row.get("zone") else "", f"placement={row.get('placement_family')}" if row.get("placement_family") else ""] if bit]
        bits.extend(_ratio_bits(row))
        if bits:
            target_rows.append(f"- {label}: " + "; ".join(bits))
    if target_rows:
        lines.append("geometry_targets:\n" + "\n".join(target_rows) + "\n")
    lines.append("Any output violating this geometry contract is invalid.\n")
    lines.append("--------------------------------------------------\n")
    return "".join(lines)


def _build_placement_plan_context(placement_plan: dict | None, item_labels_by_key: dict[str, str] | None) -> str:
    if not isinstance(placement_plan, dict) or not placement_plan:
        return ""
    zones = placement_plan.get("placement_zones") if isinstance(placement_plan.get("placement_zones"), dict) else {}
    if not zones and not placement_plan.get("anchor_item_key"):
        return ""
    lines = ["\n<PLACEMENT PLAN (BINDING)>\n"]
    if placement_plan.get("anchor_item_key"):
        lines.append(f"anchor_item_key={placement_plan.get('anchor_item_key')}\n")
    zone_rows = []
    for item_key, zone in list(zones.items())[:10]:
        label = str((item_labels_by_key or {}).get(str(item_key)) or item_key or "Item").strip()
        if isinstance(zone, dict):
            bits = [bit for bit in [f"zone={zone.get('zone')}" if zone.get("zone") else "", f"placement={zone.get('placement_family')}" if zone.get("placement_family") else ""] if bit]
            targets = zone.get("room_ratio_targets") if isinstance(zone.get("room_ratio_targets"), dict) else {}
            bits.extend(_ratio_bits(targets))
            support_priority = zone.get("support_priority") if isinstance(zone.get("support_priority"), dict) else {}
            if support_priority:
                order = [str(value) for value in (support_priority.get("order") or []) if str(value).strip()]
                if order:
                    bits.append("support_priority=" + " > ".join(order))
                available_targets = support_priority.get("available_targets") or []
                available_bits = []
                for target in available_targets[:4]:
                    if not isinstance(target, dict):
                        continue
                    target_label = str(target.get("label") or target.get("target_key") or "").strip()
                    support_type = str(target.get("support_type") or "").strip()
                    if target_label:
                        available_bits.append(f"{target_label}" + (f"({support_type})" if support_type else ""))
                if available_bits:
                    bits.append("available_supports=" + ", ".join(available_bits))
                rule = str(support_priority.get("rule") or "").strip()
                if rule:
                    bits.append(f"support_rule={rule}")
            orientation = str(zone.get("orientation_hint") or "").strip()
            if orientation:
                bits.append(f"orientation={orientation}")
            if bits:
                zone_rows.append(f"- {label}: " + "; ".join(bits))
        elif str(zone or "").strip():
            zone_rows.append(f"- {label}: zone={zone}")
    if zone_rows:
        lines.append("\n".join(zone_rows) + "\n")
    lines.append("Place items according to these zones before applying styling.\n")
    lines.append("--------------------------------------------------\n")
    return "".join(lines)


def _item_target_key_for_prompt(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("target_key") or item.get("source_index") or item.get("label") or "").strip()


def _item_dims_for_prompt(item: dict | None) -> dict:
    if not isinstance(item, dict):
        return {}
    dims = item.get("requested_dims_mm") or item.get("dims_mm") or {}
    return dims if isinstance(dims, dict) else {}


def _safe_positive_int(value) -> int:
    try:
        number = int(value or 0)
    except Exception:
        return 0
    return number if number > 0 else 0


def _dimension_sum_for_grouped_sheet(item: dict | None) -> int | None:
    dims = _item_dims_for_prompt(item)
    values = [
        _safe_positive_int(dims.get("width_mm")),
        _safe_positive_int(dims.get("depth_mm")),
        _safe_positive_int(dims.get("height_mm")),
    ]
    if not any(values):
        return None
    return sum(values)


def _category_text_for_grouped_sheet(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    profile = item.get("identity_profile") if isinstance(item.get("identity_profile"), dict) else {}
    product_identity = item.get("product_identity") if isinstance(item.get("product_identity"), dict) else {}
    values = [
        item.get("mainCategory"),
        item.get("main_category"),
        item.get("main_category_name"),
        item.get("category_name"),
        item.get("categoryName"),
        item.get("category_canonical"),
        item.get("category"),
        item.get("subCategory"),
        item.get("sub_category"),
        item.get("type"),
        product_identity.get("family"),
        profile.get("family"),
    ]
    return " ".join(str(value or "").strip().lower() for value in values if str(value or "").strip())


def _is_grouped_small_item_category(item: dict | None) -> bool:
    text = _category_text_for_grouped_sheet(item)
    if not text:
        return False
    return any(keyword in text for keyword in _GROUPED_SMALL_ITEM_CATEGORY_KEYWORDS)


def _grouped_small_item_sheet_priority(pair: tuple[int, dict]) -> tuple:
    original_index, item = pair
    dimension_sum = _dimension_sum_for_grouped_sheet(item)
    is_prop = _is_grouped_small_item_category(item)
    return (
        0 if is_prop else 1,
        0 if dimension_sum is not None else 1,
        dimension_sum if dimension_sum is not None else 10**12,
        original_index,
    )


def _select_grouped_small_item_sheet_items(items: list[dict], sheet_count: int) -> list[dict]:
    if sheet_count <= 0:
        return []
    candidates = [(index, item) for index, item in enumerate(items) if isinstance(item, dict)]
    if not candidates:
        return []
    limit = min(max(0, int(sheet_count)), _GROUPED_SMALL_ITEM_SHEET_LIMIT)
    return [item for _, item in sorted(candidates, key=_grouped_small_item_sheet_priority)[:limit]]


def _split_cutout_reference_items_for_generation(
    items: list[dict],
    *,
    direct_sort_key: Callable[[dict], tuple | int | float],
) -> tuple[list[dict], list[dict]]:
    valid_items = [item for item in (items or []) if isinstance(item, dict)]
    grouped_sheet_count = max(0, len(valid_items) - _DIRECT_CUTOUT_REFERENCE_LIMIT)
    grouped_sheet_items = _select_grouped_small_item_sheet_items(
        valid_items,
        sheet_count=grouped_sheet_count,
    )
    grouped_sheet_item_ids = {id(item) for item in grouped_sheet_items}
    direct_items = [item for item in valid_items if id(item) not in grouped_sheet_item_ids]
    direct_items.sort(key=direct_sort_key, reverse=True)
    return direct_items[:_DIRECT_CUTOUT_REFERENCE_LIMIT], grouped_sheet_items


def _format_grouped_sheet_item_row(slot: str, item: dict) -> str:
    label = _item_display_label_for_prompt(item)
    raw_label = str(item.get("label") or "").strip()
    item_key = _item_target_key_for_prompt(item)
    item_id = str(item.get("item_id") or "").strip()
    source_index = str(item.get("source_index") or "").strip()
    category = _item_category_for_prompt(item)
    dims = _item_dims_for_prompt(item)
    width = dims.get("width_mm")
    depth = dims.get("depth_mm")
    height = dims.get("height_mm")
    dims_text = (
        f"W={width if width is not None else 'null'}mm "
        f"D={depth if depth is not None else 'null'}mm "
        f"H={height if height is not None else 'null'}mm"
    )
    raw_label_text = f" | RawLabel={raw_label}" if raw_label and raw_label != label else ""
    return (
        f"{slot} = {item_key or label} | Label={label} | SourceIndex={source_index or 'null'} "
        f"| ItemID={item_id or 'null'} | Category={category} | {dims_text}"
        f"{raw_label_text}"
        f"{_build_reference_identity_suffix(item)}"
    )


def _build_grouped_small_item_sheet_reference(items: list[dict]) -> tuple[str, Image.Image]:
    selected = [item for item in (items or []) if isinstance(item, dict)][: _GROUPED_SMALL_ITEM_SHEET_LIMIT]
    sheet_size = _GROUPED_SMALL_ITEM_SHEET_SIZE
    sheet = Image.new("RGB", (sheet_size, sheet_size), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        slot_font = ImageFont.truetype("arialbd.ttf", 38)
    except Exception:
        slot_font = ImageFont.load_default()
    margin = 96
    gap = 48
    cols = 4
    rows = 2
    cell_w = (sheet_size - margin * 2 - gap * (cols - 1)) // cols
    cell_h = (sheet_size - margin * 2 - gap * (rows - 1)) // rows
    rows_text = []

    for index, item in enumerate(selected, start=1):
        slot = f"S{index}"
        rows_text.append(_format_grouped_sheet_item_row(slot, item))
        col = (index - 1) % cols
        row = (index - 1) // cols
        left = margin + col * (cell_w + gap)
        top = margin + row * (cell_h + gap)
        right = left + cell_w
        bottom = top + cell_h
        draw.rectangle((left, top, right, bottom), outline=(200, 200, 200), width=3)
        draw.rectangle((left, top, left + 118, top + 72), fill=(0, 0, 0))
        draw.text((left + 22, top + 16), slot, fill=(255, 255, 255), font=slot_font)

        crop_path = str(item.get("crop_path") or "").strip()
        if not crop_path or not os.path.exists(crop_path):
            continue
        try:
            with Image.open(crop_path) as crop:
                crop_img = crop.convert("RGBA")
                max_w = cell_w - 64
                max_h = cell_h - 104
                crop_img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                paste_x = left + (cell_w - crop_img.width) // 2
                paste_y = top + 72 + max(0, (max_h - crop_img.height) // 2)
                background = Image.new("RGBA", crop_img.size, "white")
                background.alpha_composite(crop_img)
                sheet.paste(background.convert("RGB"), (paste_x, paste_y))
        except Exception:
            continue

    slot_range = f"S1-S{len(selected)}" if selected else "S1-S0"
    header = (
        "Grouped Small Item Reference Sheet (COMPACT PRODUCT LOCKS - each labeled slot is a separate purchasable item; "
        "the sheet is not one combined object). "
        f"Use {slot_range} as individual purchasable items, preserving each product's identity and approximate scale. "
        "Read slot metadata below to map each sheet image cell to its product card:\n"
        + "\n".join(rows_text)
    )
    return header, sheet


def _item_identifier_bits_for_prompt(item: dict | None) -> list[str]:
    if not isinstance(item, dict):
        return []
    bits: list[str] = []
    for key in ("source_index", "item_id", "target_key"):
        value = str(item.get(key) or "").strip()
        if value:
            bits.append(f"{key}={value}")
    return bits


def _item_analysis_description_for_prompt(item: dict | None, *, limit: int = 2400) -> str:
    if not isinstance(item, dict):
        return ""
    text = " ".join(str(item.get("description") or "").split()).strip()
    if not text:
        return ""
    return text[: max(1, int(limit))].strip()


def _item_anchor_priority(item: dict | None, *, explicit_primary_key: str) -> tuple:
    if not isinstance(item, dict):
        return (-1, -1, -1, -1, -1)
    item_key = _item_target_key_for_prompt(item)
    dims = _item_dims_for_prompt(item)
    identity_profile = item.get("identity_profile") or {}
    product_identity = item.get("product_identity") or {}
    family = str(
        product_identity.get("family")
        or identity_profile.get("family")
        or item.get("category_canonical")
        or item.get("category")
        or ""
    ).strip().lower()

    try:
        width_mm = int(dims.get("width_mm") or 0)
    except Exception:
        width_mm = 0
    try:
        depth_mm = int(dims.get("depth_mm") or 0)
    except Exception:
        depth_mm = 0
    try:
        height_mm = int(dims.get("height_mm") or 0)
    except Exception:
        height_mm = 0
    try:
        volume_proxy = int(item.get("volume_proxy") or 0)
    except Exception:
        volume_proxy = 0
    if volume_proxy <= 0:
        volume_proxy = max(width_mm * max(depth_mm, 1) * max(height_mm, 1), width_mm * max(height_mm, 1))

    family_bonus_map = {
        "main_sofa": 9,
        "lounge_sofa": 9,
        "sofa": 9,
        "storage_cabinet_shelf": 8,
        "storage": 8,
        "cabinet": 8,
        "desk_table": 7,
        "dining_table": 7,
        "table": 6,
        "lounge_chair": 6,
        "armchair": 6,
        "chair": 5,
        "floor_lamp": 5,
        "pendant_lamp": 4,
        "table_lamp": 2,
        "mirror": 1,
        "rug": 0,
        "decor": 0,
        "plant": 0,
    }
    family_bonus = family_bonus_map.get(family, 3 if family else 0)
    room_presence = str(identity_profile.get("room_presence_class") or "").strip().lower()
    presence_bonus = 0
    if "anchor" in room_presence:
        presence_bonus = 3
    elif "large" in room_presence:
        presence_bonus = 2
    elif "medium" in room_presence:
        presence_bonus = 1
    has_dims = 1 if any(v > 0 for v in (width_mm, depth_mm, height_mm)) else 0
    explicit_primary = 1 if explicit_primary_key and item_key == explicit_primary_key else 0
    return (explicit_primary, family_bonus, presence_bonus, has_dims, volume_proxy)


def _select_primary_anchor_keys(furniture_specs_json: dict | None, *, limit: int = 4) -> list[str]:
    if not isinstance(furniture_specs_json, dict):
        return []
    items = [item for item in (furniture_specs_json.get("items") or []) if isinstance(item, dict)]
    if not items:
        return []
    explicit_primary_key = str(
        ((furniture_specs_json.get("primary_scale") or {}) if isinstance(furniture_specs_json.get("primary_scale"), dict) else {}).get("target_key")
        or ((furniture_specs_json.get("primary") or {}) if isinstance(furniture_specs_json.get("primary"), dict) else {}).get("target_key")
        or ""
    ).strip()
    two_pass_summary = (furniture_specs_json.get("two_pass_strategy") or {}) if isinstance(furniture_specs_json.get("two_pass_strategy"), dict) else {}
    pass1_primary_keys = [
        str(value or "").strip()
        for value in (two_pass_summary.get("pass1_primary_keys") or [])
        if str(value or "").strip()
    ]
    excluded_keys = {
        str(value or "").strip()
        for value in (
            list(two_pass_summary.get("pass1_support_keys") or [])
            + list(two_pass_summary.get("pass2_detail_keys") or [])
        )
        if str(value or "").strip()
    }
    explicit_role_data_present = bool(pass1_primary_keys or excluded_keys)
    if explicit_role_data_present:
        selected: list[str] = []
        for item_key in pass1_primary_keys + ([explicit_primary_key] if explicit_primary_key else []):
            if not item_key or item_key in excluded_keys or item_key in selected:
                continue
            selected.append(item_key)
            if len(selected) >= limit:
                return selected
        if selected:
            return selected
    scored_items = [
        (_item_anchor_priority(item, explicit_primary_key=explicit_primary_key), item)
        for item in items
    ]
    sorted_items = [item for _, item in sorted(scored_items, key=lambda pair: pair[0], reverse=True)]
    eligible_items = [
        item
        for score, item in sorted(scored_items, key=lambda pair: pair[0], reverse=True)
        if (
            score[0] == 1
            or (
                score[1] >= 2
                and score[2] >= 1
                and "tiny" not in str(((item.get("identity_profile") or {}).get("room_presence_class") or "")).lower()
                and str(((item.get("identity_profile") or {}).get("absolute_size_class") or "")).lower() != "tiny"
            )
        )
    ]
    candidate_items = eligible_items or sorted_items[:1]
    selected: list[str] = []
    for item in candidate_items:
        item_key = _item_target_key_for_prompt(item)
        if not item_key or item_key in selected or item_key in excluded_keys:
            continue
        selected.append(item_key)
        if len(selected) >= limit:
            break
    return selected


def _build_item_exactness_card_row(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    raw_label = str(item.get("label") or item.get("category") or "Unknown Item").strip()
    label = _item_display_label_for_prompt(item)
    if not label:
        return ""
    try:
        qty = max(1, int(item.get("qty") or 1))
    except Exception:
        qty = 1
    dims = _item_dims_for_prompt(item)
    category = _item_category_for_prompt(item)
    dims_text = _format_identity_dims(dims)
    bits = ["reference_image=authoritative_cutout", f"qty={qty}"]
    if category:
        bits.append(f"category={category}")
    analysis_description = _item_analysis_description_for_prompt(item)
    if analysis_description:
        bits.append(f"detailed_visual_analysis={analysis_description}")
    if raw_label and raw_label != label:
        bits.append(f"raw_label={raw_label}")
    visual_alias = _visual_alias_for_prompt(item)
    if visual_alias:
        bits.append(f"visual_alias={visual_alias}")
    bits.extend(_item_identifier_bits_for_prompt(item))
    if dims_text:
        bits.append(dims_text)
    guardrails = _category_prompt_guardrails(category)
    if guardrails:
        bits.append(f"category_rules={', '.join(guardrails[:6])}")
    profile = item.get("identity_profile") or {}
    product_identity = item.get("product_identity") or {}
    reference_features = item.get("reference_features") or {}
    archetype = item.get("archetype_strategy") or {}
    if isinstance(profile, dict) or isinstance(product_identity, dict):
        family = ""
        if isinstance(product_identity, dict):
            family = str(product_identity.get("family") or "").strip()
        if not family and isinstance(profile, dict):
            family = str(profile.get("family") or "").strip()
        if family:
            bits.append(f"family={family}")
    topology = ""
    if isinstance(product_identity, dict):
        topology = _prompt_cue_list(product_identity.get("topology_cues"))
    if not topology and isinstance(profile, dict):
        topology = _prompt_cue_list(profile.get("topology_cues") or profile.get("shape_cues"))
    if not topology and isinstance(reference_features, dict):
        topology = _prompt_cue_list(reference_features.get("silhouette_cues"))
    if topology:
        bits.append(f"topology={topology}")
    support = ""
    if isinstance(product_identity, dict):
        support = _prompt_cue_list(product_identity.get("support_geometry"))
    if not support and isinstance(profile, dict):
        support = _prompt_cue_list(profile.get("support_geometry"))
    if not support and isinstance(reference_features, dict):
        support = _prompt_cue_list(reference_features.get("support_geometry"))
    if support:
        bits.append(f"support={support}")
    materials = ""
    if isinstance(profile, dict):
        materials = _prompt_cue_list(profile.get("material_cues"))
    if not materials and isinstance(reference_features, dict):
        materials = _prompt_cue_list(
            list(reference_features.get("material_cues") or [])
            + list(reference_features.get("color_cues") or [])
            + list(reference_features.get("surface_finish") or [])
        )
    if materials:
        bits.append(f"materials={materials}")
    parts = ""
    if isinstance(profile, dict):
        parts = _prompt_cue_list(profile.get("distinctive_parts"))
    if not parts and isinstance(reference_features, dict):
        parts = _prompt_cue_list(
            list(reference_features.get("distinctive_parts") or [])
            + list(reference_features.get("support_geometry") or [])
        )
    if parts:
        bits.append(f"parts={parts}")
    preserve = ""
    if isinstance(product_identity, dict):
        preserve = _prompt_cue_list(product_identity.get("preserve_rules"))
    if not preserve and isinstance(profile, dict):
        preserve = _prompt_cue_list(profile.get("preserve_rules"))
    if not preserve and isinstance(reference_features, dict):
        preserve = _prompt_cue_list(reference_features.get("preserve_rules"))
    if preserve:
        bits.append(f"preserve={preserve}")
    if isinstance(reference_features, dict):
        forbid_identity_changes = _prompt_cue_list(reference_features.get("negative_identity_constraints"))
        if forbid_identity_changes:
            bits.append(f"forbid_identity_changes={forbid_identity_changes}")
        if _is_weak_reference_analysis_item(item):
            bits.append("weak_text_analysis=image_is_contract")
            bits.append("if_text_cues_are_sparse_match_reference_crop_outline_parts_and_count")
    if isinstance(archetype, dict):
        strategy = str(archetype.get("render_strategy") or "").strip()
        if strategy:
            bits.append(f"strategy={strategy}")
        forbid = _prompt_cue_list(archetype.get("forbidden_substitutions"))
        if forbid:
            bits.append(f"forbid={forbid}")
    if qty == 1:
        bits.append("exactly_one_instance_required")
        bits.append("duplicate_instances_invalid")
    bits.append("same_family_substitute=invalid")
    return f"- {label}: " + "; ".join(bits)


_WEAK_REFERENCE_ANALYSIS_QUALITIES = {
    "fallback",
    "fallback_after_weak_model",
    "fallback_after_invalid_model",
    "model_insufficient",
    "model_weak",
    "weak_model",
}


def _reference_signal_count_for_prompt(item: dict | None) -> int:
    if not isinstance(item, dict):
        return 0
    reference_features = item.get("reference_features") if isinstance(item.get("reference_features"), dict) else {}
    identity_profile = item.get("identity_profile") if isinstance(item.get("identity_profile"), dict) else {}
    product_identity = item.get("product_identity") if isinstance(item.get("product_identity"), dict) else {}
    signal_keys = (
        "silhouette_cues",
        "material_cues",
        "color_cues",
        "surface_finish",
        "distinctive_parts",
        "support_geometry",
        "preserve_rules",
        "negative_identity_constraints",
        "topology_cues",
    )
    count = 0
    for source in (reference_features, identity_profile, product_identity):
        for key in signal_keys:
            value = source.get(key)
            if isinstance(value, list):
                count += len([row for row in value if str(row or "").strip()])
            elif str(value or "").strip():
                count += 1
    return count


def _is_weak_reference_analysis_item(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    reference_features = item.get("reference_features") if isinstance(item.get("reference_features"), dict) else {}
    analysis_quality = str(reference_features.get("analysis_quality") or "").strip().lower()
    extraction_mode = str(reference_features.get("extraction_mode") or "").strip().lower()
    if analysis_quality in _WEAK_REFERENCE_ANALYSIS_QUALITIES or extraction_mode == "fallback":
        return True
    has_analysis_signal = "analysis_quality" in reference_features or "extraction_mode" in reference_features
    return bool(item.get("crop_path") and has_analysis_signal and _reference_signal_count_for_prompt(item) <= 2)


def _build_reference_identity_suffix(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    profile = item.get("identity_profile") if isinstance(item.get("identity_profile"), dict) else {}
    product_identity = item.get("product_identity") if isinstance(item.get("product_identity"), dict) else {}
    reference_features = item.get("reference_features") if isinstance(item.get("reference_features"), dict) else {}
    topology = (
        _prompt_cue_list(product_identity.get("topology_cues"))
        or _prompt_cue_list(profile.get("topology_cues") or profile.get("shape_cues"))
        or _prompt_cue_list(reference_features.get("silhouette_cues"))
    )
    parts = (
        _prompt_cue_list(profile.get("distinctive_parts"))
        or _prompt_cue_list(
            list(reference_features.get("distinctive_parts") or [])
            + list(reference_features.get("support_geometry") or [])
        )
        or _prompt_cue_list(product_identity.get("support_geometry"))
    )
    materials = _prompt_cue_list(profile.get("material_cues")) or _prompt_cue_list(
        list(reference_features.get("material_cues") or [])
        + list(reference_features.get("color_cues") or [])
        + list(reference_features.get("surface_finish") or [])
    )
    preserve = (
        _prompt_cue_list(product_identity.get("preserve_rules"))
        or _prompt_cue_list(profile.get("preserve_rules"))
        or _prompt_cue_list(reference_features.get("preserve_rules"))
    )
    forbid_identity_changes = _prompt_cue_list(reference_features.get("negative_identity_constraints"))
    fields = ["IdentityMustMatch=exact reference crop geometry, outline, visible part count, and instance count"]
    if topology:
        fields.append(f"TopologyCues={topology}")
    if parts:
        fields.append(f"DistinctiveParts={parts}")
    if materials:
        fields.append(f"MaterialCues={materials}")
    if preserve:
        fields.append(f"PreserveRules={preserve}")
    if forbid_identity_changes:
        fields.append(f"ForbiddenIdentityChanges={forbid_identity_changes}")
    if _is_weak_reference_analysis_item(item):
        fields.append("WeakTextAnalysis=ignore sparse text if needed; the attached reference crop defines the required product shape")
    fields.append(
        "InvalidIf=generic same-family substitute, missing listed topology/distinctive parts, duplicate qty=1 instance, or cross-product feature borrowing"
    )
    return " | " + " | ".join(fields)


def _build_item_exactness_cards_context(furniture_specs_json: dict | None, *, primary_anchor_keys: list[str] | None = None) -> str:
    if not isinstance(furniture_specs_json, dict):
        return ""
    anchor_key_set = {
        str(value or "").strip()
        for value in (primary_anchor_keys or [])
        if str(value or "").strip()
    }
    primary_rows: list[str] = []
    secondary_rows: list[str] = []
    for item in furniture_specs_json.get("items") or []:
        row = _build_item_exactness_card_row(item)
        if not row:
            continue
        item_key = _item_target_key_for_prompt(item)
        if item_key and item_key in anchor_key_set:
            primary_rows.append(row)
        else:
            secondary_rows.append(row)
    if not primary_rows and not secondary_rows:
        return ""
    lines = [
        "\n<ITEM EXACTNESS CARDS>\n",
        "The attached cutout image is the authoritative product-shape source. Text tags are only scale, placement, and exception guardrails.\n",
        "Do not restyle, simplify, or generalize an item into a same-family substitute. If text and image conflict, follow the image.\n",
        "If an item has sparse or weak text analysis, the image is still mandatory: preserve the reference crop outline, visible parts, proportions, material impression, and exact instance count.\n",
        "If multiple rows share the same label, they are still separate products when source_index, item_id, or target_key differ. Render each distinct product once per its qty; never merge them or reuse one reference image for another row.\n",
        "Do not borrow color, material, silhouette, topology, lampshade, stacked-body, leg/support, or distinctive-part cues across product rows. Each row may use only its own attached reference image and its own item card.\n",
    ]
    if primary_rows:
        lines.extend(
            [
                "<PRIMARY PRODUCT LOCKS>\n",
                "Match these hero products first. If any tradeoff exists, preserve these exact products before styling or simplifying any supporting item.\n",
                *[row + "\n" for row in primary_rows],
            ]
        )
    if secondary_rows:
        lines.extend(
            [
                "<SECONDARY SUPPORTING ITEMS>\n",
                "Render these after the primary product locks are correct. Keep product identity if visible, but never let these override the primary locks.\n",
                *[row + "\n" for row in secondary_rows],
            ]
        )
    lines.append("--------------------------------------------------\n")
    return "".join(lines)


def _build_fallback_furniture_guidance_context(furniture_specs: str | None) -> str:
    text = str(furniture_specs or "").strip()
    if not text:
        return ""
    return (
        "\n<FALLBACK ITEM GUIDANCE>\n"
        "Structured item cards are unavailable for this request.\n"
        "Use the fallback list below only as a last-resort reference, and prefer any attached reference images over this prose.\n"
        f"{text}\n"
        "--------------------------------------------------\n"
    )


def _summarize_scale_review(diagnostics: dict | None) -> dict:
    raw = diagnostics or {}
    failed_rules = list(raw.get("failed_rules") or [])
    unmatched_items = list(raw.get("unmatched_items") or [])
    matched_items = raw.get("matched_items") or {}
    issue_records = list(raw.get("issue_records") or [])
    if issue_records:
        bucket_counts = {
            "fidelity_fail_count": sum(
                1
                for row in issue_records
                if str((row or {}).get("rule_kind") or "") in _FIDELITY_RULE_KINDS
            ),
            "placement_fail_count": sum(1 for row in issue_records if str((row or {}).get("rule_kind") or "") == "placement_violation"),
            "geometry_fail_count": sum(
                1
                for row in issue_records
                if str((row or {}).get("rule_kind") or "") in _GEOMETRY_RULE_KINDS
            ),
        }
    else:
        bucket_counts = _review_bucket_counts(failed_rules)
    review_pass = not failed_rules and not unmatched_items and bool(matched_items)
    weighted_score = _weighted_issue_score(issue_records)
    if weighted_score <= 0 and (failed_rules or unmatched_items):
        weighted_score = round(
            (len(unmatched_items) * 3.0)
            + (bucket_counts["fidelity_fail_count"] * 2.5)
            + (bucket_counts["placement_fail_count"] * 2.0)
            + (bucket_counts["geometry_fail_count"] * 1.5),
            4,
        )
    return {
        "review_pass": review_pass,
        "matched_source_count": len(matched_items),
        "unmatched_source_count": len(unmatched_items),
        "unmatched_source_items": unmatched_items,
        **bucket_counts,
        "weighted_issue_score": weighted_score,
        "review_score": ((len(matched_items) * 4) - int(round(weighted_score * 10))),
    }


def _reference_item_importance(item: dict, matched: dict | None = None, *, is_primary: bool = False) -> float:
    profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    product_identity = (item.get("product_identity") or {}) if isinstance(item, dict) else {}
    archetype = (item.get("archetype_strategy") or {}) if isinstance(item, dict) else {}
    envelope = (item.get("layout_envelope") or {}) if isinstance(item, dict) else {}
    placement_contract = (item.get("placement_contract") or {}) if isinstance(item, dict) else {}
    room_targets = (placement_contract.get("room_ratio_targets") or {}) if isinstance(placement_contract, dict) else {}
    score = 1.0
    for key in ("room_width_ratio", "room_depth_ratio", "room_height_ratio", "footprint_ratio"):
        value = envelope.get(key)
        if value is None:
            value = room_targets.get(key)
        if isinstance(value, (int, float)) and value > 0:
            score += min(0.4, float(value) * 2.0)
    score += min(0.5, len(product_identity.get("preserve_rules") or profile.get("preserve_rules") or []) * 0.10)
    score += min(0.4, len(product_identity.get("support_geometry") or []) * 0.10)
    score += min(0.4, len(product_identity.get("opening_or_gap_features") or []) * 0.10)
    score += min(0.3, len(profile.get("distinctive_parts") or []) * 0.10)
    try:
        score += min(0.6, int(item.get("category_score") or 0) / 20.0)
    except Exception:
        pass
    try:
        volume_proxy = float(item.get("volume_proxy") or 0)
        if volume_proxy > 0:
            score += min(0.7, volume_proxy / 2000000000.0)
    except Exception:
        pass
    if matched and matched.get("item_importance"):
        try:
            score = max(score, float(matched.get("item_importance") or 0.0))
        except Exception:
            pass
    family = str(profile.get("family") or item.get("category") or "").strip().lower()
    if family in {"mirror", "rug"}:
        score *= 1.05
    if str(archetype.get("strictness") or "").strip().lower() == "critical":
        score += 0.55
    try:
        score += min(0.8, float(archetype.get("criticality") or 0.0) * 0.25)
    except Exception:
        pass
    if is_primary:
        score += 0.75
    return round(score, 3)


def _reference_thumbnail_size(item: dict, matched: dict | None = None, *, is_primary: bool = False) -> int:
    importance = _reference_item_importance(item, matched, is_primary=is_primary)
    if importance >= 2.5:
        return 768
    if importance >= 1.8:
        return 640
    return 512


def _is_fluorescent_guide_pixel(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r >= 180 and g >= 170 and b <= 140 and abs(r - g) <= 80


def _is_guide_like_pixel(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r >= 175 and g >= 160 and b <= 190 and abs(r - g) <= 80


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _extract_guide_line_positions(guide_img: Image.Image) -> tuple[list[int], list[int], set[tuple[int, int]]]:
    width, height = guide_img.size
    pixels = guide_img.load()
    guide_mask: set[tuple[int, int]] = set()
    row_positions: list[int] = []
    col_positions: list[int] = []

    for y in range(height):
        row_hits = 0
        for x in range(width):
            if _is_fluorescent_guide_pixel(pixels[x, y]):
                guide_mask.add((x, y))
                row_hits += 1
        if row_hits / max(1, width) >= 0.35:
            row_positions.append(y)

    for x in range(width):
        col_hits = 0
        for y in range(height):
            if (x, y) in guide_mask:
                col_hits += 1
        if col_hits / max(1, height) >= 0.35:
            col_positions.append(x)

    return row_positions, col_positions, guide_mask


def _pixel_delta(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return float(abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])) / 3.0


def _line_energy(img: Image.Image, axis: str, index: int) -> float:
    width, height = img.size
    if axis == "row":
        if index <= 0 or index >= height - 1:
            return 0.0
    else:
        if index <= 0 or index >= width - 1:
            return 0.0

    pixels = img.load()
    scores: list[float] = []
    if axis == "row":
        for x in range(width):
            here = pixels[x, index]
            prev_px = pixels[x, index - 1]
            next_px = pixels[x, index + 1]
            scores.append(max(_pixel_delta(here, prev_px), _pixel_delta(here, next_px)))
    else:
        for y in range(height):
            here = pixels[index, y]
            prev_px = pixels[index - 1, y]
            next_px = pixels[index + 1, y]
            scores.append(max(_pixel_delta(here, prev_px), _pixel_delta(here, next_px)))
    return _mean(scores)


def _aligned_line_energy(img: Image.Image, axis: str, indices: list[int], *, search_radius: int = 8) -> float:
    if not indices:
        return 0.0
    limit = img.size[1] if axis == "row" else img.size[0]
    scores: list[float] = []
    for index in indices:
        best = 0.0
        for probe in range(max(1, index - search_radius), min(limit - 1, index + search_radius + 1)):
            best = max(best, _line_energy(img, axis, probe))
        scores.append(best)
    return _mean(scores)


def _baseline_line_energy(img: Image.Image, axis: str, guide_indices: list[int], *, exclusion_radius: int = 8) -> float:
    limit = img.size[1] if axis == "row" else img.size[0]
    blocked: set[int] = set()
    for index in guide_indices:
        blocked.update(range(max(1, index - exclusion_radius), min(limit - 1, index + exclusion_radius + 1)))

    candidates = [idx for idx in range(1, limit - 1) if idx not in blocked]
    if not candidates:
        return 0.0

    sample_count = min(24, len(candidates))
    if len(candidates) > sample_count:
        step = len(candidates) / sample_count
        sampled = [candidates[min(len(candidates) - 1, int(round(i * step)))] for i in range(sample_count)]
    else:
        sampled = candidates
    return _mean([_line_energy(img, axis, idx) for idx in sampled])


def _sample_points(points: set[tuple[int, int]], *, max_points: int = 512) -> list[tuple[int, int]]:
    ordered = sorted(points)
    if len(ordered) <= max_points:
        return ordered
    step = len(ordered) / max_points
    return [ordered[min(len(ordered) - 1, int(i * step))] for i in range(max_points)]


def _point_energy(img: Image.Image, x: int, y: int) -> float:
    width, height = img.size
    if x <= 0 or y <= 0 or x >= width - 1 or y >= height - 1:
        return 0.0
    pixels = img.load()
    here = pixels[x, y]
    return max(
        _pixel_delta(here, pixels[x - 1, y]),
        _pixel_delta(here, pixels[x + 1, y]),
        _pixel_delta(here, pixels[x, y - 1]),
        _pixel_delta(here, pixels[x, y + 1]),
    )


def _aligned_mask_energy(img: Image.Image, guide_mask: set[tuple[int, int]], *, search_radius: int = 8) -> float:
    sampled = _sample_points(guide_mask)
    if not sampled:
        return 0.0

    width, height = img.size
    scores: list[float] = []
    for x, y in sampled:
        best = 0.0
        for px in range(max(1, x - search_radius), min(width - 1, x + search_radius + 1)):
            for py in range(max(1, y - search_radius), min(height - 1, y + search_radius + 1)):
                best = max(best, _point_energy(img, px, py))
        scores.append(best)
    return _mean(scores)


def _baseline_mask_energy(img: Image.Image, guide_mask: set[tuple[int, int]], *, max_points: int = 256) -> float:
    width, height = img.size
    stride = max(4, int(((width * height) / max(1, max_points)) ** 0.5))
    points: list[tuple[int, int]] = []
    for y in range(max(1, stride // 2), height - 1, stride):
        for x in range(max(1, stride // 2), width - 1, stride):
            if (x, y) not in guide_mask:
                points.append((x, y))
    if not points:
        return 0.0
    return _mean([_point_energy(img, x, y) for x, y in points[:max_points]])


def _guide_mask_overlap_ratio(rendered_img: Image.Image, guide_mask: set[tuple[int, int]], *, search_radius: int = 2) -> float:
    sampled = _sample_points(guide_mask)
    if not sampled:
        return 0.0

    width, height = rendered_img.size
    pixels = rendered_img.load()
    hits = 0
    for x, y in sampled:
        found = False
        for px in range(max(0, x - search_radius), min(width - 1, x + search_radius) + 1):
            for py in range(max(0, y - search_radius), min(height - 1, y + search_radius) + 1):
                if _is_guide_like_pixel(pixels[px, py]):
                    found = True
                    break
            if found:
                break
        if found:
            hits += 1
    return hits / max(1, len(sampled))


def _has_scale_guide_leak(rendered_path: str, scale_guide_path: str | None) -> bool:
    if not rendered_path or not scale_guide_path:
        return False
    if not (os.path.exists(rendered_path) and os.path.exists(scale_guide_path)):
        return False

    guide_img = None
    rendered_img = None
    try:
        guide_img = Image.open(scale_guide_path).convert("RGB")
        rendered_img = Image.open(rendered_path).convert("RGB")
        target_size = (256, 256)
        guide_img = guide_img.resize(target_size, Image.Resampling.BILINEAR)
        rendered_img = rendered_img.resize(target_size, Image.Resampling.BILINEAR)

        _, _, guide_mask = _extract_guide_line_positions(guide_img)
        if len(guide_mask) < 32:
            return False

        overlap_ratio = _guide_mask_overlap_ratio(rendered_img, guide_mask, search_radius=2)
        if os.getenv("SCALE_GUIDE_DEBUG", "0") == "1":
            print(
                f"[GuideLeakDebug] rendered={os.path.basename(rendered_path)} "
                f"guide={os.path.basename(scale_guide_path)} mask_points={len(guide_mask)} "
                f"overlap={overlap_ratio:.4f} threshold=0.7500",
                flush=True,
            )
        return overlap_ratio >= 0.75
    except Exception:
        return False
    finally:
        try:
            if guide_img:
                guide_img.close()
        except Exception:
            pass
        try:
            if rendered_img:
                rendered_img.close()
        except Exception:
            pass


def generate_furnished_room(
    room_path,
    style_prompt,
    ref_path,
    unique_id,
    *,
    furniture_specs=None,
    furniture_specs_json=None,
    room_dimensions=None,
    placement_instructions=None,
    scale_guide_path=None,
    primary_item=None,
    room_dims_parsed=None,
    wall_span_norm=None,
    size_hierarchy=None,
    scale_plan=None,
    geometry_contract=None,
    scene_contract=None,
    placement_plan=None,
    start_time=0,
    room_planes=None,
    windows_present=None,
    room_analysis_text=None,
    enable_scale_check=False,
    max_generation_attempts: int | None = None,
    furnished_scene_reference_path: str | None = None,
    furniture_atlas_reference_path: str | None = None,
    total_timeout_limit: float,
    detect_windows_present: Callable[[str], bool],
    logger,
    parse_room_dimensions_mm: Callable[[str], dict],
    normalize_dims_dict: Callable[[dict], dict],
    is_two_dim_ok_label: Callable[[str], bool],
    available_dim_axes: Callable[[dict], set],
    summary_ref,
    log_brief: bool,
    log_summary: bool,
    allow_all_safety_settings: Callable[[], Any],
    call_generation_with_failover: Callable[..., Any] | None = None,
    generation_model_name: str | None = None,
    call_gemini_with_failover: Callable[..., Any] | None = None,
    model_name: str | None = None,
    match_aspect_to_target: Callable[[str, str], str | None],
    validate_furnished_scale: Callable[..., tuple[bool, list]],
):
    if time.time() - start_time > total_timeout_limit:
        return None
    room_img = None
    extra_imgs = []
    try:
        normalized_style_prompt = ""
        if isinstance(style_prompt, dict):
            normalized_style_prompt = str(style_prompt.get("prompt") or "").strip()
            if not normalized_style_prompt:
                style_name_hint = str(style_prompt.get("name") or style_prompt.get("label") or "").strip()
                if style_name_hint:
                    normalized_style_prompt = f"Style direction: preserve the {style_name_hint} mood while keeping the listed product identities exact."
        else:
            normalized_style_prompt = str(style_prompt or "").strip()
        room_img = Image.open(room_path)
        if windows_present is None:
            windows_present = detect_windows_present(room_path)
        try:
            logger.info(f"[WindowCheck] present={bool(windows_present)} path={room_path}")
        except Exception:
            pass

        width, height = room_img.size
        ratio_instruction = "LANDSCAPE (16:9 Ratio)"
        expected_ratio = 16 / 9
        ratio_tol = 0.1
        system_instruction = "You are an expert interior designer AI."
        generation_call = call_generation_with_failover or call_gemini_with_failover
        resolved_generation_model = generation_model_name or model_name
        if generation_call is None:
            raise TypeError(
                "generate_furnished_room requires call_generation_with_failover or call_gemini_with_failover"
            )
        if resolved_generation_model is None:
            raise TypeError("generate_furnished_room requires generation_model_name or model_name")

        def _remaining_timeout_sec() -> float:
            try:
                elapsed = max(0.0, float(time.time() - start_time))
            except Exception:
                elapsed = float(total_timeout_limit)
            return max(0.0, float(total_timeout_limit) - elapsed)

        def _stage2_generation_timeout_cap() -> float | None:
            if not b_lite_runtime:
                return None
            return 150.0

        def _bounded_stage2_timeout() -> float:
            current_timeout = _remaining_timeout_sec()
            if current_timeout <= 0.0:
                return 0.0
            timeout_cap = _stage2_generation_timeout_cap()
            if timeout_cap is not None:
                current_timeout = min(current_timeout, timeout_cap)
            return current_timeout

        def _deadline_validation_result() -> dict[str, Any]:
            return {
                "ok": False,
                "issues": ["deadline_budget_exhausted"],
                "diagnostics": {
                    "failed_rules": ["deadline_budget_exhausted"],
                    "matched_items": {},
                    "unmatched_items": [],
                    "rule_details": {},
                    "deadline_budget_exhausted": True,
                },
            }

        room_analysis_context = ""
        if room_analysis_text:
            room_analysis_context = (
                "\n<ROOM STRUCTURE & SCALE ANALYSIS (LONG)>\n"
                "Use this to preserve architecture and scale. Do NOT invent new openings.\n"
                f"{room_analysis_text}\n"
                "--------------------------------------------------\n"
            )

        primary_anchor_keys = _select_primary_anchor_keys(furniture_specs_json)
        anchor_key_set = set(primary_anchor_keys)
        specs_context = _build_item_exactness_cards_context(
            furniture_specs_json,
            primary_anchor_keys=primary_anchor_keys,
        )
        if not specs_context:
            specs_context = _build_fallback_furniture_guidance_context(furniture_specs)

        dims_table_context = ""
        try:
            if furniture_specs_json and isinstance(furniture_specs_json, dict):
                rows = []
                for it in (furniture_specs_json.get("items") or []):
                    lbl = _item_display_label_for_prompt(it)
                    qty = it.get("qty") or 1
                    dm = it.get("dims_mm") or {}
                    w = dm.get("width_mm")
                    d = dm.get("depth_mm")
                    h = dm.get("height_mm")
                    if any([w, d, h]):
                        qtxt = f" qty={qty}" if qty and qty > 1 else ""
                        rows.append(f"- {lbl}{qtxt}: W={w or 'null'}mm, D={d or 'null'}mm, H={h or 'null'}mm")
                if rows:
                    dims_table_context = (
                        "\n<FURNITURE DIMENSIONS TABLE (MM) - REFERENCE>\n"
                        "Use these real-world measurements as guidance. Do NOT invent new sizes.\n"
                        "Items with null W/D/H are incomplete; do NOT guess missing numbers. Use visual scale cues and keep within room limits.\n"
                        + "\n".join(rows)
                        + "\nGuidelines:\n"
                        "- No furniture item should exceed room width or room depth.\n"
                        "- Rugs/carpets: if rug width is within 10% of room width, it should visually span almost wall-to-wall.\n"
                        "- Wall storage/sideboard: if width is <= 1500mm in specs, it should NOT look like it spans most of the wall.\n"
                        "--------------------------------------------------\n"
                    )
        except Exception:
            dims_table_context = ""

        spatial_context = ""
        calculated_analysis = ""
        ratio_rules_context = ""
        incomplete_dims_context = ""
        inventory_context = ""
        scale_guide_context = ""
        identity_context = ""
        layout_envelope_context = ""
        small_scale_context = ""
        scale_plan_context = ""
        geometry_contract_context = ""
        scene_contract_context = ""
        placement_plan_context = ""
        strict_scale_requested = bool(isinstance(scale_plan, dict) and scale_plan.get("strict_scale_requested"))
        b_lite_runtime = strict_scale_requested
        item_labels_by_key: dict[str, str] = {}
        if isinstance(furniture_specs_json, dict):
            for item in furniture_specs_json.get("items") or []:
                if not isinstance(item, dict):
                    continue
                item_key = str(item.get("target_key") or item.get("source_index") or item.get("label") or "").strip()
                if item_key and item_key not in item_labels_by_key:
                    item_labels_by_key[item_key] = _item_display_label_for_prompt(item)
        anchor_labels = [item_labels_by_key.get(item_key, item_key) for item_key in primary_anchor_keys if item_key in item_labels_by_key]
        scale_plan_context = _build_scale_plan_context(scale_plan, item_labels_by_key)
        geometry_contract_context = _build_geometry_contract_context(geometry_contract, item_labels_by_key)
        if strict_scale_requested or isinstance(geometry_contract, dict):
            placement_plan_context = _build_placement_plan_context(placement_plan, item_labels_by_key)

        try:
            _room_dims = room_dims_parsed or parse_room_dimensions_mm(room_dimensions or "")
            room_w = int(_room_dims.get("width_mm") or 0)
            room_d = int(_room_dims.get("depth_mm") or 0)
            room_h = int(_room_dims.get("height_mm") or 0)
            effective_room_w = room_w
            wall_span_ratio = 1.0
            try:
                if isinstance(wall_span_norm, (list, tuple)) and len(wall_span_norm) == 2:
                    x_left = float(wall_span_norm[0])
                    x_right = float(wall_span_norm[1])
                    wall_span_ratio = max(0.0, min(1.0, x_right - x_left))
                    if room_w > 0 and 0.25 <= wall_span_ratio < 0.98:
                        effective_room_w = max(1, int(round(room_w * wall_span_ratio)))
            except Exception:
                wall_span_ratio = 1.0

            _primary = (
                primary_item
                or (furniture_specs_json or {}).get("primary_scale")
                or (furniture_specs_json or {}).get("primary")
                or {}
            )
            _p_dims = _primary.get("dims_mm") or {}
            p_w = int(_p_dims.get("width_mm") or 0)
            p_d = int(_p_dims.get("depth_mm") or 0)
            p_h = int(_p_dims.get("height_mm") or 0)

            if not p_w and furniture_specs_json and isinstance(furniture_specs_json, dict):
                try:
                    p_w = int(furniture_specs_json.get("max_width_mm") or 0)
                except Exception:
                    pass

            try:
                complete_items = []
                incomplete_items = []
                inventory_labels = []
                inventory_rows = []
                small_scale_rows = []
                if furniture_specs_json and isinstance(furniture_specs_json, dict):
                    total_requested_qty = 0

                    for it in (furniture_specs_json.get("items") or []):
                        raw_label = (it.get("label") or "").strip() or "Unknown Item"
                        label = _item_display_label_for_prompt(it)
                        qty = it.get("qty") or 1
                        try:
                            qty = max(1, int(qty))
                        except Exception:
                            qty = 1
                        inventory_labels.append(label)
                        total_requested_qty += qty
                        category = _item_category_for_prompt(it)
                        inventory_bits = [f"qty={qty}"]
                        if category:
                            inventory_bits.append(f"category={category}")
                        if raw_label and raw_label != label:
                            inventory_bits.append(f"raw_label={raw_label}")
                        visual_alias = _visual_alias_for_prompt(it)
                        if visual_alias:
                            inventory_bits.append(f"visual_alias={visual_alias}")
                        inventory_bits.extend(_item_identifier_bits_for_prompt(it))
                        inventory_rows.append(f"- {label}: " + "; ".join(inventory_bits))
                        dm = it.get("dims_mm") or {}
                        w = int(dm.get("width_mm") or 0)
                        d = int(dm.get("depth_mm") or 0)
                        h = int(dm.get("height_mm") or 0)
                        missing = []
                        if w <= 0:
                            missing.append("W")
                        if d <= 0:
                            missing.append("D")
                        if h <= 0:
                            missing.append("H")

                        if missing:
                            incomplete_items.append((label, missing))
                            if log_brief:
                                print(f"[Dims] FAIL {label} missing {','.join(missing)}", flush=True)
                            try:
                                summary = summary_ref.get()
                                if isinstance(summary, dict):
                                    summary["dims_fail"] = summary.get("dims_fail", 0) + 1
                            except Exception:
                                pass
                            continue
                        complete_items.append({"label": label, "w": w, "d": d, "h": h})
                        room_width_ratio = None
                        room_depth_ratio = None
                        if room_w > 0 and room_d > 0:
                            try:
                                room_width_ratio = float(w / room_w)
                            except Exception:
                                room_width_ratio = None
                            try:
                                room_depth_ratio = float(d / room_d)
                            except Exception:
                                room_depth_ratio = None
                        category_text = str(category or "").lower()
                        surface_decor = bool("decor" in category_text and decor_prefers_surface_placement(it))
                        compact_object = bool(
                            any(token in category_text for token in ("table_lamp", "stool", "vase", "accessory"))
                            or surface_decor
                            or ("rug" in category_text and room_width_ratio is not None and room_width_ratio <= 0.35)
                            or (room_width_ratio is not None and room_width_ratio <= 0.18)
                            or (room_depth_ratio is not None and room_depth_ratio <= 0.18)
                        )
                        if compact_object:
                            compact_bits = [f"W={w}mm", f"D={d}mm", f"H={h}mm"]
                            compact_note = "keep visually compact"
                            if "rug" in category_text:
                                compact_note = "keep this rug compact relative to the room, not wall-to-wall"
                            elif "table_lamp" in category_text:
                                compact_note = "keep this as a surface-scale object and table-lamp scale object, never side-table sized; place by priority: storage/cabinet top first, side table second, floor fallback only when neither support exists"
                            elif any(token in category_text for token in ("vase", "accessory")):
                                compact_note = "keep this as a surface-scale object, never side-table sized"
                            elif surface_decor:
                                compact_note = "keep this as a reference-scale compact object only when its dimensions/reference indicate tabletop scale, never side-table sized"
                            small_scale_rows.append(f"- {label}: " + "; ".join(compact_bits) + f"; {compact_note}.")

                if incomplete_items:
                    if strict_scale_requested:
                        incomplete_dims_context = (
                            "\n<STRICT SCALE CONTRACT VIOLATION>\n"
                            + "\n".join([f"- {lbl}: missing {', '.join(miss)}" for lbl, miss in incomplete_items])
                            + "\nRule: Do NOT estimate missing numbers in strict scale mode. This candidate is invalid until every item has W/D/H.\n"
                            + "--------------------------------------------------\n"
                        )
                    else:
                        incomplete_dims_context = (
                            "\n<INCOMPLETE DIMENSIONS (DO NOT IGNORE)>\n"
                            + "\n".join([f"- {lbl}: missing {', '.join(miss)}" for lbl, miss in incomplete_items])
                            + "\nRule: Do NOT invent missing numbers, but you MUST still render these items.\n"
                            + "Estimate size from the moodboard and keep within room limits and relative proportions.\n"
                            + "--------------------------------------------------\n"
                        )

                if inventory_rows:
                    inventory_context = (
                        "\n<ITEM INVENTORY (MUST RENDER ALL ITEMS)>\n"
                        f"Distinct items: {len(inventory_labels)} | Total requested quantity: {total_requested_qty}\n"
                        + "\n".join(inventory_rows)
                        + "\nRules:\n"
                        + "- Render every listed item exactly the requested quantity. Do not add bonus objects.\n"
                        + "- Do not duplicate rugs, accent chairs, or tables beyond the listed qty.\n"
                        + "- If qty=1, exactly one instance must appear. If qty>1, render identical multiples only for that item.\n"
                        + "- Never collapse a repeated item into a single instance.\n"
                        + "- If space is tight, reduce size slightly or use shelves/walls where appropriate, but never omit or replace items.\n"
                        + "--------------------------------------------------\n"
                    )

                if small_scale_rows:
                    small_scale_context = (
                        "\n<SMALL ITEM SCALE GUARDRAILS>\n"
                        "These listed items must stay visually compact on the first pass. Do not enlarge them into anchor furniture.\n"
                        + "\n".join(small_scale_rows)
                        + "\n--------------------------------------------------\n"
                    )

                def _ratio_str(value, total, cap=None):
                    if not value or not total:
                        return "n/a"
                    pct = round((value / total) * 100, 1)
                    if cap is not None and pct > cap:
                        return f"{cap:.1f}% (cap)"
                    return f"{pct:.1f}%"

                abs_lines = []
                abs_warn_labels = []
                if room_w > 0 and room_d > 0 and room_h > 0:
                    for it in complete_items:
                        w = it["w"]
                        d = it["d"]
                        h = it["h"]
                        label = it["label"]
                        abs_lines.append(
                            f"- {label}: room W={_ratio_str(w, room_w, 100.0)}, D={_ratio_str(d, room_d, 100.0)}, H={_ratio_str(h, room_h, 100.0)}"
                        )
                        over = []
                        if w > room_w:
                            over.append("W")
                        if d > room_d:
                            over.append("D")
                        if h > room_h:
                            over.append("H")
                        if over:
                            abs_warn_labels.append(label)
                        try:
                            summary = summary_ref.get()
                            if isinstance(summary, dict):
                                summary["dims_warn"] = summary.get("dims_warn", 0) + 1
                        except Exception:
                            pass
                else:
                    if log_brief and not log_summary:
                        print("[Dims] WARN room W/D/H missing; skip absolute ratios", flush=True)
                    try:
                        summary = summary_ref.get()
                        if isinstance(summary, dict):
                            summary["dims_warn"] = summary.get("dims_warn", 0) + 1
                    except Exception:
                        pass

                rel_lines = []
                rel_warn_labels = []
                primary_label = _primary.get("label", "Primary Furniture")
                if p_w > 0 and p_d > 0 and p_h > 0:
                    for it in complete_items:
                        label = it["label"]
                        if label == primary_label:
                            continue
                        rel_w = round((it["w"] / p_w) * 100, 1)
                        rel_d = round((it["d"] / p_d) * 100, 1)
                        rel_h = round((it["h"] / p_h) * 100, 1)
                        rel_lines.append(f"- {label}: W={rel_w:.1f}%, D={rel_d:.1f}%, H={rel_h:.1f}% of {primary_label}")
                        if rel_w > 100 or rel_d > 100 or rel_h > 100:
                            rel_warn_labels.append(label)
                        try:
                            summary = summary_ref.get()
                            if isinstance(summary, dict):
                                summary["dims_warn"] = summary.get("dims_warn", 0) + 1
                        except Exception:
                            pass
                elif log_brief:
                    print("[Dims] WARN primary W/D/H missing; skip relative ratios", flush=True)

                if log_brief and not log_summary:
                    if abs_warn_labels:
                        sample = ", ".join(abs_warn_labels[:3])
                        extra = len(abs_warn_labels) - 3
                        suffix = f" (+{extra} more)" if extra > 0 else ""
                        print(f"[Dims] WARN {len(abs_warn_labels)} items exceed room W/D/H: {sample}{suffix}", flush=True)
                    if rel_warn_labels:
                        sample = ", ".join(rel_warn_labels[:3])
                        extra = len(rel_warn_labels) - 3
                        suffix = f" (+{extra} more)" if extra > 0 else ""
                        print(f"[Dims] WARN {len(rel_warn_labels)} items larger than primary: {sample}{suffix}", flush=True)

                order_w = " > ".join([x["label"] for x in sorted(complete_items, key=lambda x: x["w"], reverse=True)]) if complete_items else ""
                order_d = " > ".join([x["label"] for x in sorted(complete_items, key=lambda x: x["d"], reverse=True)]) if complete_items else ""
                order_h = " > ".join([x["label"] for x in sorted(complete_items, key=lambda x: x["h"], reverse=True)]) if complete_items else ""
                height_caps = []
                for it in complete_items:
                    if it["h"] > 0:
                        height_caps.append(f"- {it['label']}: H must be <= {it['h']}mm")

                if not isinstance(geometry_contract, dict) and (abs_lines or rel_lines or order_w or order_d or order_h):
                    ratio_rules_context = "\n<CRITICAL: W/D/H RATIO RULES (ALL FURNITURE)>\nApply ratios only to items with complete W/D/H.\n"
                    if abs_lines:
                        ratio_rules_context += "ABSOLUTE RATIOS (item vs room):\n" + "\n".join(abs_lines) + "\n"
                    else:
                        ratio_rules_context += "ABSOLUTE RATIOS: room W/D/H missing or invalid.\n"
                    if rel_lines:
                        ratio_rules_context += f"RELATIVE RATIOS (item vs {primary_label}):\n" + "\n".join(rel_lines) + "\n"
                    if order_w or order_d or order_h:
                        ratio_rules_context += (
                            "DIMENSION ORDER (largest -> smallest):\n"
                            + f"- WIDTH: {order_w}\n"
                            + f"- DEPTH: {order_d}\n"
                            + f"- HEIGHT: {order_h}\n"
                        )
                    if height_caps:
                        ratio_rules_context += "HEIGHT CAPS (STRICT):\n" + "\n".join(height_caps) + "\n"
                    ratio_rules_context += "--------------------------------------------------\n"
            except Exception:
                pass

            if effective_room_w > 0 and p_w > 0:
                occ = round((p_w / effective_room_w) * 100, 1)
                gap_total_mm = effective_room_w - p_w
                gap_side_mm = int(gap_total_mm / 2) if gap_total_mm > 0 else 0
                primary_d_disp = f"{p_d}mm" if p_d > 0 else "unknown"
                primary_h_disp = f"{p_h}mm" if p_h > 0 else "unknown"
                room_d_disp = f"{room_d}mm" if room_d > 0 else "unknown"
                room_h_disp = f"{room_h}mm" if room_h > 0 else "unknown"
                calculated_analysis += (
                    f"   - **PRIMARY ANCHOR:** {_primary.get('label','Primary Furniture')} "
                    f"(W {p_w}mm, D {primary_d_disp}, H {primary_h_disp})\n"
                )
                calculated_analysis += f"   - **ROOM DIMS:** W {room_w}mm, D {room_d_disp}, H {room_h_disp}\n"
                if effective_room_w != room_w:
                    calculated_analysis += (
                        f"   - **USABLE WALL SPAN:** approx {effective_room_w}mm "
                        f"({round(wall_span_ratio * 100, 1)}% of room width).\n"
                    )
                calculated_analysis += f"   - **CALCULATED GAP (WIDTH):** Total empty space width = {gap_total_mm}mm. (approx {gap_side_mm}mm on each side).\n"
                calculated_analysis += f"   - **WIDTH OCCUPANCY:** {occ}% (The furniture takes up {occ}% of the wall).\n"
                if occ > 92:
                    calculated_analysis += "   - **ACTION: WALL-TO-WALL FIT.** The furniture is almost as wide as the room. It must TOUCH the side walls or have negligible gaps.\n"
                elif occ > 80:
                    calculated_analysis += "   - **ACTION: TIGHT FIT.** The furniture dominates the wall. Leave only SMALL gaps on the sides.\n"
                else:
                    calculated_analysis += "   - **ACTION: STANDARD FIT.** Center the furniture with visible breathing room on sides.\n"

            if room_d > 0 and p_d > 0:
                depth_occ = round((p_d / room_d) * 100, 1)
                calculated_analysis += f"   - **DEPTH OCCUPANCY:** {depth_occ}% (Floor depth usage).\n"
            if room_h > 0 and p_h > 0:
                height_occ = round((p_h / room_h) * 100, 1)
                calculated_analysis += f"   - **HEIGHT OCCUPANCY:** {height_occ}% (Height usage).\n"
            if room_w <= 0 or p_w <= 0:
                calculated_analysis += "   - (No reliable W/D/H dimensions found; apply relative scaling from reference hierarchy)\n"
        except Exception:
            pass

        if room_dimensions or placement_instructions:
            spatial_context = "\n<PHYSICAL SPACE CONSTRAINTS (STRICT ADHERENCE)>\n"
            if room_dimensions:
                spatial_context += f"- **ACTUAL ROOM DIMENSIONS:** {room_dimensions}\n"
            if placement_instructions:
                spatial_context += f"- **PLACEMENT INSTRUCTIONS:** {placement_instructions}\n"
                spatial_context += build_placement_prompt_block(placement_instructions)
            spatial_context += (
                "**SCALING RULE:** You MUST calibrate the scale of all furniture relative to the ACTUAL ROOM DIMENSIONS provided.\n"
                f"{calculated_analysis}\n"
                "Do NOT shrink furniture to create artificial empty space. If the room is small, it should look appropriately filled.\n"
                "--------------------------------------------------\n"
            )
        elif room_analysis_text:
            spatial_context = (
                "\n<ROOM-SCALE INFERENCE RULES>\n"
                "No explicit room dimensions were provided. Infer scale only from the input room geometry and architecture.\n"
                "- Keep doors, windows, mullions, columns, ceiling drops, baseboards, and floor plank scale fixed.\n"
                "- Use those architectural anchors to judge furniture size. Do not enlarge compact lamps, stools, or rugs into anchor-sized objects.\n"
                "- Preserve the existing room depth and camera position; never rotate the room to build a more frontal composition.\n"
                "--------------------------------------------------\n"
            )

        if scale_guide_path:
            scale_guide_context = (
                "\n<SCALE GUIDE STATUS (DEBUG ONLY)>\n"
                "A 500mm x 500mm floor guide was analyzed offline for spatial calibration.\n"
                "The guide image itself is NOT provided to the model.\n"
                "Do NOT render any grid lines, guides, overlays, or measurement marks in the final image.\n"
                "The final output must remain a clean staged interior photo.\n"
                "--------------------------------------------------\n"
            )

        if isinstance(scene_contract, dict):
            critical_item_keys = ", ".join([str(x) for x in (scene_contract.get("critical_item_keys") or [])[:8]])
            critical_families = ", ".join([str(x) for x in (scene_contract.get("critical_families") or [])[:8]])
            if critical_item_keys or critical_families:
                scene_contract_context = (
                    "\n<SCENE CONTRACT>\n"
                    f"geometry_source={scene_contract.get('geometry_source')} confidence={scene_contract.get('geometry_confidence')}\n"
                    + (f"critical_items={critical_item_keys}\n" if critical_item_keys else "")
                    + (f"critical_families={critical_families}\n" if critical_families else "")
                    + "Do not sacrifice critical items for overall mood.\n"
                    + "--------------------------------------------------\n"
                )

        size_hierarchy_hint = ""
        try:
            if size_hierarchy and isinstance(size_hierarchy, list):
                size_hierarchy_hint = " > ".join([str(x) for x in size_hierarchy if x])
            elif furniture_specs_json and isinstance(furniture_specs_json, dict):
                hierarchy = (
                    furniture_specs_json.get("size_hierarchy_scale")
                    or furniture_specs_json.get("size_hierarchy")
                    or []
                )
                if isinstance(hierarchy, list):
                    size_hierarchy_hint = " > ".join([str(x) for x in hierarchy if x])
        except Exception:
            size_hierarchy_hint = ""

        if windows_present:
            window_context = (
                "<WINDOWS DETECTED: YES>\n"
                "Curtains are the ONLY allowed extra element even if not listed.\n"
                "Add minimal floor-to-ceiling **Sheer White Chiffon Curtains** ONLY along the vertical edges of the visible window glass.\n"
                "Do NOT cover solid walls or doors. Keep coverage to outer 10-15% of the glass.\n"
                "If any window is unclear or not visible, do NOT add curtains there.\n\n"
            )
        else:
            window_context = (
                "<WINDOWS DETECTED: NO>\n"
                "Do NOT add curtains or blinds. Do NOT add or invent windows.\n\n"
            )

        room_input_label = "Empty Room (Target Canvas - KEEP THIS):"
        task_intro = (
            "IMAGE MANIPULATION TASK (Virtual Staging - Overlay Only):\n"
            "Your goal is to PLACE furniture into the EXISTING empty room image without changing the room itself.\n\n"
        )
        listed_items_rule = "6. **ONLY LISTED ITEMS:** Render every listed item exactly the requested quantity. Do NOT add extra furniture, extra rugs, or generic substitutes.\n"

        user_original_prompt = (
            f"{task_intro}"
            "<CRITICAL: ARCHITECTURAL FREEZE (PRIORITY #1)>\n"
            "<SAME EMPTY ROOM STRUCTURE LOCK>\n"
            "- Treat the input empty room as a locked camera plate. The final image must be the same room photograph with furniture composited into it, not a newly staged camera view.\n"
            "- Do not rotate the camera, pan sideways, orbit, truck, dolly, or switch to a more frontal composition. Keep the exact same viewing direction and vanishing points.\n"
            "- The door/window/wall/corner layout must be pixel-position consistent with the input empty room. A listed item may cover part of the room, but uncovered architecture must remain in the same location, scale, and orientation.\n"
            "- If fitting furniture would require moving a door, window, wall corner, ceiling edge, floor pattern, or camera position, keep the room unchanged and adjust only the furniture placement/scale.\n\n"
            "1. **DO NOT RE-GENERATE THE ROOM:** The walls, ceiling, floor pattern, room corners, door/window positions, and any visible openings/views must remain 100% IDENTICAL to the input empty room.\n"
            "2. **PERSPECTIVE LOCK:** You must use the EXACT same camera angle, lens, viewpoint, vanishing points, and perspective. Do not zoom in, do not zoom out.\n"
            "3. **DEPTH PRESERVATION:** Do not expand the room. Keep the original spatial depth.\n"
            "4. **FRAMING LOCK:** Keep the full room framing. Do NOT crop to a close-up. The ceiling and floor edges must match the input.\n"
            "5. **CORNER VISIBILITY:** Both left and right wall corners must remain visible, matching the input framing.\n\n"
            "6. **OPENING LOCK:** Keep every door, window, balcony opening, wall return, and column on the exact same side and in the exact same pixel location as the input image.\n"
            "7. **NO ROOM ROTATION OR RESTAGING:** Do not rotate the room, do not pan/truck/dolly/orbit the camera, do not convert an angled shot into a frontal shot, and do not recompose the architecture around the furniture.\n\n"
            "<CRITICAL: FURNITURE COMPOSITING>\n"
            "1. **SCALE:** Fit furniture realistically within the *existing* floor space.\n"
            "2. **PLACEMENT:** Obey the item category and the user's placement instruction. Floor items belong on the floor, wall-attached items stay on the wall plane, and ceiling fixtures remain suspended from the ceiling plane.\n"
            "3. **AXIS ALIGNMENT:** If the room edges, window mullions, or major wall lines are straight, keep sofas, storage, rugs, and large tables parallel or perpendicular to those dominant room axes. Do not place them on a casual 20-60 degree diagonal unless explicit instructions require it.\n"
            "4. **PRODUCT EXACTNESS FIRST:** Match each provided furniture cutout as the exact product identity. Same-family substitutes are invalid even if the placement and scale feel plausible.\n"
            "4a. **PRODUCT PART LOCK:** keep each product's facing direction, module count, visible part count, support geometry, lampshade color, and silhouette from its reference.\n"
            "4b. **NO PRODUCT RECOMPOSITION:** Do not rotate, simplify, fuse, split, or recompose product parts to improve styling. A less stylish exact product is better than a plausible substitute.\n"
            "4c. **LIGHTING SCALE LOCK:** Do not miniaturize lighting products into tabletop decor, tabletop sculpture, or duplicated small props; floor lamps stay floor-standing, table lamps use this support priority: storage/cabinet top first, side table second, floor fallback only when neither support exists; pendant/ceiling lights stay attached to the ceiling plane.\n"
            "4d. **ARTWORK / POSTER PLACEMENT:** Paintings, posters, framed prints, and wall art go on a solid wall first; if not mounted, they may lean slightly from the floor against a non-window wall. Never place them on a window surface, window plane, or directly in front of a window.\n"
            + (
                f"4e. **PRIMARY LOCK ORDER:** Resolve these hero products first and keep them exact before refining any supporting item: {', '.join(anchor_labels[:4])}.\n"
                if anchor_labels
                else ""
            )
            + (
                "4f. **SUPPORTING ITEM RULE:** If the scene becomes visually crowded, simplify secondary items before changing any primary lock silhouette, material, frame, or proportions.\n"
                if anchor_labels
                else ""
            )
            +
            "5. **STYLE:** Match the intended style implied by the provided furniture items.\n"
            f"{listed_items_rule}"
            f"{window_context}"
            f"<CRITICAL: MATHEMATICAL SCALE ENFORCEMENT (PRIORITY #0)>\nYou are provided with ACTUAL DIMENSIONS, PRIMARY ANCHOR, and SIZE HIERARCHY. Do not ignore them.\nIMPORTANT: The 'PRIMARY ANCHOR' is the largest movable furniture reference (EXCLUDING rugs/carpets when possible).\nSIZE HIERARCHY (largest -> smallest, exclude rugs/carpets): {size_hierarchy_hint}\n\n"
            "You are provided with ACTUAL DIMENSIONS and item-to-room ratio guidance. Do not ignore them.\n"
            "1. **SPECIFIC SCALE ANALYSIS FOR THIS REQUEST:**\n"
            f"{calculated_analysis if calculated_analysis else '   - (Apply relative scaling based on provided specs)'}\n"
            "2. **RELATIVE W/D/H HIERARCHY:**\n"
            "   - You MUST maintain the visual width/depth/height hierarchy specified in the specs.\n"
            "   - Example: If Item A (H: 950mm) is taller than Item B (H: 775mm), Item A MUST be rendered taller than Item B in the image.\n"
            "3. **RATIO LOCK:**\n"
            "   - Calculate: (Furniture W/D/H) / (Room W/D/H) = Coverage ratios.\n"
            "   - Strictly follow these percentages. Do not shrink items into 'miniature' versions to create empty space.\n"
            "   - **STRICT PROHIBITION:** Do not resize items for 'vibe' or 'aesthetic balance'. Follow the NUMBERS strictly.\n"
            "4. **HEIGHT CONSISTENCY:**\n"
            "   - Do NOT make a shorter item appear taller by placing it closer to the camera.\n"
            "   - Apparent height must respect the real H ratios across all items.\n"
            "5. **NO GUIDE ARTIFACTS:**\n"
            "   - Never render grid lines, measurement marks, drafting guides, fluorescent overlays, or any scale annotation in the final image.\n"
            "<CRITICAL: LIGHTING PRESERVATION (PRIORITY #1)>\n"
            "1. **KEEP EXISTING LIGHTING LOGIC:** Follow the input image's visible light sources and direction.\n"
            "2. **EXPOSURE RULE:** Bright and airy (not dark), while preserving highlight detail (no blown-out whites).\n"
            "3. **LIGHT DIRECTION:** Keep shadows consistent with the existing key light direction.\n"
            "4. **NO DIM ROOM:** Do NOT generate a dim, underexposed, moody, or nighttime look.\n"
            "5. **WHITE BALANCE:** Natural neutral white balance. Avoid excessive yellow/orange cast, but preserve realistic sunlight warmth and material color.\n"
            "6. **NO NEW OPENINGS:** Do not add new windows/doors or fake exterior light sources.\n\n"
            "<CRITICAL: PHOTOREALISTIC LIGHTING INTEGRATION (HYBRID: DAYLIGHT + ARTIFICIAL)>\n"
            "1. **LIGHTING STATE: SUBTLE SUPPORT ONLY (NEUTRAL):**\n"
            "   - **ACTION:** Keep interior fixtures ON only if they appear in the reference; no extra fixtures.\n"
            "   - **VISUALS:** Avoid visible glow/bloom halos. Lights should look realistic and restrained.\n"
            "2. **LIGHTING HIERARCHY (KEY vs. FILL):**\n"
            "   - **KEY LIGHT (DOMINANT):** Use the existing dominant light source visible in the input. Do NOT invent new openings.\n"
            "   - **FILL LIGHT (SECONDARY):** Interior lights act as gentle fill. They must NOT overpower the key light.\n"
            "3. **STRICT COLOR TEMPERATURE CONTROL (NO YELLOW):**\n"
            "   - **Target Temperature:** Use **Neutral White (4000K-5000K)** for any artificial lights to match daylight.\n"
            "   - **PROHIBITED:** No warm/tungsten/orange bulbs (2700K). No vintage/sepia cast.\n"
            "4. **SHADOW PHYSICS:**\n"
            "   - Cast soft, directional shadows driven by the existing key light direction.\n"
            "   - Use interior lights only to lift the darkest corners slightly.\n"
            "   - Shadows and light gradients must be smooth and clean; avoid blotchy noise or muddy patches on floors.\n"
            "5. **ATMOSPHERE:**\n"
            "   - Bright and airy, but never overlit. Preserve highlight detail and avoid glare.\n"
            "   - Lighting must feel natural and cohesive across all surfaces (especially floors); no artificial blotches.\n"
            "   - **OUTPUT RULE:** Return the image with furniture added, blended with the existing lighting (daylight or ambient) without introducing new openings.\n"
            "<PHOTOREAL MATERIAL REALISM>\n"
            "Preserve real material texture and tactile surface detail: leather grain, fabric weave, wood grain, glass reflections, and metal highlights.\n"
            "Avoid clay-like, waxy, plastic, CGI, overly smooth, or over-airbrushed furniture surfaces.\n"
        )

        style_direction_context = (
            f"<STYLE DIRECTION>\n{normalized_style_prompt}\n--------------------------------------------------\n"
            if normalized_style_prompt
            else ""
        )

        base_prompt = (
            "ACT AS: Professional Interior Photographer.\n"
            f"{style_direction_context}"
            f"{room_analysis_context}\n"
            f"{specs_context}\n"
            f"{dims_table_context}\n"
            f"{incomplete_dims_context}\n"
            f"{identity_context}\n"
            f"{layout_envelope_context}\n"
            f"{small_scale_context}\n"
            f"{scale_plan_context}\n"
            f"{geometry_contract_context}\n"
            f"{scene_contract_context}\n"
            f"{placement_plan_context}\n"
            f"{spatial_context}\n"
            f"{scale_guide_context}\n"
            f"{inventory_context}\n"
            f"{ratio_rules_context}\n"
            f"{user_original_prompt}\n\n"
            f"<CRITICAL: OUTPUT FORMAT ENFORCEMENT -> {ratio_instruction}>\n"
            "1. **FULL BLEED CANVAS:** The output image MUST fill the entire canvas from edge to edge. **NO WHITE BARS.** NO SPLIT SCREENS.\n"
            "2. **NO TEXT OVERLAY:** Do NOT write any dimensions, labels, or watermarks on the final image. It must be a clean photo.\n"
            "3. **ASPECT RATIO LOCK (HARD):** You MUST output EXACTLY " + ratio_instruction + ". Any other ratio is invalid.\n"
            "4. **LANDSCAPE OUTPUT ONLY:** Keep the final main render landscape (16:9) even if some references or inputs are portrait.\n"
            "5. **IGNORE SOURCE ORIENTATION:** Preserve the scene faithfully, but do not switch the final canvas to portrait.\n"
            "6. **IGNORE REFERENCE RATIO:** You MUST output a " + ratio_instruction + " image. Do not mimic any reference image shape.\n"
            "7. **NO MULTI-PANEL OUTPUT:** Output must be ONE single staged room photograph only. Do NOT append catalog sheets, white inventory panels, split layouts, or include the reference image anywhere."
        ).replace("{size_hierarchy_hint}", size_hierarchy_hint or "")

        reference_content = []

        furniture_atlas_reference_attached = bool(
            furniture_atlas_reference_path
            and os.path.exists(furniture_atlas_reference_path)
        )
        if furniture_atlas_reference_attached:
            with Image.open(furniture_atlas_reference_path) as atlas_reference_opened:
                atlas_reference_img = ImageOps.exif_transpose(
                    atlas_reference_opened
                ).convert("RGB")
            try:
                atlas_reference_img.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
            except Exception:
                pass
            extra_imgs.append(atlas_reference_img)
            reference_content += [
                (
                    "Furniture-Only Object Atlas Reference "
                    "(MOVABLE OBJECT identity, count, material, color, and source adjacency evidence only; "
                    "the neutral tiles contain no valid room camera, crop, perspective, vanishing-point, "
                    "architecture, or source pixel-position authority. Reproject the listed objects into the "
                    "FINAL locked target canvas and never duplicate fragmented atlas regions)."
                ),
                atlas_reference_img,
            ]

        furnished_scene_reference_attached = bool(
            furnished_scene_reference_path
            and os.path.exists(furnished_scene_reference_path)
        )
        if furnished_scene_reference_attached:
            with Image.open(furnished_scene_reference_path) as scene_reference_opened:
                scene_reference_img = ImageOps.exif_transpose(
                    scene_reference_opened
                ).convert("RGB")
            try:
                scene_reference_img.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
            except Exception:
                pass
            extra_imgs.append(scene_reference_img)
            reference_content += [
                (
                    "Furnished Scene Reference (EXACT MOVABLE-SCENE INVENTORY AND APPEARANCE ONLY - "
                    "restore every visible movable object with the same identity, count, material, color, physical "
                    "orientation, relative arrangement, and world-space footprint; this reference has ZERO authority "
                    "over camera, crop, perspective, vanishing points, architecture, or source pixel coordinates)."
                ),
                scene_reference_img,
            ]

        def _build_content(
            *,
            prompt_override: str | None = None,
            reference_override: list | None = None,
            room_image_override=None,
            room_label: str | None = None,
        ):
            prompt = prompt_override if prompt_override is not None else base_prompt
            image = room_image_override if room_image_override is not None else room_img
            refs = reference_override if reference_override is not None else reference_content
            if furniture_atlas_reference_attached or furnished_scene_reference_attached:
                return [
                    prompt,
                    *list(refs or []),
                    (
                        room_label
                        or "FINAL Locked Empty-Room Target Canvas "
                        "(SOLE camera, crop, perspective, architecture, and pixel-position authority - EDIT THIS IMAGE):"
                    ),
                    image,
                ]
            return [prompt, room_label or room_input_label, image, *list(refs or [])]

        try:
            if furniture_specs_json and isinstance(furniture_specs_json, dict):
                cutouts = []
                grouped_sheet_items = []
                items_for_cutout = list(furniture_specs_json.get("items") or [])
                def _cutout_item_key(row: dict) -> str:
                    return str(row.get("target_key") or row.get("source_index") or row.get("label") or "").strip()

                def _cutout_scale_priority(row: dict):
                    dm = (row or {}).get("dims_mm") or {}
                    try:
                        w = int(dm.get("width_mm") or 0)
                    except Exception:
                        w = 0
                    try:
                        d = int(dm.get("depth_mm") or 0)
                    except Exception:
                        d = 0
                    try:
                        h = int(dm.get("height_mm") or 0)
                    except Exception:
                        h = 0
                    has_dims = 1 if (w > 0 or d > 0 or h > 0) else 0
                    try:
                        vol = int((row or {}).get("volume_proxy") or 0)
                    except Exception:
                        vol = 0
                    try:
                        cat = int((row or {}).get("category_score") or 0)
                    except Exception:
                        cat = 0
                    try:
                        idx = int((row or {}).get("index") or 0)
                    except Exception:
                        idx = 0
                    item_key = _cutout_item_key(row)
                    is_primary_anchor = 1 if item_key and item_key in anchor_key_set else 0
                    return (is_primary_anchor, has_dims, vol, w, d, h, cat, -idx)

                valid_cutout_items = []
                for it in items_for_cutout:
                    cp = it.get("crop_path") if isinstance(it, dict) else None
                    if cp and os.path.exists(cp):
                        valid_cutout_items.append(it)
                first_pass_items, grouped_sheet_items = _split_cutout_reference_items_for_generation(
                    valid_cutout_items,
                    direct_sort_key=_cutout_scale_priority,
                )
                cutouts.extend(first_pass_items)
                for it in cutouts:
                    cp = it.get("crop_path")
                    raw_lbl = (it.get("label") or "").strip() or "Item"
                    lbl = _item_display_label_for_prompt(it)
                    item_key = str(it.get("target_key") or it.get("source_index") or it.get("label") or "").strip()
                    category = _item_category_for_prompt(it)
                    qty = int(it.get("qty") or 1)
                    if qty < 1:
                        qty = 1
                    item_id = str(it.get("item_id") or "").strip()
                    source_index = str(it.get("source_index") or "").strip()
                    dims = normalize_dims_dict(it.get("requested_dims_mm") or it.get("dims_mm") or {})
                    w = dims.get("width_mm")
                    d = dims.get("depth_mm")
                    h = dims.get("height_mm")
                    opts = it.get("options")
                    opts_txt = "null"
                    if isinstance(opts, (dict, list)):
                        try:
                            opts_txt = json.dumps(opts, ensure_ascii=False)
                        except Exception:
                            opts_txt = str(opts)
                    elif isinstance(opts, str) and opts.strip():
                        opts_txt = opts.strip()
                    cutout_img = Image.open(cp)
                    try:
                        max_thumb = _reference_thumbnail_size(it)
                        cutout_img.thumbnail((max_thumb, max_thumb), Image.Resampling.LANCZOS)
                    except Exception:
                        pass
                    extra_imgs.append(cutout_img)
                    reference_header = (
                        "Furniture Cutout Reference (LISTED PRODUCT LOCK - ADD THIS EXACT PRODUCT IN THE MAIN RENDER; "
                        "generic same-family substitutes are invalid; topology, distinctive parts, and material cues must match the reference; "
                        "cross-product feature borrowing is invalid). "
                    )
                    if item_key and item_key in anchor_key_set:
                        reference_header = (
                            "Furniture Cutout Reference (PRIMARY PRODUCT LOCK - PRIMARY EXACTNESS ANCHOR - MUST MATCH THIS EXACT PRODUCT DESIGN; "
                            "cross-product feature borrowing is invalid). "
                        )
                    raw_label_text = f"| RawLabel={raw_lbl} " if raw_lbl and raw_lbl != lbl else ""
                    reference_entry = [
                        (
                            reference_header
                            + f"Label={lbl} | TargetKey={item_key or 'null'} "
                            + raw_label_text
                            + f"| SourceIndex={source_index or 'null'} | ItemID={item_id or 'null'} "
                            + f"| Category={category} | Qty={qty} | W={w if w is not None else 'null'}mm "
                            f"D={d if d is not None else 'null'}mm H={h if h is not None else 'null'}mm "
                            f"| Options={opts_txt}"
                            f"{_build_reference_identity_suffix(it)}"
                        ),
                        cutout_img,
                    ]
                    reference_content += reference_entry
                if grouped_sheet_items:
                    sheet_header, sheet_img = _build_grouped_small_item_sheet_reference(grouped_sheet_items)
                    extra_imgs.append(sheet_img)
                    reference_content += [sheet_header, sheet_img]
            if not reference_content and ref_path:
                fallback_refs = ref_path if isinstance(ref_path, (list, tuple)) else [ref_path]
                for index, raw_path in enumerate(fallback_refs, start=1):
                    path_str = str(raw_path or "").strip()
                    if not path_str or not os.path.exists(path_str):
                        continue
                    ref_img = Image.open(path_str)
                    try:
                        ref_img.thumbnail((384, 384), Image.Resampling.LANCZOS)
                    except Exception:
                        pass
                    extra_imgs.append(ref_img)
                    fallback_entry = [
                        f"Fallback Furniture Reference Image {index} (EXACTNESS ANCHOR - use this reference image even if structured item cards are unavailable).",
                        ref_img,
                    ]
                    reference_content += fallback_entry
        except Exception:
            pass

        remaining = max(30, total_timeout_limit - (time.time() - start_time))
        safety_settings = allow_all_safety_settings()

        def _save_render_from_response(response, *, prefix: str):
            if response and hasattr(response, "candidates") and response.candidates and hasattr(response, "parts"):
                for part in response.parts:
                    if hasattr(part, "inline_data"):
                        timestamp = int(time.time())
                        filename = f"{prefix}_{timestamp}_{unique_id}.png"
                        path = os.path.join("outputs", filename)
                        with open(path, "wb") as output_file:
                            output_file.write(part.inline_data.data)
                        normalized_path = _normalize_render_candidate_aspect(
                            path,
                            room_path,
                            expected_ratio=expected_ratio,
                            ratio_tol=ratio_tol,
                            match_aspect_to_target=match_aspect_to_target,
                            log_brief=log_brief,
                        )
                        if normalized_path is None:
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                            return None
                        if normalized_path != path:
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                        return normalized_path
            return None

        effective_generation_attempts: int | None = None
        try:
            if max_generation_attempts is not None:
                effective_generation_attempts = max(1, int(max_generation_attempts))
        except Exception:
            effective_generation_attempts = None

        def _generation_request_options(current_timeout: float) -> dict:
            request_options = {
                "timeout": current_timeout,
                "aspect_ratio": "16:9",
                "image_size": "4K",
                "thinking_level": "high",
                "include_thoughts": False,
            }
            if effective_generation_attempts is not None:
                request_options["max_attempts"] = effective_generation_attempts
            elif b_lite_runtime:
                request_options["max_attempts"] = 1
            return request_options

        def _call_generation(content: list, *, current_timeout: float, log_tag: str):
            response = generation_call(
                resolved_generation_model,
                content,
                _generation_request_options(current_timeout),
                safety_settings,
                system_instruction,
                log_tag=log_tag,
            )
            return response

        def _render_once():
            current_timeout = _bounded_stage2_timeout()
            if current_timeout <= 0.0:
                return None
            content = _build_content()
            response = _call_generation(
                content,
                current_timeout=current_timeout,
                log_tag="Stage2.Furnish",
            )
            return _save_render_from_response(response, prefix="result")

        b_lite_runtime = strict_scale_requested
        max_attempts = effective_generation_attempts if effective_generation_attempts is not None else (1 if b_lite_runtime else 3)
        guide_attached_to_prompt = False
        last_path = None
        last_success_path = None
        scalecheck_fail_count = 0
        scalecheck_retry_count = 0
        scale_check_failed = False
        scalecheck_issues: list[str] = []
        last_structured_failed_rules: list[str] = []
        scalecheck_diagnostics: dict = {}
        def _validate_candidate(
            candidate_path: str,
            focus_item_keys: list[str] | None = None,
            *,
            skip_reference_review: bool = False,
        ):
            if guide_attached_to_prompt and _has_scale_guide_leak(candidate_path, scale_guide_path):
                return {
                    "ok": False,
                    "issues": ["scale_guide_leak_detected"],
                    "diagnostics": {
                        "failed_rules": ["scale_guide_leak_detected"],
                        "matched_items": {},
                        "unmatched_items": [],
                        "rule_details": {},
                    },
                }
            if not (enable_scale_check and furniture_specs_json and room_dims_parsed):
                return {"ok": True, "issues": [], "diagnostics": {}}
            if _remaining_timeout_sec() <= 0.0:
                return _deadline_validation_result()
            try:
                remap_detect_timeout_sec = max(8, int(min(20.0, max(8.0, _remaining_timeout_sec()))))
                validation_result = validate_furnished_scale(
                    candidate_path,
                    furniture_specs_json,
                    room_dims_parsed,
                    room_planes,
                    primary_label=(primary_item or {}).get("label"),
                    include_diagnostics=True,
                    scale_plan=scale_plan,
                    geometry_contract=geometry_contract,
                    focus_item_keys=focus_item_keys,
                    skip_reference_review=skip_reference_review,
                    absolute_deadline_ts=(start_time + float(total_timeout_limit)),
                    remap_detect_timeout_sec=remap_detect_timeout_sec,
                    remap_detect_retry=0,
                )
                if isinstance(validation_result, tuple) and len(validation_result) >= 3:
                    ok, issues, diagnostics = validation_result[0], validation_result[1], validation_result[2]
                elif isinstance(validation_result, tuple) and len(validation_result) >= 2:
                    ok, issues = validation_result[0], validation_result[1]
                    diagnostics = {}
                else:
                    ok, issues, diagnostics = False, ["validator returned invalid result"], {}
                return {"ok": bool(ok), "issues": list(issues or []), "diagnostics": dict(diagnostics or {})}
            except Exception as exc:
                return {
                    "ok": False,
                    "issues": [f"validator exception: {exc}"],
                    "diagnostics": {
                        "failed_rules": ["validation_exception"],
                        "matched_items": {},
                        "unmatched_items": [],
                        "rule_details": {},
                    },
                }

        def _build_result(path: str | None):
            if not path:
                return None
            current_failed_rules = _merge_rule_ids(
                list((scalecheck_diagnostics or {}).get("failed_rules") or []),
                _extract_failed_rule_ids(scalecheck_issues),
            )
            if not current_failed_rules or set(current_failed_rules).issubset({"validation_exception", "scale_validation_exception"}):
                current_failed_rules = list(last_structured_failed_rules)
            result = {
                "path": path,
                "scalecheck_fail_count": scalecheck_fail_count,
                "scalecheck_retry_count": scalecheck_retry_count,
                "scale_check_failed": scale_check_failed,
                "scalecheck_issues": list(scalecheck_issues),
                "scalecheck_failed_rules": list(current_failed_rules if scale_check_failed else []),
            }
            if any(scalecheck_diagnostics.get(key) for key in ("matched_items", "unmatched_items", "rule_details", "detected_rows")):
                result["scalecheck_diagnostics"] = dict(scalecheck_diagnostics or {})
            return result

        for attempt in range(max_attempts):
            try:
                last_path = _render_once()
            except Exception as exc:
                if log_brief:
                    print(f"[ScaleCheck] render attempt {attempt+1}/{max_attempts} raised: {exc}", flush=True)
                else:
                    logger.warning(f"[ScaleCheck] render attempt {attempt+1}/{max_attempts} raised: {exc}")
                if attempt < max_attempts - 1:
                    scalecheck_retry_count += 1
                    continue
                return _build_result(last_success_path)

            if not last_path:
                if attempt < max_attempts - 1:
                    scalecheck_retry_count += 1
                continue

            last_success_path = last_path
            scale_check_failed = False
            scalecheck_issues = []
            scalecheck_diagnostics = {}
            validation = _validate_candidate(last_path)
            if not validation["ok"]:
                scalecheck_fail_count += 1
                scalecheck_issues = list(validation["issues"] or [])
                scalecheck_diagnostics = dict(validation["diagnostics"] or {})
                structured_rules = _merge_rule_ids(
                    list((scalecheck_diagnostics or {}).get("failed_rules") or []),
                    _extract_failed_rule_ids(scalecheck_issues),
                )
                if structured_rules and not set(structured_rules).issubset({"validation_exception", "scale_validation_exception"}):
                    last_structured_failed_rules = list(structured_rules)
                scale_check_failed = True
                if log_brief:
                    print(f"[ScaleCheck] FAIL attempt {attempt+1}/{max_attempts}: {', '.join(scalecheck_issues)}", flush=True)
                else:
                    logger.warning(f"[ScaleCheck] FAIL attempt {attempt+1}/{max_attempts}: {scalecheck_issues}")

                deadline_budget_exhausted = "deadline_budget_exhausted" in scalecheck_issues
                if deadline_budget_exhausted:
                    return _build_result(last_success_path or last_path)

                if attempt < max_attempts - 1:
                    scalecheck_retry_count += 1
                    continue
                return _build_result(last_success_path or last_path)

            scale_check_failed = False
            scalecheck_issues = []
            scalecheck_diagnostics = dict(validation["diagnostics"] or {})
            return _build_result(last_success_path or last_path)
        return _build_result(last_success_path or last_path)
    except Exception as exc:
        print(f"!! Stage 2 ?먮윭: {exc}", flush=True)
        return None
    finally:
        for im in extra_imgs:
            try:
                im.close()
            except Exception:
                pass
        try:
            if room_img:
                room_img.close()
        except Exception:
            pass
