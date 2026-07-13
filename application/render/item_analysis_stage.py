import json
import os
import re
import time
from typing import Any, Callable, Optional

from PIL import Image
from application.render.item_analysis_profile import (
    COMPACT_ITEM_ANALYSIS_PROFILE,
    DETAILED_ITEM_ANALYSIS_PROFILE,
    normalize_item_analysis_profile,
)
from application.render.reference_features_stage import (
    extract_reference_features,
    should_extract_reference_features,
)


_GENERIC_DESC_PREFIXES = (
    "a high quality ",
    "high quality ",
    "beautiful ",
    "modern ",
    "nice ",
)
_MATERIAL_HINT_RE = re.compile(r"\b(wood|walnut|oak|marble|stone|glass|metal|chrome|steel|fabric|linen|boucle|leather|rattan|mirror|reflective)\b", re.IGNORECASE)
_SHAPE_HINT_RE = re.compile(r"\b(round|circular|oval|square|rectangular|arched|curved|modular|low-profile|pedestal|spindle|flaring|blocky|slim|thin|tufted|rolled|boxy)\b", re.IGNORECASE)


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _normalize_dims_for_description(dims_mm: dict | None) -> dict[str, int | None]:
    dims_mm = dims_mm if isinstance(dims_mm, dict) else {}
    return {
        "width_mm": _coerce_positive_int(dims_mm.get("width_mm")),
        "depth_mm": _coerce_positive_int(dims_mm.get("depth_mm")),
        "height_mm": _coerce_positive_int(dims_mm.get("height_mm")),
        "radius_mm": _coerce_positive_int(dims_mm.get("radius_mm")),
    }


def _family_display(label: str, category: str | None) -> str:
    family = str(category or label or "furniture").strip().lower()
    family = re.sub(r"[_-]+", " ", family)
    return family or "furniture"


def _absolute_size_class(*, family: str, dims_mm: dict | None) -> str:
    dims = _normalize_dims_for_description(dims_mm)
    width_mm = dims.get("width_mm") or 0
    depth_mm = dims.get("depth_mm") or 0
    height_mm = dims.get("height_mm") or 0
    radius_mm = dims.get("radius_mm") or 0
    max_dim = max(width_mm, depth_mm, height_mm, radius_mm)
    footprint_max = max(width_mm, depth_mm)
    normalized_family = str(family or "").strip().lower()

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


def _size_scale_note(*, family: str, size_class: str) -> str:
    normalized_family = str(family or "").strip().lower()
    if normalized_family == "rug":
        notes = {
            "small": "a compact rug accent, not a wall-to-wall floor piece",
            "medium": "a mid-scale rug that supports a seating group without taking over the room",
            "large": "a large rug anchor that can sit under the primary seating group",
            "extra-large": "an oversized rug anchor that should dominate the floor footprint",
        }
        return notes.get(size_class, "a rug sized to its real-world floor footprint")
    if normalized_family == "table_lamp":
        notes = {
            "tiny": "a tiny surface accessory, never anchor-sized furniture",
            "small": "a compact surface object, not a side table or stool",
            "medium": "a modest tabletop object that stays visually secondary",
            "large": "a substantial decorative object that still reads smaller than anchor furniture",
        }
        return notes.get(size_class, "a compact surface-scale object")
    if normalized_family == "decor":
        notes = {
            "tiny": "a tiny decorative object, never anchor-sized furniture",
            "small": "a compact decorative object, placed on a surface only when its reference or dimensions indicate tabletop scale",
            "medium": "a mid-scale decorative object, placed according to its reference instead of automatically on a tabletop",
            "large": "a large decorative object that may be floor-standing or wall/floor placed according to its reference",
        }
        return notes.get(size_class, "a decorative object placed according to its reference scale")
    notes = {
        "tiny": "a very small accessory-scale piece",
        "small": "a compact accent piece",
        "medium": "a standard human-scale furniture piece",
        "large": "a large anchor furniture piece",
        "extra-large": "an oversized anchor furniture piece",
    }
    return notes.get(size_class, "a human-scale furniture piece")


