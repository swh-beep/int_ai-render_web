import json
import os
import time
from typing import Callable

from PIL import Image, ImageDraw, ImageOps
from application.render.postprocess_support import category_match_family
from shared.image_canvas import get_image_size, match_aspect_to_ratio


def _normalize_ratio_string(value: str | None, default: str = "4:5") -> str:
    text = str(value or "").strip()
    if ":" not in text:
        return default
    left, right = text.split(":", 1)
    try:
        width = max(1, int(left))
        height = max(1, int(right))
    except Exception:
        return default
    return f"{width}:{height}"


def _parse_ratio(value: str | None) -> tuple[int, int]:
    text = _normalize_ratio_string(value)
    left, right = text.split(":", 1)
    width = max(1, int(left))
    height = max(1, int(right))
    return (width, height)


def _normalize_generated_detail_ratio(
    image_path: str,
    *,
    requested_ratio: str,
    ratio_tol: float = 0.02,
    max_crop_fraction: float = 0.20,
) -> str | None:
    ratio_w, ratio_h = _parse_ratio(requested_ratio)
    expected_ratio = float(ratio_w) / float(ratio_h)

    width, height = get_image_size(image_path, exif_safe=True)
    if width <= 0 or height <= 0:
        return None

    current_ratio = width / height
    if abs(current_ratio - expected_ratio) <= ratio_tol:
        return image_path

    if current_ratio > expected_ratio:
        retained_fraction = expected_ratio / current_ratio if current_ratio > 0 else 0.0
    else:
        retained_fraction = current_ratio / expected_ratio if expected_ratio > 0 else 0.0
    crop_fraction = max(0.0, 1.0 - retained_fraction)
    if crop_fraction > max_crop_fraction:
        return None

    return match_aspect_to_ratio(image_path, expected_ratio)


def _coerce_box_2d(value) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [float(v) for v in value]
    except Exception:
        return None
    if ymax <= ymin or xmax <= xmin:
        return None
    return [ymin, xmin, ymax, xmax]


def _box_center(box_2d: list[float] | None) -> tuple[float, float] | None:
    box = _coerce_box_2d(box_2d)
    if box is None:
        return None
    ymin, xmin, ymax, xmax = box
    return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)


def _is_full_frame_box(box_2d: list[float] | None) -> bool:
    box = _coerce_box_2d(box_2d)
    if box is None:
        return False
    ymin, xmin, ymax, xmax = box
    return ymin <= 1.0 and xmin <= 1.0 and ymax >= 999.0 and xmax >= 999.0


_RENDER_LOCALIZED_BOX_SOURCES = frozenset(
    {
        "detail_current_image_analysis",
        "main_render",
        "selected_variant_review",
    }
)


def _has_localized_render_box(target_item: dict | None) -> bool:
    if not isinstance(target_item, dict):
        return False
    box_source = str(target_item.get("box_source") or "").strip().lower()
    return box_source in _RENDER_LOCALIZED_BOX_SOURCES


def _eligible_crop_box_2d(target_item: dict | None) -> list[float] | None:
    if not isinstance(target_item, dict):
        return None

    box_2d = _coerce_box_2d(target_item.get("box_2d"))
    if box_2d is None or _is_full_frame_box(box_2d):
        return None

    if not _has_localized_render_box(target_item):
        return None

    return box_2d


def _context_distance_score(candidate: dict, target_item: dict | None) -> float:
    if not isinstance(candidate, dict) or not isinstance(target_item, dict):
        return float("inf")

    target_center = _box_center(target_item.get("box_2d")) or _box_center(target_item.get("source_box_2d"))
    candidate_center = _box_center(candidate.get("box_2d")) or _box_center(candidate.get("source_box_2d"))
    if target_center is None or candidate_center is None:
        return float("inf")

    dx = target_center[0] - candidate_center[0]
    dy = target_center[1] - candidate_center[1]
    return (dx * dx + dy * dy) ** 0.5


def _compact_prompt_metadata(value, *, max_chars: int = 180) -> str | None:
    if value in (None, "", [], {}):
        return None
    try:
        text = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        text = str(value)
    text = str(text).strip()
    if not text:
        return None
    if len(text) > max_chars:
        return text[: max_chars - 3].rstrip() + "..."
    return text


