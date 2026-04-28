import json
import os
import re
import time
from typing import Any, Callable, Optional

from PIL import Image
from application.render.reference_features_stage import (
    extract_reference_features,
    should_extract_reference_features,
)


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
                "Identify ALL discrete furniture items in this image (Sofa, Chair, Table, Lamp, Rug, Ottoman, etc.).\n"
                "**NOTE:** The background is a neutral grey (#D2D2D2) for contrast. Do not detect the background itself.\n"
                "Return a JSON list where each item has:\n"
                "- 'label': Name of the item.\n"
                "- 'box_2d': [ymin, xmin, ymax, xmax] coordinates normalized to 0-1000 scale.\n"
                "\n"
                "<CRITICAL: SORTING ORDER>\n"
                "**YOU MUST SORT THE LIST BY PHYSICAL SIZE (VOLUME) FROM LARGEST TO SMALLEST.**\n"
                "1. Largest items first (e.g., Sofa, Bed, Large Rug, Wardrobe).\n"
                "2. Medium items second (e.g., Armchair, Coffee Table, Console).\n"
                "3. Small items last (e.g., Side Table, Lamp, Vase, Decor).\n"
                "Ignore walls, windows, and floors. Focus on movable objects."
            )
            detect_model = model_name or default_model_name
            detect_timeout = max(10, int(timeout_sec or 120))
            response = call_gemini_with_failover(
                detect_model,
                [prompt, img],
                {
                    "timeout": detect_timeout,
                    "max_attempts": max(1, int(max_attempts or 1)),
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

    return [{"label": "Main Furniture"}, {"label": "Coffee Table"}, {"label": "Lounge Chair"}]


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

        if enable_text_read:
            prompt = (
                f"Analyze this image cutout of a '{label}'.\n"
                "IMPORTANT: Look specifically at the TEXT written below or near the object.\n"
                "1. **READ EXTRACT DIMENSIONS:** If there is text like 'W: 2800', 'Width 2800mm', '2800*1450', extract these numbers EXACTLY in millimeters.\n"
                "   - Support radius notation too (R, 반지름, Ø, ⌀, Φ).\n"
                "2. **LONG DESCRIPTION (50-70 words):** Describe material, color, shape, proportions, silhouette, and scale cues.\n"
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
                "Write a 50-70 word visual description (material, color, shape, proportions, silhouette, scale cues).\n"
                f"{hint_line}"
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

        desc = f"A high quality {label}."
        dims_str = ""
        resolved_dims_mm = normalize_dims_dict(provided_dims_mm or {})

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
                        dims_str = f" Dimensions: W={width_mm}mm, D={depth_mm}mm, H={height_mm}mm."
                        if not log_brief:
                            print(f"   -> [Text Read] {label}: {dims_str} (Source: {data.get('raw_text_found')})", flush=True)
                    elif radius_mm and (height_mm or depth_mm or width_mm):
                        dims_str = f" Dimensions: R={radius_mm}mm"
                        if height_mm:
                            dims_str += f", H={height_mm}mm"
                        elif depth_mm:
                            dims_str += f", D={depth_mm}mm"
                        elif width_mm:
                            dims_str += f", W={width_mm}mm"
                        dims_str += "."
                        if not log_brief:
                            print(f"   -> [Text Read] {label}: {dims_str} (Source: {data.get('raw_text_found')})", flush=True)
                    elif log_brief:
                        print(f"[Text Read] FAIL {label}", flush=True)

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

        return {
            "label": label,
            "description": desc + dims_str,
            "box_2d": box,
            "crop_path": crop_path,
            "reference_features": reference_features,
            "target_key": item_data.get("target_key"),
            "source_index": item_data.get("source_index"),
            "category": item_data.get("category"),
            "category_canonical": item_data.get("category_canonical"),
            "item_id": item_data.get("item_id"),
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
        description=f"A high quality {item_data.get('label','Furniture')}.",
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

    return {
        "label": item_data.get("label", "Furniture"),
        "description": f"A high quality {item_data.get('label','Furniture')}.",
        "box_2d": item_data.get("box_2d"),
        "crop_path": None,
        "reference_features": fallback_reference_features,
        "target_key": item_data.get("target_key"),
        "source_index": item_data.get("source_index"),
        "category": item_data.get("category"),
        "category_canonical": item_data.get("category_canonical"),
        "item_id": item_data.get("item_id"),
    }
