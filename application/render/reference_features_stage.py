import os
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
}
_MEDIUM_PRIORITY_REFERENCE_REASONS = {
    "thin_floor_footprint_object",
    "tiny_absolute_scale_object",
}


def _extract_keyword_cues(text: str, keywords: tuple[str, ...], limit: int = 4) -> list[str]:
    normalized = str(text or "").lower()
    cues: list[str] = []
    for keyword in keywords:
        if keyword in normalized and keyword not in cues:
            cues.append(keyword)
        if len(cues) >= limit:
            break
    return cues


def _coerce_str_list(value: Any, *, limit: int = 6) -> list[str]:
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
    if family in _RUG_FAMILIES or (height_mm > 0 and height_mm <= 40 and max(width_mm, depth_mm) >= 500):
        return True, "thin_floor_footprint_object"
    if max_dim > 0 and max_dim <= 250:
        return True, "tiny_absolute_scale_object"
    if family in _SUPPORT_GEOMETRY_FAMILIES:
        return True, "support_geometry_object"
    if family in _SEATING_FAMILIES and max(width_mm, depth_mm) >= 900:
        return True, "topology_sensitive_seating"
    if family in _LIGHT_FAMILIES and max_dim >= 1200:
        return True, "large_light_anchor_candidate"
    if max(width_mm, depth_mm) >= 1400:
        return True, "large_anchor_candidate"
    return False, "fallback_only"


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


def _fallback_reference_features(*, label: str, category: str | None, description: str) -> dict:
    text_blob = " ".join([str(label or ""), str(category or ""), str(description or "")]).strip()
    material_cues = _extract_keyword_cues(text_blob, _MATERIAL_CUE_KEYWORDS)
    silhouette_cues = _extract_keyword_cues(text_blob, _SHAPE_CUE_KEYWORDS)
    reflective_surface = any(
        token in material_cues for token in ("mirror", "reflective", "glass", "chrome")
    ) or str(category or "").strip().lower() == "mirror"
    return {
        "silhouette_cues": silhouette_cues,
        "material_cues": material_cues,
        "distinctive_parts": [],
        "preserve_rules": [],
        "reflective_surface": reflective_surface,
    }


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
    fallback = _fallback_reference_features(label=label, category=category, description=description)
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
        bounded_timeout = _bounded_timeout(25.0, absolute_deadline_ts=absolute_deadline_ts)
        if bounded_timeout is None:
            return fallback
        dims = dims_mm or {}
        prompt = (
            "REFERENCE FEATURE EXTRACTION TASK.\n"
            "Analyze this furniture crop and return compact structural cues for fidelity preservation.\n"
            f"Label: {label or 'Item'}\n"
            f"Category: {category or 'unknown'}\n"
            f"Description: {description or ''}\n"
            f"Dims(mm): W={dims.get('width_mm')}, D={dims.get('depth_mm')}, H={dims.get('height_mm')}\n"
            "Return STRICT JSON ONLY with keys: "
            "\"silhouette_cues\", \"material_cues\", \"distinctive_parts\", \"preserve_rules\", \"reflective_surface\".\n"
            "Focus on topology, frame/support shape, openings/gaps, tabletop/base shape, and reflective behavior.\n"
            "Do not describe the room or camera."
        )
        crop_img = _normalize_crop_for_prompt(crop_path)
        response = call_gemini_with_failover(
            analysis_model_name,
            [prompt, crop_img],
            {"timeout": int(bounded_timeout), "max_attempts": 1},
            {},
            log_tag="Analysis.ReferenceFeatures",
        )
        if not response or not getattr(response, "text", None):
            return fallback
        parsed = safe_json_from_model_text(response.text) or {}
        if not isinstance(parsed, dict):
            return fallback
        return {
            "silhouette_cues": _coerce_str_list(parsed.get("silhouette_cues")) or fallback["silhouette_cues"],
            "material_cues": _coerce_str_list(parsed.get("material_cues")) or fallback["material_cues"],
            "distinctive_parts": _coerce_str_list(parsed.get("distinctive_parts")),
            "preserve_rules": _coerce_str_list(parsed.get("preserve_rules")),
            "reflective_surface": _coerce_bool(parsed.get("reflective_surface"), fallback["reflective_surface"]),
        }
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
