import os
import re
import time
from typing import Any, Callable

from application.render.postprocess_support import category_match_family

from PIL import Image


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
    "glassy",
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
    "wall-mounted",
    "pedestal",
    "spindle",
    "flaring",
    "blocky",
    "slim",
    "thin",
)

_SEATING_FAMILIES = {"sofa", "lounge_sofa", "lounge_seating", "chair", "lounge_chair", "armchair", "loveseat"}
_SUPPORT_GEOMETRY_FAMILIES = {"table", "desk", "stool", "storage"}
_REFLECTIVE_FAMILIES = {"mirror"}
_RUG_FAMILIES = {"rug"}
_LIGHT_FAMILIES = {"floor_lamp", "table_lamp", "light", "ceiling_light", "wall_light"}
_HIGH_PRIORITY_REFERENCE_REASONS = {
    "reflective_wall_object",
    "support_geometry_object",
    "topology_sensitive_seating",
    "large_light_anchor_candidate",
    "light_fixture_identity_object",
    "decor_reference_identity_object",
}
_MEDIUM_PRIORITY_REFERENCE_REASONS = {
    "thin_floor_footprint_object",
    "tiny_absolute_scale_object",
    "general_reference_identity_object",
}

_GENERIC_FEATURE_TOKENS = {
    "art",
    "chair",
    "decor",
    "desk",
    "furniture",
    "item",
    "lamp",
    "light",
    "object",
    "rug",
    "sofa",
    "table",
}


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _ultra_detailed_item_analysis_enabled() -> bool:
    return _env_truthy("AI_RENDER_ULTRA_DETAILED_ITEM_ANALYSIS")


def _extract_keyword_cues(text: str, keywords: tuple[str, ...], limit: int = 4) -> list[str]:
    normalized = str(text or "").lower()
    cues: list[str] = []
    for keyword in keywords:
        if keyword in normalized and keyword not in cues:
            cues.append(keyword)
        if len(cues) >= limit:
            break
    return cues


def _coerce_str_list(value: Any, *, limit: int | None = None) -> list[str]:
    if limit is None:
        limit = 16 if _ultra_detailed_item_analysis_enabled() else 6
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for raw in value:
        text = str(raw or "").strip()
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _coerce_bool(value: Any, fallback: bool = False) -> bool:
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
    return fallback


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _normalize_dims_mm(dims_mm: dict | None) -> dict[str, int | None]:
    dims_mm = dims_mm if isinstance(dims_mm, dict) else {}
    return {
        "width_mm": _coerce_positive_int(dims_mm.get("width_mm")),
        "depth_mm": _coerce_positive_int(dims_mm.get("depth_mm")),
        "height_mm": _coerce_positive_int(dims_mm.get("height_mm")),
        "radius_mm": _coerce_positive_int(dims_mm.get("radius_mm")),
    }


def _append_unique(target: list[str], *values: str, limit: int = 8) -> None:
    for value in values:
        text = str(value or "").strip()
        if not text or text in target:
            continue
        target.append(text)
        if len(target) >= limit:
            return


def should_extract_reference_features(
    *,
    label: str,
    category: str | None,
    category_canonical: str | None,
    dims_mm: dict | None,
) -> tuple[bool, str]:
    family = category_match_family(category or category_canonical or label)
    dims = _normalize_dims_mm(dims_mm)
    width_mm = dims.get("width_mm") or 0
    depth_mm = dims.get("depth_mm") or 0
    height_mm = dims.get("height_mm") or 0
    radius_mm = dims.get("radius_mm") or 0
    max_dim = max(width_mm, depth_mm, height_mm, radius_mm)

    if family in _REFLECTIVE_FAMILIES:
        return True, "reflective_wall_object"
    if family in _LIGHT_FAMILIES:
        return True, "light_fixture_identity_object"
    if family in _SEATING_FAMILIES:
        return True, "topology_sensitive_seating"
    if family in {"decor", "plant"}:
        return True, "decor_reference_identity_object"
    if family in _RUG_FAMILIES or (height_mm > 0 and height_mm <= 40 and max(width_mm, depth_mm) >= 500):
        return True, "thin_floor_footprint_object"
    if max_dim > 0 and max_dim <= 250:
        return True, "tiny_absolute_scale_object"
    if family in _SUPPORT_GEOMETRY_FAMILIES:
        return True, "support_geometry_object"
    if max(width_mm, depth_mm) >= 1400:
        return True, "large_anchor_candidate"
    return True, "general_reference_identity_object"


