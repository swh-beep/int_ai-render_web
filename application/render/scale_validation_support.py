import math
import os
import re
import time
import uuid
from collections import Counter
from typing import Any, Callable, Optional

from PIL import Image
from application.render.batch_detection_support import (
    build_detection_rows_from_matches,
    detect_rows_from_render,
    match_items_to_detected_rows,
)
from application.render.geometry_measurement_stage import (
    build_measurement_specs,
    summarize_measurements,
    unresolved_measurement_targets,
)
from application.render.postprocess_support import (
    _SENSITIVE_REMAP_FAMILIES,
    canonical_category,
    category_match_family,
    decor_prefers_surface_placement,
    remap_match_score,
)
from infrastructure.ai.analysis_provider_dispatch import GEMINI_ANALYSIS_DEFAULT


_MATCH_STRATEGY_CONFIDENCE = {
    "target_key": 0.98,
    "source_index": 0.95,
    "family_unique": 0.72,
    "label_unique": 0.62,
    "identity_key": 0.55,
}

_COMMON_RULE_SEVERITY = {
    "unmatched_item": 1.00,
    "low_confidence_match": 0.55,
    "scale_fit_violation": 0.95,
    "placement_violation": 0.90,
    "reference_shape_drift": 1.10,
    "reference_material_drift": 0.75,
    "reference_integration_drift": 1.00,
    "reflection_violation": 1.00,
    "validation_exception": 1.20,
}

_FAMILY_SEVERITY_MULTIPLIERS = {
    "mirror": 1.20,
    "rug": 1.10,
    "electronics": 1.15,
    "decor": 1.05,
    "plant": 1.05,
    "stool": 1.05,
    "floor_lamp": 1.05,
    "table_lamp": 1.05,
    "ceiling_light": 1.10,
    "wall_light": 1.05,
    "sofa": 1.10,
    "lounge_seating": 1.10,
    "chair": 1.05,
    "lounge_chair": 1.05,
    "table": 1.05,
    "storage": 1.05,
}

_EXTRA_INSTANCE_TRACKED_FAMILIES = {
    "sofa",
    "lounge_sofa",
    "lounge_seating",
    "chair",
    "lounge_chair",
    "table",
    "storage",
    "rug",
    "electronics",
    "decor",
    "plant",
    "stool",
    "floor_lamp",
    "table_lamp",
    "ceiling_light",
    "wall_light",
}

_WEAK_REFERENCE_ANALYSIS_QUALITIES = {
    "fallback",
    "fallback_after_weak_model",
    "fallback_after_invalid_model",
    "model_insufficient",
    "model_weak",
    "weak_model",
}


def _coerce_model_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def detect_back_wall_span_norm(
    empty_room_path: str,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
) -> tuple:
    try:
        with Image.open(empty_room_path) as img:
            prompt = (
                "TASK: ROOM GEOMETRY MEASUREMENT.\\n"
                "In this empty room photo, find the BACK WALL usable span where main furniture would sit.\\n"
                "Return STRICT JSON ONLY: {\\\"x_left\\\":0.0, \\\"x_right\\\":1.0} using normalized [0..1].\\n"
                "Use the floor-wall boundary; ignore doors/windows if they reduce usable span. Approximate if unsure."
            )
            response = call_gemini_with_failover(
                analysis_model_name,
                [prompt, img],
                {"timeout": 70},
                {},
                log_tag="Analysis.BackWallSpan",
            )
            parsed = safe_json_from_model_text(response.text if response and hasattr(response, "text") else "")
            if isinstance(parsed, dict):
                x_left = float(parsed.get("x_left", 0.0))
                x_right = float(parsed.get("x_right", 1.0))
                x_left = max(0.0, min(1.0, x_left))
                x_right = max(0.0, min(1.0, x_right))
                if x_right - x_left >= 0.2:
                    return (x_left, x_right)
    except Exception:
        pass
    return (0.0, 1.0)


def detect_windows_present(
    room_path: str,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
) -> bool:
    try:
        with Image.open(room_path) as img:
            img.thumbnail((1024, 1024))
            prompt = (
                "TASK: WINDOW VISIBILITY CHECK.\n"
                "Answer ONLY with YES or NO.\n"
                "Question: Are any windows or glass exterior openings clearly visible in this room image?\n"
                "If uncertain, answer NO."
            )
            response = call_gemini_with_failover(
                analysis_model_name,
                [prompt, img],
                {"timeout": 60},
                {},
                log_tag="Analysis.WindowsPresent",
            )
            text = (response.text if response and hasattr(response, "text") else "").strip().lower()
            if text.startswith("yes"):
                return True
            if text.startswith("no"):
                return False
    except Exception:
        pass
    return False


def crop_ref_item_image(ref_path: str, box_2d: list, out_path: str):
    try:
        if not box_2d:
            return None
        with Image.open(ref_path) as img:
            width, height = img.size
            ymin, xmin, ymax, xmax = box_2d
            left = int(xmin / 1000 * width)
            top = int(ymin / 1000 * height)
            right = int(xmax / 1000 * width)
            bottom = int(ymax / 1000 * height)
            left = max(0, min(width - 1, left))
            right = max(left + 1, min(width, right))
            top = max(0, min(height - 1, top))
            bottom = max(top + 1, min(height, bottom))
            crop = img.crop((left, top, right, bottom))
            crop.save(out_path, "PNG")
            return out_path
    except Exception:
        return None


def detect_primary_bbox_norm(
    staged_path: str,
    ref_item_crop_path: Optional[str],
    primary_label: Optional[str],
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
):
    try:
        with Image.open(staged_path) as img:
            prompt = (
                "OBJECT LOCALIZATION TASK.\\n"
                "Find the PRIMARY ANCHOR furniture in the staged room image.\\n"
                "Return STRICT JSON ONLY: {\\\"xmin\\\":0.0,\\\"ymin\\\":0.0,\\\"xmax\\\":1.0,\\\"ymax\\\":1.0}.\\n"
                "bbox must tightly cover only that furniture. If reference crop is provided, match that object."
            )
            content = [prompt, "Staged room image:", img]
            if primary_label:
                content.insert(1, f"Primary label hint: {primary_label}")
            ref_img = None
            try:
                if ref_item_crop_path and os.path.exists(ref_item_crop_path):
                    ref_img = Image.open(ref_item_crop_path)
                    content += ["Reference item crop:", ref_img]
                response = call_gemini_with_failover(
                    analysis_model_name,
                    content,
                    {"timeout": 70},
                    {},
                    log_tag="Analysis.PrimaryBBox",
                )
            finally:
                if ref_img:
                    ref_img.close()
            parsed = safe_json_from_model_text(response.text if response and hasattr(response, "text") else "")
            if isinstance(parsed, dict):
                xmin = float(parsed.get("xmin", 0.0))
                xmax = float(parsed.get("xmax", 1.0))
                ymin = float(parsed.get("ymin", 0.0))
                ymax = float(parsed.get("ymax", 1.0))
                xmin = max(0.0, min(1.0, xmin))
                xmax = max(0.0, min(1.0, xmax))
                ymin = max(0.0, min(1.0, ymin))
                ymax = max(0.0, min(1.0, ymax))
                if xmax - xmin > 0.05 and ymax - ymin > 0.05:
                    return (xmin, ymin, xmax, ymax)
    except Exception:
        pass
    return None


def detect_item_bbox_norm(
    staged_path: str,
    ref_item_crop_path: Optional[str],
    item_label: Optional[str],
    item_context: Optional[dict] = None,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
    timeout_sec: float = 70.0,
):
    try:
        with Image.open(staged_path) as img:
            prompt = (
                "OBJECT LOCALIZATION TASK.\n"
                "Find the specified furniture item in the staged room image.\n"
                "Return STRICT JSON ONLY: {\"xmin\":0.0,\"ymin\":0.0,\"xmax\":1.0,\"ymax\":1.0}.\n"
                "bbox must tightly cover only that furniture. If reference crop is provided, match that object."
            )
            content = [prompt, "Staged room image:", img]
            if item_label:
                content.insert(1, f"Item label hint: {item_label}")
            if isinstance(item_context, dict):
                family = item_context.get("family")
                dims_mm = item_context.get("dims_mm") or {}
                envelope = item_context.get("layout_envelope") or {}
                preserve_rules = item_context.get("preserve_rules") or []
                archetype_strategy = item_context.get("archetype_strategy") or {}
                if family:
                    content.insert(2, f"Family hint: {family}")
                if dims_mm:
                    content.insert(
                        3,
                        f"Expected dims(mm): W={dims_mm.get('width_mm')} D={dims_mm.get('depth_mm')} H={dims_mm.get('height_mm')}",
                    )
                ratio_bits = []
                if envelope.get("room_width_ratio") is not None:
                    ratio_bits.append(f"room_width_ratio={envelope.get('room_width_ratio')}")
                if envelope.get("room_height_ratio") is not None:
                    ratio_bits.append(f"room_height_ratio={envelope.get('room_height_ratio')}")
                if ratio_bits:
                    content.insert(4, "Expected layout envelope: " + ", ".join(ratio_bits))
                if preserve_rules:
                    content.insert(5, "Must preserve: " + ", ".join([str(x) for x in preserve_rules[:3]]))
                if archetype_strategy.get("render_strategy"):
                    content.insert(6, f"Archetype strategy: {archetype_strategy.get('render_strategy')}")
                if archetype_strategy.get("forbidden_substitutions"):
                    content.insert(7, "Forbidden substitutions: " + ", ".join([str(x) for x in (archetype_strategy.get("forbidden_substitutions") or [])[:3]]))
            ref_img = None
            try:
                if ref_item_crop_path and os.path.exists(ref_item_crop_path):
                    ref_img = Image.open(ref_item_crop_path)
                    content += ["Reference item crop:", ref_img]
                response = call_gemini_with_failover(
                    analysis_model_name,
                    content,
                    {
                        "timeout": max(1.0, float(timeout_sec or 70.0)),
                        "max_attempts": 1,
                    },
                    {},
                    log_tag="Analysis.ItemBBox",
                )
            finally:
                if ref_img:
                    ref_img.close()
            parsed = safe_json_from_model_text(response.text if response and hasattr(response, "text") else "")
            if isinstance(parsed, dict):
                xmin = float(parsed.get("xmin", 0.0))
                xmax = float(parsed.get("xmax", 1.0))
                ymin = float(parsed.get("ymin", 0.0))
                ymax = float(parsed.get("ymax", 1.0))
                xmin = max(0.0, min(1.0, xmin))
                xmax = max(0.0, min(1.0, xmax))
                ymin = max(0.0, min(1.0, ymin))
                ymax = max(0.0, min(1.0, ymax))
                min_width = 0.05
                min_height = 0.05
                if isinstance(item_context, dict):
                    envelope = item_context.get("layout_envelope") or {}
                    expected_w = envelope.get("room_width_ratio")
                    expected_h = envelope.get("room_height_ratio")
                    family = str(item_context.get("family") or "").strip().lower()
                    if isinstance(expected_w, (int, float)) and expected_w > 0:
                        min_width = max(0.02, min(0.05, float(expected_w) * 0.35))
                    if isinstance(expected_h, (int, float)) and expected_h > 0:
                        min_height = max(0.02, min(0.05, float(expected_h) * 0.35))
                    if family in {"floor_lamp", "table_lamp", "stool", "mirror"}:
                        min_width = min(min_width, 0.02)
                        min_height = min(min_height, 0.02)
                if xmax - xmin > min_width and ymax - ymin > min_height:
                    return (xmin, ymin, xmax, ymax)
    except Exception:
        pass
    return None


