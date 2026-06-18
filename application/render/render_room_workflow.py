from typing import Any, Callable

from application.render.reference_preparation import prepare_render_references
from application.render.render_analysis_stage import run_render_analysis_stage
from application.render.render_audience_stage import run_render_audience_stage
from application.render.render_bootstrap_stage import run_render_bootstrap_stage
from application.render.render_empty_stage import run_render_empty_stage
from application.render.render_input_stage import run_render_input_stage
from application.render.render_postprocess_stage import run_render_postprocess_stage
from application.render.render_response_stage import build_render_response_payload, log_render_summary
from application.render.render_scale_stage import run_render_scale_stage
from application.render.render_variant_stage import run_render_variant_stage
from application.render.qc_gate_stage import annotate_variant_reviews, select_rankable_paths, sort_variant_paths
from application.render.scale_plan_support import build_scale_plan
from application.render.postprocess_support import resolve_item_family
from application.render.two_pass_strategy_stage import apply_two_pass_strategy
from application.render.render_workflow_contracts import (
    RenderWorkflowDependencies,
    RenderWorkflowRequest,
)


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _room_dims_summary_line(room_dims_contract) -> str:
    contract = room_dims_contract.as_dict() if hasattr(room_dims_contract, "as_dict") else dict(room_dims_contract or {})
    dims = dict(contract.get("dims_mm_center") or {})
    width_mm = _coerce_positive_int(dims.get("width_mm"))
    depth_mm = _coerce_positive_int(dims.get("depth_mm"))
    height_mm = _coerce_positive_int(dims.get("height_mm"))
    if not any((width_mm, depth_mm, height_mm)):
        return ""

    source = str(contract.get("source") or "").strip().lower()
    confidence = str(contract.get("confidence") or "").strip().lower()
    label = "USER-PROVIDED ROOM DIMENSIONS" if source == "explicit" else "ANALYSIS-DERIVED ROOM DIMENSIONS"
    dims_text = ", ".join(
        [
            f"W {width_mm if width_mm else 'unknown'}mm",
            f"D {depth_mm if depth_mm else 'unknown'}mm",
            f"H {height_mm if height_mm else 'unknown'}mm",
        ]
    )
    if source == "explicit" or confidence in {"", "none"}:
        return f"{label}: {dims_text}."
    return f"{label}: {dims_text}. Confidence: {confidence}."


def _room_dims_complete(dims: dict | None) -> bool:
    dims = dims if isinstance(dims, dict) else {}
    return all(_coerce_positive_int(dims.get(key)) is not None for key in ("width_mm", "depth_mm", "height_mm"))


def _room_dims_contract_dict(room_dims_contract) -> dict:
    return room_dims_contract.as_dict() if hasattr(room_dims_contract, "as_dict") else dict(room_dims_contract or {})


def _room_dims_contract_requests_strict(room_dims_contract) -> bool:
    contract = _room_dims_contract_dict(room_dims_contract)
    mode = str(contract.get("strict_scale_mode") or "").strip().lower()
    return bool(mode == "strict_geometry_mode" and contract.get("room_dims_valid") and _room_dims_complete(contract.get("dims_mm_center")))


def _room_dimensions_text_from_dims(dims: dict | None) -> str:
    dims = dims if isinstance(dims, dict) else {}
    width_mm = _coerce_positive_int(dims.get("width_mm"))
    depth_mm = _coerce_positive_int(dims.get("depth_mm"))
    height_mm = _coerce_positive_int(dims.get("height_mm"))
    if not all((width_mm, depth_mm, height_mm)):
        return ""
    return f"W {width_mm}mm x D {depth_mm}mm x H {height_mm}mm"


def _merge_room_analysis_text(room_analysis_text: str | None, room_dims_contract) -> str:
    base = str(room_analysis_text or "").strip()
    summary_line = _room_dims_summary_line(room_dims_contract)
    if not summary_line:
        return base
    if summary_line in base:
        return base
    if "USER-PROVIDED ROOM DIMENSIONS:" in base or "ANALYSIS-DERIVED ROOM DIMENSIONS:" in base:
        return base
    if not base:
        return summary_line
    return f"{base}\n{summary_line}"


def _normalize_style_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-").replace(" ", "-")


def _resolve_style_prompt(style_map: dict | None, style_name: Any) -> Any:
    if not isinstance(style_map, dict):
        return "Custom Moodboard Style"

    raw_key = str(style_name or "").strip()
    if raw_key in style_map:
        return style_map[raw_key]

    normalized = _normalize_style_key(style_name)
    if normalized and normalized in style_map:
        return style_map[normalized]

    for key, value in style_map.items():
        if _normalize_style_key(key) == normalized:
            return value

    return "Custom Moodboard Style"


def _placement_family_for_item(item: dict) -> str:
    family = resolve_item_family(item)
    if family == "mirror":
        return "wall_attached"
    if family == "rug":
        return "rug"
    if family in {"table_lamp", "decor"}:
        return "surface_placed"
    return "floor_placed"


def _refresh_layout_envelopes(items: list[dict] | None, room_dims_center: dict | None) -> list[dict]:
    room_dims_center = room_dims_center if isinstance(room_dims_center, dict) else {}
    room_width_mm = _coerce_positive_int(room_dims_center.get("width_mm")) or 0
    room_depth_mm = _coerce_positive_int(room_dims_center.get("depth_mm")) or 0
    room_height_mm = _coerce_positive_int(room_dims_center.get("height_mm")) or 0
    refreshed: list[dict] = []
    for row in items or []:
        if not isinstance(row, dict):
            continue
        row_copy = dict(row)
        dims = dict(row_copy.get("requested_dims_mm") or row_copy.get("dims_mm") or {})
        width_mm = _coerce_positive_int(dims.get("width_mm")) or 0
        depth_mm = _coerce_positive_int(dims.get("depth_mm")) or 0
        height_mm = _coerce_positive_int(dims.get("height_mm")) or 0
        envelope = {
            "room_width_ratio": round(width_mm / room_width_mm, 4) if room_width_mm > 0 and width_mm > 0 else None,
            "room_depth_ratio": round(depth_mm / room_depth_mm, 4) if room_depth_mm > 0 and depth_mm > 0 else None,
            "room_height_ratio": round(height_mm / room_height_mm, 4) if room_height_mm > 0 and height_mm > 0 else None,
            "footprint_ratio": round((width_mm * depth_mm) / max(1, room_width_mm * room_depth_mm), 4)
            if room_width_mm > 0 and room_depth_mm > 0 and width_mm > 0 and depth_mm > 0
            else None,
            "placement_family": _placement_family_for_item(row_copy),
        }
        row_copy["layout_envelope"] = envelope
        identity = dict(row_copy.get("identity_profile") or {})
        if identity:
            identity["layout_envelope"] = envelope
            row_copy["identity_profile"] = identity
        refreshed.append(row_copy)
    return refreshed


def _rebind_primary_item(items: list[dict] | None, primary_item: dict | None) -> dict | None:
    if not isinstance(primary_item, dict) or not primary_item:
        return primary_item
    primary_key = str(primary_item.get("target_key") or "")
    for row in items or []:
        if str((row or {}).get("target_key") or "") == primary_key:
            return row
    return primary_item