def _remaining_deadline_sec(absolute_deadline_ts: float | None) -> float | None:
    if absolute_deadline_ts is None:
        return None
    try:
        return max(0.0, float(absolute_deadline_ts) - float(time.time()))
    except Exception:
        return 0.0


def _should_use_reference_feature_model(*, extraction_reason: str, absolute_deadline_ts: float | None) -> bool:
    reason = str(extraction_reason or "").strip().lower()
    if not reason:
        return True
    if reason == "fallback_only":
        return False

    remaining = _remaining_deadline_sec(absolute_deadline_ts)
    if remaining is None:
        return True
    if _ultra_detailed_item_analysis_enabled():
        return remaining >= 25.0

    if reason in _HIGH_PRIORITY_REFERENCE_REASONS:
        return remaining >= 35.0
    if reason in _MEDIUM_PRIORITY_REFERENCE_REASONS:
        return remaining >= 90.0
    return remaining >= 150.0


def _bounded_timeout(requested_sec: float, *, absolute_deadline_ts: float | None, minimum_sec: float = 8.0) -> float | None:
    remaining = _remaining_deadline_sec(absolute_deadline_ts)
    if remaining is None:
        return float(requested_sec)
    timeout_sec = min(float(requested_sec), max(0.0, remaining))
    if timeout_sec <= minimum_sec:
        return None
    return max(float(minimum_sec), timeout_sec)


def _normalize_crop_for_prompt(crop_path: str) -> Image.Image:
    with Image.open(crop_path) as img:
        normalized = img.convert("RGB")
        max_edge = max(normalized.size)
        if max_edge > 1024:
            scale = 1024 / max_edge
            normalized = normalized.resize(
                (max(1, int(normalized.size[0] * scale)), max(1, int(normalized.size[1] * scale))),
                Image.Resampling.LANCZOS,
            )
        return normalized.copy()


def _fallback_reference_features(
    *,
    label: str,
    category: str | None,
    description: str,
    dims_mm: dict | None = None,
) -> dict:
    text_blob = " ".join([str(label or ""), str(category or ""), str(description or "")]).strip()
    material_cues = _extract_keyword_cues(text_blob, _MATERIAL_CUE_KEYWORDS)
    silhouette_cues = _extract_keyword_cues(text_blob, _SHAPE_CUE_KEYWORDS)
    preserve_rules: list[str] = []
    family = category_match_family(category or label)
    dims = _normalize_dims_mm(dims_mm)
    max_dim = max([value or 0 for value in dims.values()])

    if family == "mirror":
        _append_unique(material_cues, "mirror", "reflective")
        _append_unique(silhouette_cues, "wall-mounted")
        _append_unique(preserve_rules, "wall-mounted reflective surface", "do not render as artwork")
    elif family == "rug":
        _append_unique(material_cues, "textile")
        _append_unique(silhouette_cues, "flat")
        _append_unique(preserve_rules, "flat floor textile", "not a raised platform")
    elif family == "ceiling_light":
        _append_unique(preserve_rules, "ceiling suspended", "do not place on floor or table")
    elif family == "wall_light":
        _append_unique(preserve_rules, "wall attached", "do not place on floor or table")
    elif family == "floor_lamp":
        _append_unique(preserve_rules, "floor standing")
    elif family == "table_lamp":
        _append_unique(preserve_rules, "surface scale", "do not enlarge into floor lamp")

    if family in {"table", "desk"} and any(token in text_blob.lower() for token in ("glass", "transparent")):
        _append_unique(material_cues, "glass")
        _append_unique(preserve_rules, "transparent tabletop")

    if max_dim > 0 and max_dim <= 250:
        _append_unique(preserve_rules, "tiny absolute scale", "do not upscale into anchor furniture")

    reflective_surface = any(
        token in material_cues for token in ("mirror", "reflective", "glass", "chrome")
    ) or str(category or "").strip().lower() == "mirror"
    return {
        "silhouette_cues": silhouette_cues,
        "material_cues": material_cues,
        "distinctive_parts": [],
        "preserve_rules": preserve_rules,
        "reflective_surface": reflective_surface,
    }