def _format_dims_sentence(dims_mm: dict | None) -> str:
    dims = _normalize_dims_for_description(dims_mm)
    parts: list[str] = []
    if dims.get("width_mm"):
        parts.append(f"W={dims['width_mm']}mm")
    if dims.get("depth_mm"):
        parts.append(f"D={dims['depth_mm']}mm")
    if dims.get("height_mm"):
        parts.append(f"H={dims['height_mm']}mm")
    if dims.get("radius_mm"):
        parts.append(f"R={dims['radius_mm']}mm")
    if not parts:
        return ""
    return "Real-world dimensions are " + ", ".join(parts) + "."


def _description_word_count(description: str) -> int:
    return len(re.findall(r"[A-Za-z0-9가-힣]+", str(description or "")))


def _looks_generic_description(description: str, label: str) -> bool:
    normalized = str(description or "").strip().lower()
    if not normalized:
        return True
    label_normalized = str(label or "").strip().lower()
    if normalized == label_normalized or normalized == f"{label_normalized}.":
        return True
    if any(normalized.startswith(prefix) for prefix in _GENERIC_DESC_PREFIXES):
        return True
    word_count = _description_word_count(normalized)
    has_material = bool(_MATERIAL_HINT_RE.search(normalized))
    has_shape = bool(_SHAPE_HINT_RE.search(normalized))
    has_numbers = bool(re.search(r"\d{2,4}\s*mm", normalized))
    if word_count < 14:
        return True
    if word_count < 24 and not (has_material and has_shape):
        return True
    if not has_material and not has_shape and not has_numbers:
        return True
    return False


def _stabilize_description(
    *,
    label: str,
    category: str | None,
    description: str,
    dims_mm: dict | None,
    reference_features: dict | None = None,
) -> str:
    base_text = str(description or "").strip()
    ref = reference_features if isinstance(reference_features, dict) else {}
    family = _family_display(label, category)
    dims = _normalize_dims_for_description(dims_mm)
    size_class = _absolute_size_class(family=family, dims_mm=dims)
    dim_sentence = _format_dims_sentence(dims)
    material_cues = [str(x).strip() for x in (ref.get("material_cues") or []) if str(x).strip()]
    silhouette_cues = [str(x).strip() for x in (ref.get("silhouette_cues") or []) if str(x).strip()]
    distinctive_parts = [str(x).strip() for x in (ref.get("distinctive_parts") or []) if str(x).strip()]
    preserve_rules = [str(x).strip() for x in (ref.get("preserve_rules") or []) if str(x).strip()]

    if not _looks_generic_description(base_text, label):
        stabilized = base_text
        cue_bits: list[str] = []
        if reference_features:
            if material_cues and not _MATERIAL_HINT_RE.search(stabilized):
                cue_bits.append("materials such as " + ", ".join(material_cues[:3]))
            if silhouette_cues and not _SHAPE_HINT_RE.search(stabilized):
                cue_bits.append("a silhouette defined by " + ", ".join(silhouette_cues[:3]))
            if distinctive_parts and "distinctive" not in stabilized.lower():
                cue_bits.append("distinctive parts like " + ", ".join(distinctive_parts[:3]))
        if cue_bits:
            if stabilized and not stabilized.endswith((".", "!", "?")):
                stabilized += "."
            stabilized = f"{stabilized} Preserve " + ", ".join(cue_bits) + "." 
        if dim_sentence and not re.search(r"\d{2,4}\s*mm", stabilized):
            if stabilized and not stabilized.endswith((".", "!", "?")):
                stabilized += "."
            stabilized = f"{stabilized} {dim_sentence}".strip()
        if size_class in {"tiny", "small"} and "small" not in stabilized.lower() and "tiny" not in stabilized.lower():
            size_sentence = f"It should read as {_size_scale_note(family=family, size_class=size_class)}."
            stabilized = f"{stabilized} {size_sentence}".strip()
        return stabilized

    cue_sentences: list[str] = []
    detail_bits: list[str] = []
    if material_cues:
        detail_bits.append("materials such as " + ", ".join(material_cues[:3]))
    if silhouette_cues:
        detail_bits.append("a silhouette defined by " + ", ".join(silhouette_cues[:3]))
    if distinctive_parts:
        detail_bits.append("distinctive parts like " + ", ".join(distinctive_parts[:3]))
    if detail_bits:
        cue_sentences.append(
            f"{label} should preserve its original identity with " + ", ".join(detail_bits) + "."
        )
    else:
        cue_sentences.append(
            f"{label} should keep its original proportions, support geometry, and recognizable outline."
        )
    cue_sentences.append(
        f"Treat it as {_size_scale_note(family=family, size_class=size_class)} rather than rescaling it into a generic anchor object."
    )
    if dim_sentence:
        cue_sentences.append(dim_sentence)
    if preserve_rules:
        cue_sentences.append("Preserve cues such as " + ", ".join(preserve_rules[:3]) + ".")

    stabilized = " ".join(cue_sentences).strip()
    if not stabilized.endswith("."):
        stabilized += "."
    return stabilized