def _hydrate_item_dims(item: dict | None, fallback_item: dict | None = None) -> dict | None:
    if not isinstance(item, dict):
        return item

    hydrated = dict(item)
    candidate_dims = [
        hydrated.get("dims_mm"),
        hydrated.get("requested_dims_mm"),
        ((hydrated.get("product_identity") or {}).get("dims_mm") if isinstance(hydrated.get("product_identity"), dict) else None),
    ]
    if isinstance(fallback_item, dict):
        candidate_dims.extend(
            [
                fallback_item.get("dims_mm"),
                fallback_item.get("requested_dims_mm"),
                ((fallback_item.get("product_identity") or {}).get("dims_mm") if isinstance(fallback_item.get("product_identity"), dict) else None),
            ]
        )

    merged_dims: dict[str, int] = {}
    for dims in candidate_dims:
        if not isinstance(dims, dict):
            continue
        for key in ("width_mm", "depth_mm", "height_mm", "radius_mm"):
            if merged_dims.get(key):
                continue
            value = _coerce_positive_int(dims.get(key))
            if value is not None:
                merged_dims[key] = value

    if merged_dims:
        hydrated["dims_mm"] = {
            "width_mm": merged_dims.get("width_mm"),
            "depth_mm": merged_dims.get("depth_mm"),
            "height_mm": merged_dims.get("height_mm"),
            "radius_mm": merged_dims.get("radius_mm"),
        }
    return hydrated


def _sync_furniture_specs_contracts(
    furniture_specs_json: dict | None,
    analyzed_items: list[dict] | None,
    placement_plan,
) -> dict | None:
    if not isinstance(furniture_specs_json, dict):
        return furniture_specs_json

    payload = dict(furniture_specs_json)
    items_by_key = {
        str((row or {}).get("target_key") or ""): row
        for row in (analyzed_items or [])
        if isinstance(row, dict) and str((row or {}).get("target_key") or "")
    }
    synced_items = []
    for src in payload.get("items") or []:
        if not isinstance(src, dict):
            continue
        item = dict(src)
        item_key = str(item.get("target_key") or "")
        enriched = items_by_key.get(item_key)
        item = _hydrate_item_dims(item, enriched)
        if enriched:
            if enriched.get("product_identity"):
                item["product_identity"] = dict(enriched.get("product_identity") or {})
            if enriched.get("identity_profile"):
                item["identity_profile"] = dict(enriched.get("identity_profile") or {})
            if enriched.get("reference_features"):
                item["reference_features"] = dict(enriched.get("reference_features") or {})
            if enriched.get("layout_envelope"):
                item["layout_envelope"] = dict(enriched.get("layout_envelope") or {})
            if enriched.get("placement_contract"):
                item["placement_contract"] = dict(enriched.get("placement_contract") or {})
            if enriched.get("identity_confidence") is not None:
                item["identity_confidence"] = enriched.get("identity_confidence")
            if enriched.get("identity_strictness"):
                item["identity_strictness"] = enriched.get("identity_strictness")
            if enriched.get("archetype_strategy"):
                item["archetype_strategy"] = dict(enriched.get("archetype_strategy") or {})
            if enriched.get("two_pass_strategy"):
                item["two_pass_strategy"] = dict(enriched.get("two_pass_strategy") or {})
            for metadata_key in (
                "category_path",
                "category_source",
                "main_category",
                "sub_category",
                "mainCategory",
                "subCategory",
                "product_type",
            ):
                if enriched.get(metadata_key) not in (None, ""):
                    item[metadata_key] = enriched.get(metadata_key)
            if enriched.get("anchor_eligible") is not None:
                item["anchor_eligible"] = bool(enriched.get("anchor_eligible"))
            if enriched.get("pass_role"):
                item["pass_role"] = enriched.get("pass_role")
            if enriched.get("strategy_priority") is not None:
                item["strategy_priority"] = int(enriched.get("strategy_priority") or 0)
            if enriched.get("requires_identity_validation") is not None:
                item["requires_identity_validation"] = bool(enriched.get("requires_identity_validation"))
            if enriched.get("identity_validation_reason"):
                item["identity_validation_reason"] = enriched.get("identity_validation_reason")
        synced_items.append(item)
    payload["items"] = synced_items
    def _sync_primary_payload(primary_payload: dict | None) -> dict | None:
        fallback = next(
            (
                row
                for row in synced_items
                if str((row or {}).get("target_key") or "") == str(((primary_payload or {}).get("target_key") or ""))
            ),
            None,
        )
        merged = _hydrate_item_dims(primary_payload, fallback)
        if not isinstance(merged, dict) or not isinstance(fallback, dict):
            return merged
        for key in (
            "product_identity",
            "identity_profile",
            "reference_features",
            "archetype_strategy",
            "two_pass_strategy",
            "layout_envelope",
            "placement_contract",
            "category_path",
            "category_source",
            "main_category",
            "sub_category",
            "mainCategory",
            "subCategory",
            "product_type",
            "identity_confidence",
            "identity_strictness",
            "anchor_eligible",
            "pass_role",
            "strategy_priority",
            "requires_identity_validation",
            "identity_validation_reason",
        ):
            if fallback.get(key) is not None:
                merged[key] = fallback.get(key)
        return merged

    payload["primary"] = _sync_primary_payload(payload.get("primary"))
    payload["primary_scale"] = _sync_primary_payload(payload.get("primary_scale"))
    if synced_items:
        _, two_pass_summary = apply_two_pass_strategy(synced_items, primary_item=payload.get("primary_scale") or payload.get("primary"))
        payload["two_pass_strategy"] = two_pass_summary
        preferred_anchor_key = str((two_pass_summary or {}).get("recommended_anchor_key") or "").strip()
        if preferred_anchor_key:
            preferred_anchor = next(
                (
                    row
                    for row in synced_items
                    if str((row or {}).get("target_key") or "").strip() == preferred_anchor_key
                ),
                None,
            )
            if isinstance(preferred_anchor, dict):
                payload["primary_scale"] = _sync_primary_payload(preferred_anchor)
    if placement_plan is not None:
        payload["placement_plan"] = placement_plan.as_dict() if hasattr(placement_plan, "as_dict") else dict(placement_plan or {})
    return payload