def _build_reference_feature_prompt(
    *,
    label: str,
    category: str | None,
    description: str,
    dims_mm: dict | None,
) -> str:
    dims = dims_mm or {}
    if _ultra_detailed_item_analysis_enabled():
        return (
            "EXHAUSTIVE REFERENCE PRODUCT IDENTITY ANALYSIS TASK.\n"
            "Analyze ONLY the provided product crop/reference image. Do not describe the room, background, camera, or styling.\n"
            "The later image renderer must be able to recreate the same product even without seeing the reference image, "
            "so every cue must be concrete, specific, and identity-preserving.\n"
            f"Label: {label or 'Item'}\n"
            f"Category: {category or 'unknown'}\n"
            f"Existing description: {description or ''}\n"
            f"Dims(mm): W={dims.get('width_mm')}, D={dims.get('depth_mm')}, H={dims.get('height_mm')}, R={dims.get('radius_mm')}\n"
            "Return STRICT JSON ONLY with keys: "
            "\"silhouette_cues\", \"material_cues\", \"color_cues\", \"distinctive_parts\", "
            "\"support_geometry\", \"surface_finish\", \"preserve_rules\", "
            "\"negative_identity_constraints\", \"reflective_surface\".\n"
            "For silhouette_cues, give many specific visible shape/proportion/topology cues: overall massing, outline, "
            "back/arm/top/edge shape, panel layout, shade shape, tabletop outline, openings, gaps, thickness, rounded corners, "
            "asymmetry, footprint, and scale relationship.\n"
            "For material_cues and color_cues, separate visible materials from colors: fabric grain, leather sheen, wood tone, "
            "stone veining, glass tint, metal finish, matte/gloss/satin quality, transparent or reflective surfaces, and exact color zones.\n"
            "For distinctive_parts, list product-specific parts that make this exact item recognizable, not generic category words.\n"
            "For support_geometry, describe every visible leg, foot, base, pedestal, sled, strut, stem, bracket, wall mount, or floor contact point.\n"
            "For surface_finish, describe texture, seams, stitching, bevels, edge treatment, reflection, shadow behavior, and translucency.\n"
            "For preserve_rules, write short imperative rules for what must remain visually unchanged.\n"
            "For negative_identity_constraints, state what the renderer must NOT turn it into: wrong category, wrong leg count/base, "
            "wrong proportions, wrong material/color, wrong transparency, wrong scale, or missing distinctive parts.\n"
            "Do not invent brand/model names. Do not output vague words like furniture, object, chair, table, lamp, or decor as the only cue. "
            "Prefer 8-16 items in each list when visible evidence supports it."
        )
    return (
        "REFERENCE PRODUCT IDENTITY ANALYSIS TASK.\n"
        "Analyze ONLY the provided product crop/reference image. Do not describe the room, camera, background, or styling.\n"
        "Return compact, concrete visual identity cues that help a later image model recreate the same product.\n"
        f"Label: {label or 'Item'}\n"
        f"Category: {category or 'unknown'}\n"
        f"Description: {description or ''}\n"
        f"Dims(mm): W={dims.get('width_mm')}, D={dims.get('depth_mm')}, H={dims.get('height_mm')}\n"
        "Return STRICT JSON ONLY with keys: "
        "\"silhouette_cues\", \"material_cues\", \"distinctive_parts\", \"preserve_rules\", \"reflective_surface\".\n"
        "For silhouette_cues, include visible shape/proportion/topology such as back shape, arm profile, shade shape, base type, leg/support structure, tabletop outline, openings, gaps, or frame geometry.\n"
        "For material_cues, include visible finish/color/material such as chrome, brass, marble, smoked glass, black leather, boucle fabric, cane, walnut, or matte ceramic.\n"
        "For distinctive_parts, name product-specific parts that make this item recognizable. Do not output only generic category words.\n"
        "For preserve_rules, write short imperative rules for what must remain visually unchanged in generation.\n"
        "Do not invent brand/model names. Do not return vague words like furniture, object, chair, table, lamp, or decor as the only cue."
    )


def _is_generic_feature_text(value: str, *, label: str, category: str | None) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    if len(text) < 5:
        return True
    normalized = re.sub(r"[^a-z0-9가-힣]+", " ", text).strip()
    if normalized in _GENERIC_FEATURE_TOKENS:
        return True
    category_family = str(category_match_family(category or label) or "").strip().lower().replace("_", " ")
    if category_family and normalized in {category_family, category_family.replace(" ", "")}:
        return True
    label_text = re.sub(r"[^a-z0-9가-힣]+", " ", str(label or "").lower()).strip()
    if label_text and normalized == label_text:
        return True
    return False