def _options_reference_features(item_data: dict | None) -> dict:
    if not isinstance(item_data, dict):
        return {}
    opts = item_data.get("options")
    if not isinstance(opts, dict):
        return {}
    features = opts.get("reference_features")
    return dict(features) if isinstance(features, dict) else {}


def _merge_reference_feature_lists(*values, limit: int = 8) -> list[str]:
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


def _merge_options_reference_features(reference_features: dict | None, item_data: dict | None) -> dict:
    provided = _options_reference_features(item_data)
    if not provided:
        return reference_features if isinstance(reference_features, dict) else {}
    merged = dict(reference_features or {})
    for key in ("silhouette_cues", "material_cues", "distinctive_parts", "preserve_rules", "color_cues"):
        merged[key] = _merge_reference_feature_lists(provided.get(key), merged.get(key))
    if provided.get("reflective_surface") is not None:
        merged["reflective_surface"] = provided.get("reflective_surface")
    elif "reflective_surface" not in merged:
        merged["reflective_surface"] = False
    merged["options_reference_features_applied"] = True
    return merged


def detect_furniture_boxes(
    moodboard_path,
    *,
    log_brief: bool,
    call_gemini_with_failover: Callable[..., Any],
    default_model_name: str,
    model_name: Optional[str] = None,
    timeout_sec: Optional[int] = None,
    max_attempts: Optional[int] = None,
):
    if not log_brief:
        print(f">> [Detection] Scanning furniture in {moodboard_path}...", flush=True)
    try:
        with Image.open(moodboard_path) as img:
            prompt = (
                "OBJECT DETECTION TASK:\n"
                "Identify ALL discrete interior objects that could make useful detail-shot targets in this image, "
                "including furniture, lighting, decor, and accessories.\n"
                "Examples: sofas, chairs, tables, lamps, rugs, ottomans, mirrors, wall art, framed prints, posters, "
                "vases, books, plants, candles, sculptures, trays, table decor, shelf decor, and small accessories.\n"
                "**NOTE:** The background is a neutral grey (#D2D2D2) for contrast. Do not detect the background itself.\n"
                "Return a JSON list where each item has:\n"
                "- 'label': Specific name of the item. Use specific labels instead of generic 'Decor' whenever possible.\n"
                "- 'box_2d': [ymin, xmin, ymax, xmax] coordinates normalized to 0-1000 scale.\n"
                "\n"
                "<CRITICAL: SORTING ORDER>\n"
                "**YOU MUST SORT THE LIST BY PHYSICAL SIZE (VOLUME) FROM LARGEST TO SMALLEST.**\n"
                "1. Largest items first (e.g., Sofa, Bed, Large Rug, Wardrobe).\n"
                "2. Medium items second (e.g., Armchair, Coffee Table, Console).\n"
                "3. Small items last (e.g., Side Table, Lamp, Vase, Decor).\n"
                "Ignore walls, windows, floors, ceiling, and built-in architecture. "
                "But do detect discrete objects attached to a wall or placed on a shelf, table, or floor."
            )
            detect_model = model_name or default_model_name
            detect_timeout = max(60, int(timeout_sec or 120))
            detect_max_attempts = max(3, int(max_attempts or 3))
            response = call_gemini_with_failover(
                detect_model,
                [prompt, img],
                {
                    "timeout": detect_timeout,
                    "max_attempts": detect_max_attempts,
                },
                {},
                log_tag="Analysis.DetectFurniture",
            )
            if response and response.text:
                text = response.text.strip()
                if "```json" in text:
                    text = text.split("```json")[1].split("```")[0].strip()
                elif "```" in text:
                    text = text.split("```")[0].strip()

                items = json.loads(text)
                if isinstance(items, list) and len(items) > 0:
                    if not log_brief:
                        print(f">> [Detection] Found {len(items)} items (Sorted): {[i.get('label') for i in items]}", flush=True)
                    return items
    except Exception as exc:
        print(f"!! Detection Failed: {exc}", flush=True)

    return []