def _target_family(item: dict | None) -> str:
    item = item if isinstance(item, dict) else {}
    identity_family = ((item.get("product_identity") or {}).get("family") if isinstance(item.get("product_identity"), dict) else None)
    if identity_family:
        return str(identity_family).strip().lower().replace("-", "_").replace(" ", "_")
    profile_family = ((item.get("identity_profile") or {}).get("family") if isinstance(item.get("identity_profile"), dict) else None)
    if profile_family:
        return str(profile_family).strip().lower().replace("-", "_").replace(" ", "_")
    resolved_family = category_match_family(item.get("category_canonical") or item.get("category") or item.get("label"))
    if resolved_family:
        return str(resolved_family).strip().lower().replace("-", "_").replace(" ", "_")
    for candidate in (item.get("category_canonical"), item.get("category"), item.get("label")):
        text = str(candidate or "").strip().lower().replace("-", "_").replace(" ", "_")
        if text:
            return text
    return ""


def _find_target_item(style_config: dict, furniture_data, normalize_label_for_match: Callable[[str], str]) -> dict | None:
    style_name = str(style_config.get("name") or "")
    style_target_key = str(style_config.get("target_key") or "").strip()
    style_target_label = str(style_config.get("target_label") or "").strip()
    if not style_target_label and style_name.startswith("Detail:"):
        style_target_label = style_name.split("Detail:", 1)[1].strip()
    target_label_norm = normalize_label_for_match(style_target_label)

    for item in furniture_data or []:
        if not isinstance(item, dict):
            continue
        candidate_key = str(item.get("target_key") or "").strip()
        if style_target_key and candidate_key == style_target_key:
            return item

    for item in furniture_data or []:
        if not isinstance(item, dict):
            continue
        candidate_label = normalize_label_for_match(str(item.get("label") or ""))
        if target_label_norm and candidate_label == target_label_norm:
            return item

    for item in furniture_data or []:
        if not isinstance(item, dict):
            continue
        candidate_label = normalize_label_for_match(str(item.get("label") or ""))
        if target_label_norm and candidate_label and (
            target_label_norm in candidate_label or candidate_label in target_label_norm
        ):
            return item
    return None


