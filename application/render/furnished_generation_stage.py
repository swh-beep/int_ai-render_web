import re
import json
import os
import time
from typing import Any, Callable

from PIL import Image
from application.render.placement_support import build_placement_prompt_block
from application.render.repair_strategy_stage import build_repair_strategy_plan
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
    if "mirror" in text:
        rules.extend(["wall_attached_or_leaning_as_reference", "preserve_reflective_face"])
    if "rug" in text or "carpet" in text:
        rules.extend(["floor_flat", "keep_footprint_shape", "not_wall_to_wall_unless_dimensions_require"])
    if any(token in text for token in ("lamp", "light", "pendant", "chandelier", "sconce")):
        rules.extend(["preserve_light_fixture_scale", "do_not_convert_into_furniture"])
    if "table_lamp" in text:
        rules.extend(["exact_lampshade_shape", "exact_base_and_stem_geometry", "no_generic_lamp_substitution"])
    if "chair" in text:
        rules.extend(["exact_back_seat_leg_geometry", "no_generic_chair_substitution"])
    if any(token in text for token in ("decor", "art", "frame")):
        rules.extend(["copy_artwork_image_content", "no_generic_wall_art_substitution"])
    if any(token in text for token in ("table_lamp", "decor", "vase", "object", "accessory")):
        rules.append("surface_or_compact_object_scale")
    return rules


def _prompt_cue_list(values, *, limit: int = 3) -> str:
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


def _item_identifier_bits_for_prompt(item: dict | None) -> list[str]:
    if not isinstance(item, dict):
        return []
    bits: list[str] = []
    for key in ("source_index", "item_id", "target_key"):
        value = str(item.get(key) or "").strip()
        if value:
            bits.append(f"{key}={value}")
    return bits


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
    label = str(item.get("label") or item.get("category") or "Unknown Item").strip()
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
    if support:
        bits.append(f"support={support}")
    materials = ""
    if isinstance(profile, dict):
        materials = _prompt_cue_list(profile.get("material_cues"))
    if not materials and isinstance(reference_features, dict):
        materials = _prompt_cue_list(reference_features.get("material_cues"))
    if materials:
        bits.append(f"materials={materials}")
    parts = ""
    if isinstance(profile, dict):
        parts = _prompt_cue_list(profile.get("distinctive_parts"))
    if not parts and isinstance(reference_features, dict):
        parts = _prompt_cue_list(reference_features.get("distinctive_parts"))
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
    if isinstance(archetype, dict):
        strategy = str(archetype.get("render_strategy") or "").strip()
        if strategy:
            bits.append(f"strategy={strategy}")
        forbid = _prompt_cue_list(archetype.get("forbidden_substitutions"))
        if forbid:
            bits.append(f"forbid={forbid}")
    bits.append("same_family_substitute=invalid")
    return f"- {label}: " + "; ".join(bits)


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
        "If multiple rows share the same label, they are still separate products when source_index, item_id, or target_key differ. Render each distinct product once per its qty; never merge them or reuse one reference image for another row.\n",
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


