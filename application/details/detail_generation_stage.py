import os
import time
from typing import Callable

from PIL import Image


def _build_target_crop(original_image: Image.Image, target_box_2d) -> Image.Image | None:
    if not isinstance(target_box_2d, (list, tuple)) or len(target_box_2d) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [float(value) for value in target_box_2d]
    except Exception:
        return None
    if xmax <= xmin or ymax <= ymin:
        return None

    width, height = original_image.size
    left = max(0, min(width - 1, int((xmin / 1000.0) * width)))
    top = max(0, min(height - 1, int((ymin / 1000.0) * height)))
    right = max(left + 1, min(width, int((xmax / 1000.0) * width)))
    bottom = max(top + 1, min(height, int((ymax / 1000.0) * height)))

    pad_x = max(8, int((right - left) * 0.18))
    pad_y = max(8, int((bottom - top) * 0.18))
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(width, right + pad_x)
    bottom = min(height, bottom + pad_y)
    return original_image.crop((left, top, right, bottom))


def generate_detail_view(
    original_image_path,
    style_config,
    unique_id,
    index,
    furniture_data=None,
    *,
    materialize_input: Callable[[str | None, str], str | None],
    normalize_label_for_match: Callable[[str], str],
    allow_harassment_only_safety_settings: Callable[[], object],
    call_gemini_with_failover: Callable[..., object],
    model_name: str,
):
    img = None
    extra_imgs = []
    temp_cutout_paths = []
    cutout_labels = []
    cutout_ref_count = 0
    try:
        img = Image.open(original_image_path)
        target_ratio = style_config.get("ratio", "16:9")
        crop_lock_block = (
            "<ABSOLUTE RULE #0 — THIS IS THE SAME PHOTO>\n"
            "This output MUST be a CROPPED/REFRAMED photograph of the EXACT SAME furnished room image provided.\n"
            "You are NOT creating a new image. You are NOT restaging. You are NOT redesigning.\n"
            "Allowed operations: camera framing, crop, zoom, slight depth-of-field.\n"
            "Forbidden operations: moving/adding/removing/replacing ANY object, changing materials, changing colors, changing lighting style.\n"
            "Every pixel that is not affected by the crop/zoom MUST remain visually consistent with the input.\n"
        )

        style_name = str(style_config.get("name") or "")
        style_target_key = str(style_config.get("target_key") or "").strip()
        style_target_label = str(style_config.get("target_label") or "").strip()
        target_box_2d = style_config.get("target_box_2d")
        if not style_target_label and style_name.startswith("Detail:"):
            style_target_label = style_name.split("Detail:", 1)[1].strip()

        target_lock_block = ""
        if style_target_key or style_target_label:
            target_lock_block = (
                "<PRIMARY TARGET LOCK>\n"
                f"- TARGET LABEL: {style_target_label or 'N/A'}\n"
                f"- TARGET KEY: {style_target_key or 'N/A'}\n"
                "- This output MUST focus on the exact same target object identity from the main render.\n"
                "- Keep this target item's geometry/design signature unchanged.\n"
                "- Other objects are context only and must never replace the target.\n\n"
            )

        target_crop = _build_target_crop(img, target_box_2d)
        if target_crop is not None:
            extra_imgs.append(target_crop)
            target_lock_block += (
                "<PRIMARY TARGET SCALE LOCK>\n"
                "- The attached in-room crop shows the target exactly as it appears in the main render.\n"
                "- Match that target size, perspective, and surrounding context. Only tighten framing around it.\n"
                "- Do NOT enlarge the target beyond what a real crop/zoom from the same scene would produce.\n\n"
            )

        final_prompt = (
            f"{crop_lock_block}\n"
            f"{target_lock_block}"
            f"{style_config['prompt']}\n\n"
            "<CRITICAL: LAYOUT FREEZE (PRIORITY #0)>\n"
            "1. **DO NOT MOVE / REARRANGE ANYTHING:** Every existing furniture, lighting fixture, decor item, and their positions must remain EXACTLY the same as the input image.\n"
            "2. **NO NEW OBJECTS:** Do NOT add new objects (no extra vases, cats, books, lamps, shelves, plants, art, etc.).\n"
            "3. **NO REMOVALS:** Do NOT remove existing objects either.\n"
            "4. **CAMERA ONLY:** The close-up must be achieved ONLY by changing the camera framing/crop/zoom. Keep the scene geometry unchanged.\n\n"
            "<OUTPUT REQUIREMENTS>\n"
            "1. Generate a photorealistic high-quality detail view based on the selected camera shot.\n"
            "2. Keep the overall interior style consistent with the main furnished room.\n"
            "3. IMPORTANT: focus on the specified target area only (close-up composition).\n"
            "4. DO NOT add text, labels, logos, or watermarks.\n"
            f"OUTPUT ASPECT RATIO: {target_ratio}"
        )

        safety_settings = allow_harassment_only_safety_settings()
        content = [final_prompt, "Original Room Reality (CANVAS - DO NOT ALTER LAYOUT):", img]
        if target_crop is not None:
            content += [
                "PRIMARY TARGET IN-ROOM CROP (match this target scale and perspective):",
                target_crop,
            ]

        try:
            max_cutout_refs = 12
            max_aux_cutout_refs = 12
            target_label_norm = normalize_label_for_match(style_target_label)
            is_detail_target_mode = bool(style_name.startswith("Detail:"))

            candidates = []
            for item in furniture_data or []:
                if not isinstance(item, dict):
                    continue
                crop_path = item.get("crop_path")
                if not crop_path:
                    continue
                local_path = materialize_input(crop_path, f"detail_cutout_{len(candidates) + 1}") if isinstance(crop_path, str) else None
                if not local_path or not os.path.exists(local_path):
                    continue
                if local_path != crop_path:
                    temp_cutout_paths.append(local_path)

                try:
                    source_index_val = int(item.get("source_index") or (len(candidates) + 1))
                except Exception:
                    source_index_val = len(candidates) + 1

                candidates.append(
                    {
                        "label": str(item.get("label") or "Item"),
                        "path": local_path,
                        "target_key": str(item.get("target_key") or "").strip(),
                        "source_index": source_index_val,
                    }
                )

            target_items = []
            other_items = []
            for candidate in candidates:
                candidate_key = str(candidate.get("target_key") or "").strip()
                candidate_label_norm = normalize_label_for_match(candidate.get("label") or "")
                is_target = False
                if style_target_key and candidate_key and candidate_key == style_target_key:
                    is_target = True
                elif target_label_norm and candidate_label_norm and candidate_label_norm == target_label_norm:
                    is_target = True
                elif target_label_norm and candidate_label_norm and (
                    target_label_norm in candidate_label_norm or candidate_label_norm in target_label_norm
                ):
                    is_target = True

                if is_target:
                    target_items.append(candidate)
                else:
                    other_items.append(candidate)

            def _cutout_sort_key(row: dict):
                try:
                    idx = int(row.get("source_index") or 10**9)
                except Exception:
                    idx = 10**9
                return (idx, str(row.get("label") or ""))

            target_items.sort(key=_cutout_sort_key)
            other_items.sort(key=_cutout_sort_key)

            ordered_items = []
            seen_paths = set()

            if target_items:
                target = target_items[0]
                if target.get("path") not in seen_paths:
                    target = dict(target)
                    target["is_target"] = True
                    ordered_items.append(target)
                    seen_paths.add(target.get("path"))

            for candidate in other_items:
                path = candidate.get("path")
                if path in seen_paths:
                    continue
                item = dict(candidate)
                item["is_target"] = False
                ordered_items.append(item)
                seen_paths.add(path)

            if not ordered_items:
                for candidate in candidates:
                    path = candidate.get("path")
                    if path in seen_paths:
                        continue
                    item = dict(candidate)
                    item["is_target"] = False
                    ordered_items.append(item)
                    seen_paths.add(path)

            if is_detail_target_mode and ordered_items:
                forced_target = [item for item in ordered_items if item.get("is_target")][:1]
                aux = [item for item in ordered_items if not item.get("is_target")]
                aux_cap = max(0, min(max_aux_cutout_refs, max_cutout_refs - len(forced_target)))
                cutout_items = forced_target + aux[:aux_cap]
            else:
                cutout_items = ordered_items[:max_cutout_refs]

            cutout_labels = [str(item.get("label") or "Item") for item in cutout_items]
            cutout_ref_count = len(cutout_labels)

            for item in cutout_items:
                cutout_img = Image.open(item["path"])
                try:
                    cutout_img.thumbnail((512, 512), Image.Resampling.LANCZOS)
                except Exception:
                    pass
                extra_imgs.append(cutout_img)
                if item.get("is_target"):
                    content += [
                        f"PRIMARY TARGET CUTOUT (ABSOLUTE PRIORITY — MUST MATCH EXACT DESIGN): {item['label']}",
                        cutout_img,
                    ]
                else:
                    content += [
                        f"Secondary Furniture Cutout Reference (context only, do not override primary target): {item['label']}",
                        cutout_img,
                    ]
        except Exception:
            pass

        response = call_gemini_with_failover(
            model_name,
            content,
            {"timeout": 90},
            safety_settings,
            log_tag="Detail.Generate",
        )
        if response and hasattr(response, "candidates") and response.candidates:
            for part in response.parts:
                if hasattr(part, "inline_data"):
                    timestamp = int(time.time())
                    safe_style_name = "".join([c for c in style_config["name"] if c.isalnum()])[:20]
                    filename = f"detail_{timestamp}_{unique_id}_{index}_{safe_style_name}.png"
                    path = os.path.join("outputs", filename)
                    with open(path, "wb") as file_obj:
                        file_obj.write(part.inline_data.data)
                    return {
                        "path": path,
                        "style_name": style_config.get("name"),
                        "cutout_ref_count": cutout_ref_count,
                        "cutout_ref_labels": cutout_labels,
                    }
        return None
    except Exception as exc:
        print(f"!! Detail Generation Error: {exc}", flush=True)
        return None
    finally:
        try:
            if img:
                img.close()
        except Exception:
            pass
        for image in extra_imgs:
            try:
                image.close()
            except Exception:
                pass
        for path in temp_cutout_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