def _box_to_pixels(box_2d: list[float], image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = image_size
    ymin, xmin, ymax, xmax = box_2d
    left = int(max(0.0, min(1000.0, xmin)) / 1000.0 * width)
    top = int(max(0.0, min(1000.0, ymin)) / 1000.0 * height)
    right = int(max(0.0, min(1000.0, xmax)) / 1000.0 * width)
    bottom = int(max(0.0, min(1000.0, ymax)) / 1000.0 * height)
    left = max(0, min(width - 1, left))
    top = max(0, min(height - 1, top))
    right = max(left + 1, min(width, right))
    bottom = max(top + 1, min(height, bottom))
    return (left, top, right, bottom)


def _clamp_bounds(bounds: tuple[float, float, float, float], image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = image_size
    left, top, right, bottom = bounds
    left = max(0.0, min(float(width - 1), left))
    top = max(0.0, min(float(height - 1), top))
    right = max(left + 1.0, min(float(width), right))
    bottom = max(top + 1.0, min(float(height), bottom))
    return (int(round(left)), int(round(top)), int(round(right)), int(round(bottom)))


def _expand_bounds(
    bounds: tuple[int, int, int, int],
    image_size: tuple[int, int],
    *,
    family: str,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bounds
    width = max(1.0, float(right - left))
    height = max(1.0, float(bottom - top))

    pad_left = width * 0.35
    pad_right = width * 0.35
    pad_top = height * 0.30
    pad_bottom = height * 0.35

    if family in {"ceiling_light", "wall_light", "light", "pendant", "chandelier", "sconce"}:
        pad_left = width * 0.55
        pad_right = width * 0.55
        pad_top = max(height * 0.55, image_size[1] * 0.08)
        pad_bottom = height * 1.60
    elif family == "rug":
        pad_left = width * 0.18
        pad_right = width * 0.18
        pad_top = height * 0.80
        pad_bottom = height * 0.25
    elif family in {"sofa", "lounge_sofa", "lounge_seating", "chair", "lounge_chair", "armchair", "loveseat"}:
        pad_left = width * 0.35
        pad_right = width * 0.35
        pad_top = height * 0.40
        pad_bottom = height * 0.45
    elif family in {"table", "desk", "storage"}:
        pad_left = width * 0.32
        pad_right = width * 0.32
        pad_top = height * 0.35
        pad_bottom = height * 0.38

    return _clamp_bounds(
        (
            float(left) - pad_left,
            float(top) - pad_top,
            float(right) + pad_right,
            float(bottom) + pad_bottom,
        ),
        image_size,
    )


def _fit_bounds_to_ratio(
    bounds: tuple[int, int, int, int],
    image_size: tuple[int, int],
    *,
    target_ratio: tuple[int, int],
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bounds
    img_w, img_h = image_size
    target_w, target_h = target_ratio
    desired_ratio = float(target_w) / float(target_h)

    crop_w = max(1.0, float(right - left))
    crop_h = max(1.0, float(bottom - top))
    center_x = (float(left) + float(right)) / 2.0
    center_y = (float(top) + float(bottom)) / 2.0

    if crop_w / crop_h > desired_ratio:
        crop_h = crop_w / desired_ratio
    else:
        crop_w = crop_h * desired_ratio

    if crop_w > float(img_w):
        crop_w = float(img_w)
        crop_h = crop_w / desired_ratio
    if crop_h > float(img_h):
        crop_h = float(img_h)
        crop_w = crop_h * desired_ratio
    if crop_w > float(img_w):
        crop_w = float(img_w)
        crop_h = crop_w / desired_ratio

    left = center_x - crop_w / 2.0
    right = center_x + crop_w / 2.0
    top = center_y - crop_h / 2.0
    bottom = center_y + crop_h / 2.0

    if left < 0.0:
        right -= left
        left = 0.0
    if right > img_w:
        left -= (right - img_w)
        right = float(img_w)
    if top < 0.0:
        bottom -= top
        top = 0.0
    if bottom > img_h:
        top -= (bottom - img_h)
        bottom = float(img_h)

    return _clamp_bounds((left, top, right, bottom), image_size)


def _render_crop_detail(
    original_image_path: str,
    style_config: dict,
    unique_id: str,
    index: int,
    target_item: dict,
) -> dict | None:
    box_2d = _eligible_crop_box_2d(target_item)
    if box_2d is None:
        return None

    style_name = str(style_config.get("name") or f"Detail{index}")
    family = _target_family(target_item)
    target_ratio = _parse_ratio(style_config.get("ratio"))

    with Image.open(original_image_path) as img:
        canvas = ImageOps.exif_transpose(img).convert("RGB")
        bounds = _box_to_pixels(box_2d, canvas.size)
        bounds = _expand_bounds(bounds, canvas.size, family=family)
        bounds = _fit_bounds_to_ratio(bounds, canvas.size, target_ratio=target_ratio)
        crop = canvas.crop(bounds)

        max_edge = max(crop.size)
        target_max = 1600
        if max_edge < target_max:
            scale = float(target_max) / float(max_edge)
            crop = crop.resize(
                (max(1, int(crop.size[0] * scale)), max(1, int(crop.size[1] * scale))),
                Image.Resampling.LANCZOS,
            )

        timestamp = int(time.time())
        safe_style_name = "".join([c for c in style_name if c.isalnum()])[:20] or f"detail{index}"
        filename = f"detail_{timestamp}_{unique_id}_{index}_{safe_style_name}.png"
        path = os.path.join("outputs", filename)
        crop.save(path, "PNG")

    return {
        "path": path,
        "style_name": style_name,
        "aspect_ratio": _normalize_ratio_string(style_config.get("ratio")),
        "cutout_ref_count": 0,
        "cutout_ref_labels": [],
        "generation_mode": "crop_extract",
        "crop_bounds_px": list(bounds),
    }


def _build_simple_scene_detail_prompt(target_label: str) -> str:
    clean_label = str(target_label or "the selected furniture or decor").strip() or "the selected furniture or decor"
    return (
        "<TASK>\n"
        f"Create one photorealistic editorial detail photograph focused on the {clean_label} area in this exact finished interior.\n\n"
        "<SOURCE OF TRUTH>\n"
        "Use the provided main room image as the only visual source of truth. This is not a redesign task.\n\n"
        "<LOCKED SCENE RULES>\n"
        "- Keep every furniture/decor item's shape, count, placement, scale, material, and color unchanged.\n"
        "- Keep the room architecture, wall/floor/window locations, lighting direction, shadows, and overall tone unchanged.\n"
        "- Do not add, remove, replace, duplicate, resize, recolor, or rearrange any object.\n"
        "- The target must remain the same object in the same physical location, with nearby objects preserved as context.\n\n"
        "<CAMERA>\n"
        "Shoot a close editorial detail from a natural in-room camera position. You may crop/reframe or move the virtual camera slightly "
        "to make the target read clearly, but the scene itself must stay fixed.\n\n"
        "<STYLE>\n"
        "High-end interior magazine photography: natural depth of field, clean composition, realistic texture, balanced shadows, no text, no watermark."
    )


def _is_gpt_image_model_name(model_name: str | None) -> bool:
    return str(model_name or "").strip().lower().startswith("gpt-image-")


def _build_gpt_image_detail_prompt(style_config: dict, target_label: str) -> str:
    style_name = str((style_config or {}).get("name") or "").strip()
    clean_label = str(target_label or "").strip()
    if not clean_label and style_name.startswith("Detail:"):
        clean_label = style_name.split("Detail:", 1)[1].strip()
    clean_label = clean_label or "the selected furniture or decor"

    camera_mode = str((style_config or {}).get("camera_mode") or "").strip().lower()
    focus_side = str((style_config or {}).get("focus_side") or "").strip().lower()
    is_overview = camera_mode == "overview_angle" or style_name == "High Angle Overview"
    is_side = camera_mode == "side_angle" or style_name.startswith("Side Composition")

    if is_overview:
        return (
            "Create a high-angle editorial photograph of this exact finished interior. "
            "Make it feel like a real interior magazine photo with natural light, shadows, and material texture. "
            "Do not change the room structure, furniture/decor shape, detail, count, color, material, scale, or placement. "
            "No text or watermark."
        )

    if is_side:
        side_text = f" from the {focus_side} side" if focus_side in {"left", "right"} else ""
        return (
            f"Create a side-composition editorial photograph{side_text} of this exact finished interior. "
            "Keep the same room and furniture arrangement while changing only the camera viewpoint. "
            "Do not change furniture/decor shape, detail, count, color, material, scale, or placement. "
            "No text or watermark."
        )

    return (
        f"Create a detailed editorial photograph focused on the {clean_label} area in this exact finished interior. "
        "Make it feel like a real interior magazine photo with natural light, shadows, depth, and material texture. "
        "The target and every visible surrounding furniture/decor item must remain the same original object from the input image. "
        "Preserve all visible object shapes, silhouettes, details, counts, colors, materials, scale relationships, and placements exactly, including background or support furniture near the target. "
        "Do not change the room structure, wall/floor/window positions, lighting direction, shadows, furniture/decor shape, detail, count, color, material, scale, or placement. "
        "Do not redesign, simplify, replace, remove, add, duplicate, or rearrange any visible object. "
        "No text or watermark."
    )


def generate_detail_view(
    original_image_path,
    style_config,
    unique_id,
    index,
    furniture_data=None,
    *,
    prefer_crop_extract: bool = False,
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
        style_name = str(style_config.get("name") or "")
        target_item = _find_target_item(style_config, furniture_data, normalize_label_for_match)
        if prefer_crop_extract and style_name.startswith("Detail:"):
            return _render_crop_detail(original_image_path, style_config, unique_id, index, target_item or {})

        img = Image.open(original_image_path)
        target_ratio = _normalize_ratio_string(style_config.get("ratio"))
        camera_mode = str(style_config.get("camera_mode") or "").strip().lower()
        focus_side = str(style_config.get("focus_side") or "").strip().lower()
        if prefer_crop_extract and style_name.startswith("Detail:"):
            scene_lock_block = (
                "<ABSOLUTE RULE #0 THIS IS THE SAME PHOTO>\n"
                "This output MUST be a CROPPED/REFRAMED photograph of the EXACT SAME furnished room image provided.\n"
                "You are NOT creating a new image. You are NOT restaging. You are NOT redesigning.\n"
                "Allowed operations: camera framing, crop, zoom, slight depth-of-field.\n"
                "Forbidden operations: moving/adding/removing/replacing ANY object, changing materials, changing colors, changing lighting style.\n"
                "Every pixel that is not affected by the crop/zoom MUST remain visually consistent with the input.\n"
            )
            camera_lock_line = "4. **CAMERA ONLY:** The close-up must be achieved ONLY by changing the camera framing/crop/zoom. Keep the scene geometry unchanged.\n\n"
        elif camera_mode == "side_angle":
            scene_lock_block = (
                "<SCENE LOCK: SAME ROOM, NEW SIDE CAMERA>\n"
                "Create a NEW side-angle editorial photograph inside the EXACT SAME finished room.\n"
                "Priority order: (1) visibly new side camera viewpoint, (2) same furniture identities, "
                "(3) same relative furniture placement and room architecture.\n"
                "Do NOT copy the source frame, do NOT use the same centered front-facing camera, and do NOT solve this as a crop/zoom.\n"
                "The camera must move laterally enough to create real parallax, changed occlusion, and visible side planes.\n"
                "Side-specific composition is allowed to crop out or minimize the opposite side of the room; do NOT force every object from the source image to remain visible.\n"
                "If a side-focus composition mask is provided, use it only to choose framing weight. Never duplicate, mirror, or copy furniture because of the mask.\n"
                "Keep the room and objects recognizable, but the viewpoint must be materially different from the input image.\n"
            )
            camera_lock_line = (
                "4. **SIDE CAMERA REQUIRED:** Change camera position and yaw substantially enough that the output cannot be mistaken for the source frame. "
                "Keep object placement fixed in the room, but allow foreground/background occlusion to change naturally from the new side viewpoint.\n\n"
            )
        else:
            scene_lock_block = (
                "<SCENE LOCK: SAME ROOM, NEW CAMERA>\n"
                "Create a NEW editorial close-up photographed inside the EXACT SAME finished room.\n"
                "You may move the camera to a nearby in-room standing-height viewpoint and change focal length to create a fresh composition.\n"
                "Keep the architecture, furniture placement, object scale, object identities, materials, and lighting direction consistent with the source image.\n"
                "Do NOT turn this into a simple digital crop of the input.\n"
            )
            camera_lock_line = "4. **NEW IN-ROOM VIEWPOINT IS ALLOWED:** You may change camera position, yaw, pitch, and focal length slightly, but the room layout and object placement must stay fixed.\n\n"

        style_target_key = str(style_config.get("target_key") or "").strip()
        style_target_label = str(style_config.get("target_label") or "").strip()
        if not style_target_label and style_name.startswith("Detail:"):
            style_target_label = style_name.split("Detail:", 1)[1].strip()

        requested_timeout_sec = style_config.get("timeout_sec")
        try:
            request_timeout_sec = float(requested_timeout_sec) if requested_timeout_sec is not None else 120.0
        except Exception:
            request_timeout_sec = 120.0
        if request_timeout_sec <= 0.0:
            return None

        if _is_gpt_image_model_name(model_name):
            response = call_gemini_with_failover(
                model_name,
                [
                    _build_gpt_image_detail_prompt(style_config, style_target_label or style_name),
                    "Main furnished room image:",
                    img,
                ],
                {
                    "timeout": max(1.0, min(120.0, request_timeout_sec)),
                    "aspect_ratio": target_ratio,
                    "max_attempts": 1,
                },
                allow_harassment_only_safety_settings(),
                log_tag="Detail.Generate.GPTImage",
            )
            if response and hasattr(response, "candidates") and response.candidates:
                for part in response.parts:
                    if hasattr(part, "inline_data"):
                        timestamp = int(time.time())
                        safe_style_name = "".join([c for c in style_name if c.isalnum()])[:20] or f"detail{index}"
                        filename = f"detail_{timestamp}_{unique_id}_{index}_{safe_style_name}.png"
                        path = os.path.join("outputs", filename)
                        with open(path, "wb") as file_obj:
                            file_obj.write(part.inline_data.data)
                        normalized_path = _normalize_generated_detail_ratio(
                            path,
                            requested_ratio=target_ratio,
                        )
                        if normalized_path is None:
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                            continue
                        if normalized_path != path:
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                            path = normalized_path
                        return {
                            "path": path,
                            "style_name": style_name,
                            "aspect_ratio": target_ratio,
                            "cutout_ref_count": 0,
                            "cutout_ref_labels": [],
                            "generation_mode": "gpt_image_detail",
                        }
            return None

        if bool(style_config.get("simple_scene_detail")):
            response = call_gemini_with_failover(
                model_name,
                [
                    _build_simple_scene_detail_prompt(style_target_label or style_name),
                    "Main furnished room image:",
                    img,
                ],
                {
                    "timeout": max(1.0, min(120.0, request_timeout_sec)),
                    "aspect_ratio": target_ratio,
                    "thinking_level": "high",
                    "include_thoughts": False,
                },
                allow_harassment_only_safety_settings(),
                log_tag="Detail.Generate.Simple",
            )
            if response and hasattr(response, "candidates") and response.candidates:
                for part in response.parts:
                    if hasattr(part, "inline_data"):
                        timestamp = int(time.time())
                        safe_style_name = "".join([c for c in style_name if c.isalnum()])[:20] or f"detail{index}"
                        filename = f"detail_{timestamp}_{unique_id}_{index}_{safe_style_name}.png"
                        path = os.path.join("outputs", filename)
                        with open(path, "wb") as file_obj:
                            file_obj.write(part.inline_data.data)
                        normalized_path = _normalize_generated_detail_ratio(
                            path,
                            requested_ratio=target_ratio,
                        )
                        if normalized_path is None:
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                            continue
                        if normalized_path != path:
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                            path = normalized_path
                        return {
                            "path": path,
                            "style_name": style_name,
                            "aspect_ratio": target_ratio,
                            "cutout_ref_count": 0,
                            "cutout_ref_labels": [],
                            "generation_mode": "simple_scene_detail",
                        }
            return None

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
        target_anchor_block = ""
        if isinstance(target_item, dict):
            target_box = _coerce_box_2d(target_item.get("box_2d"))
            source_box = _coerce_box_2d(target_item.get("source_box_2d"))
            target_family = _target_family(target_item)
            target_anchor_lines = []
            if target_box is not None:
                target_anchor_lines.append(f"- TARGET BOX IN SOURCE IMAGE (0-1000): {target_box}")
            if source_box is not None:
                target_anchor_lines.append(f"- ORIGINAL CACHED TARGET BOX (0-1000): {source_box}")
            box_source = str(target_item.get("box_source") or "").strip()
            if box_source:
                target_anchor_lines.append(f"- BOX SOURCE: {box_source}")
            if target_family:
                target_anchor_lines.append(f"- TARGET FAMILY: {target_family}")
            placement_contract = _compact_prompt_metadata(target_item.get("placement_contract"))
            if placement_contract:
                target_anchor_lines.append(f"- PLACEMENT CONTRACT: {placement_contract}")
            layout_envelope = _compact_prompt_metadata(target_item.get("layout_envelope"))
            if layout_envelope:
                target_anchor_lines.append(f"- LAYOUT ENVELOPE: {layout_envelope}")
            if target_anchor_lines:
                target_anchor_block = "<TARGET ANCHOR>\n" + "\n".join(target_anchor_lines) + "\n\n"

        is_angle_style = camera_mode in {"overview_angle", "side_angle"} or style_name == "High Angle Overview" or style_name.startswith("Side Composition")
        if is_angle_style:
            output_focus_line = (
                "3. IMPORTANT: this is a room angle shot, not an object close-up. "
                "Keep enough room context visible to clearly read the camera direction, room geometry, and spatial relationship.\n"
            )
        else:
            output_focus_line = (
                "3. IMPORTANT: focus on the specified target area only and make it read like a dedicated editorial shot of that object in the room.\n"
            )

        final_prompt = (
            f"{scene_lock_block}\n"
            f"{target_lock_block}"
            f"{target_anchor_block}"
            f"{style_config['prompt']}\n\n"
            "<CRITICAL: LAYOUT FREEZE (PRIORITY #0)>\n"
            "1. **DO NOT MOVE / REARRANGE ANYTHING:** Every existing furniture, lighting fixture, decor item, and their positions must remain EXACTLY the same as the input image.\n"
            "2. **NO NEW OBJECTS:** Do NOT add new objects (no extra vases, cats, books, lamps, shelves, plants, art, etc.).\n"
            "3. **NO REMOVALS:** Do NOT remove existing objects either.\n"
            "3b. **PRESERVE THE MAIN-SHOT LAYOUT:** Keep the target object anchored to the same physical footprint, neighboring objects, wall/floor relationship, and room geometry seen in the main render.\n"
            f"{camera_lock_line}"
            "<OUTPUT REQUIREMENTS>\n"
            "1. Generate a photorealistic high-quality detail view based on the selected camera shot.\n"
            "2. Keep the overall interior style consistent with the main furnished room.\n"
            f"{output_focus_line}"
            "4. DO NOT add text, labels, logos, or watermarks.\n"
            f"OUTPUT ASPECT RATIO: {target_ratio}"
        )

        safety_settings = allow_harassment_only_safety_settings()
        content = [final_prompt, "Original Room Reality (scene anchor, keep layout stable):", img]

        if camera_mode == "side_angle" and focus_side in {"left", "right"}:
            try:
                mask_width, mask_height = 768, 432
                mask = Image.new("RGB", (mask_width, mask_height), color=(72, 72, 72))
                draw = ImageDraw.Draw(mask)
                if focus_side == "left":
                    draw.rectangle((0, 0, int(mask_width * 0.70), mask_height), fill=(245, 245, 245))
                    draw.rectangle((int(mask_width * 0.70), 0, mask_width, mask_height), fill=(98, 98, 98))
                else:
                    draw.rectangle((0, 0, int(mask_width * 0.30), mask_height), fill=(98, 98, 98))
                    draw.rectangle((int(mask_width * 0.30), 0, mask_width, mask_height), fill=(245, 245, 245))
                extra_imgs.append(mask)
                content += [
                    (
                        f"Side Focus Composition Mask ({focus_side.upper()} side target): "
                        "white area is the dominant source-image side, dark area is peripheral/cropped. "
                        "This is only a framing guide, not an object or furniture reference."
                    ),
                    mask,
                ]
            except Exception:
                pass

        try:
            max_cutout_refs = 12
            max_detail_aux_cutout_refs = 2
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
                        "box_2d": _coerce_box_2d(item.get("box_2d")),
                        "source_box_2d": _coerce_box_2d(item.get("source_box_2d")),
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
            if is_detail_target_mode and target_items:
                target_anchor = target_items[0]
                other_items.sort(
                    key=lambda row: (
                        _context_distance_score(row, target_anchor),
                        *_cutout_sort_key(row),
                    )
                )
            else:
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
                aux_cap = max(0, min(max_detail_aux_cutout_refs, max_cutout_refs - len(forced_target)))
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
                        f"PRIMARY TARGET CUTOUT (ABSOLUTE PRIORITY, MUST MATCH EXACT DESIGN): {item['label']}",
                        cutout_img,
                    ]
                else:
                    content += [
                        f"Secondary Furniture Cutout Reference (context only, do not override primary target): {item['label']}",
                        cutout_img,
                    ]
        except Exception:
            pass

        requested_ratio = target_ratio
        response = call_gemini_with_failover(
            model_name,
            content,
            {
                "timeout": max(1.0, min(120.0, request_timeout_sec)),
                "aspect_ratio": requested_ratio,
                "thinking_level": "high",
                "include_thoughts": False,
            },
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
                    normalized_path = _normalize_generated_detail_ratio(
                        path,
                        requested_ratio=requested_ratio,
                    )
                    if normalized_path is None:
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        continue
                    if normalized_path != path:
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        path = normalized_path
                    return {
                        "path": path,
                        "style_name": style_config.get("name"),
                        "aspect_ratio": requested_ratio,
                        "cutout_ref_count": cutout_ref_count,
                        "cutout_ref_labels": cutout_labels,
                        "generation_mode": "model_regeneration",
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