def _crop_item_with_padding(moodboard_path, item_data, unique_id=None, item_index=None, save_crop=True):
    box = item_data.get("box_2d")
    label = item_data.get("label", "Furniture")
    cropped_img = None
    crop_path = None
    cutout_img = None
    try:
        img = Image.open(moodboard_path)
        width, height = img.size
        if box:
            ymin, xmin, ymax, xmax = box
            base_top = int(ymin / 1000 * height)
            base_bottom = int(ymax / 1000 * height)
            base_left = int(xmin / 1000 * width)
            base_right = int(xmax / 1000 * width)

            box_w_px = max(1, base_right - base_left)
            box_h_px = max(1, base_bottom - base_top)

            pad_bottom_px = max(int(box_h_px * 2.0), int(height * 0.18))
            pad_top_px = max(int(box_h_px * 1.2), int(height * 0.12))
            pad_left_px = max(int(box_w_px * 1.2), int(width * 0.16))
            pad_right_px = max(int(box_w_px * 2.0), int(width * 0.24))

            space_left = base_left
            space_right = width - base_right
            if space_right > space_left * 1.2:
                pad_right_px = max(pad_right_px, int(width * 0.34))
                pad_left_px = max(pad_left_px, int(width * 0.12))
            elif space_left > space_right * 1.2:
                pad_left_px = max(pad_left_px, int(width * 0.34))
                pad_right_px = max(pad_right_px, int(width * 0.12))

            top = max(0, base_top - pad_top_px)
            bottom = min(height, base_bottom + pad_bottom_px)
            left = max(0, base_left - pad_left_px)
            right = min(width, base_right + pad_right_px)

            min_w = int(width * 0.26)
            min_h = int(height * 0.26)
            if right - left < min_w:
                pad = int(min_w / 2)
                left = max(0, base_left - pad)
                right = min(width, base_right + pad)
            if bottom - top < min_h:
                pad = int(min_h / 2)
                top = max(0, base_top - pad)
                bottom = min(height, base_bottom + pad)

            cropped_img = img.crop((left, top, right, bottom))
            cutout_img = img.crop((base_left, base_top, base_right, base_bottom))
        else:
            cropped_img = img.copy()
            cutout_img = img.copy()
        img.close()

        try:
            if cropped_img:
                crop_width, crop_height = cropped_img.size
                target_max = 1600
                if max(crop_width, crop_height) < target_max:
                    scale = target_max / max(crop_width, crop_height)
                    new_width = max(1, int(crop_width * scale))
                    new_height = max(1, int(crop_height * scale))
                    cropped_img = cropped_img.resize((new_width, new_height), Image.LANCZOS)
        except Exception:
            pass

        try:
            if save_crop and unique_id is not None and item_index is not None and cutout_img is not None:
                safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(label))[:40]
                crop_filename = f"crop_{unique_id}_{int(item_index):02d}_{safe_label}.png"
                crop_path = os.path.join("outputs", crop_filename)
                cutout_img.save(crop_path, "PNG")
        except Exception:
            crop_path = None
    finally:
        if cutout_img:
            try:
                cutout_img.close()
            except Exception:
                pass
    return cropped_img, crop_path