def crop_bbox_norm_image(image_path: str, bbox_norm: tuple, out_path: str):
    try:
        if not bbox_norm:
            return None
        with Image.open(image_path) as img:
            width, height = img.size
            xmin, ymin, xmax, ymax = bbox_norm
            left = int(max(0.0, min(1.0, xmin)) * width)
            top = int(max(0.0, min(1.0, ymin)) * height)
            right = int(max(0.0, min(1.0, xmax)) * width)
            bottom = int(max(0.0, min(1.0, ymax)) * height)
            left = max(0, min(width - 1, left))
            right = max(left + 1, min(width, right))
            top = max(0, min(height - 1, top))
            bottom = max(top + 1, min(height, bottom))
            crop = img.crop((left, top, right, bottom))
            crop.save(out_path, "PNG")
            return out_path
    except Exception:
        return None


def _normalized_item_category(item: dict) -> str:
    raw = item.get("category") or item.get("category_canonical") or item.get("label") or ""
    family = category_match_family(raw)
    if family:
        return family.replace("-", "_").replace(" ", "_")
    canonical = canonical_category(raw)
    if canonical:
        return canonical.replace("-", "_").replace(" ", "_")
    text = str(raw).strip().lower()
    return text.replace("-", "_").replace(" ", "_")


def _normalized_label(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _normalized_family(value: Any) -> str:
    family = category_match_family(value)
    return str(family or "").strip().lower().replace("-", "_").replace(" ", "_")


def _coerce_failed_rule_ids(issues: list[str] | tuple | set | None) -> list[str]:
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


def _identity_richness_score(item: dict) -> float:
    identity_profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    product_identity = (item.get("product_identity") or {}) if isinstance(item, dict) else {}
    archetype_strategy = (item.get("archetype_strategy") or {}) if isinstance(item, dict) else {}
    score = 0.0
    if item.get("crop_path") and os.path.exists(str(item.get("crop_path"))):
        score += 1.0
    score += min(0.9, len(identity_profile.get("distinctive_parts") or []) * 0.25)
    score += min(0.8, len(product_identity.get("preserve_rules") or identity_profile.get("preserve_rules") or []) * 0.20)
    score += min(0.6, len(product_identity.get("topology_cues") or []) * 0.15)
    score += min(0.4, len(product_identity.get("support_geometry") or []) * 0.10)
    score += min(0.4, len(product_identity.get("opening_or_gap_features") or []) * 0.10)
    score += min(0.5, len(identity_profile.get("material_cues") or []) * 0.10)
    score += min(0.5, len(identity_profile.get("shape_cues") or []) * 0.10)
    if str(identity_profile.get("silhouette_summary") or "").strip():
        score += 0.25
    if identity_profile.get("reflective_surface") or bool(product_identity.get("reflection_constraints")):
        score += 0.20
    return round(score, 3)


def _reference_feature_signal_count(item: dict) -> int:
    reference_features = item.get("reference_features") if isinstance(item, dict) and isinstance(item.get("reference_features"), dict) else {}
    identity_profile = item.get("identity_profile") if isinstance(item, dict) and isinstance(item.get("identity_profile"), dict) else {}
    product_identity = item.get("product_identity") if isinstance(item, dict) and isinstance(item.get("product_identity"), dict) else {}
    signal_keys = (
        "silhouette_cues",
        "material_cues",
        "color_cues",
        "surface_finish",
        "distinctive_parts",
        "support_geometry",
        "preserve_rules",
        "negative_identity_constraints",
    )
    count = 0
    for source in (reference_features, identity_profile, product_identity):
        for key in signal_keys:
            value = source.get(key)
            if isinstance(value, list):
                count += len([row for row in value if str(row or "").strip()])
            elif str(value or "").strip():
                count += 1
    for key in ("silhouette_summary", "product_identity_summary"):
        if str(identity_profile.get(key) or product_identity.get(key) or "").strip():
            count += 1
    return count


def _has_weak_reference_identity(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    crop_path = item.get("crop_path")
    if not crop_path or not os.path.exists(str(crop_path)):
        return False
    reference_features = item.get("reference_features") if isinstance(item.get("reference_features"), dict) else {}
    analysis_quality = str(reference_features.get("analysis_quality") or "").strip().lower()
    extraction_mode = str(reference_features.get("extraction_mode") or "").strip().lower()
    if analysis_quality in _WEAK_REFERENCE_ANALYSIS_QUALITIES or extraction_mode == "fallback":
        return True
    has_analysis_signal = "analysis_quality" in reference_features or "extraction_mode" in reference_features
    return bool(has_analysis_signal and _reference_feature_signal_count(item) <= 2)


def _should_review_reference_fidelity(item: dict) -> bool:
    crop_path = item.get("crop_path")
    if not crop_path or not os.path.exists(crop_path):
        return False
    return _identity_richness_score(item) >= 1.0 or _has_weak_reference_identity(item)


def _item_importance_score(item: dict, *, is_primary: bool = False) -> float:
    identity_profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    envelope = (item.get("layout_envelope") or {}) if isinstance(item, dict) else {}
    placement_contract = (item.get("placement_contract") or {}) if isinstance(item, dict) else {}
    room_targets = (placement_contract.get("room_ratio_targets") or {}) if isinstance(placement_contract, dict) else {}
    product_identity = (item.get("product_identity") or {}) if isinstance(item, dict) else {}
    archetype_strategy = (item.get("archetype_strategy") or {}) if isinstance(item, dict) else {}
    volume_proxy = float(item.get("volume_proxy") or 0)
    category_score = float(item.get("category_score") or 0)
    score = 1.0
    if volume_proxy > 0:
        score += min(0.9, math.log10(volume_proxy + 1.0) / 6.0)
    if category_score > 0:
        score += min(0.4, category_score / 20.0)
    for key in ("room_width_ratio", "room_depth_ratio", "room_height_ratio", "footprint_ratio"):
        value = envelope.get(key)
        if value is None:
            value = room_targets.get(key)
        if isinstance(value, (int, float)) and value > 0:
            score += min(0.4, float(value) * 2.0)
    score += min(0.5, _identity_richness_score(item) * 0.18)
    if product_identity.get("family") == "mirror":
        score += 0.1
    if str(archetype_strategy.get("strictness") or "").strip().lower() == "critical":
        score += 0.35
    try:
        score += min(0.6, float(archetype_strategy.get("criticality") or 0.0) * 0.2)
    except Exception:
        pass
    if is_primary:
        score += 0.75
    return round(score, 3)


def _rule_kind_for_id(rule_id: str) -> str:
    normalized = str(rule_id or "").strip()
    if normalized.startswith("scale_plan_") or normalized == "strict_scale_contract_not_ready":
        return "scale_fit_violation"
    if normalized.startswith("reference_shape_drift"):
        return "reference_shape_drift"
    if normalized.startswith("reference_material_drift"):
        return "reference_material_drift"
    if normalized.startswith("reference_integration_drift"):
        return "reference_integration_drift"
    if normalized.startswith("extra_instance_detected"):
        return "reference_integration_drift"
    if normalized.startswith("mirror_reflection_drift"):
        return "reflection_violation"
    if normalized in {"wall_attached_floor_collision", "ceiling_attached_height_violation", "rug_floating_above_floor_zone", "floor_item_floating"}:
        return "placement_violation"
    if normalized in {"primary_width_vs_room_width", "rug_vs_anchor_footprint", "tiny_item_vs_anchor_height", "relative_height_vs_anchor"}:
        return "scale_fit_violation"
    if normalized in {"validation_exception", "scale_validation_exception"}:
        return "validation_exception"
    if normalized in {"unmatched_item", "unmatched_source_items", "primary_anchor_unmatched", "no_matched_items"} or normalized.endswith("_unmatched"):
        return "unmatched_item"
    if normalized == "low_confidence_match":
        return "low_confidence_match"
    return normalized or "scale_fit_violation"


def _issue_severity(rule_id: str, family: str | None = None) -> float:
    kind = _rule_kind_for_id(rule_id)
    severity = _COMMON_RULE_SEVERITY.get(kind, 0.8)
    family_multiplier = _FAMILY_SEVERITY_MULTIPLIERS.get(str(family or "").strip().lower(), 1.0)
    return round(severity * family_multiplier, 3)


def _build_issue_record(
    *,
    rule_id: str,
    item_key: str | None,
    family: str | None,
    item_importance: float,
    confidence: float,
    stage: str,
    evidence: dict | None = None,
    match_strategy: str | None = None,
) -> dict:
    severity = _issue_severity(rule_id, family)
    confidence = max(0.05, min(1.0, float(confidence or 0.0)))
    weighted_score = round(severity * confidence * max(0.1, float(item_importance or 0.0)), 4)
    evidence_dict = dict(evidence or {})
    return {
        "rule_id": rule_id,
        "rule_kind": _rule_kind_for_id(rule_id),
        "item_key": item_key,
        "family": family,
        "severity": severity,
        "confidence": confidence,
        "item_importance": float(item_importance or 0.0),
        "weighted_score": weighted_score,
        "stage": stage,
        "match_strategy": match_strategy,
        "plan_metric": evidence_dict.get("metric"),
        "expected_value": evidence_dict.get("expected"),
        "observed_value": evidence_dict.get("observed"),
        "delta": evidence_dict.get("delta"),
        "tolerance": evidence_dict.get("tolerance"),
        "evidence": evidence_dict,
    }


def _build_scale_plan_index(scale_plan: dict | None) -> dict[str, dict]:
    if not isinstance(scale_plan, dict):
        return {}
    indexed: dict[str, dict] = {}
    for row in scale_plan.get("items") or []:
        if not isinstance(row, dict):
            continue
        for key in (
            row.get("target_key"),
            row.get("source_index"),
            row.get("label"),
        ):
            if key in (None, ""):
                continue
            indexed.setdefault(str(key), row)
    return indexed


def _resolve_scale_plan_item(scale_plan_index: dict[str, dict], item: dict, fallback_index: int) -> dict | None:
    for key in (
        item.get("target_key"),
        item.get("source_index"),
        item.get("label"),
    ):
        if key in (None, ""):
            continue
        row = scale_plan_index.get(str(key))
        if row:
            return row
    item_key = _item_identity_key(item, fallback_index)
    return scale_plan_index.get(str(item_key))


def _summarize_scale_plan_measurements(rows: list[dict]) -> dict:
    valid_rows = [row for row in rows if isinstance(row, dict)]
    if not valid_rows:
        return {
            "measurement_count": 0,
            "critical_ratio_fail_count": 0,
            "mean_relative_error": 0.0,
            "max_relative_error": 0.0,
        }
    relative_errors = [float(row.get("relative_error") or 0.0) for row in valid_rows]
    critical_fail_count = sum(
        1
        for row in valid_rows
        if float(row.get("delta") or 0.0) > float(row.get("tolerance") or 0.0)
    )
    return {
        "measurement_count": len(valid_rows),
        "critical_ratio_fail_count": critical_fail_count,
        "mean_relative_error": round(sum(relative_errors) / max(1, len(relative_errors)), 4),
        "max_relative_error": round(max(relative_errors), 4),
    }
def _build_detection_item_context(item: dict) -> dict:
    identity_profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    product_identity = (item.get("product_identity") or {}) if isinstance(item, dict) else {}
    archetype_strategy = (item.get("archetype_strategy") or {}) if isinstance(item, dict) else {}
    layout_envelope = (item.get("layout_envelope") or {}) if isinstance(item, dict) else {}
    placement_contract = (item.get("placement_contract") or {}) if isinstance(item, dict) else {}
    dims_mm = (item.get("dims_mm") or {}) if isinstance(item, dict) else {}
    return {
        "family": product_identity.get("family") or identity_profile.get("family") or _normalized_item_category(item),
        "dims_mm": dims_mm,
        "layout_envelope": layout_envelope,
        "placement_contract": placement_contract,
        "preserve_rules": list((product_identity.get("preserve_rules") or identity_profile.get("preserve_rules") or [])[:4]),
        "archetype_strategy": dict(archetype_strategy or {}),
    }


def _review_reference_fidelity(
    staged_path: str,
    item: dict,
    bbox_norm: tuple,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
    timeout_sec: float = 70.0,
) -> list[str]:
    if not _should_review_reference_fidelity(item):
        return []

    target_key = str(item.get("target_key") or item.get("source_index") or item.get("label") or "item")
    item_category = _normalized_item_category(item)
    is_mirror = item_category == "mirror"
    identity_profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    product_identity = (item.get("product_identity") or {}) if isinstance(item, dict) else {}
    archetype_strategy = (item.get("archetype_strategy") or {}) if isinstance(item, dict) else {}
    placement_contract = (item.get("placement_contract") or {}) if isinstance(item, dict) else {}
    layout_envelope = (item.get("layout_envelope") or {}) if isinstance(item, dict) else {}
    silhouette_summary = str(identity_profile.get("silhouette_summary") or "").strip()
    topology_cues = ", ".join((product_identity.get("topology_cues") or [])[:4])
    support_geometry = ", ".join((product_identity.get("support_geometry") or [])[:4])
    distinctive_parts = ", ".join((identity_profile.get("distinctive_parts") or [])[:4])
    preserve_rules = ", ".join((product_identity.get("preserve_rules") or identity_profile.get("preserve_rules") or [])[:4])
    material_cues = ", ".join((identity_profile.get("material_cues") or [])[:4])
    required_parts = ", ".join((archetype_strategy.get("required_parts") or [])[:4])
    forbidden_substitutions = ", ".join((archetype_strategy.get("forbidden_substitutions") or [])[:4])
    placement_family = str(
        placement_contract.get("placement_family")
        or layout_envelope.get("placement_family")
        or ""
    ).strip().lower()
    placement_zone = str(placement_contract.get("zone") or "").strip().lower()
    rendered_crop_path = os.path.join("outputs", f"ref_fidelity_{uuid.uuid4().hex[:8]}.png")
    unresolved_issue = f"reference_review_unresolved:{target_key}"
    try:
        rendered_crop_path = crop_bbox_norm_image(staged_path, bbox_norm, rendered_crop_path)
        if not rendered_crop_path or not os.path.exists(rendered_crop_path):
            return [unresolved_issue]

        with Image.open(item["crop_path"]) as ref_img, Image.open(rendered_crop_path) as rendered_img:
            prompt = (
                "REFERENCE FURNITURE FIDELITY REVIEW.\n"
                f"Stable item id: {target_key}\n"
                f"Item label: {item.get('label') or 'Item'}\n"
                "Decide whether the rendered crop still depicts the same furniture object as the reference crop.\n"
                "Focus on silhouette/topology, support geometry, material identity, must-preserve parts, and whether the object feels naturally integrated into the room.\n"
                "Natural integration means grounded floor or wall contact, no obvious halo/cutout edge, and plausible local blending or occlusion instead of a pasted-in composite.\n"
                "Ignore room lighting, camera angle, and absolute scale unless they reveal pasted-looking compositing.\n"
                + (f"Silhouette summary: {silhouette_summary}\n" if silhouette_summary else "")
                + (f"Topology cues: {topology_cues}\n" if topology_cues else "")
                + (f"Support geometry: {support_geometry}\n" if support_geometry else "")
                + (f"Distinctive parts: {distinctive_parts}\n" if distinctive_parts else "")
                + (f"Preserve rules: {preserve_rules}\n" if preserve_rules else "")
                + (f"Material cues: {material_cues}\n" if material_cues else "")
                + (f"Required parts: {required_parts}\n" if required_parts else "")
                + (f"Forbidden substitutions: {forbidden_substitutions}\n" if forbidden_substitutions else "")
                + (f"Expected placement family: {placement_family}\n" if placement_family else "")
                + (f"Expected placement zone: {placement_zone}\n" if placement_zone else "")
                + (
                    "For mirrors, also check whether the reflective surface still behaves like a wall mirror and reflects a plausible opposite room view.\n"
                    if is_mirror
                    else ""
                )
                + "Return STRICT JSON ONLY: "
                + (
                    '{"same_object":true,"shape_match":true,"material_match":true,"integration_match":true,"grounded_contact":true,"halo_cutout_free":true,"blending_natural":true,"reflection_match":true,"reason":"short reason"}'
                    if is_mirror
                    else '{"same_object":true,"shape_match":true,"material_match":true,"integration_match":true,"grounded_contact":true,"halo_cutout_free":true,"blending_natural":true,"reason":"short reason"}'
                )
            )
            response = call_gemini_with_failover(
                analysis_model_name,
                [prompt, "Reference crop:", ref_img, "Rendered crop:", rendered_img],
                {"timeout": max(1.0, float(timeout_sec or 70.0))},
                {},
                log_tag="Analysis.ReferenceFidelity",
            )
        parsed = safe_json_from_model_text(response.text if response and hasattr(response, "text") else "")
        if not isinstance(parsed, dict):
            return [unresolved_issue]

        same_object = _coerce_model_bool(parsed.get("same_object"))
        shape_match = _coerce_model_bool(parsed.get("shape_match"))
        material_match = _coerce_model_bool(parsed.get("material_match"))
        integration_match = _coerce_model_bool(parsed.get("integration_match"))
        grounded_contact = _coerce_model_bool(parsed.get("grounded_contact"))
        halo_cutout_free = _coerce_model_bool(parsed.get("halo_cutout_free"))
        blending_natural = _coerce_model_bool(parsed.get("blending_natural"))
        reflection_match = _coerce_model_bool(parsed.get("reflection_match"))
        issues = []
        if same_object is False or shape_match is False:
            issues.append(f"reference_shape_drift:{target_key}")
        if material_match is False:
            issues.append(f"reference_material_drift:{target_key}")
        if (
            integration_match is False
            or grounded_contact is False
            or halo_cutout_free is False
            or blending_natural is False
        ):
            issues.append(f"reference_integration_drift:{target_key}")
        if is_mirror and reflection_match is False:
            issues.append(f"mirror_reflection_drift:{target_key}")
        return issues
    except Exception:
        return [unresolved_issue]
    finally:
        try:
            if rendered_crop_path and os.path.exists(rendered_crop_path):
                os.remove(rendered_crop_path)
        except Exception:
            pass


def score_scale(bbox_norm: tuple, wall_span_norm: tuple, target_ratio: float) -> float:
    try:
        xmin, ymin, xmax, ymax = bbox_norm
        x_left, x_right = wall_span_norm if wall_span_norm else (0.0, 1.0)
        span = max(1e-6, (x_right - x_left))
        width = max(1e-6, (xmax - xmin))
        actual = width / span
        target = max(1e-6, float(target_ratio))
        error = abs(actual - target)
        tolerance = max(0.08, target * 0.20)
        score = 1.0 - min(1.0, error / tolerance)
        return float(max(0.0, min(1.0, score)))
    except Exception:
        return 0.0


def _item_identity_key(item: dict, fallback_index: int) -> str:
    target_key = item.get("target_key")
    if target_key not in (None, ""):
        return str(target_key)
    source_index = item.get("source_index")
    if source_index not in (None, ""):
        return str(source_index)
    label = item.get("label")
    if label not in (None, ""):
        return f"{label}#{fallback_index}"
    return f"item_{fallback_index}"


def _normalize_item_entries(items: list) -> list[tuple[Any, dict]]:
    normalized: list[tuple[Any, dict]] = []
    for fallback_index, entry in enumerate(items if isinstance(items, list) else []):
        item_index = fallback_index
        item = entry
        if isinstance(entry, tuple) and len(entry) == 2 and isinstance(entry[1], dict):
            item_index, item = entry
        if not isinstance(item, dict):
            continue
        normalized.append((item_index, item))
    return normalized


def _is_rug_item(item: dict) -> bool:
    if item.get("is_rug"):
        return True
    category = str(item.get("category") or "").strip().lower()
    if category == "rug":
        return True
    label = str(item.get("label") or "").strip().lower()
    return label == "rug"


def _coerce_bbox_norm(bbox_value: Any) -> Optional[tuple]:
    if isinstance(bbox_value, dict):
        if "bbox_norm" in bbox_value:
            bbox_value = bbox_value.get("bbox_norm")
        elif "bbox" in bbox_value:
            bbox_value = bbox_value.get("bbox")
    if not isinstance(bbox_value, (list, tuple)) or len(bbox_value) != 4:
        return None
    try:
        xmin = float(bbox_value[0])
        ymin = float(bbox_value[1])
        xmax = float(bbox_value[2])
        ymax = float(bbox_value[3])
    except Exception:
        return None
    xmin = max(0.0, min(1.0, xmin))
    ymin = max(0.0, min(1.0, ymin))
    xmax = max(0.0, min(1.0, xmax))
    ymax = max(0.0, min(1.0, ymax))
    if xmax <= xmin or ymax <= ymin:
        return None
    return (xmin, ymin, xmax, ymax)


def _normalize_detection_rows(
    detected_rows: Optional[list] = None,
    detected_boxes: Optional[Any] = None,
) -> list:
    rows = []
    if detected_rows:
        for index, row in enumerate(detected_rows):
            if not isinstance(row, dict):
                continue
            bbox_norm = _coerce_bbox_norm(row.get("bbox_norm") or row.get("bbox"))
            if bbox_norm is None:
                continue
            rows.append(
                {
                    "label": row.get("label"),
                    "category": row.get("category"),
                    "category_canonical": row.get("category_canonical"),
                    "family": row.get("family"),
                    "target_key": row.get("target_key"),
                    "source_index": row.get("source_index"),
                    "bbox_norm": bbox_norm,
                    "box_2d": row.get("box_2d"),
                    "_row_index": index,
                }
            )
        return rows

    if isinstance(detected_boxes, dict):
        for index, (key, bbox_value) in enumerate(detected_boxes.items()):
            bbox_norm = _coerce_bbox_norm(bbox_value)
            if bbox_norm is None:
                continue
            rows.append(
                {
                    "label": key,
                    "category": None,
                    "category_canonical": None,
                    "family": None,
                    "target_key": key,
                    "source_index": None,
                    "bbox_norm": bbox_norm,
                    "box_2d": None,
                    "_row_index": index,
                }
            )
        return rows

    if isinstance(detected_boxes, list):
        for index, bbox_value in enumerate(detected_boxes):
            bbox_norm = _coerce_bbox_norm(bbox_value)
            if bbox_norm is None:
                continue
            rows.append(
                {
                    "label": None,
                    "category": None,
                    "category_canonical": None,
                    "family": None,
                    "target_key": None,
                    "source_index": index,
                    "bbox_norm": bbox_norm,
                    "box_2d": None,
                    "_row_index": index,
                }
            )
    return rows


def _merge_detected_rows(*row_sets: Optional[list]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple] = set()
    for row_set in row_sets:
        for row in row_set or []:
            if not isinstance(row, dict):
                continue
            bbox_norm = _coerce_bbox_norm(row.get("bbox_norm") or row.get("bbox"))
            if bbox_norm is None:
                continue
            key = (
                tuple(round(float(value), 4) for value in bbox_norm),
                _normalized_label(row.get("label")),
                str(row.get("target_key") or ""),
                str(row.get("source_index") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "label": row.get("label"),
                    "category": row.get("category"),
                    "category_canonical": row.get("category_canonical"),
                    "family": row.get("family"),
                    "target_key": row.get("target_key"),
                    "source_index": row.get("source_index"),
                    "bbox_norm": bbox_norm,
                    "box_2d": row.get("box_2d"),
                }
            )
    return merged


def _build_label_counts(items: list[tuple[Any, dict]], rows: list[dict]) -> tuple[Counter, Counter]:
    item_counts = Counter()
    row_counts = Counter()
    for _, item in items:
        label_key = _normalized_label(item.get("label"))
        if label_key:
            item_counts[label_key] += 1
    for row in rows:
        label_key = _normalized_label(row.get("label"))
        if label_key:
            row_counts[label_key] += 1
    return item_counts, row_counts


def _build_family_counts(items: list[tuple[Any, dict]], rows: list[dict]) -> tuple[Counter, Counter]:
    item_counts = Counter()
    row_counts = Counter()
    for _, item in items:
        family_key = _normalized_family(item.get("category") or item.get("category_canonical") or item.get("label"))
        if family_key:
            item_counts[family_key] += 1
    for row in rows:
        family_key = _normalized_family(row.get("category") or row.get("category_canonical") or row.get("label"))
        if family_key:
            row_counts[family_key] += 1
    return item_counts, row_counts


def _index_detection_rows(rows: list) -> dict:
    indexed = {}
    for row in rows:
        bbox_norm = row.get("bbox_norm")
        if bbox_norm is None:
            continue
        for key in (row.get("target_key"), row.get("source_index"), row.get("label")):
            if key in (None, ""):
                continue
            indexed.setdefault(str(key), []).append(row)
        family_key = _normalized_family(row.get("category") or row.get("category_canonical") or row.get("label"))
        if family_key:
            indexed.setdefault(f"family::{family_key}", []).append(row)
    return indexed


def _match_detection_row(
    item: dict,
    indexed_rows: dict,
    fallback_index: int,
    item_label_counts: Optional[Counter] = None,
    row_label_counts: Optional[Counter] = None,
    item_family_counts: Optional[Counter] = None,
    row_family_counts: Optional[Counter] = None,
    used_row_indexes: Optional[set] = None,
) -> tuple[Optional[str], Optional[dict], Optional[str], float]:
    if used_row_indexes is None:
        used_row_indexes = set()
    candidates = [
        ("target_key", item.get("target_key")),
        ("source_index", item.get("source_index")),
    ]
    for strategy, candidate in candidates:
        if candidate in (None, ""):
            continue
        candidate_key = str(candidate)
        matched_rows = indexed_rows.get(candidate_key)
        if matched_rows:
            for row in matched_rows:
                row_index = row.get("_row_index")
                if row_index in used_row_indexes:
                    continue
                used_row_indexes.add(row_index)
                return candidate_key, row, strategy, _MATCH_STRATEGY_CONFIDENCE[strategy]

    family_key = _normalized_family(item.get("category") or item.get("category_canonical") or item.get("label"))
    if family_key and (item_family_counts or {}).get(family_key, 0) == 1 and (row_family_counts or {}).get(family_key, 0) == 1:
        for row in indexed_rows.get(f"family::{family_key}", []) or []:
            row_index = row.get("_row_index")
            if row_index in used_row_indexes:
                continue
            used_row_indexes.add(row_index)
            return f"family::{family_key}", row, "family_unique", _MATCH_STRATEGY_CONFIDENCE["family_unique"]

    label_key = _normalized_label(item.get("label"))
    if label_key and (item.get("target_key") in (None, "") and item.get("source_index") in (None, "")):
        if (item_label_counts or {}).get(label_key, 0) == 1 and (row_label_counts or {}).get(label_key, 0) == 1:
            for row in indexed_rows.get(str(item.get("label")), []) or []:
                row_index = row.get("_row_index")
                if row_index in used_row_indexes:
                    continue
                used_row_indexes.add(row_index)
                return str(item.get("label")), row, "label_unique", _MATCH_STRATEGY_CONFIDENCE["label_unique"]

    fallback_key = _item_identity_key(item, fallback_index)
    matched_rows = indexed_rows.get(fallback_key)
    if matched_rows:
        for row in matched_rows:
            row_index = row.get("_row_index")
            if row_index in used_row_indexes:
                continue
            used_row_indexes.add(row_index)
            return fallback_key, row, "identity_key", _MATCH_STRATEGY_CONFIDENCE["identity_key"]
    return None, None, None, 0.0


def _resolve_primary_match(
    complete_items: list,
    matched_items: dict,
    *,
    primary_label: Optional[str] = None,
    primary_target_key: Optional[str] = None,
    primary_source_index: Optional[Any] = None,
) -> tuple[Optional[str], Optional[dict], bool]:
    selector_plan = [
        ("target_key", primary_target_key),
        ("source_index", primary_source_index),
        ("label", primary_label),
    ]
    explicit_primary_selector_present = any(selector not in (None, "") for _, selector in selector_plan)

    for selector_name, selector_value in selector_plan:
        if selector_value in (None, ""):
            continue
        for fallback_index, item in complete_items:
            if selector_name == "target_key":
                if str(item.get("target_key") or "") != str(selector_value):
                    continue
            elif selector_name == "source_index":
                if str(item.get("source_index")) != str(selector_value):
                    continue
            else:
                if str(item.get("label") or "") != str(selector_value):
                    continue

            item_key = _item_identity_key(item, fallback_index)
            if item_key in matched_items:
                return item_key, item, False

    fallback_candidates = []
    for fallback_index, item in complete_items:
        item_key = _item_identity_key(item, fallback_index)
        if item_key not in matched_items:
            continue
        dims = item.get("dims_mm") or {}
        bbox_norm = (matched_items.get(item_key) or {}).get("bbox_norm") or []
        bbox_area = 0.0
        if len(bbox_norm) == 4:
            bbox_area = max(0.0, float(bbox_norm[2] - bbox_norm[0])) * max(0.0, float(bbox_norm[3] - bbox_norm[1]))
        width_mm = float(dims.get("width_mm") or 0)
        height_mm = float(dims.get("height_mm") or 0)
        fallback_candidates.append((width_mm * max(1.0, height_mm), bbox_area, item_key, item))

    if fallback_candidates:
        _, _, item_key, item = max(fallback_candidates, key=lambda row: (row[0], row[1]))
        return item_key, item, explicit_primary_selector_present

    return None, None, False


_INCOMPLETE_ITEMS_ISSUE = "incomplete_items_missing_required_dimensions"
_NO_MATCHED_ITEMS_ISSUE = "no_matched_items"
_PRIMARY_UNMATCHED_ISSUE = "primary_anchor_unmatched"
_UNMATCHED_ITEMS_RULE = "unmatched_source_items"
_VALIDATION_EXCEPTION_ISSUE = "scale_validation_exception"
_UNMATCHED_FAMILY_OVERRIDE_RULES = {
    "mirror": "mirror_unmatched",
    "rug": "rug_unmatched",
    "floor_lamp": "lamp_unmatched",
    "table_lamp": "lamp_unmatched",
    "sofa": "sofa_unmatched",
    "lounge_seating": "sofa_unmatched",
    "table": "table_unmatched",
    "storage": "storage_unmatched",
}

def _placement_family(item: dict) -> str:
    identity_profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    envelope = (item.get("layout_envelope") or {}) if isinstance(item, dict) else {}
    placement_contract = (item.get("placement_contract") or {}) if isinstance(item, dict) else {}
    category = _normalized_item_category(item)
    placement_hint = str(placement_contract.get("placement_family") or envelope.get("placement_family") or "").strip().lower()
    if placement_hint in {"wall_attached", "ceiling_attached", "rug", "surface_placed", "floor_placed", "small_free_object"}:
        return placement_hint
    if identity_profile.get("wall_attached_expected"):
        return "wall_attached"
    if identity_profile.get("ceiling_attached_expected") or category == "ceiling_light":
        return "ceiling_attached"
    if category in {"art", "poster", "mirror", "frame", "wall_art", "wall_decor"}:
        return "wall_attached"
    if category == "wall_light":
        return "wall_attached"
    if category == "rug":
        return "rug"
    if category == "table_lamp" or (category == "decor" and decor_prefers_surface_placement(item)):
        return "surface_placed"
    if identity_profile.get("floor_contact_expected") or not category:
        return "floor_placed"
    return "floor_placed"


def validate_scale_from_detection_map(
    items: list,
    room_dims: dict,
    *,
    room_planes: Optional[dict] = None,
    scale_plan: Optional[dict] = None,
    geometry_contract: Optional[dict] = None,
    detected_rows: Optional[list] = None,
    detected_boxes: Optional[Any] = None,
    detected_boxes_norm: Optional[Any] = None,
    primary_label: Optional[str] = None,
    primary_target_key: Optional[str] = None,
    primary_source_index: Optional[Any] = None,
) -> tuple:
    try:
        items = _normalize_item_entries(items)
        room_dims = room_dims if isinstance(room_dims, dict) else {}
        if detected_boxes is None:
            detected_boxes = detected_boxes_norm
        rows = _normalize_detection_rows(detected_rows=detected_rows, detected_boxes=detected_boxes)
        indexed_rows = _index_detection_rows(rows)
        scale_plan_index = _build_scale_plan_index(scale_plan)
        geometry_target_index = {
            str(row.get("target_key") or ""): row
            for row in ((geometry_contract or {}).get("item_targets") or [])
            if isinstance(row, dict) and str(row.get("target_key") or "")
        } if isinstance(geometry_contract, dict) else {}
        item_label_counts, row_label_counts = _build_label_counts(items, rows)
        item_family_counts, row_family_counts = _build_family_counts(items, rows)
        used_row_indexes = set()

        complete_items = []
        for index, item in items:
            dims = item.get("dims_mm") or {}
            width = int(dims.get("width_mm") or 0)
            depth = int(dims.get("depth_mm") or 0)
            height = int(dims.get("height_mm") or 0)
            if width > 0 and depth > 0 and height > 0:
                complete_items.append((index, item))

        if items and len(complete_items) != len(items):
            return False, [_INCOMPLETE_ITEMS_ISSUE], {
                "failed_rules": [_INCOMPLETE_ITEMS_ISSUE],
                "matched_items": {},
                "rule_details": {},
                "unmatched_items": [],
                "issue_records": [
                    _build_issue_record(
                        rule_id=_INCOMPLETE_ITEMS_ISSUE,
                        item_key=None,
                        family=None,
                        item_importance=1.0,
                        confidence=1.0,
                        stage="scale_validation",
                    )
                ],
            }

        matched_items = {}
        unmatched_items = []
        item_lookup: dict[str, dict] = {}
        for fallback_index, item in complete_items:
            item_key = _item_identity_key(item, fallback_index)
            item_lookup[item_key] = item
            match_key, row, match_strategy, match_confidence = _match_detection_row(
                item,
                indexed_rows,
                fallback_index,
                item_label_counts=item_label_counts,
                row_label_counts=row_label_counts,
                item_family_counts=item_family_counts,
                row_family_counts=row_family_counts,
                used_row_indexes=used_row_indexes,
            )
            bbox_norm = list(_coerce_bbox_norm(row.get("bbox_norm"))) if row else None
            family = _normalized_family(item.get("category") or item.get("category_canonical") or item.get("label"))
            item_importance = _item_importance_score(item)
            if row and bbox_norm is not None:
                matched_items[item_key] = {
                    "label": item.get("label"),
                    "target_key": item.get("target_key"),
                    "source_index": item.get("source_index"),
                    "match_key": match_key,
                    "bbox_norm": bbox_norm,
                    "dims_mm": item.get("dims_mm") or {},
                    "category": item.get("category"),
                    "family": family,
                    "identity_profile": item.get("identity_profile") or {},
                    "layout_envelope": item.get("layout_envelope") or {},
                    "is_rug": _is_rug_item(item),
                    "match_strategy": match_strategy,
                    "match_confidence": match_confidence,
                    "box_match_score": match_confidence,
                    "item_importance": item_importance,
                }
            else:
                unmatched_items.append(
                    {
                        "item_key": item_key,
                        "label": item.get("label"),
                        "target_key": item.get("target_key"),
                        "source_index": item.get("source_index"),
                        "category": item.get("category"),
                        "family": family,
                        "item_importance": item_importance,
                    }
                )

        extra_detected_items = []
        for row_index, row in enumerate(rows):
            if row_index in used_row_indexes or not isinstance(row, dict):
                continue
            family = _normalized_family(row.get("category") or row.get("category_canonical") or row.get("label"))
            if family not in _EXTRA_INSTANCE_TRACKED_FAMILIES:
                continue
            requested_count = int(item_family_counts.get(family) or 0)
            if requested_count <= 0:
                continue
            bbox_norm = _coerce_bbox_norm(row.get("bbox_norm"))
            if bbox_norm is None:
                continue
            xmin, ymin, xmax, ymax = bbox_norm
            area_norm = max(0.0, float(xmax) - float(xmin)) * max(0.0, float(ymax) - float(ymin))
            if family in {"electronics", "decor", "plant", "stool"}:
                min_area_norm = 0.0025
            elif family in {"floor_lamp", "table_lamp", "ceiling_light", "wall_light"}:
                min_area_norm = 0.004
            elif family in {"rug", "sofa", "lounge_sofa", "lounge_seating", "storage"}:
                min_area_norm = 0.025
            else:
                min_area_norm = 0.01
            if area_norm < min_area_norm:
                continue
            extra_detected_items.append(
                {
                    "family": family,
                    "label": row.get("label"),
                    "bbox_norm": list(bbox_norm),
                    "area_norm": round(area_norm, 4),
                    "requested_count": requested_count,
                }
            )

        if not matched_items:
            issue_records = [
                _build_issue_record(
                    rule_id=_NO_MATCHED_ITEMS_ISSUE,
                    item_key=str(row.get("item_key") or ""),
                    family=str(row.get("family") or ""),
                    item_importance=float(row.get("item_importance") or 1.0),
                    confidence=0.6,
                    stage="scale_validation",
                )
                for row in unmatched_items
            ] or [
                _build_issue_record(
                    rule_id=_NO_MATCHED_ITEMS_ISSUE,
                    item_key=None,
                    family=None,
                    item_importance=1.0,
                    confidence=1.0,
                    stage="scale_validation",
                )
            ]
            return False, [_NO_MATCHED_ITEMS_ISSUE], {
                "failed_rules": [_NO_MATCHED_ITEMS_ISSUE],
                "matched_items": {},
                "rule_details": {},
                "unmatched_items": unmatched_items,
                "issue_records": issue_records,
            }

        primary_key, primary_item, primary_fallback_used = _resolve_primary_match(
            complete_items,
            matched_items,
            primary_label=primary_label,
            primary_target_key=primary_target_key,
            primary_source_index=primary_source_index,
        )

        if primary_item is None or primary_key not in matched_items:
            failed_rules = [_PRIMARY_UNMATCHED_ISSUE]
            issues = [_PRIMARY_UNMATCHED_ISSUE]
            issue_records = [
                _build_issue_record(
                    rule_id=_PRIMARY_UNMATCHED_ISSUE,
                    item_key=None,
                    family=None,
                    item_importance=1.8,
                    confidence=1.0,
                    stage="scale_validation",
                )
            ]
            if unmatched_items:
                failed_rules.append(_UNMATCHED_ITEMS_RULE)
                issues.append(f"{_UNMATCHED_ITEMS_RULE}: {', '.join(item['item_key'] for item in unmatched_items)}")
                for row in unmatched_items:
                    issue_records.append(
                        _build_issue_record(
                            rule_id="unmatched_item",
                            item_key=str(row.get("item_key") or ""),
                            family=str(row.get("family") or ""),
                            item_importance=float(row.get("item_importance") or 1.0),
                            confidence=0.6,
                            stage="scale_validation",
                        )
                    )
            return False, issues, {
                "failed_rules": failed_rules,
                "matched_items": matched_items,
                "rule_details": {},
                "unmatched_items": unmatched_items,
                "issue_records": issue_records,
            }

        matched_items[primary_key]["item_importance"] = _item_importance_score(primary_item, is_primary=True)
        primary_bbox = matched_items[primary_key]["bbox_norm"]
        primary_dims = primary_item.get("dims_mm") or {}
        room_w = float(room_dims.get("width_mm") or 0)
        failed_rules = []
        issues = []
        rule_details = {}
        issue_records = []
        plan_measurements: list[dict] = []

        def _append_issue(rule_id: str, message: str, *, item_key: str | None = None, evidence: dict | None = None):
            if rule_id not in failed_rules:
                failed_rules.append(rule_id)
            issues.append(message)
            matched = matched_items.get(str(item_key or "")) or {}
            item = item_lookup.get(str(item_key or "")) or {}
            family = str(
                matched.get("family")
                or _normalized_family(item.get("category") or item.get("label"))
                or str((evidence or {}).get("family") or "")
            )
            importance = float(matched.get("item_importance") or _item_importance_score(item, is_primary=(str(item_key or "") == str(primary_key))))
            confidence = float(matched.get("match_confidence") or 0.65)
            issue_records.append(
                _build_issue_record(
                    rule_id=rule_id,
                    item_key=str(item_key or "") or None,
                    family=family,
                    item_importance=importance,
                    confidence=confidence,
                    stage="scale_validation",
                    evidence=evidence,
                    match_strategy=matched.get("match_strategy"),
                )
            )

        primary_width_mm = float(primary_dims.get("width_mm") or 0)
        if room_w > 0 and primary_width_mm > 0:
            observed_primary_width = float(primary_bbox[2] - primary_bbox[0])
            expected_primary_width = primary_width_mm / room_w
            tolerance = max(0.08, expected_primary_width * 0.20)
            delta = abs(observed_primary_width - expected_primary_width)
            rule_details["primary_width_vs_room_width"] = {
                "item_key": primary_key,
                "observed": observed_primary_width,
                "expected": expected_primary_width,
                "tolerance": tolerance,
                "delta": delta,
            }
            if delta > tolerance:
                _append_issue(
                    "primary_width_vs_room_width",
                    f"primary_width_vs_room_width: observed={observed_primary_width:.3f} expected={expected_primary_width:.3f}",
                    item_key=primary_key,
                    evidence=rule_details["primary_width_vs_room_width"],
                )

        primary_width_ratio = float(primary_bbox[2] - primary_bbox[0])
        primary_width_mm = max(1e-6, float(primary_dims.get("width_mm") or 0))
        primary_height_ratio = float(primary_bbox[3] - primary_bbox[1])
        primary_height_mm = max(1e-6, float(primary_dims.get("height_mm") or 0))
        plan_room_planes = dict((scale_plan or {}).get("room_planes") or {}) if isinstance(scale_plan, dict) else {}
        wall_span_source = (scale_plan or {}).get("wall_span_norm") if isinstance(scale_plan, dict) else None
        wall_span_width = None
        if isinstance(wall_span_source, (list, tuple)) and len(wall_span_source) == 2:
            try:
                wall_span_width = max(1e-6, float(wall_span_source[1]) - float(wall_span_source[0]))
            except Exception:
                wall_span_width = None
        if wall_span_width is None:
            wall_span_width = 1.0
        plan_y_top = None
        plan_y_bottom = None
        for candidate in (plan_room_planes, room_planes or {}):
            if not isinstance(candidate, dict):
                continue
            try:
                plan_y_top = float(candidate.get("y_top", 0.0))
                plan_y_bottom = float(candidate.get("y_bottom", 1.0))
                if plan_y_bottom > plan_y_top:
                    break
            except Exception:
                plan_y_top = None
                plan_y_bottom = None
        wall_h_norm = None
        if plan_y_top is not None and plan_y_bottom is not None and plan_y_bottom > plan_y_top:
            wall_h_norm = max(1e-6, plan_y_bottom - plan_y_top)
        rule_details["primary_anchor_resolution"] = {
            "item_key": primary_key,
            "fallback_used": bool(primary_fallback_used),
            "requested_label": primary_label,
            "requested_target_key": primary_target_key,
            "requested_source_index": primary_source_index,
        }
        for fallback_index, item in complete_items:
            item_key = _item_identity_key(item, fallback_index)
            matched = matched_items.get(item_key)
            if not matched:
                continue
            bbox_norm = matched.get("bbox_norm") or []
            if len(bbox_norm) != 4:
                continue
            plan_row = _resolve_scale_plan_item(scale_plan_index, item, fallback_index)
            geometry_target = geometry_target_index.get(item_key)
            measurement_rows = build_measurement_specs(
                item_key=item_key,
                bbox_norm=bbox_norm,
                primary_bbox_norm=primary_bbox,
                room_dims=room_dims,
                scale_plan_row=plan_row,
                geometry_target=geometry_target,
                wall_span_norm=wall_span_source,
                room_planes={"y_top": plan_y_top, "y_bottom": plan_y_bottom} if plan_y_top is not None and plan_y_bottom is not None else None,
            )
            for measurement_row in measurement_rows:
                plan_measurements.append(measurement_row)
                if float(measurement_row.get("delta") or 0.0) > float(measurement_row.get("tolerance") or 0.0):
                    _append_issue(
                        str(measurement_row.get("rule_id") or "scale_plan_ratio_violation"),
                        f"{measurement_row.get('rule_id')}: {item_key} observed={float(measurement_row.get('observed') or 0.0):.3f} expected={float(measurement_row.get('expected') or 0.0):.3f}",
                        item_key=item_key,
                        evidence=measurement_row,
                    )
        for item_key, matched in matched_items.items():
            if float(matched.get("match_confidence") or 0.0) < 0.7:
                _append_issue(
                    "low_confidence_match",
                    f"low_confidence_match: {item_key} strategy={matched.get('match_strategy')}",
                    item_key=item_key,
                    evidence={
                        "match_strategy": matched.get("match_strategy"),
                        "match_confidence": matched.get("match_confidence"),
                    },
                )
        if unmatched_items:
            failed_rules.append(_UNMATCHED_ITEMS_RULE)
            issues.append(f"{_UNMATCHED_ITEMS_RULE}: {', '.join(item['item_key'] for item in unmatched_items)}")
            rule_details[_UNMATCHED_ITEMS_RULE] = {
                "items": unmatched_items,
                "expected_measurements": unresolved_measurement_targets(
                    unmatched_items=unmatched_items,
                    geometry_contract=geometry_contract,
                ),
            }
            for row in unmatched_items:
                item_key = str(row.get("item_key") or "")
                family_rule = _UNMATCHED_FAMILY_OVERRIDE_RULES.get(_normalized_family(row.get("category") or row.get("label")))
                issue_records.append(
                    _build_issue_record(
                        rule_id="unmatched_item",
                        item_key=item_key or None,
                        family=str(row.get("family") or ""),
                        item_importance=float(row.get("item_importance") or 1.0),
                        confidence=0.6,
                        stage="scale_validation",
                        evidence={
                            "summary_rule": _UNMATCHED_ITEMS_RULE,
                            "family_override_rule": family_rule,
                        },
                    )
                )
        if extra_detected_items:
            rule_details["extra_detected_items"] = list(extra_detected_items)
            extras_by_family: dict[str, list[dict]] = {}
            for row in extra_detected_items:
                extras_by_family.setdefault(str(row.get("family") or ""), []).append(row)
            for family, rows_for_family in extras_by_family.items():
                _append_issue(
                    "extra_instance_detected",
                    f"extra_instance_detected:{family}",
                    item_key=family,
                    evidence={
                        "family": family,
                        "extra_count": len(rows_for_family),
                        "requested_count": int(item_family_counts.get(family) or 0),
                        "rows": rows_for_family,
                    },
                )
        rug_details = []
        for item_key, matched in matched_items.items():
            if not matched.get("is_rug"):
                continue
            rug_bbox = matched["bbox_norm"]
            rug_dims = matched.get("dims_mm") or {}
            rug_width_mm = float(rug_dims.get("width_mm") or 0)
            if rug_width_mm <= 0:
                continue
            observed_ratio = float(rug_bbox[2] - rug_bbox[0]) / max(1e-6, primary_width_ratio)
            expected_ratio = rug_width_mm / primary_width_mm
            tolerance = max(0.12, expected_ratio * 0.20)
            delta = abs(observed_ratio - expected_ratio)
            rug_details.append(
                {
                    "rug_key": item_key,
                    "anchor_key": primary_key,
                    "observed": observed_ratio,
                    "expected": expected_ratio,
                    "tolerance": tolerance,
                    "delta": delta,
                }
            )
            if delta > tolerance:
                _append_issue(
                    "rug_vs_anchor_footprint",
                    f"rug_vs_anchor_footprint: {item_key} observed={observed_ratio:.3f} expected={expected_ratio:.3f}",
                    item_key=item_key,
                    evidence=rug_details[-1],
                )

        if rug_details:
            rule_details["rug_vs_anchor_footprint"] = rug_details

        tiny_height_details = []
        for item_key, matched in matched_items.items():
            if item_key == primary_key:
                continue
            if matched.get("is_rug"):
                continue
            dims_mm = matched.get("dims_mm") or {}
            item_height_mm = float(dims_mm.get("height_mm") or 0)
            if item_height_mm <= 0 or primary_height_mm <= 0:
                continue
            expected_ratio = item_height_mm / primary_height_mm
            if expected_ratio > 0.25:
                continue
            bbox_norm = matched.get("bbox_norm") or []
            if len(bbox_norm) != 4:
                continue
            observed_ratio = float(bbox_norm[3] - bbox_norm[1]) / max(1e-6, primary_height_ratio)
            tolerance = max(0.08, expected_ratio * 0.35)
            delta = abs(observed_ratio - expected_ratio)
            tiny_height_details.append(
                {
                    "item_key": item_key,
                    "anchor_key": primary_key,
                    "observed": observed_ratio,
                    "expected": expected_ratio,
                    "tolerance": tolerance,
                    "delta": delta,
                }
            )
            if observed_ratio > expected_ratio + tolerance:
                _append_issue(
                    "tiny_item_vs_anchor_height",
                    f"tiny_item_vs_anchor_height: {item_key} observed={observed_ratio:.3f} expected={expected_ratio:.3f}",
                    item_key=item_key,
                    evidence=tiny_height_details[-1],
                )

        if tiny_height_details:
            rule_details["tiny_item_vs_anchor_height"] = tiny_height_details

        relative_height_details = []
        for item_key, matched in matched_items.items():
            if item_key == primary_key:
                continue
            if matched.get("is_rug"):
                continue
            dims_mm = matched.get("dims_mm") or {}
            item_height_mm = float(dims_mm.get("height_mm") or 0)
            if item_height_mm <= 0 or primary_height_mm <= 0:
                continue
            bbox_norm = matched.get("bbox_norm") or []
            if len(bbox_norm) != 4:
                continue
            observed_ratio = float(bbox_norm[3] - bbox_norm[1]) / max(1e-6, primary_height_ratio)
            expected_ratio = item_height_mm / primary_height_mm
            tolerance = max(0.10, expected_ratio * 0.40)
            delta = abs(observed_ratio - expected_ratio)
            relative_height_details.append(
                {
                    "item_key": item_key,
                    "anchor_key": primary_key,
                    "observed": observed_ratio,
                    "expected": expected_ratio,
                    "tolerance": tolerance,
                    "delta": delta,
                }
            )
            if delta > tolerance:
                _append_issue(
                    "relative_height_vs_anchor",
                    f"relative_height_vs_anchor: {item_key} observed={observed_ratio:.3f} expected={expected_ratio:.3f}",
                    item_key=item_key,
                    evidence=relative_height_details[-1],
                )

        if relative_height_details:
            rule_details["relative_height_vs_anchor"] = relative_height_details

        if room_planes:
            try:
                y_top = float(room_planes.get("y_top", 0.0))
                y_bottom = float(room_planes.get("y_bottom", 1.0))
                wall_h_norm = max(1e-6, y_bottom - y_top)
                placement_details = []
                for item_key, matched in matched_items.items():
                    bbox_norm = matched.get("bbox_norm") or []
                    if len(bbox_norm) != 4:
                        continue
                    family = _placement_family(matched)
                    _, ymin, _, ymax = bbox_norm
                    detail = {
                        "item_key": item_key,
                        "family": family,
                        "ymin": float(ymin),
                        "ymax": float(ymax),
                        "floor_line": float(y_bottom),
                    }
                    if family == "wall_attached":
                        max_bottom = y_bottom - max(0.04, wall_h_norm * 0.10)
                        detail["max_bottom"] = float(max_bottom)
                        if float(ymax) > max_bottom:
                            _append_issue("wall_attached_floor_collision", f"wall_attached_floor_collision: {item_key}", item_key=item_key, evidence=detail)
                        placement_details.append(detail)
                    elif family == "ceiling_attached":
                        max_top = y_top + max(0.18, wall_h_norm * 0.22)
                        max_bottom = y_top + max(0.46, wall_h_norm * 0.55)
                        detail["max_top"] = float(max_top)
                        detail["max_bottom"] = float(max_bottom)
                        if float(ymin) > max_top or float(ymax) > max_bottom:
                            _append_issue(
                                "ceiling_attached_height_violation",
                                f"ceiling_attached_height_violation: {item_key}",
                                item_key=item_key,
                                evidence=detail,
                            )
                        placement_details.append(detail)
                    elif family == "rug":
                        min_bottom = y_bottom - max(0.16, wall_h_norm * 0.20)
                        detail["min_bottom"] = float(min_bottom)
                        if float(ymax) < min_bottom:
                            _append_issue("rug_floating_above_floor_zone", f"rug_floating_above_floor_zone: {item_key}", item_key=item_key, evidence=detail)
                        placement_details.append(detail)
                    elif family == "floor_placed":
                        min_bottom = y_top + wall_h_norm * 0.40
                        detail["min_bottom"] = float(min_bottom)
                        if float(ymax) < min_bottom:
                            _append_issue("floor_item_floating", f"floor_item_floating: {item_key}", item_key=item_key, evidence=detail)
                        placement_details.append(detail)
                    elif family == "surface_placed":
                        placement_details.append(detail)
                if placement_details:
                    rule_details["placement_family_checks"] = placement_details
            except Exception:
                _append_issue(_VALIDATION_EXCEPTION_ISSUE, _VALIDATION_EXCEPTION_ISSUE, evidence={"stage": "placement_family_checks"})

        diagnostics = {
            "failed_rules": failed_rules,
            "matched_items": matched_items,
            "rule_details": rule_details,
            "room_dims_mm": room_dims,
            "room_planes": room_planes,
            "unmatched_items": unmatched_items,
            "issue_records": issue_records,
            "scale_plan_measurements": plan_measurements,
            "ratio_qc_summary": summarize_measurements(plan_measurements),
        }
        return (not failed_rules, issues, diagnostics)
    except Exception as exc:
        return False, [_VALIDATION_EXCEPTION_ISSUE], {
            "failed_rules": [_VALIDATION_EXCEPTION_ISSUE],
            "matched_items": {},
            "rule_details": {},
            "unmatched_items": [],
            "issue_records": [
                _build_issue_record(
                    rule_id=_VALIDATION_EXCEPTION_ISSUE,
                    item_key=None,
                    family=None,
                    item_importance=1.0,
                    confidence=1.0,
                    stage="scale_validation",
                    evidence={"exception": str(exc)},
                )
            ],
            "exception": str(exc),
        }


def reorder_by_scale_best_pick(
    result_urls: list,
    ref_path: str,
    primary: dict,
    room_dims: dict,
    wall_span_norm: tuple,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
) -> list:
    try:
        room_w = int(room_dims.get("width_mm") or 0)
        primary_w = int((primary.get("dims_mm") or {}).get("width_mm") or 0)
        if room_w <= 0 or primary_w <= 0:
            return result_urls
        target_ratio = primary_w / room_w

        ref_crop = None
        try:
            out_crop = os.path.join("outputs", f"ref_primary_{uuid.uuid4().hex[:8]}.png")
            ref_crop = crop_ref_item_image(ref_path, primary.get("box_2d"), out_crop) if primary.get("box_2d") else None
        except Exception:
            ref_crop = None

        scored = []
        for index, url in enumerate(result_urls or []):
            local = os.path.join("outputs", os.path.basename(url))
            bbox = detect_primary_bbox_norm(
                local,
                ref_crop,
                primary.get("label"),
                call_gemini_with_failover=call_gemini_with_failover,
                analysis_model_name=analysis_model_name,
                safe_json_from_model_text=safe_json_from_model_text,
            )
            if bbox is None:
                scored.append((0.0, index, url))
                continue
            scored.append((score_scale(bbox, wall_span_norm, target_ratio), index, url))

        scored.sort(key=lambda row: (row[0], -row[1]), reverse=True)
        return [url for _, _, url in scored]
    except Exception:
        return result_urls


def validate_furnished_scale(
    staged_path: str,
    furniture_specs_json: dict,
    room_dims: dict,
    room_planes: Optional[dict],
    primary_label: Optional[str] = None,
    include_diagnostics: bool = False,
    scale_plan: Optional[dict] = None,
    geometry_contract: Optional[dict] = None,
    focus_item_keys: Optional[list[str]] = None,
    skip_reference_review: bool = False,
    *,
    detect_furniture_boxes: Optional[Callable[..., list]] = None,
    remap_model_name: Optional[str] = None,
    remap_detect_timeout_sec: int = 60,
    remap_detect_retry: int = 1,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
    log_brief: bool,
    logger,
    absolute_deadline_ts: float | None = None,
):
    try:
        def _return(ok: bool, issues: list[str], diagnostics: Optional[dict] = None):
            if include_diagnostics:
                return ok, issues, diagnostics or {}
            return ok, issues

        def _remaining_deadline_sec() -> float | None:
            if absolute_deadline_ts is None:
                return None
            try:
                return max(0.0, float(absolute_deadline_ts) - float(time.time()))
            except Exception:
                return 0.0

        def _bounded_timeout(requested_sec: float, *, minimum_sec: float) -> float | None:
            remaining = _remaining_deadline_sec()
            if remaining is None:
                return float(requested_sec)
            timeout_sec = min(float(requested_sec), max(0.0, remaining))
            if timeout_sec <= minimum_sec:
                return None
            return max(float(minimum_sec), float(timeout_sec))

        strict_contract_candidates = [
            contract
            for contract in (geometry_contract, scale_plan)
            if isinstance(contract, dict) and contract.get("strict_scale_requested")
        ]
        strict_runtime = bool(strict_contract_candidates)
        strict_contract = next(
            (contract for contract in strict_contract_candidates if not contract.get("strict_scale_ready")),
            strict_contract_candidates[0] if strict_contract_candidates else None,
        )
        if isinstance(strict_contract, dict) and strict_contract.get("strict_scale_requested") and not strict_contract.get("strict_scale_ready"):
            missing_requirements = list(strict_contract.get("missing_requirements") or [])
            diagnostics = {
                "failed_rules": ["strict_scale_contract_not_ready"],
                "matched_items": {},
                "rule_details": {"missing_requirements": missing_requirements},
                "unmatched_items": [],
                "detected_rows": [],
                "issue_records": [
                    _build_issue_record(
                        rule_id="strict_scale_contract_not_ready",
                        item_key=None,
                        family=None,
                        item_importance=1.5,
                        confidence=1.0,
                        stage="scale_validation",
                        evidence={"missing_requirements": missing_requirements},
                    )
                ],
                "scale_plan_measurements": [],
                "ratio_qc_summary": summarize_measurements([]),
            }
            return _return(False, ["strict_scale_contract_not_ready"], diagnostics)

        if not furniture_specs_json or not isinstance(furniture_specs_json, dict):
            return _return(True, [], {})
        items = furniture_specs_json.get("items") or []
        if not items:
            return _return(True, [], {})

        complete_items = []
        for item_index, item in _normalize_item_entries(items):
            dims = item.get("dims_mm") or {}
            width = int(dims.get("width_mm") or 0)
            depth = int(dims.get("depth_mm") or 0)
            height = int(dims.get("height_mm") or 0)
            if width > 0 and depth > 0 and height > 0:
                complete_items.append((item_index, item))

        if items and len(complete_items) != len(items):
            return _return(
                False,
                [_INCOMPLETE_ITEMS_ISSUE],
                {
                    "failed_rules": [_INCOMPLETE_ITEMS_ISSUE],
                    "matched_items": {},
                    "rule_details": {},
                    "unmatched_items": [],
                    "detected_rows": [],
                },
            )

        if not complete_items:
            return _return(True, [], {"failed_rules": [], "matched_items": {}, "rule_details": {}, "unmatched_items": [], "detected_rows": []})

        room_h = int((room_dims or {}).get("height_mm") or 0)
        wall_h_norm = None
        if room_planes:
            try:
                y_top = float(room_planes.get("y_top", 0.0))
                y_bottom = float(room_planes.get("y_bottom", 1.0))
                y_top = max(0.0, min(1.0, y_top))
                y_bottom = max(0.0, min(1.0, y_bottom))
                wall_h_norm = max(1e-6, (y_bottom - y_top))
            except Exception:
                wall_h_norm = None

        primary_info = (
            furniture_specs_json.get("primary_scale")
            or furniture_specs_json.get("primary")
            or {}
        )
        if not primary_label:
            primary_label = primary_info.get("label")
        primary_target_key = primary_info.get("target_key")
        primary_source_index = primary_info.get("source_index")
        if not any(selector not in (None, "") for selector in (primary_label, primary_target_key, primary_source_index)):
            for _, item in complete_items:
                primary_label = item.get("label") or ""
                if primary_target_key in (None, ""):
                    primary_target_key = item.get("target_key")
                if primary_source_index in (None, ""):
                    primary_source_index = item.get("source_index")
                break

        def _looks_like_openai_analysis_model(model_name: Optional[str]) -> bool:
            normalized = str(model_name or "").strip().lower()
            return normalized.startswith(("gpt-", "o1", "o3", "o4"))

        def _fast_detection_model_name(requested_model_name: Optional[str]) -> str:
            normalized = str(requested_model_name or "").strip()
            if normalized and (not strict_runtime or not _looks_like_openai_analysis_model(normalized)):
                return normalized
            if strict_runtime:
                return GEMINI_ANALYSIS_DEFAULT
            return normalized or str(analysis_model_name or "").strip() or GEMINI_ANALYSIS_DEFAULT

        detection_model_name = _fast_detection_model_name(remap_model_name or analysis_model_name)

        def _pass_role(item: dict) -> str:
            return str(((item.get("two_pass_strategy") or {}).get("pass_role") or item.get("pass_role") or "")).strip().lower()

        def _structural_archetype(item: dict) -> str:
            return str(((item.get("archetype_strategy") or {}).get("structural_archetype") or "")).strip().lower()

        def _family(item: dict) -> str:
            return str(
                ((item.get("identity_profile") or {}).get("family"))
                or item.get("category_canonical")
                or item.get("category")
                or item.get("label")
                or ""
            ).strip().lower()

        def _matches_primary_identity(item: dict, fallback_index: int) -> bool:
            item_key = _item_identity_key(item, fallback_index)
            if primary_target_key not in (None, "") and str(item.get("target_key") or "") == str(primary_target_key):
                return True
            if primary_source_index not in (None, "") and str(item.get("source_index") or "") == str(primary_source_index):
                return True
            if primary_label and str(item.get("label") or "") == str(primary_label):
                return True
            return bool(item_key and primary_target_key and item_key == str(primary_target_key))

        def _is_geometry_shortlist_item(item: dict, fallback_index: int) -> bool:
            if _matches_primary_identity(item, fallback_index):
                return True
            pass_role = _pass_role(item)
            if pass_role in {"pass1_anchor", "pass1_footprint", "pass2_small"}:
                return True
            family = _family(item)
            if family == "rug":
                return True
            archetype = _structural_archetype(item)
            return archetype in {"thin_floor_footprint_object", "tiny_absolute_scale_object"}

        def _should_short_circuit_geometry_review(
            diagnostics: dict | None,
            *,
            has_raw_batch_rows: bool,
            has_geometry_matches: bool,
        ) -> bool:
            failed_rules = {str(rule or "").strip() for rule in (diagnostics or {}).get("failed_rules") or [] if str(rule or "").strip()}
            if "strict_scale_contract_not_ready" in failed_rules:
                return True
            if failed_rules.intersection({"primary_width_vs_room_width", "rug_vs_anchor_footprint", "tiny_item_vs_anchor_height"}):
                return True
            if failed_rules.intersection({"primary_anchor_unmatched", "no_matched_items"}):
                return not (has_raw_batch_rows or has_geometry_matches)
            ratio_summary = (diagnostics or {}).get("ratio_qc_summary") or {}
            critical_ratio_fail_count = int(ratio_summary.get("critical_ratio_fail_count") or 0)
            if critical_ratio_fail_count > 0:
                return has_geometry_matches or not has_raw_batch_rows
            return False

        def _should_run_reference_review(item: dict, fallback_index: int) -> bool:
            if not _should_review_reference_fidelity(item):
                return False
            identity_richness = _identity_richness_score(item)
            archetype = _structural_archetype(item)
            pass_role = _pass_role(item)
            if _has_weak_reference_identity(item):
                return True
            if str(((item.get("archetype_strategy") or {}).get("strictness") or "")).strip().lower() == "critical":
                return True
            if archetype in {
                "support_geometry_sensitive_object",
                "reflective_wall_object",
                "tiny_absolute_scale_object",
                "thin_floor_footprint_object",
                "topology_sensitive_seating",
            }:
                return True
            if _matches_primary_identity(item, fallback_index) and identity_richness >= 1.2:
                return True
            if pass_role in {"pass1_anchor", "pass1_footprint"} and identity_richness >= 1.2:
                return True
            if pass_role.startswith("pass2") and identity_richness >= 1.5 and _item_importance_score(item) >= 1.25:
                return True
            return False

        def _needs_critical_fallback_detect(item: dict, fallback_index: int) -> bool:
            item_key = _item_identity_key(item, fallback_index)
            if item_key in detected_item_keys:
                return False
            if focus_keys and item_key in focus_keys:
                return True
            if _matches_primary_identity(item, fallback_index):
                return True
            if _is_geometry_shortlist_item(item, fallback_index):
                return True
            if _should_run_reference_review(item, fallback_index):
                return True
            archetype = _structural_archetype(item)
            return archetype in {
                "support_geometry_sensitive_object",
                "reflective_wall_object",
                "tiny_absolute_scale_object",
                "thin_floor_footprint_object",
            }

        focus_keys = {str(value or "").strip() for value in (focus_item_keys or []) if str(value or "").strip()}
        scoped_items = [
            (fallback_index, item)
            for fallback_index, item in complete_items
            if (
                not focus_keys
                or _matches_primary_identity(item, fallback_index)
                or _is_geometry_shortlist_item(item, fallback_index)
                or _item_identity_key(item, fallback_index) in focus_keys
            )
        ]
        if not scoped_items:
            scoped_items = list(complete_items)

        geometry_shortlist = [(idx, item) for idx, item in scoped_items if _is_geometry_shortlist_item(item, idx)]
        if not geometry_shortlist:
            geometry_shortlist = list(scoped_items)

        scoped_only_items = [item for _, item in scoped_items]
        batch_matches = []
        batch_detected_rows = []
        batch_validation_rows = []
        batch_detected_item_keys = set()
        batch_detect_succeeded = False
        batch_timeout_sec = _bounded_timeout(remap_detect_timeout_sec, minimum_sec=3.0)
        if detect_furniture_boxes and staged_path and os.path.exists(staged_path) and batch_timeout_sec is not None:
            raw_batch_rows = detect_rows_from_render(
                staged_path,
                detect_furniture_boxes=detect_furniture_boxes,
                model_name=detection_model_name,
                timeout_sec=batch_timeout_sec,
                retry=remap_detect_retry,
                max_attempts=1,
                canonical_category=canonical_category,
                category_match_family=category_match_family,
            )
            batch_validation_rows = list(raw_batch_rows or [])
            if raw_batch_rows:
                batch_matches = match_items_to_detected_rows(
                    scoped_only_items,
                    raw_batch_rows,
                    remap_match_score=remap_match_score,
                    category_match_family=category_match_family,
                    canonical_category=canonical_category,
                    sensitive_remap_families=_SENSITIVE_REMAP_FAMILIES,
                )
                batch_detected_rows = build_detection_rows_from_matches(batch_matches)
                batch_detect_succeeded = bool(batch_validation_rows)
                for (fallback_index, item), match in zip(scoped_items, batch_matches):
                    if (match or {}).get("picked_row"):
                        batch_detected_item_keys.add(_item_identity_key(item, fallback_index))

        geometry_detected_rows = _merge_detected_rows(batch_detected_rows, batch_validation_rows)
        detected_item_keys = set(batch_detected_item_keys)
        if not geometry_detected_rows:
            for fallback_index, item in geometry_shortlist:
                label = item.get("label") or "Item"
                bbox_timeout_sec = _bounded_timeout(min(20.0, float(remap_detect_timeout_sec or 20.0)), minimum_sec=3.0)
                if bbox_timeout_sec is None:
                    continue
                bbox = detect_item_bbox_norm(
                    staged_path,
                    item.get("crop_path"),
                    label,
                    item_context=_build_detection_item_context(item),
                    call_gemini_with_failover=call_gemini_with_failover,
                    analysis_model_name=detection_model_name,
                    safe_json_from_model_text=safe_json_from_model_text,
                    timeout_sec=bbox_timeout_sec,
                )
                if bbox:
                    geometry_detected_rows.append(
                        {
                            "label": label,
                            "target_key": item.get("target_key"),
                            "source_index": item.get("source_index"),
                            "bbox_norm": bbox,
                        }
                    )
                    detected_item_keys.add(_item_identity_key(item, fallback_index))

        geometry_ok, geometry_issues, geometry_diagnostics = validate_scale_from_detection_map(
            [item for _, item in geometry_shortlist],
            room_dims or {},
            room_planes=room_planes,
            scale_plan=scale_plan,
            geometry_contract=geometry_contract,
            detected_rows=geometry_detected_rows,
            primary_label=primary_label,
            primary_target_key=primary_target_key,
            primary_source_index=primary_source_index,
        )
        geometry_has_matches = bool((geometry_diagnostics or {}).get("matched_items"))
        if not geometry_ok and _should_short_circuit_geometry_review(
            geometry_diagnostics,
            has_raw_batch_rows=bool(batch_validation_rows),
            has_geometry_matches=geometry_has_matches,
        ):
            geometry_diagnostics = dict(geometry_diagnostics or {})
            geometry_diagnostics["detected_rows"] = list(geometry_detected_rows or [])
            geometry_diagnostics["cheap_first_short_circuit"] = True
            geometry_diagnostics["cheap_first_item_keys"] = [
                _item_identity_key(item, fallback_index)
                for fallback_index, item in geometry_shortlist
            ]
            if batch_validation_rows:
                geometry_diagnostics["batch_detect_used"] = True
                geometry_diagnostics["batch_detect_row_count"] = len(batch_validation_rows)
                geometry_diagnostics["batch_detect_matched_row_count"] = len(batch_detected_rows)
            if focus_keys:
                geometry_diagnostics["focus_item_keys"] = sorted(focus_keys)
            return _return(False, list(geometry_issues or []), geometry_diagnostics)

        critical_fallback_candidates = []
        critical_fallback_keys: set[str] = set()
        for fallback_index, item in scoped_items:
            item_key = _item_identity_key(item, fallback_index)
            if item_key in critical_fallback_keys:
                continue
            if (
                item_key in focus_keys
                or _matches_primary_identity(item, fallback_index)
                or _is_geometry_shortlist_item(item, fallback_index)
                or _needs_critical_fallback_detect(item, fallback_index)
            ):
                critical_fallback_candidates.append((fallback_index, item))
                critical_fallback_keys.add(item_key)
        if len(critical_fallback_candidates) < 3:
            remaining_candidates = []
            for fallback_index, item in scoped_items:
                item_key = _item_identity_key(item, fallback_index)
                if item_key in critical_fallback_keys:
                    continue
                dims = item.get("dims_mm") or {}
                try:
                    width_mm = float(dims.get("width_mm") or 0.0)
                except Exception:
                    width_mm = 0.0
                try:
                    depth_mm = float(dims.get("depth_mm") or 0.0)
                except Exception:
                    depth_mm = 0.0
                try:
                    height_mm = float(dims.get("height_mm") or 0.0)
                except Exception:
                    height_mm = 0.0
                fallback_size_score = max(width_mm * max(1.0, depth_mm), height_mm)
                remaining_candidates.append((fallback_size_score, width_mm, height_mm, fallback_index, item))
            remaining_candidates.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))
            for _, _, _, fallback_index, item in remaining_candidates:
                critical_fallback_candidates.append((fallback_index, item))
                if len(critical_fallback_candidates) >= 3:
                    break
        if not critical_fallback_candidates:
            critical_fallback_candidates = list(geometry_shortlist or scoped_items)

        detected_rows = list(geometry_detected_rows)
        if not batch_detect_succeeded:
            fallback_candidates = list(critical_fallback_candidates)
        else:
            fallback_candidates = [
                (fallback_index, item)
                for fallback_index, item in scoped_items
                if _needs_critical_fallback_detect(item, fallback_index)
            ]
        for fallback_index, item in fallback_candidates:
            item_key = _item_identity_key(item, fallback_index)
            if item_key in detected_item_keys:
                continue
            label = item.get("label") or "Item"
            bbox_timeout_sec = _bounded_timeout(min(20.0, float(remap_detect_timeout_sec or 20.0)), minimum_sec=3.0)
            if bbox_timeout_sec is None:
                continue
            bbox = detect_item_bbox_norm(
                staged_path,
                item.get("crop_path"),
                label,
                item_context=_build_detection_item_context(item),
                call_gemini_with_failover=call_gemini_with_failover,
                analysis_model_name=detection_model_name,
                safe_json_from_model_text=safe_json_from_model_text,
                timeout_sec=bbox_timeout_sec,
            )
            if bbox:
                detected_rows.append(
                    {
                        "label": label,
                        "target_key": item.get("target_key"),
                        "source_index": item.get("source_index"),
                        "bbox_norm": bbox,
                    }
                )
                detected_item_keys.add(item_key)

        scale_ok, scale_issues, diagnostics = validate_scale_from_detection_map(
            [item for _, item in scoped_items],
            room_dims or {},
            room_planes=room_planes,
            scale_plan=scale_plan,
            geometry_contract=geometry_contract,
            detected_rows=detected_rows,
            primary_label=primary_label,
            primary_target_key=primary_target_key,
            primary_source_index=primary_source_index,
        )
        diagnostics = dict(diagnostics or {})
        diagnostics["detected_rows"] = list(detected_rows or [])
        if batch_validation_rows:
            diagnostics["batch_detect_used"] = True
            diagnostics["batch_detect_row_count"] = len(batch_validation_rows)
            diagnostics["batch_detect_matched_row_count"] = len(batch_detected_rows)

        issues = list(scale_issues)
        issue_records = list(diagnostics.get("issue_records") or [])

        matched_items = diagnostics.get("matched_items") or {}
        primary_key, _, _ = _resolve_primary_match(
            complete_items,
            matched_items,
            primary_label=primary_label,
            primary_target_key=primary_target_key,
            primary_source_index=primary_source_index,
        )

        primary_match = matched_items.get(primary_key) if primary_key else None
        primary_bbox = (primary_match or {}).get("bbox_norm")
        primary_dims = (primary_match or {}).get("dims_mm") or {}
        primary_h_mm = float(primary_dims.get("height_mm") or 0)
        if not primary_bbox or primary_h_mm <= 0:
            failed = list(dict.fromkeys(issues + [_PRIMARY_UNMATCHED_ISSUE]))
            diagnostics["failed_rules"] = list(dict.fromkeys((diagnostics.get("failed_rules") or []) + [_PRIMARY_UNMATCHED_ISSUE]))
            return _return(False, failed, diagnostics)

        primary_h_px = max(1e-6, (primary_bbox[3] - primary_bbox[1]))

        def _append_review_issue(rule_id: str, message: str, *, item_key: str | None = None, evidence: dict | None = None):
            issues.append(message)
            matched = matched_items.get(str(item_key or "")) or {}
            family = str(matched.get("family") or "")
            item_importance = float(matched.get("item_importance") or 1.0)
            confidence = float(matched.get("match_confidence") or 0.65)
            issue_records.append(
                _build_issue_record(
                    rule_id=rule_id,
                    item_key=str(item_key or "") or None,
                    family=family,
                    item_importance=item_importance,
                    confidence=confidence,
                    stage="reference_review",
                    evidence=evidence,
                    match_strategy=matched.get("match_strategy"),
                )
            )

        for fallback_index, item in scoped_items:
            label = item.get("label") or "Item"
            item_key = _item_identity_key(item, fallback_index)
            if item_key == primary_key:
                continue
            bbox = (matched_items.get(item_key) or {}).get("bbox_norm")
            if not bbox:
                continue
            dims = item.get("dims_mm") or {}
            height_mm = float(dims.get("height_mm") or 0)
            if height_mm <= 0:
                continue

            _, ymin, _, ymax = bbox
            h_px = max(1e-6, (ymax - ymin))
            observed_rel = h_px / primary_h_px
            expected_rel = height_mm / primary_h_mm
            if not log_brief:
                logger.info("[ScaleCheck] %s rel_obs=%.3f rel_exp=%.3f", label, observed_rel, expected_rel)
            rel_thresh = expected_rel + max(0.02, expected_rel * 0.05)
            if observed_rel > rel_thresh:
                _append_review_issue(
                    "relative_height_vs_anchor",
                    f"{item_key} taller than expected vs primary",
                    item_key=item_key,
                    evidence={"observed_rel": observed_rel, "expected_rel": expected_rel, "threshold": rel_thresh},
                )

            if room_h > 0 and wall_h_norm:
                observed_room = h_px / wall_h_norm
                expected_room = height_mm / room_h
                if not log_brief:
                    logger.info("[ScaleCheck] %s room_obs=%.3f room_exp=%.3f", label, observed_room, expected_room)
                room_thresh = expected_room + max(0.02, expected_room * 0.05)
                if observed_room > room_thresh:
                    _append_review_issue(
                        "relative_height_vs_anchor",
                        f"{item_key} exceeds expected room height ratio",
                        item_key=item_key,
                        evidence={"observed_room": observed_room, "expected_room": expected_room, "threshold": room_thresh},
                    )

        if skip_reference_review:
            if issues:
                diagnostics["failed_rules"] = list(dict.fromkeys((diagnostics.get("failed_rules") or []) + _coerce_failed_rule_ids(issues)))
                diagnostics["issue_records"] = issue_records
                if focus_keys:
                    diagnostics["focus_item_keys"] = sorted(focus_keys)
                diagnostics["reference_review_skipped"] = True
                return _return(False, issues, diagnostics)
            diagnostics["issue_records"] = issue_records
            if focus_keys:
                diagnostics["focus_item_keys"] = sorted(focus_keys)
            diagnostics["reference_review_skipped"] = True
            return _return(True, [], diagnostics) if scale_ok else _return(False, scale_issues, diagnostics)

        reference_review_candidates = []
        for fallback_index, item in scoped_items:
            item_key = _item_identity_key(item, fallback_index)
            bbox = (matched_items.get(item_key) or {}).get("bbox_norm")
            if not bbox:
                continue
            if not _should_run_reference_review(item, fallback_index):
                continue
            primary_priority = 1 if _matches_primary_identity(item, fallback_index) else 0
            weak_reference_priority = 1 if _has_weak_reference_identity(item) else 0
            reference_review_candidates.append(
                (
                    primary_priority,
                    weak_reference_priority,
                    _item_importance_score(item, is_primary=_matches_primary_identity(item, fallback_index)),
                    fallback_index,
                    item_key,
                    item,
                    bbox,
                )
            )
        reference_review_candidates.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3]))
        reference_review_performed_keys: list[str] = []
        reference_review_skipped_item_keys: list[str] = []
        for candidate_index, (_, _, _, fallback_index, item_key, item, bbox) in enumerate(reference_review_candidates):
            if candidate_index >= 7:
                reference_review_skipped_item_keys.append(item_key)
                continue
            fidelity_timeout_sec = _bounded_timeout(25.0, minimum_sec=4.0)
            if fidelity_timeout_sec is None:
                reference_review_skipped_item_keys.extend(
                    [
                        candidate_item_key
                        for _, _, _, _, candidate_item_key, _, _ in reference_review_candidates[candidate_index:]
                    ]
                )
                break
            reference_review_performed_keys.append(item_key)
            fidelity_issues = _review_reference_fidelity(
                staged_path,
                item,
                bbox,
                call_gemini_with_failover=call_gemini_with_failover,
                analysis_model_name=analysis_model_name,
                safe_json_from_model_text=safe_json_from_model_text,
                timeout_sec=fidelity_timeout_sec,
            )
            for raw_issue in fidelity_issues:
                parsed_rule_ids = _coerce_failed_rule_ids([raw_issue])
                rule_id = parsed_rule_ids[0] if parsed_rule_ids else "reference_shape_drift"
                _append_review_issue(rule_id, raw_issue, item_key=item_key, evidence={"raw_issue": raw_issue})
        if reference_review_performed_keys:
            diagnostics["reference_review_performed_keys"] = list(reference_review_performed_keys)
        if reference_review_skipped_item_keys:
            diagnostics["reference_review_skipped_item_keys"] = list(reference_review_skipped_item_keys)

        if issues:
            diagnostics["failed_rules"] = list(dict.fromkeys((diagnostics.get("failed_rules") or []) + _coerce_failed_rule_ids(issues)))
            diagnostics["issue_records"] = issue_records
            if focus_keys:
                diagnostics["focus_item_keys"] = sorted(focus_keys)
            return _return(False, issues, diagnostics)
        diagnostics["issue_records"] = issue_records
        if focus_keys:
            diagnostics["focus_item_keys"] = sorted(focus_keys)
        return _return(True, [], diagnostics) if scale_ok else _return(False, scale_issues, diagnostics)
    except Exception:
        return _return(
            False,
            [_VALIDATION_EXCEPTION_ISSUE],
            {
                "failed_rules": [_VALIDATION_EXCEPTION_ISSUE],
                "matched_items": {},
                "rule_details": {},
                "unmatched_items": [],
                "detected_rows": [],
                "issue_records": [
                    _build_issue_record(
                        rule_id=_VALIDATION_EXCEPTION_ISSUE,
                        item_key=None,
                        family=None,
                        item_importance=1.0,
                        confidence=1.0,
                        stage="reference_review",
                    )
                ],
            },
        )