def _copy_prompt_payload_value(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


_MAIN_PROMPT_ITEM_KEYS = (
    "label",
    "name",
    "category",
    "category_canonical",
    "category_path",
    "category_source",
    "main_category",
    "sub_category",
    "mainCategory",
    "subCategory",
    "product_type",
    "qty",
    "dims_mm",
    "requested_dims_mm",
    "target_key",
    "source_index",
    "item_id",
    "payload_index",
    "index",
    "crop_path",
    "options",
    "reference_features",
    "identity_profile",
    "product_identity",
    "archetype_strategy",
    "layout_envelope",
    "placement_contract",
    "identity_confidence",
    "identity_strictness",
    "pass_role",
    "strategy_priority",
    "requires_identity_validation",
    "identity_validation_reason",
    "two_pass_strategy",
)

_PASS2_RENDER_ROLES = {
    "pass2_wall",
    "pass2_support_sensitive",
    "pass2_small",
    "pass2_floor_secondary",
    "pass2_decor",
    "pass2_detail",
}
_MAX_PASS1_RENDER_ITEMS = 10


def _build_main_prompt_item_payload(src: dict | None) -> dict:
    if not isinstance(src, dict):
        return {}
    item = {}
    for key in _MAIN_PROMPT_ITEM_KEYS:
        if src.get(key) is not None:
            item[key] = _copy_prompt_payload_value(src.get(key))
    if not item.get("dims_mm") and isinstance(item.get("requested_dims_mm"), dict):
        item["dims_mm"] = dict(item["requested_dims_mm"])
    return item


def _build_simple_generation_specs(furniture_specs_json: dict | None) -> dict | None:
    if not isinstance(furniture_specs_json, dict):
        return furniture_specs_json

    simple_payload: dict[str, Any] = {}
    for key in (
        "max_width_mm",
        "max_depth_mm",
        "max_height_mm",
        "room_dims_mm",
        "room_dims",
        "size_hierarchy",
        "size_hierarchy_scale",
    ):
        if furniture_specs_json.get(key) is not None:
            simple_payload[key] = _copy_prompt_payload_value(furniture_specs_json.get(key))

    simple_items = []
    for src in furniture_specs_json.get("items") or []:
        if not isinstance(src, dict):
            continue
        item = _build_main_prompt_item_payload(src)
        simple_items.append(item)
    simple_payload["items"] = simple_items

    primary_key = str(
        ((furniture_specs_json.get("primary_scale") or {}) if isinstance(furniture_specs_json.get("primary_scale"), dict) else {}).get("target_key")
        or ((furniture_specs_json.get("primary") or {}) if isinstance(furniture_specs_json.get("primary"), dict) else {}).get("target_key")
        or ""
    ).strip()
    if primary_key:
        primary = next((item for item in simple_items if str(item.get("target_key") or "").strip() == primary_key), None)
        if isinstance(primary, dict):
            simple_payload["primary"] = dict(primary)
            simple_payload["primary_scale"] = dict(primary)
    elif isinstance(furniture_specs_json.get("primary"), dict):
        simple_payload["primary"] = _build_main_prompt_item_payload(furniture_specs_json.get("primary") or {})
    elif isinstance(furniture_specs_json.get("primary_scale"), dict):
        simple_payload["primary_scale"] = _build_main_prompt_item_payload(furniture_specs_json.get("primary_scale") or {})

    if furniture_specs_json.get("two_pass_strategy") is not None:
        simple_payload["two_pass_strategy"] = _copy_prompt_payload_value(furniture_specs_json.get("two_pass_strategy"))

    return simple_payload


def _item_target_key(item: dict | None) -> str:
    return str((item or {}).get("target_key") or "").strip()


def _item_pass_role(item: dict | None) -> str:
    if not isinstance(item, dict):
        return ""
    role = str(item.get("pass_role") or "").strip().lower()
    if role:
        return role
    strategy = item.get("two_pass_strategy") if isinstance(item.get("two_pass_strategy"), dict) else {}
    return str((strategy or {}).get("pass_role") or "").strip().lower()


def _split_generation_specs_for_render_passes(furniture_specs_json: dict | None) -> tuple[dict | None, dict | None]:
    simple_payload = _build_simple_generation_specs(furniture_specs_json)
    if not isinstance(simple_payload, dict):
        return simple_payload, None

    items = [item for item in (simple_payload.get("items") or []) if isinstance(item, dict)]
    if not items:
        return simple_payload, None

    two_pass_summary = simple_payload.get("two_pass_strategy") if isinstance(simple_payload.get("two_pass_strategy"), dict) else {}
    pass2_keys = {
        str(key or "").strip()
        for key in (two_pass_summary or {}).get("pass2_detail_keys") or []
        if str(key or "").strip()
    }

    pass1_items: list[dict] = []
    pass2_items: list[dict] = []
    for item in items:
        item_key = _item_target_key(item)
        role = _item_pass_role(item)
        if role in _PASS2_RENDER_ROLES or (item_key and item_key in pass2_keys):
            pass2_items.append(item)
        else:
            pass1_items.append(item)

    if not pass1_items and pass2_items:
        pass1_items.append(pass2_items.pop(0))
    if len(pass1_items) > _MAX_PASS1_RENDER_ITEMS:
        pass2_items = pass1_items[_MAX_PASS1_RENDER_ITEMS:] + pass2_items
        pass1_items = pass1_items[:_MAX_PASS1_RENDER_ITEMS]

    pass1_payload = dict(simple_payload)
    pass1_payload["items"] = [dict(item) for item in pass1_items]

    if not pass2_items:
        return pass1_payload, None

    pass2_payload = dict(simple_payload)
    pass2_payload["items"] = [dict(item) for item in pass2_items]
    pass2_payload["render_pass_mode"] = "pass2_additive_edit"
    pass2_payload["pass2_source_pass1_item_count"] = len(pass1_items)
    pass2_payload["pass2_additive_instruction"] = (
        "Preserve the already furnished room exactly and add only these secondary detail items."
    )
    return pass1_payload, pass2_payload


def _item_requires_identity_validation(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    strategy = item.get("two_pass_strategy") if isinstance(item.get("two_pass_strategy"), dict) else {}
    return bool(item.get("requires_identity_validation") or (strategy or {}).get("requires_identity_validation"))


def _pass2_requires_identity_validation(furniture_specs_json: dict | None) -> bool:
    if not isinstance(furniture_specs_json, dict):
        return False
    if any(_item_requires_identity_validation(item) for item in (furniture_specs_json.get("items") or []) if isinstance(item, dict)):
        return True
    summary = furniture_specs_json.get("two_pass_strategy") if isinstance(furniture_specs_json.get("two_pass_strategy"), dict) else {}
    return bool((summary or {}).get("identity_validation_required_keys"))


def _build_compact_generation_specs_text(furniture_specs_json: dict | None) -> str | None:
    if not isinstance(furniture_specs_json, dict):
        return None

    rows: list[str] = []
    for index, item in enumerate(furniture_specs_json.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or f"Item {index}").strip()
        if not label:
            label = f"Item {index}"
        try:
            qty = max(1, int(item.get("qty") or 1))
        except Exception:
            qty = 1
        qty_text = f" (qty={qty})" if qty > 1 else ""

        dims = item.get("requested_dims_mm") or item.get("dims_mm") or {}
        dims_bits = []
        if isinstance(dims, dict):
            for key, short in (("width_mm", "W"), ("depth_mm", "D"), ("height_mm", "H"), ("radius_mm", "R")):
                value = _coerce_positive_int(dims.get(key))
                if value is not None:
                    dims_bits.append(f"{short}={value}mm")

        family = (
            item.get("category_canonical")
            or item.get("category")
            or ""
        )

        bits = []
        if family:
            bits.append(f"category={family}")
        if dims_bits:
            bits.append(", ".join(dims_bits))
        rows.append(f"{index}. {label}{qty_text}: " + " | ".join(bits))
    text = "\n".join(rows).strip()
    return text or None


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


def _review_summary_from_scalecheck_diagnostics(diagnostics: dict | None, *, scale_check_failed: bool, failed_rules: list[str], issues: list[str]) -> dict:
    raw = diagnostics or {}
    matched_items = raw.get("matched_items") or {}
    unmatched_items = list(raw.get("unmatched_items") or [])
    all_failed_rules = list(raw.get("failed_rules") or failed_rules or [])
    issue_records = list(raw.get("issue_records") or [])
    if issue_records:
        fidelity_fail_count = sum(
            1
            for row in issue_records
            if str((row or {}).get("rule_kind") or "") in {"reference_shape_drift", "reference_material_drift", "reflection_violation"}
        )
        placement_fail_count = sum(1 for row in issue_records if str((row or {}).get("rule_kind") or "") == "placement_violation")
        geometry_fail_count = sum(
            1
            for row in issue_records
            if str((row or {}).get("rule_kind") or "") in {"scale_fit_violation", "validation_exception", "low_confidence_match"}
        )
    else:
        fidelity_fail_count = sum(1 for rule in all_failed_rules if str(rule).startswith("reference_"))
        placement_fail_count = sum(
            1
            for rule in all_failed_rules
            if str(rule) in {"wall_attached_floor_collision", "rug_floating_above_floor_zone", "floor_item_floating"}
        )
        geometry_fail_count = max(0, len(all_failed_rules) - fidelity_fail_count - placement_fail_count)
    weighted_issue_score = _weighted_issue_score(issue_records)
    if weighted_issue_score <= 0 and (all_failed_rules or unmatched_items or issues or scale_check_failed):
        weighted_issue_score = round(
            (len(unmatched_items) * 3.0)
            + (fidelity_fail_count * 2.5)
            + (placement_fail_count * 2.0)
            + (geometry_fail_count * 1.5)
            + (len(list(issues or [])) * 0.35)
            + (1.5 if scale_check_failed else 0.0),
            4,
        )
    review_pass = bool(matched_items) and not scale_check_failed and not unmatched_items and not all_failed_rules
    review_score = (len(matched_items) * 4) - int(round(weighted_issue_score * 10))
    return {
        "review_pass": review_pass,
        "review_score": int(review_score),
        "weighted_issue_score": weighted_issue_score,
        "matched_source_count": len(matched_items),
        "unmatched_source_count": len(unmatched_items),
        "unmatched_source_items": unmatched_items,
        "fidelity_fail_count": fidelity_fail_count,
        "placement_fail_count": placement_fail_count,
        "geometry_fail_count": geometry_fail_count,
    }


def _compact_variant_diagnostics(variant_results: list) -> list[dict]:
    diagnostics: list[dict] = []
    for index, row in enumerate(variant_results or []):
        if not isinstance(row, dict):
            if row:
                diagnostics.append(
                    {
                        "variant_index": index,
                        "path": row,
                        "scalecheck_fail_count": 0,
                        "scalecheck_retry_count": 0,
                        "scale_check_failed": False,
                        "scalecheck_issues": [],
                        "scalecheck_failed_rules": [],
                        "scalecheck_diagnostics": {},
                        "review_pass": False,
                        "review_score": 0,
                        "matched_source_count": 0,
                        "unmatched_source_count": 0,
                        "unmatched_source_items": [],
                        "fidelity_fail_count": 0,
                        "placement_fail_count": 0,
                        "geometry_fail_count": 0,
                        "weighted_issue_score": 0.0,
                        "repair_applied": False,
                        "repair_attempt_count": 0,
                        "repair_target_keys": [],
                        "repair_target_labels": [],
                    }
                )
            continue
        path = row.get("path")
        if not path:
            continue
        raw_diagnostics = dict(row.get("scalecheck_diagnostics") or {})
        inferred_review_pass = bool(raw_diagnostics) and (
            not bool(row.get("scale_check_failed", False))
            and not list(row.get("scalecheck_failed_rules") or [])
            and not list(row.get("scalecheck_issues") or [])
        )
        review_summary = _review_summary_from_scalecheck_diagnostics(
            raw_diagnostics,
            scale_check_failed=bool(row.get("scale_check_failed", False)),
            failed_rules=list(row.get("scalecheck_failed_rules") or []),
            issues=list(row.get("scalecheck_issues") or []),
        )
        diagnostics.append(
            {
                "variant_index": index,
                "path": path,
                "scalecheck_fail_count": int(row.get("scalecheck_fail_count") or 0),
                "scalecheck_retry_count": int(row.get("scalecheck_retry_count") or 0),
                "scale_check_failed": bool(row.get("scale_check_failed", False)),
                "scalecheck_issues": list(row.get("scalecheck_issues") or []),
                "scalecheck_failed_rules": list(row.get("scalecheck_failed_rules") or []),
                "scalecheck_diagnostics": raw_diagnostics,
                "review_pass": bool(row.get("review_pass", review_summary["review_pass"] if raw_diagnostics else inferred_review_pass)),
                "review_score": int(row.get("review_score") or (review_summary["review_score"] if raw_diagnostics else 0)),
                "matched_source_count": int(row.get("matched_source_count") or review_summary["matched_source_count"]),
                "unmatched_source_count": int(row.get("unmatched_source_count") or review_summary["unmatched_source_count"]),
                "unmatched_source_items": list(row.get("unmatched_source_items") or review_summary["unmatched_source_items"]),
                "fidelity_fail_count": int(row.get("fidelity_fail_count") or review_summary["fidelity_fail_count"]),
                "placement_fail_count": int(row.get("placement_fail_count") or review_summary["placement_fail_count"]),
                "geometry_fail_count": int(row.get("geometry_fail_count") or review_summary["geometry_fail_count"]),
                "weighted_issue_score": float(row.get("weighted_issue_score") or review_summary["weighted_issue_score"]),
                "repair_applied": bool(row.get("repair_applied", False)),
                "repair_attempt_count": int(row.get("repair_attempt_count") or 0),
                "repair_target_keys": list(row.get("repair_target_keys") or []),
                "repair_target_labels": list(row.get("repair_target_labels") or []),
            }
        )
    return diagnostics


def _variant_quality_sort_key(path: str, diagnostics_by_path: dict[str, dict]) -> tuple:
    row = diagnostics_by_path.get(path) or {}
    review_score = int(row.get("review_score") or 0)
    weighted_issue_score = float(row.get("weighted_issue_score") or 0.0)
    return (
        0 if row.get("review_pass") else 1,
        weighted_issue_score,
        1 if row.get("scale_check_failed") else 0,
        int(row.get("unmatched_source_count") or 0),
        int(row.get("fidelity_fail_count") or 0),
        int(row.get("placement_fail_count") or 0),
        int(row.get("geometry_fail_count") or 0),
        int(row.get("scalecheck_fail_count") or 0),
        int(row.get("scalecheck_retry_count") or 0),
        -review_score,
        int(row.get("variant_index") or 0),
    )


def _fallback_rank_candidates(
    candidate_results: list[str] | None,
    variant_diagnostics: list[dict] | None,
    *,
    audience: str,
) -> list[str]:
    ordered = [str(path) for path in (candidate_results or []) if path]
    if len(ordered) <= 1:
        return ordered
    diagnostics_by_path = {
        str(row.get("path")): row
        for row in (variant_diagnostics or [])
        if isinstance(row, dict) and row.get("path")
    }
    ordered.sort(key=lambda path: _variant_quality_sort_key(path, diagnostics_by_path))
    if audience == "external":
        return ordered[:1]
    return ordered


def _select_rankable_results(generated_results: list[str], diagnostics_by_path: dict[str, dict]) -> list[str]:
    eligible = [
        path
        for path in (generated_results or [])
        if path and bool((diagnostics_by_path.get(path) or {}).get("review_pass"))
    ]
    if eligible:
        return eligible
    fallback = list(generated_results or [])
    fallback.sort(key=lambda path: _variant_quality_sort_key(path, diagnostics_by_path))
    return fallback[: max(1, min(2, len(fallback)))]


def _selected_result_reason_for_row(row: dict | None) -> str:
    row = row if isinstance(row, dict) else {}
    if row.get("hard_qc_pass"):
        return "hard_qc_pass_ranked"
    if row.get("soft_qc_pass"):
        return "soft_qc_pass_ranked"
    if row.get("review_pass"):
        return "review_pass_ranked"
    if _is_validation_unavailable_best_effort_candidate(row):
        return "strict_validation_unavailable_best_effort"
    if _is_strict_delivery_best_effort_candidate(row):
        return "strict_delivery_best_effort"
    try:
        if float(row.get("weighted_issue_score") or 0.0) > 0.0:
            return "all_failed_weighted_fallback"
    except Exception:
        pass
    return "best_effort_least_bad"


def _polish_selected_best_result(
    generated_results: list[str] | None,
    *,
    audience: str,
    unique_id: str,
    selected_result_reason: str | None,
    polish_main_image: Callable[..., str | None] | None,
    logger,
) -> tuple[list[str], str | None]:
    delivery_paths = [str(path) for path in (generated_results or []) if path]
    if not delivery_paths or not callable(polish_main_image):
        return delivery_paths, selected_result_reason

    updated_paths = list(delivery_paths)
    polished_any = False
    for index, source_path in enumerate(delivery_paths):
        polished_path = None
        try:
            try:
                polished_path = polish_main_image(
                    source_path,
                    unique_id=unique_id,
                    audience=audience,
                    selected_result_reason=selected_result_reason,
                    is_selected_best=(index == 0),
                    variant_position=index + 1,
                )
            except TypeError:
                polished_path = polish_main_image(source_path, unique_id=unique_id)
        except Exception as exc:
            try:
                logger.warning(f"[MainPolish] skipped: {exc}")
            except Exception:
                pass
            continue

        polished_path = str(polished_path or "").strip()
        if not polished_path:
            continue
        updated_paths[index] = polished_path
        polished_any = True

    if not polished_any:
        try:
            logger.warning(
                "[MainPolish] no polished output; using unpolished candidate "
                f"unique_id={unique_id} audience={audience} reason={selected_result_reason or 'unknown'}"
            )
        except Exception:
            pass
        return delivery_paths, selected_result_reason
    if selected_result_reason:
        return updated_paths, f"{selected_result_reason}_polished"
    return updated_paths, "polished_best"


def _is_validation_unavailable_best_effort_candidate(row: dict | None) -> bool:
    row = row if isinstance(row, dict) else {}
    if not row.get("path"):
        return False
    if not bool(row.get("scale_check_failed")):
        return False
    failed_rules = {
        str(rule or "").strip()
        for rule in (
            list(row.get("scalecheck_failed_rules") or [])
            + list(row.get("hard_failed_rules") or [])
        )
        if str(rule or "").strip()
    }
    if not failed_rules or not failed_rules.issubset({"no_matched_items"}):
        return False
    if int(row.get("matched_source_count") or 0) > 0:
        return False
    if int(row.get("fidelity_fail_count") or 0) > 0:
        return False
    if int(row.get("placement_fail_count") or 0) > 0:
        return False
    if int(row.get("geometry_fail_count") or 0) > 0:
        return False
    return True


def _is_strict_delivery_best_effort_candidate(row: dict | None) -> bool:
    row = row if isinstance(row, dict) else {}
    if not row.get("path"):
        return False
    failed_rules = {
        str(rule or "").strip()
        for rule in (
            list(row.get("scalecheck_failed_rules") or [])
            + list(row.get("hard_failed_rules") or [])
        )
        if str(rule or "").strip()
    }
    if failed_rules.intersection(
        {
            "strict_scale_contract_not_ready",
            "validation_exception",
            "scale_validation_exception",
            "scale_guide_leak_detected",
            "incomplete_items",
        }
    ):
        return False
    if int(row.get("fidelity_fail_count") or 0) > 0:
        return False
    if int(row.get("placement_fail_count") or 0) > 0:
        return False
    matched_source_count = int(row.get("matched_source_count") or 0)
    unmatched_source_count = int(row.get("unmatched_source_count") or 0)
    if matched_source_count <= 0:
        return False
    if bool(row.get("repair_applied")):
        return True
    return unmatched_source_count <= 2 and matched_source_count >= max(4, unmatched_source_count + 1)


def _select_final_generated_results(
    candidate_results: list[str] | None,
    variant_diagnostics: list[dict] | None,
    *,
    strict_scale_requested: bool,
) -> tuple[list[str], int | None, str | None, dict | None, bool]:
    ordered_candidates = [str(path) for path in (candidate_results or []) if path]
    rows_by_path = {
        str(row.get("path")): row
        for row in (variant_diagnostics or [])
        if isinstance(row, dict) and row.get("path")
    }
    hard_qc_paths = {
        path
        for path, row in rows_by_path.items()
        if isinstance(row, dict) and row.get("hard_qc_pass")
    }
    soft_qc_paths = {
        path
        for path, row in rows_by_path.items()
        if isinstance(row, dict) and row.get("soft_qc_pass")
    }
    if strict_scale_requested:
        if hard_qc_paths:
            final_results = [path for path in ordered_candidates if path in hard_qc_paths]
        elif soft_qc_paths:
            final_results = [path for path in ordered_candidates if path in soft_qc_paths]
        else:
            best_effort_paths = [
                path
                for path in ordered_candidates
                if _is_validation_unavailable_best_effort_candidate(rows_by_path.get(path))
            ]
            if best_effort_paths:
                final_results = list(best_effort_paths)
            else:
                strict_delivery_paths = [
                    path
                    for path in ordered_candidates
                    if _is_strict_delivery_best_effort_candidate(rows_by_path.get(path))
                ]
                strict_delivery_paths.sort(key=lambda path: _variant_quality_sort_key(path, rows_by_path))
                final_results = strict_delivery_paths[:1]
            if not final_results and ordered_candidates:
                all_failed_paths = list(ordered_candidates)
                all_failed_paths.sort(key=lambda path: _variant_quality_sort_key(path, rows_by_path))
                final_results = all_failed_paths[:1]
    else:
        final_results = list(ordered_candidates)
    if not final_results:
        return [], None, None, None, True
    selected_path = final_results[0]
    selected_row = rows_by_path.get(selected_path)
    selected_result_index = None
    if isinstance(selected_row, dict):
        selected_result_index = selected_row.get("variant_index")
    return final_results, selected_result_index, _selected_result_reason_for_row(selected_row), selected_row, False


def _resolve_postprocess_ranking_inputs(
    generated_results: list[str] | None,
    variant_diagnostics: list[dict] | None,
    *,
    strict_scale_requested: bool,
) -> tuple[list[str], bool]:
    diagnostics_by_path = {
        str(row.get("path")): row
        for row in (variant_diagnostics or [])
        if isinstance(row, dict) and row.get("path")
    }
    has_hard_qc_candidates = any(bool(row.get("hard_qc_pass")) for row in (variant_diagnostics or []))
    has_soft_qc_candidates = any(bool(row.get("soft_qc_pass")) for row in (variant_diagnostics or []))
    rankable_results = select_rankable_paths(
        variant_diagnostics,
        strict_internal=bool(strict_scale_requested),
    )
    allow_failed_rerank = bool(rankable_results) and (
        not strict_scale_requested or has_hard_qc_candidates or has_soft_qc_candidates
    )
    if not strict_scale_requested and not rankable_results and generated_results:
        rankable_results = _select_rankable_results(list(generated_results or []), diagnostics_by_path)
        allow_failed_rerank = bool(rankable_results)
    return list(rankable_results or []), bool(allow_failed_rerank)


def _should_launch_budgeted_fallback_variant(
    variant_diagnostics: list[dict] | None,
    *,
    strict_scale_requested: bool,
    remaining_budget_sec: float | None,
    rankable_selector: Callable[..., list[str]] = select_rankable_paths,
) -> bool:
    if not strict_scale_requested:
        return False
    if remaining_budget_sec is None or float(remaining_budget_sec) < 360.0:
        return False
    rankable_results = rankable_selector(
        variant_diagnostics,
        strict_internal=True,
    )
    return not bool(rankable_results)


def _bbox_norm_to_box_2d(bbox_norm: Any) -> list[int] | None:
    if not isinstance(bbox_norm, (list, tuple)) or len(bbox_norm) != 4:
        return None
    try:
        xmin, ymin, xmax, ymax = [float(value) for value in bbox_norm]
    except Exception:
        return None
    clamped = [max(0.0, min(1.0, value)) for value in (xmin, ymin, xmax, ymax)]
    xmin, ymin, xmax, ymax = clamped
    if xmax <= xmin or ymax <= ymin:
        return None
    return [
        int(round(ymin * 1000)),
        int(round(xmin * 1000)),
        int(round(ymax * 1000)),
        int(round(xmax * 1000)),
    ]


def _apply_selected_review_boxes_to_analyzed_items(full_analyzed_data: list[dict] | None, selected_variant_review: dict | None) -> list[dict]:
    rows = list(full_analyzed_data or [])
    if not rows or not isinstance(selected_variant_review, dict):
        return rows
    diagnostics = selected_variant_review.get("scalecheck_diagnostics") or {}
    if not isinstance(diagnostics, dict):
        return rows
    matched_items = diagnostics.get("matched_items") or {}
    if not isinstance(matched_items, dict) or not matched_items:
        return rows

    applied: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            applied.append(row)
            continue
        match = None
        for candidate_key in (
            row.get("target_key"),
            row.get("source_index"),
            row.get("label"),
        ):
            if candidate_key in (None, ""):
                continue
            candidate_match = matched_items.get(str(candidate_key))
            if isinstance(candidate_match, dict):
                match = candidate_match
                break
        if not isinstance(match, dict):
            applied.append(row)
            continue
        box_2d = _bbox_norm_to_box_2d(match.get("bbox_norm"))
        if not box_2d:
            applied.append(row)
            continue
        updated = dict(row)
        current_box = updated.get("box_2d")
        if isinstance(current_box, list) and len(current_box) == 4 and updated.get("source_box_2d") in (None, []):
            updated["source_box_2d"] = list(current_box)
        updated["box_2d"] = box_2d
        updated["box_source"] = "selected_variant_review"
        if match.get("label"):
            updated["box_label_detected"] = match.get("label")
        if match.get("match_confidence") is not None:
            try:
                confidence = float(match.get("match_confidence"))
                updated["box_match_confidence"] = confidence
                updated["box_match_score"] = confidence
            except Exception:
                pass
        applied.append(updated)
    return applied


def _can_skip_postprocess_remap(
    *,
    strict_scale_requested: bool,
    variant_diagnostics: list[dict] | None,
    remaining_budget_sec: float | None = None,
) -> bool:
    if remaining_budget_sec is not None and remaining_budget_sec < 30.0:
        return True
    if not strict_scale_requested:
        return False
    rows = [row for row in (variant_diagnostics or []) if isinstance(row, dict)]
    if not rows:
        return False
    if any(
        bool(row.get("repair_applied"))
        or int(row.get("repair_attempt_count") or 0) > 0
        or bool(row.get("repair_target_keys"))
        for row in rows
    ):
        return False
    return any(int(row.get("matched_source_count") or 0) > 0 for row in rows)


def run_render_room_workflow(
    request: RenderWorkflowRequest,
    deps: RenderWorkflowDependencies,
) -> dict:
    summary_token = None
    try:
        bootstrap = run_render_bootstrap_stage(
            generate_unique_id=deps.runtime.generate_unique_id,
            time_now=deps.runtime.time_now,
            log_section=deps.runtime.log_section,
            summary_ref=deps.runtime.summary_ref,
        )
        unique_id = bootstrap.unique_id
        start_time = bootstrap.start_time
        summary = bootstrap.summary
        summary_token = bootstrap.summary_token
        absolute_deadline_ts = float(start_time) + max(1.0, float(deps.runtime.total_timeout_limit_sec or 1800.0))

        audience_result = run_render_audience_stage(
            audience=request.audience,
            normalize_audience=deps.storage.normalize_audience,
            build_s3_prefix=deps.storage.build_s3_prefix,
        )
        aud = audience_result.audience
        enable_scale_check = audience_result.enable_scale_check
        prefix_main_user = audience_result.prefix_main_user
        prefix_main_empty = audience_result.prefix_main_empty
        prefix_main_rendered = audience_result.prefix_main_rendered
        prefix_customize = audience_result.prefix_customize

        input_result = run_render_input_stage(
            upload_file=request.file,
            unique_id=unique_id,
            time_now=deps.runtime.time_now,
            standardize_image=deps.storage.standardize_image,
        )
        timestamp = input_result.timestamp
        std_path = input_result.std_path

        empty_stage_result = run_render_empty_stage(
            std_path=std_path,
            unique_id=unique_id,
            start_time=start_time,
            generate_empty_room=deps.generation.generate_empty_room,
        )
        step1_img = empty_stage_result.step1_img
        step1_raw = empty_stage_result.step1_raw

        scale_stage_result = run_render_scale_stage(
            audience=aud,
            dimensions=request.dimensions,
            parse_room_dimensions_mm=deps.analysis.parse_room_dimensions_mm,
            room_dims_valid_fn=deps.analysis.room_dims_valid_fn,
            build_explicit_room_dims_contract_fn=deps.analysis.build_explicit_room_dims_contract,
            logger=deps.runtime.logger,
        )
        room_dims_parsed = scale_stage_result.room_dims_parsed
        enable_scale_guidance = scale_stage_result.enable_scale_guidance
        room_dims_contract = getattr(scale_stage_result, "room_dims_contract", None)
        strict_scale_requested = bool(getattr(scale_stage_result, "strict_scale_requested", aud == "internal"))
        room_planes = scale_stage_result.room_planes
        wall_span_norm = scale_stage_result.wall_span_norm
        windows_present = scale_stage_result.windows_present
        room_analysis_text = scale_stage_result.room_analysis_text
        furniture_specs_text = scale_stage_result.furniture_specs_text
        furniture_specs_json = scale_stage_result.furniture_specs_json
        primary_item = scale_stage_result.primary_item
        scale_guide_path = scale_stage_result.scale_guide_path
        size_hierarchy = scale_stage_result.size_hierarchy
        full_analyzed_data = scale_stage_result.full_analyzed_data

        reference_selection = prepare_render_references(
            moodboard_items=request.moodboard_items,
            style=request.style,
            room=request.room,
            variant=request.variant,
            moodboard=request.moodboard,
            timestamp=timestamp,
            unique_id=unique_id,
            prefix_customize=prefix_customize,
            use_s3_moodboard=deps.runtime.use_s3_moodboard,
            materialize_input=deps.storage.materialize_input,
            resolve_image_url=deps.storage.resolve_image_url,
            build_item_target_key=deps.analysis.build_item_target_key,
            canonical_category=deps.analysis.canonical_category,
            find_s3_moodboard_key=deps.storage.find_s3_moodboard_key,
            s3_public_url=deps.storage.s3_public_url,
        )
        mb_url = reference_selection.mb_url
        ref_paths = reference_selection.ref_paths
        item_refs = reference_selection.item_refs

        analysis_result = run_render_analysis_stage(
            ref_paths=ref_paths,
            item_refs=item_refs,
            step1_img=step1_img,
            step1_raw=step1_raw,
            dimensions=request.dimensions,
            unique_id=unique_id,
            detect_furniture_boxes=deps.analysis.detect_furniture_boxes,
            canonical_category=deps.analysis.canonical_category,
            build_item_target_key=deps.analysis.build_item_target_key,
            analyze_room_structure=deps.analysis.analyze_room_structure,
            analyze_cropped_item=deps.analysis.analyze_cropped_item,
            normalize_dims_dict=deps.analysis.normalize_dims_dict,
            parse_object_dimensions_mm=deps.analysis.parse_object_dimensions_mm,
            build_furniture_specs_json=deps.analysis.build_furniture_specs_json,
            create_scale_guide_overlay_with_model=deps.analysis.create_scale_guide_overlay_with_model,
            match_aspect_to_target=deps.analysis.match_aspect_to_target,
            enable_scale_guidance=enable_scale_guidance,
            strict_scale_requested=strict_scale_requested,
            room_dims_parsed=room_dims_parsed,
            summary=summary,
            logger=deps.runtime.logger,
            log_brief=deps.runtime.log_brief,
            max_concurrency_analysis=deps.runtime.max_concurrency_analysis,
            cart_max_analysis_workers=deps.runtime.cart_max_analysis_workers,
            absolute_deadline_ts=absolute_deadline_ts,
        )
        windows_present = analysis_result.windows_present
        room_analysis_text = analysis_result.room_analysis_text
        if getattr(analysis_result, "room_planes", None) is not None:
            room_planes = analysis_result.room_planes
        if getattr(analysis_result, "wall_span_norm", None) is not None:
            wall_span_norm = analysis_result.wall_span_norm
        furniture_specs_text = analysis_result.furniture_specs_text
        furniture_specs_json = analysis_result.furniture_specs_json
        full_analyzed_data = analysis_result.full_analyzed_data or []
        primary_item = analysis_result.primary_item
        scale_guide_path = analysis_result.scale_guide_path
        size_hierarchy = analysis_result.size_hierarchy
        room_analysis_payload = {
            "room_text": room_analysis_text,
            "windows_present": windows_present,
            "room_planes": room_planes,
            "wall_span_norm": wall_span_norm,
            "estimated_dimensions_mm": getattr(analysis_result, "estimated_room_dims", None),
        }
        room_dims_contract = deps.analysis.estimate_room_dims_contract(
            room=request.room,
            explicit_room_dims=room_dims_parsed,
            room_dims_valid=bool(getattr(scale_stage_result, "room_dims_valid", False)),
            room_analysis=room_analysis_payload,
            analyzed_items=full_analyzed_data,
            primary_item=primary_item,
            audience=aud,
        )
        if _room_dims_contract_requests_strict(room_dims_contract):
            strict_scale_requested = True
        room_analysis_text = _merge_room_analysis_text(room_analysis_text, room_dims_contract)
        room_dims_center = dict(
            getattr(room_dims_contract, "dims_mm_center", None)
            or ((room_dims_contract or {}).get("dims_mm_center") if isinstance(room_dims_contract, dict) else None)
            or {}
        )
        if room_dims_center:
            room_dims_parsed = {
                "width_mm": room_dims_center.get("width_mm") or room_dims_parsed.get("width_mm"),
                "depth_mm": room_dims_center.get("depth_mm") or room_dims_parsed.get("depth_mm"),
                "height_mm": room_dims_center.get("height_mm") or room_dims_parsed.get("height_mm"),
            }
        full_analyzed_data = _refresh_layout_envelopes(full_analyzed_data, room_dims_center)
        full_analyzed_data, product_identities = deps.analysis.build_product_identity_bundle(full_analyzed_data)
        full_analyzed_data, archetype_strategies = deps.analysis.build_archetype_strategies(
            full_analyzed_data,
            primary_item=primary_item,
        )
        full_analyzed_data, two_pass_strategy = apply_two_pass_strategy(full_analyzed_data, primary_item=primary_item)
        preferred_anchor_key = str((two_pass_strategy or {}).get("recommended_anchor_key") or "").strip()
        if preferred_anchor_key:
            primary_item = next(
                (
                    row
                    for row in full_analyzed_data
                    if str((row or {}).get("target_key") or "").strip() == preferred_anchor_key
                ),
                primary_item,
            )
        primary_item = _rebind_primary_item(full_analyzed_data, primary_item)
        primary_item = _hydrate_item_dims(primary_item, (furniture_specs_json or {}).get("primary_scale") or (furniture_specs_json or {}).get("primary"))
        scale_plan = build_scale_plan(
            items=full_analyzed_data,
            room_dims_parsed=room_dims_parsed,
            room_dims_contract=room_dims_contract.as_dict() if hasattr(room_dims_contract, "as_dict") else room_dims_contract,
            geometry_contract=None,
            room_planes=room_planes,
            wall_span_norm=wall_span_norm,
            primary_item=primary_item,
            strict_scale_requested=bool(strict_scale_requested),
        )
        scene_contract = deps.analysis.build_scene_contract(
            room=request.room,
            audience=aud,
            room_dims_contract=room_dims_contract,
            room_analysis_text=room_analysis_text,
            room_planes=room_planes,
            wall_span_norm=wall_span_norm,
            windows_present=windows_present,
            analyzed_items=full_analyzed_data,
            primary_item=primary_item,
        )
        placement_plan, full_analyzed_data = deps.analysis.build_placement_plan(
            analyzed_items=full_analyzed_data,
            primary_item=primary_item,
            scene_contract=scene_contract,
            placement_instructions=request.placement,
        )
        primary_item = _rebind_primary_item(full_analyzed_data, primary_item)
        primary_item = _hydrate_item_dims(primary_item, (furniture_specs_json or {}).get("primary_scale") or (furniture_specs_json or {}).get("primary"))
        geometry_contract = deps.analysis.build_geometry_contract(
            room_dims_contract=room_dims_contract,
            scene_contract=scene_contract,
            placement_plan=placement_plan,
            analyzed_items=full_analyzed_data,
            primary_item=primary_item,
            strict_scale_requested=bool(strict_scale_requested),
        )
        primary_item = _rebind_primary_item(full_analyzed_data, primary_item)
        primary_item = _hydrate_item_dims(primary_item, (furniture_specs_json or {}).get("primary_scale") or (furniture_specs_json or {}).get("primary"))
        furniture_specs_json = _sync_furniture_specs_contracts(furniture_specs_json, full_analyzed_data, placement_plan)
        if isinstance(furniture_specs_json, dict):
            furniture_specs_json["two_pass_strategy"] = two_pass_strategy
        scale_plan = build_scale_plan(
            items=full_analyzed_data,
            room_dims_parsed=room_dims_parsed,
            room_dims_contract=room_dims_contract.as_dict() if hasattr(room_dims_contract, "as_dict") else room_dims_contract,
            geometry_contract=geometry_contract.as_dict() if hasattr(geometry_contract, "as_dict") else geometry_contract,
            room_planes=room_planes,
            wall_span_norm=wall_span_norm,
            primary_item=primary_item,
            strict_scale_requested=bool(strict_scale_requested),
        )

        qc_geometry_source = str(getattr(room_dims_contract, "source", "") or ((room_dims_contract or {}).get("source") if isinstance(room_dims_contract, dict) else "") or "unknown")
        qc_geometry_confidence = str(getattr(room_dims_contract, "confidence", "") or ((room_dims_contract or {}).get("confidence") if isinstance(room_dims_contract, dict) else "") or "none")
        qc_strict_scale_mode = str(getattr(room_dims_contract, "strict_scale_mode", "") or ((room_dims_contract or {}).get("strict_scale_mode") if isinstance(room_dims_contract, dict) else "") or "advisory_geometry_mode")

        if windows_present is None:
            windows_present = False
        ref_input = ref_paths if len(ref_paths) > 1 else (ref_paths[0] if ref_paths else None)
        resolved_style_prompt = _resolve_style_prompt(deps.runtime.style_map, request.style)
        deps.runtime.log_section("[Stage 2] unified main generation start (best-of-3)")
        generation_specs_json, pass2_generation_specs_json = _split_generation_specs_for_render_passes(furniture_specs_json)
        generation_specs_text = _build_compact_generation_specs_text(generation_specs_json) or furniture_specs_text
        primary_item = _hydrate_item_dims(
            primary_item,
            ((generation_specs_json or {}).get("primary_scale") if isinstance(generation_specs_json, dict) else None)
            or ((generation_specs_json or {}).get("primary") if isinstance(generation_specs_json, dict) else None),
        )
        size_hierarchy = (
            ((generation_specs_json or {}).get("size_hierarchy_scale") if isinstance(generation_specs_json, dict) else None)
            or ((generation_specs_json or {}).get("size_hierarchy") if isinstance(generation_specs_json, dict) else None)
            or size_hierarchy
        )
        scale_plan_dict = dict(scale_plan or {})
        room_dims_contract_dict = _room_dims_contract_dict(room_dims_contract)
        geometry_contract_dict = geometry_contract.as_dict() if hasattr(geometry_contract, "as_dict") else dict(geometry_contract or {})
        scene_contract_dict = scene_contract.as_dict() if hasattr(scene_contract, "as_dict") else dict(scene_contract or {})
        placement_plan_dict = placement_plan.as_dict() if hasattr(placement_plan, "as_dict") else dict(placement_plan or {})
        stage2_strict_scale_requested = bool(strict_scale_requested and _room_dims_complete(room_dims_parsed))
        stage2_enable_scale_check = bool(enable_scale_check and stage2_strict_scale_requested)
        generation_dimensions = str(request.dimensions or "").strip()
        if not generation_dimensions and stage2_strict_scale_requested:
            generation_dimensions = _room_dimensions_text_from_dims(room_dims_parsed)

        variant_results = run_render_variant_stage(
            step1_img=step1_img,
            style_prompt=resolved_style_prompt,
            ref_input=ref_input,
            unique_id=unique_id,
            furniture_specs_text=generation_specs_text,
            furniture_specs_json=generation_specs_json,
            dimensions=generation_dimensions,
            placement=request.placement,
            scale_guide_path=scale_guide_path,
            primary_item=primary_item,
            room_dims_parsed=room_dims_parsed,
            wall_span_norm=wall_span_norm,
            size_hierarchy=size_hierarchy,
            scale_plan=scale_plan_dict,
            geometry_contract=geometry_contract_dict,
            scene_contract=scene_contract_dict,
            placement_plan=placement_plan_dict,
            start_time=start_time,
            room_planes=room_planes,
            windows_present=windows_present,
            room_analysis_text=room_analysis_text,
            enable_scale_check=stage2_enable_scale_check,
            generate_furnished_room=deps.generation.generate_furnished_room,
            max_variants=3,
            max_workers=3,
            max_generation_attempts=1,
            start_index=0,
        )
        variant_diagnostics = _compact_variant_diagnostics(variant_results)
        variant_diagnostics = annotate_variant_reviews(
            variant_diagnostics,
            strict_internal=stage2_strict_scale_requested,
            geometry_source=qc_geometry_source,
            geometry_confidence=qc_geometry_confidence,
            strict_scale_mode=qc_strict_scale_mode,
        )
        generated_results = []
        for row in variant_results or []:
            if not isinstance(row, dict):
                if row:
                    generated_results.append(row)
                continue
            path = row.get("path")
            if path:
                generated_results.append(path)

        strict_delivery_scale_requested = bool(aud == "external" and stage2_strict_scale_requested)
        rankable_results, allow_failed_rerank = _resolve_postprocess_ranking_inputs(
            generated_results,
            variant_diagnostics,
            strict_scale_requested=strict_delivery_scale_requested,
        )

        postprocess_result = run_render_postprocess_stage(
            generated_results=generated_results,
            rankable_results=rankable_results,
            full_analyzed_data=full_analyzed_data,
            audience=aud,
            allow_failed_rerank=allow_failed_rerank,
            rank_best_variant=deps.postprocess.rank_best_variant,
            refresh_item_boxes_from_main_render=deps.postprocess.refresh_item_boxes_from_main_render,
            attach_volume_ranks=deps.postprocess.attach_volume_ranks,
            volume_ranking_snapshot=deps.postprocess.volume_ranking_snapshot,
            logger=deps.runtime.logger,
            log_brief=deps.runtime.log_brief,
            skip_main_render_remap=_can_skip_postprocess_remap(
                strict_scale_requested=stage2_strict_scale_requested,
                variant_diagnostics=variant_diagnostics,
                remaining_budget_sec=max(0.0, absolute_deadline_ts - float(deps.runtime.time_now())),
            ),
            absolute_deadline_ts=absolute_deadline_ts,
        )
        candidate_results = list(postprocess_result.generated_results or [])
        if not bool(getattr(postprocess_result, "rerank_applied", False)):
            candidate_results = _fallback_rank_candidates(
                candidate_results,
                variant_diagnostics,
                audience=aud,
            )
        generated_results = list(candidate_results)
        full_analyzed_data = postprocess_result.full_analyzed_data
        volume_ranking = postprocess_result.volume_ranking
        (
            generated_results,
            selected_result_index,
            selected_result_reason,
            selected_variant_review,
            strict_final_result_blocked,
        ) = _select_final_generated_results(
            candidate_results,
            variant_diagnostics,
            strict_scale_requested=strict_delivery_scale_requested,
        )
        if not strict_final_result_blocked:
            if aud == "external":
                generated_results = list(generated_results[:1])
            generated_results, selected_result_reason = _polish_selected_best_result(
                generated_results,
                audience=aud,
                unique_id=unique_id,
                selected_result_reason=selected_result_reason,
                polish_main_image=deps.generation.polish_main_image,
                logger=deps.runtime.logger,
            )
            if pass2_generation_specs_json and generated_results:
                deps.runtime.log_section("[Stage 2B] additive detail generation start (pass2)")
                pass2_specs_text = _build_compact_generation_specs_text(pass2_generation_specs_json)
                pass2_identity_validation_required = _pass2_requires_identity_validation(pass2_generation_specs_json)
                pass2_placement = "\n".join(
                    part
                    for part in (
                        str(request.placement or "").strip(),
                        "SECOND PASS ADDITIVE EDIT: preserve the current furnished room, architecture, camera, lighting, and all first-pass furniture exactly. Add only the listed secondary decor/detail items. Do not move, remove, resize, recolor, or replace existing furniture.",
                    )
                    if part
                )
                pass2_results = run_render_variant_stage(
                    step1_img=generated_results[0],
                    style_prompt=resolved_style_prompt,
                    ref_input=ref_input,
                    unique_id=f"{unique_id}_p2",
                    furniture_specs_text=pass2_specs_text,
                    furniture_specs_json=pass2_generation_specs_json,
                    dimensions=generation_dimensions,
                    placement=pass2_placement,
                    scale_guide_path=scale_guide_path,
                    primary_item=primary_item,
                    room_dims_parsed=room_dims_parsed,
                    wall_span_norm=wall_span_norm,
                    size_hierarchy=size_hierarchy,
                    scale_plan=scale_plan_dict,
                    geometry_contract=geometry_contract_dict,
                    scene_contract=scene_contract_dict,
                    placement_plan=placement_plan_dict,
                    start_time=start_time,
                    room_planes=room_planes,
                    windows_present=windows_present,
                    room_analysis_text=room_analysis_text,
                    enable_scale_check=pass2_identity_validation_required,
                    generate_furnished_room=deps.generation.generate_furnished_room,
                    max_variants=1,
                    max_workers=1,
                    max_generation_attempts=2 if pass2_identity_validation_required else 1,
                    start_index=20,
                )
                pass2_paths = [
                    str((row or {}).get("path") or "")
                    for row in pass2_results or []
                    if isinstance(row, dict) and (row or {}).get("path")
                ]
                if pass2_paths:
                    generated_results, selected_result_reason = _polish_selected_best_result(
                        pass2_paths,
                        audience=aud,
                        unique_id=f"{unique_id}_p2",
                        selected_result_reason="pass2_additive_edit",
                        polish_main_image=deps.generation.polish_main_image,
                        logger=deps.runtime.logger,
                    )
                    selected_result_index = 0
                    selected_result_reason = f"{selected_result_reason}_after_pass2"
        if selected_variant_review:
            full_analyzed_data = _apply_selected_review_boxes_to_analyzed_items(
                full_analyzed_data,
                selected_variant_review,
            )
            try:
                full_analyzed_data = deps.postprocess.attach_volume_ranks(full_analyzed_data)
                volume_ranking = deps.postprocess.volume_ranking_snapshot(full_analyzed_data)
            except Exception as exc:
                deps.runtime.logger.exception(f"[VolumeRank] selected-review reuse failed: {exc}")

        log_render_summary(summary, log_summary=deps.runtime.log_summary, logger=deps.runtime.logger)
        return build_render_response_payload(
            std_path=std_path,
            step1_img=step1_img,
            scale_guide_path=None,
            generated_results=generated_results,
            selected_result_index=selected_result_index,
            selected_result_reason=selected_result_reason,
            selected_variant_review=selected_variant_review,
            variant_diagnostics=variant_diagnostics,
            candidate_results=candidate_results,
            final_result_blocked=strict_final_result_blocked,
            scale_plan=scale_plan_dict,
            room_dims_contract=room_dims_contract_dict,
            geometry_contract=geometry_contract_dict,
            scene_contract=scene_contract_dict,
            placement_plan=placement_plan_dict,
            include_replay_debug=(aud == "internal"),
            moodboard_url=mb_url,
            furniture_data=full_analyzed_data,
            volume_ranking=volume_ranking,
            prefix_main_user=prefix_main_user,
            prefix_main_empty=prefix_main_empty,
            prefix_main_rendered=prefix_main_rendered,
            resolve_image_url=deps.storage.resolve_image_url,
        )
    finally:
        deps.runtime.reset_summary_token(summary_token)