def analyze_cropped_item(
    moodboard_path,
    item_data,
    *,
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    safe_extract_json: Callable[[str], dict],
    normalize_dims_dict: Callable[[dict], dict],
    log_brief: bool,
    unique_id=None,
    item_index=None,
    save_crop=True,
    enable_text_read=True,
    analysis_profile: str | None = None,
    allow_reference_feature_model: bool = False,
    provided_dims_mm=None,
    absolute_deadline_ts: float | None = None,
):
    cropped_img = None
    cutout_img = None
    try:
        box = item_data.get("box_2d")
        label = item_data.get("label", "Furniture")

        img = Image.open(moodboard_path)
        width, height = img.size

        if box:
            ymin, xmin, ymax, xmax = box
            base_top = int(ymin / 1000 * height)
            base_bottom = int(ymax / 1000 * height)
            base_left = int(xmin / 1000 * width)
            base_right = int(xmax / 1000 * width)

            box_w_px = max(1, base_right - base_left)
            box_h_px = max(1, base_bottom - base_top)

            pad_bottom_px = max(int(box_h_px * 2.0), int(height * 0.18))
            pad_top_px = max(int(box_h_px * 1.2), int(height * 0.12))
            pad_left_px = max(int(box_w_px * 1.2), int(width * 0.16))
            pad_right_px = max(int(box_w_px * 2.0), int(width * 0.24))

            space_left = base_left
            space_right = width - base_right
            if space_right > space_left * 1.2:
                pad_right_px = max(pad_right_px, int(width * 0.34))
                pad_left_px = max(pad_left_px, int(width * 0.12))
            elif space_left > space_right * 1.2:
                pad_left_px = max(pad_left_px, int(width * 0.34))
                pad_right_px = max(pad_right_px, int(width * 0.12))

            top = max(0, base_top - pad_top_px)
            bottom = min(height, base_bottom + pad_bottom_px)
            left = max(0, base_left - pad_left_px)
            right = min(width, base_right + pad_right_px)

            min_w = int(width * 0.26)
            min_h = int(height * 0.26)
            if right - left < min_w:
                pad = int(min_w / 2)
                left = max(0, base_left - pad)
                right = min(width, base_right + pad)
            if bottom - top < min_h:
                pad = int(min_h / 2)
                top = max(0, base_top - pad)
                bottom = min(height, base_bottom + pad)

            cropped_img = img.crop((left, top, right, bottom))
            cutout_img = img.crop((base_left, base_top, base_right, base_bottom))

            try:
                if cropped_img:
                    crop_width, crop_height = cropped_img.size
                    target_max = 1600
                    if max(crop_width, crop_height) < target_max:
                        scale = target_max / max(crop_width, crop_height)
                        new_width = max(1, int(crop_width * scale))
                        new_height = max(1, int(crop_height * scale))
                        cropped_img = cropped_img.resize((new_width, new_height), Image.LANCZOS)
            except Exception:
                pass
        else:
            cropped_img = img.copy()
            cutout_img = img.copy()

        img.close()

        crop_path = None
        try:
            if save_crop and unique_id is not None and item_index is not None:
                safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(label))[:40]
                crop_filename = f"crop_{unique_id}_{int(item_index):02d}_{safe_label}.png"
                crop_path = os.path.join("outputs", crop_filename)
                if cutout_img:
                    cutout_img.save(crop_path, "PNG")
        except Exception:
            crop_path = None

        resolved_dims_mm = normalize_dims_dict(provided_dims_mm or {})
        default_analysis_profile = (
            DETAILED_ITEM_ANALYSIS_PROFILE if enable_text_read else COMPACT_ITEM_ANALYSIS_PROFILE
        )
        resolved_analysis_profile = normalize_item_analysis_profile(
            analysis_profile,
            default=default_analysis_profile,
        )
        detailed_analysis_enabled = resolved_analysis_profile == DETAILED_ITEM_ANALYSIS_PROFILE
        if not detailed_analysis_enabled:
            extract_ref_features, extraction_reason = should_extract_reference_features(
                label=label,
                category=item_data.get("category"),
                category_canonical=item_data.get("category_canonical"),
                dims_mm=resolved_dims_mm,
            )
            reference_model_allowed = bool(allow_reference_feature_model and extract_ref_features)
            reference_features = extract_reference_features(
                crop_path=crop_path,
                label=label,
                category=item_data.get("category"),
                description=f"{label} product reference image is authoritative.",
                dims_mm=resolved_dims_mm,
                call_gemini_with_failover=call_gemini_with_failover,
                analysis_model_name=analysis_model_name,
                safe_json_from_model_text=safe_extract_json,
                log_brief=log_brief,
                allow_model_call=reference_model_allowed,
                extraction_reason=extraction_reason,
                absolute_deadline_ts=absolute_deadline_ts,
            )
            if isinstance(reference_features, dict):
                reference_features["extraction_mode"] = "model" if reference_model_allowed else "deterministic"
                reference_features["extraction_reason"] = extraction_reason or "authoritative_reference_image"
                reference_features["analysis_profile"] = resolved_analysis_profile
            reference_features = _merge_options_reference_features(reference_features, item_data)
            final_desc = _stabilize_description(
                label=label,
                category=item_data.get("category") or item_data.get("category_canonical"),
                description=f"{label} product reference image is authoritative.",
                dims_mm=resolved_dims_mm,
                reference_features=reference_features,
            )
            if cropped_img:
                try:
                    cropped_img.close()
                except Exception:
                    pass
            if cutout_img:
                try:
                    cutout_img.close()
                except Exception:
                    pass
            return {
                "label": label,
                "description": final_desc,
                "box_2d": box,
                "crop_path": crop_path,
                "reference_features": reference_features,
                "target_key": item_data.get("target_key"),
                "source_index": item_data.get("source_index"),
                "category": item_data.get("category"),
                "category_canonical": item_data.get("category_canonical"),
                "product_name": item_data.get("product_name"),
                "item_id": item_data.get("item_id"),
                "item_analysis_profile": resolved_analysis_profile,
            }

        if enable_text_read:
            prompt = (
                f"Analyze this image cutout of a '{label}'.\n"
                "IMPORTANT: Look specifically at the TEXT written below or near the object.\n"
                "1. **READ EXTRACT DIMENSIONS:** If there is text like 'W: 2800', 'Width 2800mm', '2800*1450', extract these numbers EXACTLY in millimeters.\n"
                "   - Support radius notation too (R, 반지름, Ø, ⌀, Φ).\n"
                "2. **LONG DESCRIPTION (90-120 words):** Describe material, color, shape, proportions, silhouette, support/base geometry, openings/gaps, and scale cues.\n"
                "   - Treat dimensions as core identity cues, not as a metadata tail.\n"
                "   - Avoid generic filler like 'high quality furniture'.\n"
                "\n"
                "Return STRICT JSON only:\n"
                "{\n"
                "  \"description\": \"Visual description...\",\n"
                "  \"dimensions_mm\": {\"width\": int/null, \"depth\": int/null, \"height\": int/null, \"radius\": int/null},\n"
                "  \"raw_text_found\": \"copy the text you read here\"\n"
                "}\n"
            )
        else:
            dims_hint = normalize_dims_dict(provided_dims_mm or {})
            w_hint = dims_hint.get("width_mm")
            d_hint = dims_hint.get("depth_mm")
            h_hint = dims_hint.get("height_mm")
            r_hint = dims_hint.get("radius_mm")
            has_dim_hint = any([(w_hint or 0) > 0, (d_hint or 0) > 0, (h_hint or 0) > 0, (r_hint or 0) > 0])

            hint_line = ""
            if has_dim_hint:
                hint_line = (
                    f"CATALOG DIMENSIONS (authoritative, mm): "
                    f"W={w_hint if (w_hint or 0) > 0 else 'null'}, "
                    f"D={d_hint if (d_hint or 0) > 0 else 'null'}, "
                    f"H={h_hint if (h_hint or 0) > 0 else 'null'}, "
                    f"R={r_hint if (r_hint or 0) > 0 else 'null'}.\n"
                    "Use these exact numbers naturally in the description body (not as a metadata tail).\n"
                    "Do NOT add template-like phrases such as 'Requested size'.\n"
                )

            prompt = (
                f"Analyze this image cutout of a '{label}'.\n"
                "Write a 90-120 word visual description.\n"
                "Cover material, color, shape, proportions, silhouette, support/base geometry, openings/gaps, and real-world scale cues.\n"
                f"{hint_line}"
                "Treat the dimensions as core identity constraints and mention whether the item reads tiny, compact, standard, large, or oversized.\n"
                "Do not use generic filler like 'a high quality chair'.\n"
                "If dimensions are missing, do NOT invent them.\n"
                "Return STRICT JSON only:\n"
                "{\n"
                "  \"description\": \"Visual description...\"\n"
                "}\n"
            )

        crop_timeout_sec = 150
        crop_max_attempts = None
        if absolute_deadline_ts is not None:
            try:
                remaining_deadline_sec = max(0.0, float(absolute_deadline_ts) - float(time.time()))
            except Exception:
                remaining_deadline_sec = 0.0
            if remaining_deadline_sec <= 10.0:
                response = None
            else:
                crop_timeout_sec = int(max(10.0, min(45.0, remaining_deadline_sec)))
                crop_max_attempts = 1
                response = call_gemini_with_failover(
                    analysis_model_name,
                    [prompt, cropped_img],
                    {"timeout": crop_timeout_sec, "max_attempts": crop_max_attempts},
                    {},
                    log_tag="Analysis.CropItem",
                )
        else:
            response = call_gemini_with_failover(
                analysis_model_name,
                [prompt, cropped_img],
                {"timeout": crop_timeout_sec},
                {},
                log_tag="Analysis.CropItem",
            )

        desc = f"{label} with its original material and silhouette preserved."

        if response and response.text:
            data = safe_extract_json(response.text)
            if data:
                desc = data.get("description", desc)
                if enable_text_read:
                    raw_dims = data.get("dimensions_mm", {})
                    width_mm = raw_dims.get("width")
                    depth_mm = raw_dims.get("depth")
                    height_mm = raw_dims.get("height")
                    radius_mm = raw_dims.get("radius")
                    extracted_dims_mm = normalize_dims_dict(
                        {
                            "width_mm": width_mm,
                            "depth_mm": depth_mm,
                            "height_mm": height_mm,
                            "radius_mm": radius_mm,
                        }
                    )
                    if extracted_dims_mm:
                        merged_dims_mm = dict(extracted_dims_mm)
                        for dim_key, dim_value in (resolved_dims_mm or {}).items():
                            if dim_value:
                                merged_dims_mm[dim_key] = dim_value
                        resolved_dims_mm = normalize_dims_dict(merged_dims_mm)

                    if width_mm and depth_mm and height_mm:
                        if not log_brief:
                            print(
                                f"   -> [Text Read] {label}: W={width_mm}mm, D={depth_mm}mm, H={height_mm}mm. "
                                f"(Source: {data.get('raw_text_found')})",
                                flush=True,
                            )
                    elif radius_mm and (height_mm or depth_mm or width_mm):
                        if not log_brief:
                            dim_bits = [f"R={radius_mm}mm"]
                            if height_mm:
                                dim_bits.append(f"H={height_mm}mm")
                            elif depth_mm:
                                dim_bits.append(f"D={depth_mm}mm")
                            elif width_mm:
                                dim_bits.append(f"W={width_mm}mm")
                            print(
                                f"   -> [Text Read] {label}: {', '.join(dim_bits)}. "
                                f"(Source: {data.get('raw_text_found')})",
                                flush=True,
                            )
                    elif log_brief:
                        print(f"[Text Read] FAIL {label}", flush=True)

        desc = _stabilize_description(
            label=label,
            category=item_data.get("category") or item_data.get("category_canonical"),
            description=desc,
            dims_mm=resolved_dims_mm,
            reference_features=None,
        )

        if cropped_img:
            try:
                cropped_img.close()
            except Exception:
                pass
        if cutout_img:
            try:
                cutout_img.close()
            except Exception:
                pass
        extract_ref_features, extraction_reason = should_extract_reference_features(
            label=label,
            category=item_data.get("category"),
            category_canonical=item_data.get("category_canonical"),
            dims_mm=resolved_dims_mm,
        )
        reference_features = extract_reference_features(
            crop_path=crop_path,
            label=label,
            category=item_data.get("category"),
            description=desc,
            dims_mm=resolved_dims_mm,
            call_gemini_with_failover=call_gemini_with_failover,
            analysis_model_name=analysis_model_name,
            safe_json_from_model_text=safe_extract_json,
            log_brief=log_brief,
            allow_model_call=extract_ref_features,
            extraction_reason=extraction_reason,
            absolute_deadline_ts=absolute_deadline_ts,
        )
        if isinstance(reference_features, dict):
            reference_features["extraction_mode"] = "model" if extract_ref_features else "fallback"
            reference_features["extraction_reason"] = extraction_reason
            reference_features["analysis_profile"] = resolved_analysis_profile
        reference_features = _merge_options_reference_features(reference_features, item_data)
        final_desc = _stabilize_description(
            label=label,
            category=item_data.get("category") or item_data.get("category_canonical"),
            description=desc,
            dims_mm=resolved_dims_mm,
            reference_features=reference_features,
        )

        return {
            "label": label,
            "description": final_desc,
            "box_2d": box,
            "crop_path": crop_path,
            "reference_features": reference_features,
            "target_key": item_data.get("target_key"),
            "source_index": item_data.get("source_index"),
            "category": item_data.get("category"),
            "category_canonical": item_data.get("category_canonical"),
            "product_name": item_data.get("product_name"),
            "item_id": item_data.get("item_id"),
            "item_analysis_profile": resolved_analysis_profile,
        }

    except Exception as exc:
        print(f"!! Crop Analysis Failed for {item_data.get('label','Furniture')}: {exc}", flush=True)
        if cropped_img:
            try:
                cropped_img.close()
            except Exception:
                pass
        if cutout_img:
            try:
                cutout_img.close()
            except Exception:
                pass

    fallback_reference_features = extract_reference_features(
        crop_path=None,
        label=item_data.get("label", "Furniture"),
        category=item_data.get("category"),
        description=f"{item_data.get('label','Furniture')} with its original identity preserved.",
        dims_mm=normalize_dims_dict(provided_dims_mm or {}),
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=analysis_model_name,
        safe_json_from_model_text=safe_extract_json,
        log_brief=log_brief,
        allow_model_call=False,
    )
    if isinstance(fallback_reference_features, dict):
        fallback_reference_features["extraction_mode"] = "fallback"
        fallback_reference_features["extraction_reason"] = "analysis_exception"
    fallback_reference_features = _merge_options_reference_features(fallback_reference_features, item_data)

    return {
        "label": item_data.get("label", "Furniture"),
        "description": _stabilize_description(
            label=item_data.get("label", "Furniture"),
            category=item_data.get("category") or item_data.get("category_canonical"),
            description=f"{item_data.get('label','Furniture')} with its original identity preserved.",
            dims_mm=normalize_dims_dict(provided_dims_mm or {}),
            reference_features=fallback_reference_features,
        ),
        "box_2d": item_data.get("box_2d"),
        "crop_path": None,
        "reference_features": fallback_reference_features,
        "target_key": item_data.get("target_key"),
        "source_index": item_data.get("source_index"),
        "category": item_data.get("category"),
        "category_canonical": item_data.get("category_canonical"),
        "item_id": item_data.get("item_id"),
    }