def _build_repair_focus_context(
    diagnostics: dict | None,
    furniture_specs_json: dict | None,
    repair_plan: dict | None = None,
) -> str:
    if not isinstance(diagnostics, dict):
        return ""
    repair_plan = repair_plan if isinstance(repair_plan, dict) else build_repair_strategy_plan(diagnostics, furniture_specs_json, limit=4)
    repair_targets = [row for row in (repair_plan.get("repair_targets") or []) if isinstance(row, dict)]
    if not repair_targets and not (diagnostics.get("failed_rules") or []):
        return ""

    by_key = {}
    for item in (furniture_specs_json or {}).get("items") or []:
        if isinstance(item, dict):
            by_key[str(item.get("target_key") or "")] = item

    lines = []
    mirror_present = False
    for row in repair_targets[:6]:
        item = by_key.get(str(row.get("target_key") or "")) or {}
        profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
        product_identity = (item.get("product_identity") or {}) if isinstance(item, dict) else {}
        archetype = (item.get("archetype_strategy") or {}) if isinstance(item, dict) else {}
        envelope = (item.get("layout_envelope") or {}) if isinstance(item, dict) else {}
        placement_contract = (item.get("placement_contract") or {}) if isinstance(item, dict) else {}
        cue_bits = []
        if row.get("repair_actions"):
            cue_bits.append(f"repair={','.join([str(x) for x in (row.get('repair_actions') or [])[:2]])}")
        if product_identity.get("family") or profile.get("family"):
            cue_bits.append(f"family={product_identity.get('family') or profile.get('family')}")
        if profile.get("silhouette_summary"):
            cue_bits.append(f"silhouette={profile.get('silhouette_summary')}")
        topology = ", ".join((product_identity.get("topology_cues") or [])[:3])
        if topology:
            cue_bits.append(f"topology={topology}")
        support = ", ".join((product_identity.get("support_geometry") or [])[:2])
        if support:
            cue_bits.append(f"support={support}")
        material_cues = ", ".join((profile.get("material_cues") or [])[:3])
        if material_cues:
            cue_bits.append(f"materials={material_cues}")
        preserve = ", ".join((product_identity.get("preserve_rules") or profile.get("preserve_rules") or [])[:3])
        if preserve:
            cue_bits.append(f"preserve={preserve}")
        if archetype.get("render_strategy"):
            cue_bits.append(f"strategy={archetype.get('render_strategy')}")
        if archetype.get("qc_strategy"):
            cue_bits.append(f"qc={', '.join([str(x) for x in (archetype.get('qc_strategy') or [])[:3]])}")
        if archetype.get("forbidden_substitutions"):
            cue_bits.append(f"forbid={', '.join([str(x) for x in (archetype.get('forbidden_substitutions') or [])[:2]])}")
        if placement_contract.get("zone"):
            cue_bits.append(f"zone={placement_contract.get('zone')}")
        if str(product_identity.get("family") or profile.get("family") or "").strip().lower() == "mirror":
            mirror_present = True
        if envelope.get("placement_family"):
            cue_bits.append(f"placement={envelope.get('placement_family')}")
        if envelope.get("room_width_ratio") is not None:
            cue_bits.append(f"room_width_ratio={envelope.get('room_width_ratio')}")
        lines.append(f"- {row.get('label')}: " + "; ".join(cue_bits))

    if not lines:
        return ""

    failed_rules = ", ".join([str(rule) for rule in (diagnostics.get("failed_rules") or [])[:8]])
    return (
        "\n<REPAIR FOCUS FOR NEXT RETRY>\n"
        "The previous attempt drifted on these items. Keep the room frozen and correct ONLY these objects.\n"
        + "\n".join(lines)
        + (f"\nFailed rules: {failed_rules}" if failed_rules else "")
        + ("\nMirror rule: mirrors must stay wall-attached and reflect the opposite room consistently.\n" if mirror_present else "")
        + "Do not redesign silhouettes, legs, support geometry, or materials.\n"
        + "--------------------------------------------------\n"
    )


def _repair_item_importance(item: dict, matched: dict | None = None, *, is_primary: bool = False) -> float:
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


def _repair_priority_score(issue: dict | None, item: dict, matched: dict | None = None, *, is_primary: bool = False) -> float:
    issue = issue or {}
    severity = float(issue.get("severity") or 0.8)
    confidence = float(issue.get("confidence") or (matched or {}).get("match_confidence") or 0.65)
    importance = float(issue.get("item_importance") or _repair_item_importance(item, matched, is_primary=is_primary))
    return round(severity * confidence * importance, 4)


def _reference_thumbnail_size(item: dict, matched: dict | None = None, *, is_primary: bool = False) -> int:
    importance = _repair_item_importance(item, matched, is_primary=is_primary)
    if importance >= 2.5:
        return 768
    if importance >= 1.8:
        return 640
    return 512