def _reference_features_sufficient(features: dict, *, label: str, category: str | None) -> bool:
    if not isinstance(features, dict):
        return False
    silhouette = _coerce_str_list(features.get("silhouette_cues"))
    materials = _coerce_str_list(features.get("material_cues"))
    distinctive = _coerce_str_list(features.get("distinctive_parts"))
    preserve_rules = _coerce_str_list(features.get("preserve_rules"))
    specific_silhouette = [
        cue for cue in silhouette if not _is_generic_feature_text(cue, label=label, category=category)
    ]
    specific_distinctive = [
        cue for cue in distinctive if not _is_generic_feature_text(cue, label=label, category=category)
    ]
    specific_rules = [
        rule for rule in preserve_rules if not _is_generic_feature_text(rule, label=label, category=category)
    ]
    return bool(len(specific_silhouette) >= 2 and materials and specific_distinctive and specific_rules)


def _normalize_reference_feature_payload(parsed: dict, fallback: dict) -> dict:
    result = {
        "silhouette_cues": _coerce_str_list(parsed.get("silhouette_cues")) or fallback["silhouette_cues"],
        "material_cues": _coerce_str_list(parsed.get("material_cues")) or fallback["material_cues"],
        "color_cues": _coerce_str_list(parsed.get("color_cues")),
        "distinctive_parts": _coerce_str_list(parsed.get("distinctive_parts")),
        "support_geometry": _coerce_str_list(parsed.get("support_geometry")),
        "surface_finish": _coerce_str_list(parsed.get("surface_finish")),
        "preserve_rules": _coerce_str_list(parsed.get("preserve_rules")),
        "negative_identity_constraints": _coerce_str_list(parsed.get("negative_identity_constraints")),
        "reflective_surface": _coerce_bool(parsed.get("reflective_surface"), fallback["reflective_surface"]),
    }
    if _ultra_detailed_item_analysis_enabled():
        result["ultra_reference_feature_analysis"] = True
    return result


def extract_reference_features(
    *,
    crop_path: str | None,
    label: str,
    category: str | None,
    description: str,
    dims_mm: dict | None,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_json_from_model_text: Callable[[str], Any],
    log_brief: bool,
    allow_model_call: bool = True,
    extraction_reason: str = "",
    absolute_deadline_ts: float | None = None,
) -> dict:
    fallback = _fallback_reference_features(label=label, category=category, description=description, dims_mm=dims_mm)
    if not allow_model_call:
        return fallback
    if not crop_path or not os.path.exists(crop_path):
        return fallback
    if not _should_use_reference_feature_model(
        extraction_reason=extraction_reason,
        absolute_deadline_ts=absolute_deadline_ts,
    ):
        return fallback

    crop_img = None
    try:
        requested_timeout = 75.0 if _ultra_detailed_item_analysis_enabled() else 25.0
        bounded_timeout = _bounded_timeout(requested_timeout, absolute_deadline_ts=absolute_deadline_ts)
        if bounded_timeout is None:
            return fallback
        prompt = _build_reference_feature_prompt(
            label=label,
            category=category,
            description=description,
            dims_mm=dims_mm,
        )
        crop_img = _normalize_crop_for_prompt(crop_path)
        last_features = None
        for attempt_index in range(1, 4):
            response = call_gemini_with_failover(
                analysis_model_name,
                [prompt, crop_img],
                {"timeout": int(bounded_timeout), "max_attempts": 1},
                {},
                log_tag="Analysis.ReferenceFeatures",
            )
            if not response or not getattr(response, "text", None):
                continue
            parsed = safe_json_from_model_text(response.text) or {}
            if not isinstance(parsed, dict):
                continue
            features = _normalize_reference_feature_payload(parsed, fallback)
            last_features = features
            if _reference_features_sufficient(features, label=label, category=category):
                features["analysis_attempts"] = attempt_index
                features["analysis_retry_count"] = max(0, attempt_index - 1)
                features["analysis_quality"] = "model_sufficient"
                return features
        result = dict(fallback)
        if isinstance(last_features, dict) and "reflective_surface" in last_features:
            result["reflective_surface"] = _coerce_bool(
                last_features.get("reflective_surface"),
                fallback["reflective_surface"],
            )
        result["analysis_attempts"] = 3
        result["analysis_retry_count"] = 2
        result["analysis_quality"] = "fallback_after_weak_model" if last_features else "fallback_after_empty_model"
        return result
    except Exception as exc:
        if not log_brief:
            print(f"[ReferenceFeatures] fallback for {label}: {exc}", flush=True)
        return fallback
    finally:
        if crop_img is not None:
            try:
                crop_img.close()
            except Exception:
                pass