def _collect_repair_targets(diagnostics: dict | None, furniture_specs_json: dict | None, limit: int = 4) -> list[dict]:
    if not isinstance(diagnostics, dict) or not isinstance(furniture_specs_json, dict):
        return []
    repair_plan = build_repair_strategy_plan(diagnostics, furniture_specs_json, limit=limit)
    targets: list[dict] = []
    item_by_key = {
        str(item.get("target_key") or item.get("source_index") or item.get("label") or ""): item
        for item in (furniture_specs_json.get("items") or [])
        if isinstance(item, dict)
    }
    matched_items = diagnostics.get("matched_items") or {}
    for row in repair_plan.get("repair_targets") or []:
        if not isinstance(row, dict):
            continue
        item_key = str(row.get("target_key") or "").strip()
        item = item_by_key.get(item_key) or {}
        match_row = matched_items.get(item_key) if isinstance(matched_items, dict) else {}
        targets.append(
            {
                "item_key": item_key,
                "item": item,
                "bbox_norm": row.get("bbox_norm") or ((match_row or {}).get("bbox_norm") if isinstance(match_row, dict) else None),
                "match_row": match_row if isinstance(match_row, dict) else {},
                "priority_score": float(row.get("priority_score") or 0.0),
                "item_importance": float(row.get("item_importance") or 0.0),
                "repair_actions": list(row.get("repair_actions") or []),
                "issue_rules": list(row.get("issue_rules") or []),
                "required_parts": list(row.get("required_parts") or []),
                "forbidden_substitutions": list(row.get("forbidden_substitutions") or []),
                "unmatched": bool(row.get("unmatched")),
            }
        )
    return targets


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
    call_repair_with_failover: Callable[..., Any] | None = None,
    repair_model_name: str | None = None,
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
        repair_call = call_repair_with_failover or call_gemini_with_failover or generation_call
        resolved_generation_model = generation_model_name or model_name
        resolved_repair_model = repair_model_name or model_name or resolved_generation_model
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

        def _stage2_generation_timeout_cap(*, repair: bool = False) -> float | None:
            if not b_lite_runtime:
                return None
            return 90.0 if repair else 150.0

        def _bounded_stage2_timeout(*, repair: bool = False) -> float:
            current_timeout = _remaining_timeout_sec()
            if current_timeout <= 0.0:
                return 0.0
            timeout_cap = _stage2_generation_timeout_cap(repair=repair)
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
                    lbl = (it.get("label") or "").strip()
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
                    item_labels_by_key[item_key] = str(item.get("label") or item.get("category") or item_key)
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
                        label = (it.get("label") or "").strip() or "Unknown Item"
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
                        compact_object = bool(
                            any(token in category_text for token in ("table_lamp", "decor", "stool", "vase", "accessory"))
                            or ("rug" in category_text and room_width_ratio is not None and room_width_ratio <= 0.35)
                            or (room_width_ratio is not None and room_width_ratio <= 0.18)
                            or (room_depth_ratio is not None and room_depth_ratio <= 0.18)
                        )
                        if compact_object:
                            compact_bits = [f"W={w}mm", f"D={d}mm", f"H={h}mm"]
                            compact_note = "keep visually compact"
                            if "rug" in category_text:
                                compact_note = "keep this rug compact relative to the room, not wall-to-wall"
                            elif any(token in category_text for token in ("table_lamp", "decor", "vase", "accessory")):
                                compact_note = "keep this as a surface-scale object, never side-table sized"
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

        user_original_prompt = (
            "IMAGE MANIPULATION TASK (Virtual Staging - Overlay Only):\n"
            "Your goal is to PLACE furniture into the EXISTING empty room image without changing the room itself.\n\n"
            "<CRITICAL: ARCHITECTURAL FREEZE (PRIORITY #1)>\n"
            "1. **DO NOT RE-GENERATE THE ROOM:** The walls, ceiling, floor pattern, and any visible openings/views must remain 100% IDENTICAL to the input image.\n"
            "2. **PERSPECTIVE LOCK:** You must use the EXACT same camera angle and perspective. Do not zoom in, do not zoom out.\n"
            "3. **DEPTH PRESERVATION:** Do not expand the room. Keep the original spatial depth.\n"
            "4. **FRAMING LOCK:** Keep the full room framing. Do NOT crop to a close-up. The ceiling and floor edges must match the input.\n"
            "5. **CORNER VISIBILITY:** Both left and right wall corners must remain visible, matching the input framing.\n\n"
            "6. **OPENING LOCK:** Keep every door, window, balcony opening, wall return, and column on the exact same side and in the exact same location as the input image.\n"
            "7. **NO ROOM ROTATION OR RESTAGING:** Do not rotate the room, do not convert an angled shot into a frontal shot, and do not recompose the architecture around the furniture.\n\n"
            "<CRITICAL: FURNITURE COMPOSITING>\n"
            "1. **SCALE:** Fit furniture realistically within the *existing* floor space.\n"
            "2. **PLACEMENT:** Obey the item category and the user's placement instruction. Floor items belong on the floor, wall-attached items stay on the wall plane, and ceiling fixtures remain suspended from the ceiling plane.\n"
            "3. **AXIS ALIGNMENT:** If the room edges, window mullions, or major wall lines are straight, keep sofas, storage, rugs, and large tables parallel or perpendicular to those dominant room axes. Do not place them on a casual 20-60 degree diagonal unless explicit instructions require it.\n"
            "4. **PRODUCT EXACTNESS FIRST:** Match each provided furniture cutout as the exact product identity. Same-family substitutes are invalid even if the placement and scale feel plausible.\n"
            + (
                f"4a. **PRIMARY LOCK ORDER:** Resolve these hero products first and keep them exact before refining any supporting item: {', '.join(anchor_labels[:4])}.\n"
                if anchor_labels
                else ""
            )
            + (
                "4b. **SUPPORTING ITEM RULE:** If the scene becomes visually crowded, simplify secondary items before changing any primary lock silhouette, material, frame, or proportions.\n"
                if anchor_labels
                else ""
            )
            +
            "5. **STYLE:** Match the intended style implied by the provided furniture items.\n"
            "6. **ONLY LISTED ITEMS:** Render every listed item exactly the requested quantity. Do NOT add extra furniture, extra rugs, or generic substitutes.\n"
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

        repair_focus_context = ""
        reference_content = []

        def _build_content(
            *,
            prompt_override: str | None = None,
            reference_override: list | None = None,
            room_image_override=None,
            room_label: str = "Empty Room (Target Canvas - KEEP THIS):",
        ):
            prompt = prompt_override if prompt_override is not None else base_prompt + repair_focus_context
            image = room_image_override if room_image_override is not None else room_img
            refs = reference_override if reference_override is not None else reference_content
            return [prompt, room_label, image, *list(refs or [])]

        try:
            if furniture_specs_json and isinstance(furniture_specs_json, dict):
                cutouts = []
                items_for_cutout = list(furniture_specs_json.get("items") or [])
                two_pass_summary = (
                    furniture_specs_json.get("two_pass_strategy")
                    if isinstance(furniture_specs_json.get("two_pass_strategy"), dict)
                    else {}
                )
                pass2_detail_keys = {
                    str(value or "").strip()
                    for value in (two_pass_summary.get("pass2_detail_keys") or [])
                    if str(value or "").strip()
                }

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

                items_for_cutout.sort(key=_cutout_scale_priority, reverse=True)
                first_pass_items = [
                    item
                    for item in items_for_cutout
                    if _cutout_item_key(item) not in pass2_detail_keys
                ]
                reserve_items = [
                    item
                    for item in items_for_cutout
                    if _cutout_item_key(item) in pass2_detail_keys
                ]
                first_pass_items = first_pass_items[:12]
                for it in first_pass_items:
                    cp = it.get("crop_path")
                    if cp and os.path.exists(cp):
                        cutouts.append(it)
                for it in cutouts:
                    cp = it.get("crop_path")
                    lbl = (it.get("label") or "").strip() or "Item"
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
                    reference_header = "Furniture Cutout Reference (SECONDARY SUPPORT ITEM - KEEP PRODUCT IDENTITY AFTER PRIMARY LOCKS ARE CORRECT). "
                    if item_key and item_key in anchor_key_set:
                        reference_header = "Furniture Cutout Reference (PRIMARY PRODUCT LOCK - PRIMARY EXACTNESS ANCHOR - MUST MATCH THIS EXACT PRODUCT DESIGN). "
                    reference_entry = [
                        (
                            reference_header
                            + f"Label={lbl} | TargetKey={item_key or 'null'} "
                            + f"| SourceIndex={source_index or 'null'} | ItemID={item_id or 'null'} "
                            + f"| Category={category} | Qty={qty} | W={w if w is not None else 'null'}mm "
                            f"D={d if d is not None else 'null'}mm H={h if h is not None else 'null'}mm "
                            f"| Options={opts_txt}"
                        ),
                        cutout_img,
                    ]
                    reference_content += reference_entry
                reserve_cutouts = []
                for it in reserve_items[:4]:
                    cp = it.get("crop_path")
                    if cp and os.path.exists(cp):
                        reserve_cutouts.append(it)
                if reserve_cutouts:
                    reference_content.append(
                        "Do NOT insert these pass2 detail items yet. They are reserve references for later localized repair only."
                    )
                for it in reserve_cutouts:
                    cp = it.get("crop_path")
                    lbl = (it.get("label") or "").strip() or "Item"
                    item_key = _cutout_item_key(it)
                    category = _item_category_for_prompt(it)
                    item_id = str(it.get("item_id") or "").strip()
                    source_index = str(it.get("source_index") or "").strip()
                    qty = int(it.get("qty") or 1)
                    if qty < 1:
                        qty = 1
                    dims = normalize_dims_dict(it.get("requested_dims_mm") or it.get("dims_mm") or {})
                    w = dims.get("width_mm")
                    d = dims.get("depth_mm")
                    h = dims.get("height_mm")
                    cutout_img = Image.open(cp)
                    try:
                        max_thumb = _reference_thumbnail_size(it)
                        cutout_img.thumbnail((max_thumb, max_thumb), Image.Resampling.LANCZOS)
                    except Exception:
                        pass
                    extra_imgs.append(cutout_img)
                    reference_content += [
                        (
                            "Pass2 Detail Reserve Reference (DO NOT INSERT IN FIRST PASS; keep for targeted repair only). "
                            + f"Label={lbl} | TargetKey={item_key} | SourceIndex={source_index or 'null'} "
                            + f"| ItemID={item_id or 'null'} | Category={category} | Qty={qty} "
                            + f"| W={w if w is not None else 'null'}mm D={d if d is not None else 'null'}mm H={h if h is not None else 'null'}mm"
                        ),
                        cutout_img,
                    ]
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
        repair_attempt_count = 0
        repair_applied = False
        repair_target_keys: list[str] = []
        repair_target_labels: list[str] = []

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

        def _attempt_localized_repair(base_render_path: str, diagnostics: dict | None, repair_plan: dict | None = None):
            targets = _collect_repair_targets(
                diagnostics,
                furniture_specs_json,
                limit=max(1, len((repair_plan or {}).get("repair_targets") or [])) if isinstance(repair_plan, dict) else 4,
            )
            if not targets:
                return None, []
            critical_targets = [
                row for row in targets
                if str((((row.get("item") or {}).get("archetype_strategy") or {}).get("strictness") or "")).strip().lower() == "critical"
                or bool(row.get("unmatched"))
            ]
            if critical_targets:
                targets = critical_targets[:3]
            else:
                targets = targets[:3]

            opened = []
            try:
                current_timeout = _bounded_stage2_timeout(repair=True)
                if current_timeout <= 0.0:
                    return None, []
                base_img = Image.open(base_render_path)
                opened.append(base_img)
                content = [
                    (
                        "LOCALIZED FURNITURE REPAIR TASK.\n"
                        "Use the current staged image as the base image.\n"
                        "Keep the room architecture, lighting, camera framing, and untouched furniture unchanged.\n"
                        "Edit ONLY the listed furniture targets. Do not redesign silhouettes.\n"
                        "If a bbox is provided, confine the edit to that region with a small safety margin.\n"
                        "If a target is missing, insert it at plausible scale using the provided layout envelope.\n"
                        + (
                            "Mirrors must stay wall-attached and reflect a plausible opposite room view.\n"
                            if any(str((((row.get('item') or {}).get('identity_profile') or {}).get('family') or '')).strip().lower() == "mirror" for row in targets)
                            else ""
                        )
                    ),
                    "Current staged image (edit this image only):",
                    base_img,
                ]
                for row in targets:
                    item = row.get("item") or {}
                    profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
                    archetype = (item.get("archetype_strategy") or {}) if isinstance(item, dict) else {}
                    envelope = (item.get("layout_envelope") or {}) if isinstance(item, dict) else {}
                    dims = normalize_dims_dict(item.get("requested_dims_mm") or item.get("dims_mm") or {})
                    bbox_norm = row.get("bbox_norm")
                    bbox_text = ""
                    if isinstance(bbox_norm, (list, tuple)) and len(bbox_norm) == 4:
                        bbox_text = f" | RepairBBoxNorm={','.join([f'{float(v):.3f}' for v in bbox_norm])}"
                    content.append(
                        (
                            "Repair target. "
                            f"Label={item.get('label') or 'Item'} | TargetKey={row.get('item_key')} "
                            f"| Family={profile.get('family') or item.get('category') or 'unknown'} "
                            f"| W={dims.get('width_mm')}mm D={dims.get('depth_mm')}mm H={dims.get('height_mm')}mm"
                            f"{bbox_text}"
                            + (
                                f" | RepairActions={', '.join((row.get('repair_actions') or [])[:3])}"
                                if row.get("repair_actions")
                                else ""
                            )
                            + (
                                f" | IssueRules={', '.join((row.get('issue_rules') or [])[:3])}"
                                if row.get("issue_rules")
                                else ""
                            )
                            + (
                                f" | DistinctiveParts={', '.join((profile.get('distinctive_parts') or [])[:4])}"
                                if profile.get("distinctive_parts")
                                else ""
                            )
                            + (
                                f" | PreserveRules={', '.join((profile.get('preserve_rules') or [])[:4])}"
                                if profile.get("preserve_rules")
                                else ""
                            )
                            + (
                                f" | Archetype={archetype.get('render_strategy')}"
                                if archetype.get("render_strategy")
                                else ""
                            )
                            + (
                                f" | ForbiddenSubstitutions={', '.join((row.get('forbidden_substitutions') or archetype.get('forbidden_substitutions') or [])[:3])}"
                                if (row.get("forbidden_substitutions") or archetype.get("forbidden_substitutions"))
                                else ""
                            )
                            + (
                                f" | RequiredParts={', '.join((row.get('required_parts') or [])[:4])}"
                                if row.get("required_parts")
                                else ""
                            )
                            + (
                                f" | LayoutEnvelope=width:{envelope.get('room_width_ratio')} depth:{envelope.get('room_depth_ratio')} height:{envelope.get('room_height_ratio')}"
                                if envelope
                                else ""
                            )
                        )
                    )
                    crop_path = item.get("crop_path")
                    if crop_path and os.path.exists(crop_path):
                        crop_img = Image.open(crop_path)
                        try:
                            max_thumb = _reference_thumbnail_size(
                                item,
                                row.get("match_row"),
                                is_primary=(str(row.get("item_key") or "") == str((primary_item or {}).get("target_key") or "")),
                            )
                            crop_img.thumbnail((max_thumb, max_thumb), Image.Resampling.LANCZOS)
                        except Exception:
                            pass
                        opened.append(crop_img)
                        content.extend(["Reference crop (must preserve exact design):", crop_img])

                request_options = {
                    "timeout": current_timeout,
                    "aspect_ratio": "16:9",
                    "thinking_level": "high",
                    "include_thoughts": False,
                }
                if effective_generation_attempts is not None:
                    request_options["max_attempts"] = effective_generation_attempts
                elif b_lite_runtime:
                    request_options["max_attempts"] = 1
                response = repair_call(
                    resolved_repair_model,
                    content,
                    request_options,
                    safety_settings,
                    system_instruction,
                    log_tag="Stage2.LocalizedRepair",
                )
                return _save_render_from_response(response, prefix="repair"), targets
            except Exception as exc:
                if log_brief:
                    print(f"[LocalizedRepair] skipped: {exc}", flush=True)
                else:
                    logger.warning(f"[LocalizedRepair] skipped: {exc}")
                return None, []
            finally:
                for image in opened:
                    try:
                        image.close()
                    except Exception:
                        pass

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
            if repair_applied or repair_attempt_count or repair_target_keys or repair_target_labels:
                result["repair_applied"] = repair_applied
                result["repair_attempt_count"] = repair_attempt_count
                result["repair_target_keys"] = list(repair_target_keys)
                result["repair_target_labels"] = list(repair_target_labels)
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

                can_try_repair = (
                    "scale_guide_leak_detected" not in scalecheck_issues
                    and not deadline_budget_exhausted
                )
                if can_try_repair:
                    repair_plan = build_repair_strategy_plan(scalecheck_diagnostics, furniture_specs_json, limit=3)
                    repair_path, repair_targets = _attempt_localized_repair(last_path, scalecheck_diagnostics, repair_plan)
                    if repair_path:
                        repair_attempt_count += 1
                        repair_applied = True
                        repair_target_keys = [str(row.get("item_key") or "") for row in repair_targets if row.get("item_key")]
                        repair_target_labels = [str(((row.get("item") or {}).get("label") or "")) for row in repair_targets if (row.get("item") or {}).get("label")]
                        partial_revalidate_keys = {
                            str(value or "").strip()
                            for value in (
                                repair_target_keys
                                + list((scalecheck_diagnostics.get("cheap_first_item_keys") or []))
                                + [
                                    str(
                                        ((furniture_specs_json.get("primary_scale") or {}) if isinstance(furniture_specs_json, dict) else {}).get("target_key")
                                        or ((furniture_specs_json.get("primary") or {}) if isinstance(furniture_specs_json, dict) else {}).get("target_key")
                                        or ""
                                    ).strip()
                                ]
                            )
                            if str(value or "").strip()
                        }
                        repaired_validation = _validate_candidate(
                            repair_path,
                            focus_item_keys=sorted(partial_revalidate_keys) if (b_lite_runtime and partial_revalidate_keys) else None,
                        )
                        if repaired_validation["ok"]:
                            should_run_full_scene_revalidate = not (
                                b_lite_runtime and _remaining_timeout_sec() < 20.0
                            )
                            if not should_run_full_scene_revalidate:
                                scale_check_failed = False
                                scalecheck_issues = []
                                scalecheck_diagnostics = dict(repaired_validation["diagnostics"] or {})
                                scalecheck_diagnostics["full_scene_revalidate_skipped_due_to_budget"] = True
                                last_success_path = repair_path
                                repair_focus_context = ""
                                return _build_result(repair_path)
                            full_scene_validation = _validate_candidate(repair_path)
                            if full_scene_validation["ok"]:
                                scale_check_failed = False
                                scalecheck_issues = []
                                scalecheck_diagnostics = dict(full_scene_validation["diagnostics"] or repaired_validation["diagnostics"] or {})
                                last_success_path = repair_path
                                repair_focus_context = ""
                                return _build_result(repair_path)
                            repaired_validation = full_scene_validation
                        repair_summary = _summarize_scale_review(repaired_validation["diagnostics"] or {})
                        current_summary = _summarize_scale_review(scalecheck_diagnostics or {})
                        repair_is_better = repair_summary.get("review_score", -999) > current_summary.get("review_score", -999)
                        if (
                            b_lite_runtime
                            and int(current_summary.get("matched_source_count") or 0) > 0
                            and int(repair_summary.get("matched_source_count") or 0) < int(current_summary.get("matched_source_count") or 0)
                        ):
                            repair_is_better = False
                        if repair_is_better:
                            scalecheck_issues = list(repaired_validation["issues"] or [])
                            scalecheck_diagnostics = dict(repaired_validation["diagnostics"] or {})
                            structured_rules = _merge_rule_ids(
                                list((scalecheck_diagnostics or {}).get("failed_rules") or []),
                                _extract_failed_rule_ids(scalecheck_issues),
                            )
                            if structured_rules and not set(structured_rules).issubset({"validation_exception", "scale_validation_exception"}):
                                last_structured_failed_rules = list(structured_rules)
                            last_success_path = repair_path
                            last_path = repair_path

                if attempt < max_attempts - 1:
                    scalecheck_retry_count += 1
                    repair_focus_context = _build_repair_focus_context(scalecheck_diagnostics, furniture_specs_json, repair_plan if can_try_repair else None)
                    continue
                return _build_result(last_success_path or last_path)

            scale_check_failed = False
            scalecheck_issues = []
            scalecheck_diagnostics = dict(validation["diagnostics"] or {})
            repair_focus_context = ""
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
