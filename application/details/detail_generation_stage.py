import json
import os
import time
from typing import Callable

from PIL import Image, ImageOps
from application.details.detail_angle_quality import (
    assess_angle_camera_guide,
    assess_angle_candidate,
)
from application.render.curtain_material_stage import CURTAIN_BLACKOUT_PERCENT, CURTAIN_DETAIL_MODE
from application.render.postprocess_support import category_match_family
from application.render.white_balance_correction import apply_reference_relative_white_balance
from shared.image_canvas import get_image_size, match_aspect_to_ratio

DETAIL_IMAGE_REQUEST_TIMEOUT_CAP_SEC = 180.0
DETAIL_ANGLE_QC_MAX_ATTEMPTS = max(1, int(os.getenv("DETAIL_ANGLE_QC_MAX_ATTEMPTS", "5") or "5"))
DETAIL_INTERNAL_ANGLE_DIRECT_MAX_ATTEMPTS = max(
    1,
    int(os.getenv("DETAIL_INTERNAL_ANGLE_DIRECT_MAX_ATTEMPTS", "2") or "2"),
)
DETAIL_INTERNAL_ANGLE_TWO_STAGE_MAX_ATTEMPTS = max(
    1,
    int(os.getenv("DETAIL_INTERNAL_ANGLE_TWO_STAGE_MAX_ATTEMPTS", "2") or "2"),
)
DETAIL_INTERNAL_ANGLE_GUIDE_MAX_ATTEMPTS = max(
    1,
    int(os.getenv("DETAIL_INTERNAL_ANGLE_GUIDE_MAX_ATTEMPTS", "2") or "2"),
)
DETAIL_CROP_MIN_SOURCE_WIDTH_PX = max(1, int(os.getenv("DETAIL_CROP_MIN_SOURCE_WIDTH_PX", "1280") or "1280"))
DETAIL_CROP_MIN_SOURCE_HEIGHT_PX = max(1, int(os.getenv("DETAIL_CROP_MIN_SOURCE_HEIGHT_PX", "1600") or "1600"))


def _remove_file_quietly(path: str | None) -> None:
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


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
        "product_reference_localization",
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


def _enforce_minimum_crop_bounds(
    bounds: tuple[int, int, int, int],
    image_size: tuple[int, int],
    *,
    target_ratio: tuple[int, int],
    min_width_px: int = DETAIL_CROP_MIN_SOURCE_WIDTH_PX,
    min_height_px: int = DETAIL_CROP_MIN_SOURCE_HEIGHT_PX,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = bounds
    img_w, img_h = image_size
    target_w, target_h = target_ratio
    desired_ratio = float(target_w) / float(target_h)

    crop_w = max(float(right - left), float(min_width_px))
    crop_h = max(float(bottom - top), float(min_height_px))
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
        left -= right - img_w
        right = float(img_w)
    if top < 0.0:
        bottom -= top
        top = 0.0
    if bottom > img_h:
        top -= bottom - img_h
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
        bounds = _enforce_minimum_crop_bounds(bounds, canvas.size, target_ratio=target_ratio)
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
        "Use a source-constrained crop/reframe from the main image camera. You may tighten framing, crop, slightly zoom, and add subtle depth of field "
        "to make the target read clearly, but do not create a new camera angle that reveals unseen sides of furniture.\n\n"
        "<STYLE>\n"
        "High-end interior magazine photography: natural depth of field, clean composition, realistic texture, balanced shadows, no text, no watermark."
    )


def _is_gpt_image_model_name(model_name: str | None) -> bool:
    return str(model_name or "").strip().lower().startswith("gpt-image-")


_DETAIL_CAMERA_RECIPES = (
    "Camera recipe: tight vertical crop from the left side, target closer to camera with original room context softly behind it.",
    "Camera recipe: stable standing-height crop weighted toward the right side, target off-center without changing object direction.",
    "Camera recipe: low camera height near floor or table level, looking slightly upward toward the target.",
    "Camera recipe: close crop, target larger in frame, no artificial obstruction.",
    "Camera recipe: natural side-light crop, target near one vertical third of the frame.",
    "Camera recipe: medium-distance contextual crop, target visible with two nearby original objects.",
)

_NO_FAKE_FOREGROUND_GUARD = (
    "Do not invent blurred foreground panels, curtains, doorframes, wall edges, or obstruction strips. "
)


def _coerce_positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _detail_camera_recipe(style_config: dict, shot_index: int | None) -> str:
    recipe_index = (
        _coerce_positive_int((style_config or {}).get("detail_index"))
        or _coerce_positive_int((style_config or {}).get("source_index"))
        or _coerce_positive_int(shot_index)
        or 1
    )
    return _DETAIL_CAMERA_RECIPES[(recipe_index - 1) % len(_DETAIL_CAMERA_RECIPES)]


_SMALL_DETAIL_TARGET_HINTS = {
    "accessory",
    "art",
    "book",
    "books",
    "candle",
    "candles",
    "cushion",
    "decor",
    "decoration",
    "decorative object",
    "framed art",
    "framed print",
    "keyboard",
    "laptop",
    "mouse",
    "object",
    "painting",
    "pillow",
    "plant",
    "poster",
    "print",
    "sculpture",
    "shelf decor",
    "small accessory",
    "small decor",
    "small object",
    "table decor",
    "tray",
    "vase",
    "wall art",
    "wall decor",
}


def _normalized_prompt_key(value) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").replace("-", " ").split())


def _is_small_decor_detail_target(style_config: dict, clean_label: str) -> bool:
    keys = {
        _normalized_prompt_key(clean_label),
        _normalized_prompt_key((style_config or {}).get("target_category")),
        _normalized_prompt_key((style_config or {}).get("target_category_canonical")),
    }
    for key in keys:
        if not key:
            continue
        if key in _SMALL_DETAIL_TARGET_HINTS:
            return True
        if any(f" {hint} " in f" {key} " for hint in _SMALL_DETAIL_TARGET_HINTS):
            return True
    return False


def _build_gpt_image_detail_prompt(style_config: dict, target_label: str, shot_index: int | None = None) -> str:
    style_name = str((style_config or {}).get("name") or "").strip()
    clean_label = str(target_label or "").strip()
    if not clean_label and style_name.startswith("Detail:"):
        clean_label = style_name.split("Detail:", 1)[1].strip()
    clean_label = clean_label or "the selected furniture or decor"

    camera_mode = str((style_config or {}).get("camera_mode") or "").strip().lower()
    focus_side = str((style_config or {}).get("focus_side") or "").strip().lower()
    is_overview = camera_mode == "overview_angle" or style_name == "High Angle Overview"
    is_side = camera_mode == "side_angle" or style_name.startswith("Side Composition")
    angle_pose_guard = (
        "Preserve each furniture/decor item's real world-space footprint, scale, identity, material, and physical orientation in the room. "
        "Because the camera moves, screen positions, visible side planes, perspective, and occlusions MUST change consistently with that camera movement. "
        "Do not keep furniture visually front-facing while rotating or rebuilding only the room. "
    )
    stability_guard = (
        "The camera may crop or hide objects, but it must never move any object into a new place. "
        "If an object is not visible in the safer reframe, leave it out of frame instead of relocating it. "
        "All objects must stay anchored to their original wall/floor/window relationships, facing direction, footprint, and nearby-object distances. "
    )

    if is_overview:
        return (
            "Using the provided image as the source room, create a genuine nearby high-angle camera view of this exact space. "
            "Move the camera higher than the main image and pitch it downward enough to reveal more top surfaces and floor planes. "
            "This must be a new camera viewpoint, not a crop, zoom, or source reframe. "
            "Keep the room layout and every visible furniture/decor item's position, shape, size, count, color, material, and nearby relationships unchanged. "
            f"{angle_pose_guard}"
            "Do not move, replace, resize, duplicate, restage, redesign, or reinterpret anything. "
            "Do not warp walls, windows, ceiling lines, doorways, mirrors, stairs, or built-in architecture. "
            "No crop-only solution; real perspective change is required. "
            "No text or watermark."
        )

    if is_side:
        side_text = "left" if focus_side == "left" else "right" if focus_side == "right" else "requested"
        foreground_parallax_side = (
            "right"
            if side_text == "left"
            else "left"
            if side_text == "right"
            else "the opposite screen direction"
        )
        return (
            f"Using the provided image as the source room, create a genuine nearby {side_text}-side camera viewpoint. "
            f"Translate the camera toward the {side_text} side of the source viewpoint and yaw gently back into the room. "
            f"{side_text.upper()} means the physical camera-body location, not which side of the room dominates the frame. "
            f"Use a clear 12-18% room-width lateral move plus a 15-25 degree yaw; nearby furniture should shift toward "
            f"screen-{foreground_parallax_side.upper()} relative to the far background. "
            "The result must have real parallax, changed occlusions, and newly visible side planes. "
            "This must be a new side camera viewpoint, not a crop, zoom, or source reframe. "
            f"{_NO_FAKE_FOREGROUND_GUARD}"
            "Keep the room layout and every visible furniture/decor item's position, shape, size, count, color, material, and nearby relationships unchanged. "
            f"{angle_pose_guard}"
            "Do not move, replace, resize, duplicate, restage, redesign, or reinterpret anything. "
            "Do not warp walls, windows, ceiling lines, doorways, mirrors, stairs, or built-in architecture. "
            "If only the background rotates while furniture remains front-facing, the result is invalid. "
            "No text or watermark."
        )

    camera_recipe = _detail_camera_recipe(style_config, shot_index)
    if _is_small_decor_detail_target(style_config, clean_label):
        return (
            f"Using the provided image as the only source, create a source-constrained editorial close-up reframe centered on the {clean_label}, not another full-room view. "
            f"The {clean_label} must be clearly visible and visually dominant in the frame. "
            "Include only enough surrounding room context to prove it is the same space. "
            "This shot may be less dynamic if that is required to preserve the main-shot furniture and layout. "
            "Use crop, slight zoom, and focal depth from the source image. "
            f"{camera_recipe} "
            "Follow the camera recipe only when it does not conflict with layout stability. "
            f"{_NO_FAKE_FOREGROUND_GUARD}"
            "Use shallow depth of field or diagonal composition if helpful. "
            "Keep the room layout and every visible furniture/decor item's position, shape, size, count, color, material, and nearby relationships unchanged. "
            f"{stability_guard}"
            f"Do not redesign, move, enlarge, duplicate, replace, or reinterpret the {clean_label}. "
            "Do not let nearby larger furniture become the main subject. "
            "Do not create a new camera angle that reveals unseen sides of furniture. "
            "Only change crop, framing, slight zoom, and focal depth. "
            "No text or watermark."
        )

    return (
        f"Using the provided image as the only source, create a source-constrained editorial detail reframe around the {clean_label} area, not another full-room view. "
        "This shot may be less dynamic if that is required to preserve the main-shot furniture and layout. "
        "Use crop, slight zoom, and focal depth from the source image. "
        f"The {clean_label} area must be the visual anchor of the frame. "
        f"{camera_recipe} "
        "Follow the camera recipe only when it does not conflict with layout stability. "
        f"{_NO_FAKE_FOREGROUND_GUARD}"
        "Use shallow depth of field or diagonal composition if helpful. "
        "Keep every visible furniture/decor item's position, shape, size, count, color, material unchanged. "
        f"{stability_guard}"
        "Do not move, replace, resize, duplicate, restage, redesign, or reinterpret anything. "
        "Do not create a new camera angle that reveals unseen sides of furniture. "
        "Only change crop, framing, slight zoom, and focal depth. "
        "No text or watermark."
    )


def _build_gpt_image_curtain_detail_prompt(style_config: dict, target_label: str) -> str:
    clean_label = str(target_label or "").strip() or "the existing curtain"
    try:
        blackout_percent = int((style_config or {}).get("blackout_percent") or CURTAIN_BLACKOUT_PERCENT)
    except Exception:
        blackout_percent = CURTAIN_BLACKOUT_PERCENT
    style_instructions = str((style_config or {}).get("prompt") or "").strip()
    return (
        f"Create a source-constrained editorial close detail of {clean_label} in this exact finished room. "
        "Use the supplied CURTAIN MATERIAL SWATCH as the absolute reference for the existing curtain's material, color, "
        "weave, threads, and surface texture. Change only the curtain surface appearance to match that swatch. "
        f"Express exactly {blackout_percent}% blackout through the curtain fabric opacity. Do not darken the room: keep the "
        "room exposure, lighting brightness, shadows, and white balance as bright as the main furnished room image. "
        "Keep the curtain position, folds, scale, and geometry fixed. Preserve the camera-visible architecture, furniture, "
        "decor, windows, object placement, and spatial relationships exactly as shown. Choose a clearly visible curtain "
        "section even when furniture overlaps the main view. Do not add, remove, move, replace, or redesign any object. "
        "No text or watermark. "
        f"CURTAIN DETAIL STYLE INSTRUCTIONS: {style_instructions}"
    )


def _build_angle_camera_guide_prompt(
    style_config: dict,
    *,
    reference_mode: str = "furnished_main",
) -> str:
    style_name = str((style_config or {}).get("name") or "")
    camera_mode = str((style_config or {}).get("camera_mode") or "").strip().lower()
    focus_side = str((style_config or {}).get("focus_side") or "").strip().lower()
    is_side_angle = camera_mode == "side_angle" or style_name.startswith("Side Composition")

    if is_side_angle:
        requested_side = "left" if focus_side == "left" else "right"
        foreground_side = "right" if requested_side == "left" else "left"
        camera_instruction = (
            f"Move the physical camera-body toward {requested_side.upper()} by roughly 12-18% of the visible room width, "
            f"then yaw 15-25 degrees back into the room. Nearby architectural edges must shift toward screen-"
            f"{foreground_side.upper()} relative to distant architecture. LEFT/RIGHT describes camera travel, not the "
            "side of the room that dominates the frame."
        )
    else:
        camera_instruction = (
            "Raise the camera roughly 0.8-1.2 m above the furnished main viewpoint and pitch it downward about "
            "18-28 degrees. Show measurably more floor and top-facing architectural surfaces without becoming "
            "bird's-eye, top-down, drone-like, or ceiling-mounted."
        )

    contract_lines = []
    for label, key in (
        ("ROOM DIMS CONTRACT", "room_dims_contract"),
        ("GEOMETRY CONTRACT", "geometry_contract"),
        ("SCENE CONTRACT", "scene_contract"),
        ("PLACEMENT PLAN", "placement_plan"),
    ):
        compact_value = _compact_prompt_metadata((style_config or {}).get(key))
        if compact_value:
            contract_lines.append(f"- {label}: {compact_value}")
    contract_block = (
        "<ARCHITECTURE CONTRACT EVIDENCE>\n"
        + "\n".join(contract_lines)
        + "\n\n"
        if contract_lines
        else ""
    )

    if reference_mode == "empty_room":
        reference_authority = (
            "- Image 1 is the SOLE architecture source. Preserve its exact room identity, topology, materials, and permanent "
            "features.\n"
            "- Its camera is only the STARTING pose and is forbidden as the output pose. The output must execute the requested "
            "physical camera move.\n"
            "- The source is already empty. Do not add furniture, decor, silhouettes, or ghost objects.\n\n"
        )
    else:
        reference_authority = (
            "- Image 1 is the sole source viewpoint and architecture reference.\n"
            "- Its camera is only the STARTING pose and is forbidden as the output pose. The output must execute the requested "
            "physical camera move.\n"
            "- Remove all movable furniture, rugs, lamps, art, plants, and decor from this guide. Preserve permanent built-ins.\n\n"
        )

    return (
        "<EMPTY ARCHITECTURE CAMERA GUIDE>\n"
        "Create one photorealistic EMPTY architectural camera plate for the exact same room from the requested new camera pose.\n"
        f"{camera_instruction}\n\n"
        "<REFERENCE AUTHORITY>\n"
        f"{reference_authority}"
        "<ARCHITECTURE LOCK>\n"
        "- Preserve the same room topology, dimensions, wall/window/door count, opening locations, ceiling structure, "
        "floor boundaries, permanent materials, and exterior views.\n"
        "- Generate real parallax and coherent vanishing points from the requested camera pose. Do not mirror the room, "
        "rotate a flat background, bend walls, duplicate openings, invent extensions, or add foreground obstruction panels.\n"
        "- This is an empty camera/architecture guide only. Do not include furniture silhouettes, ghost furniture, "
        "measurement marks, text, logos, or watermarks.\n"
        f"{contract_block}"
        "OUTPUT: one clean, wide, horizontal 16:9 photorealistic empty-room camera plate."
    )


def _build_angle_refurnish_prompt(
    style_config: dict,
    *,
    locked_camera_side: str | None = None,
) -> str:
    style_name = str((style_config or {}).get("name") or "")
    camera_mode = str((style_config or {}).get("camera_mode") or "").strip().lower()
    focus_side = str((style_config or {}).get("focus_side") or "").strip().lower()
    is_side_angle = camera_mode == "side_angle" or style_name.startswith("Side Composition")
    if is_side_angle:
        requested_side = (
            locked_camera_side
            if str(locked_camera_side or "").strip().lower() in {"left", "right"}
            else "left"
            if focus_side == "left"
            else "right"
        )
        camera_assertion = (
            f"The locked plate is the observed physical camera-{requested_side.upper()} viewpoint. Preserve its exact "
            "camera location, vanishing points, perspective, and framing."
        )
    else:
        camera_assertion = (
            "The locked plate is the requested elevated, downward-pitched viewpoint. Preserve its exact camera height, "
            "vanishing points, perspective, and framing."
        )

    return (
        "<LOCKED CAMERA PLATE REFURNISH>\n"
        "Re-render the exact furnished scene from the Furnished Main Reference into the EMPTY LOCKED CAMERA PLATE.\n"
        f"{camera_assertion}\n\n"
        "<IMAGE AUTHORITY>\n"
        "- Image 1, the EMPTY LOCKED CAMERA PLATE, is absolute truth for camera pose, architecture, visible room extent, "
        "perspective, vanishing points, openings, and permanent surfaces.\n"
        "- Image 2, the Furnished Main Reference, is absolute truth for every furniture/decor identity, count, geometry, "
        "material, color, size hierarchy, world-space footprint, orientation, and neighboring relationship. Its camera, crop, "
        "vanishing points, perspective, and source-image pixel coordinates have ZERO authority.\n\n"
        "<COMPOSITING CONTRACT>\n"
        "- Keep the locked plate architecture pixel-position consistent wherever furniture does not occlude it. Do not "
        "pan, orbit, truck, dolly, zoom, crop, mirror, or regenerate the room from the furnished source camera.\n"
        "- Place the same furniture back at the same physical world-space locations relative to walls, windows, doors, "
        "corners, and neighboring objects. Reproject screen positions, visible faces, perspective, overlaps, and occlusions "
        "naturally for the locked plate camera.\n"
        "- Do not keep furniture front-facing while the room changes. Do not copy furniture at its old source-image pixel "
        "positions. Do not move, rotate, resize, replace, simplify, duplicate, remove, or invent any item.\n"
        "- Natural out-of-frame cropping or camera occlusion is allowed; never relocate an item just to keep it visible.\n"
        "- Match the furnished main lighting direction, exposure, material realism, and neutral color balance while "
        "respecting the locked plate geometry.\n"
        "OUTPUT: one photorealistic, wide, horizontal 16:9 furnished view. No text, logos, or watermarks."
    )


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
    prefer_crop_extract: bool = False,
    materialize_input: Callable[[str | None, str], str | None],
    normalize_label_for_match: Callable[[str], str],
    allow_harassment_only_safety_settings: Callable[[], object],
    call_gemini_with_failover: Callable[..., object],
    model_name: str,
    call_analysis_with_failover: Callable[..., object] | None = None,
    analysis_model_name: str | None = None,
    safe_json_from_model_text: Callable[[str], object] | None = None,
    refurnish_locked_angle: Callable[..., object] | None = None,
):
    img = None
    extra_imgs = []
    temp_cutout_paths = []
    cutout_labels = []
    cutout_ref_count = 0
    salvage_candidate = None
    angle_fallback_artifacts: set[str] = set()
    try:
        style_name = str(style_config.get("name") or "")
        target_item = _find_target_item(style_config, furniture_data, normalize_label_for_match)
        if prefer_crop_extract and style_name.startswith("Detail:"):
            return _render_crop_detail(original_image_path, style_config, unique_id, index, target_item or {})

        img = Image.open(original_image_path)
        material_reference_img = None
        material_reference_path = str(style_config.get("material_reference_path") or "").strip()
        if material_reference_path:
            try:
                local_material_path = materialize_input(material_reference_path, "detail_curtain_material")
                if local_material_path and os.path.exists(local_material_path):
                    with Image.open(local_material_path) as material_opened:
                        material_reference_img = ImageOps.exif_transpose(material_opened).convert("RGB")
                        material_reference_img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    extra_imgs.append(material_reference_img)
            except Exception:
                material_reference_img = None
        target_ratio = _normalize_ratio_string(style_config.get("ratio"))
        camera_mode = str(style_config.get("camera_mode") or "").strip().lower()
        focus_side = str(style_config.get("focus_side") or "").strip().lower()
        is_side_angle = camera_mode == "side_angle" or style_name.startswith("Side Composition")
        is_overview_angle = camera_mode == "overview_angle" or style_name == "High Angle Overview"
        is_angle_style = is_side_angle or is_overview_angle
        is_internal_angle = is_angle_style and bool(style_config.get("internal_angle_generation"))
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
        elif is_side_angle:
            camera_travel_side = "left" if focus_side == "left" else "right" if focus_side == "right" else "requested"
            foreground_parallax_side = (
                "right"
                if camera_travel_side == "left"
                else "left"
                if camera_travel_side == "right"
                else "the opposite screen direction"
            )
            scene_lock_block = (
                "<SCENE LOCK: SAME ROOM, REAL SIDE CAMERA MOVE>\n"
                "Create a genuine nearby side-angle camera view of the exact same finished room.\n"
                f"Move the camera toward the {camera_travel_side} side of the source viewpoint and yaw gently back into the room, "
                f"so the {camera_travel_side} side viewpoint is unmistakable.\n"
                "Camera direction means the physical camera-body location, not which side of the room dominates the image.\n"
                f"Use a clear lateral translation of roughly 12-18% of the visible room width plus a 15-25 degree yaw. "
                f"Nearby furniture should shift toward screen-{foreground_parallax_side} relative to the far background.\n"
                "This must be a new side camera viewpoint, not a crop, zoom, or source reframe.\n"
                "Priority order: (1) unmistakable real side-camera parallax with changed projected positions, side planes, and occlusions, "
                "(2) same physical room topology, (3) same furniture identities and world-space placement.\n"
                "Side-specific composition is allowed to crop out or minimize the opposite side of the room; do NOT relocate objects to keep them visible.\n"
                "Do not rotate or rebuild only the room around static front-facing furniture.\n"
            )
            camera_lock_line = (
                "4. **REAL SIDE CAMERA MOVE REQUIRED:** Use a nearby lateral camera translation plus modest yaw. "
                f"The camera travels toward the {camera_travel_side.upper()} side of the source viewpoint and looks back into the room. "
                f"Foreground objects therefore move toward screen-{foreground_parallax_side.upper()} relative to distant architecture. "
                "Keep object world-space placement, physical orientation, footprint, and room geometry fixed, while allowing the screen projection, visible sides, occlusions, and perspective to change naturally. "
                "Do not add blurred foreground panels, curtains, doorframes, wall edges, or obstruction strips.\n\n"
            )
        elif is_overview_angle:
            scene_lock_block = (
                "<SCENE LOCK: SAME ROOM, REAL HIGH CAMERA MOVE>\n"
                "Create a genuine nearby high-angle camera view of the exact same finished room.\n"
                "Raise the camera roughly 0.8-1.2 m above the main viewpoint and pitch downward about 18-28 degrees so substantially more top surfaces and floor planes are visible.\n"
                "This must be a new high camera viewpoint, not a crop, zoom, or source reframe.\n"
                "Priority order: (1) unmistakable real high-camera perspective with changed projected positions and top-plane visibility, "
                "(2) same physical room topology, (3) same furniture identities and world-space placement.\n"
                "Do not rotate or rebuild only the room around static front-facing furniture.\n"
            )
            camera_lock_line = (
                "4. **REAL HIGH CAMERA MOVE REQUIRED:** Use camera elevation plus downward pitch. "
                "Keep object world-space placement, physical orientation, footprint, and room geometry fixed, while allowing the screen projection, visible top surfaces, occlusions, and perspective to change naturally.\n\n"
            )
        else:
            scene_lock_block = (
                "<SCENE LOCK: SAME ROOM, SOURCE-CONSTRAINED REFRAME>\n"
                "Create a source-constrained editorial reframe of the exact same finished room.\n"
                "Use the main render as the visual source of truth. This is a crop/reframe/detail-polish task, not a restaging task.\n"
                "Keep the architecture, furniture placement, object scale, object identities, materials, lighting direction, and nearby-object relationships unchanged.\n"
                "Do not create a new camera angle that reveals unseen sides of furniture.\n"
            )
            camera_lock_line = "4. **SOURCE-CONSTRAINED REFRAME ONLY:** Use crop, framing, slight zoom, and focal depth. Keep object placement, facing direction, footprint, and room geometry fixed.\n\n"

        style_target_key = str(style_config.get("target_key") or "").strip()
        style_target_label = str(style_config.get("target_label") or "").strip()
        target_box_2d = style_config.get("target_box_2d")
        if not style_target_label and style_name.startswith("Detail:"):
            style_target_label = style_name.split("Detail:", 1)[1].strip()

        requested_timeout_sec = style_config.get("timeout_sec")
        try:
            request_timeout_sec = (
                float(requested_timeout_sec)
                if requested_timeout_sec is not None
                else DETAIL_IMAGE_REQUEST_TIMEOUT_CAP_SEC
            )
        except Exception:
            request_timeout_sec = DETAIL_IMAGE_REQUEST_TIMEOUT_CAP_SEC
        if request_timeout_sec <= 0.0:
            return None

        if _is_gpt_image_model_name(model_name):
            if str(style_config.get("detail_mode") or "").strip().lower() == CURTAIN_DETAIL_MODE:
                gpt_prompt = _build_gpt_image_curtain_detail_prompt(
                    style_config,
                    style_target_label or style_name,
                )
            else:
                gpt_prompt = _build_gpt_image_detail_prompt(style_config, style_target_label or style_name, index)
            gpt_content = [
                gpt_prompt,
                "Main furnished room image:",
                img,
            ]
            if material_reference_img is not None:
                gpt_content.extend(
                    [
                        "CURTAIN MATERIAL SWATCH (material/color/weave reference only; not an object or framing reference):",
                        material_reference_img,
                    ]
                )
            response = call_gemini_with_failover(
                model_name,
                gpt_content,
                {
                    "timeout": max(1.0, min(DETAIL_IMAGE_REQUEST_TIMEOUT_CAP_SEC, request_timeout_sec)),
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
                    "timeout": max(1.0, min(DETAIL_IMAGE_REQUEST_TIMEOUT_CAP_SEC, request_timeout_sec)),
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

        if is_angle_style:
            output_focus_line = (
                "3. IMPORTANT: this is a room angle shot, not an object close-up. "
                "Keep enough room context visible to clearly read the camera direction, room geometry, and spatial relationship.\n"
            )
        else:
            output_focus_line = (
                "3. IMPORTANT: focus on the specified target area only and make it read like a dedicated editorial shot of that object in the room.\n"
            )

        angle_context_block = ""
        if is_angle_style:
            reference_authority = (
                "<ANGLE REFERENCE AUTHORITY>\n"
                "- The furnished main image is the sole truth for furniture identity, count, material, styling, lighting, and world-space placement.\n"
                "- The empty-room image, when attached, is architecture-only evidence for walls, windows, doors, openings, ceiling, and floor topology.\n"
                "- Never remove furniture because it is absent from the empty-room reference, and never copy the empty-room camera framing.\n\n"
            )
            angle_context_lines = []
            for label, key in (
                ("ROOM DIMS CONTRACT", "room_dims_contract"),
                ("GEOMETRY CONTRACT", "geometry_contract"),
                ("SCENE CONTRACT", "scene_contract"),
                ("PLACEMENT PLAN", "placement_plan"),
            ):
                compact_value = _compact_prompt_metadata(style_config.get(key))
                if compact_value:
                    angle_context_lines.append(f"- {label}: {compact_value}")
            if angle_context_lines:
                angle_context_block = (
                    reference_authority
                    + "<ANGLE SCENE CONTRACT>\n"
                    + "\n".join(angle_context_lines)
                    + "\n\n"
                )
            else:
                angle_context_block = reference_authority

        target_crop = None if is_angle_style else _build_target_crop(img, target_box_2d)
        if target_crop is not None:
            extra_imgs.append(target_crop)
            target_lock_block += (
                "<PRIMARY TARGET SCALE LOCK>\n"
                "- The attached in-room crop shows the target exactly as it appears in the main render.\n"
                "- Match that target size, perspective, and surrounding context. Only tighten framing around it.\n"
                "- Do NOT enlarge the target beyond what a real crop/zoom from the same scene would produce.\n\n"
            )

        pose_lock_line = (
            "3c. **WORLD-SPACE POSE LOCK:** Do not move or physically rotate sofas, chairs, lamps, tables, decor, or rugs. "
            "Their real room positions and orientations stay fixed, but their projected screen positions, visible faces, occlusions, and perspective must change naturally when the camera moves.\n"
            if is_angle_style
            else "3c. **NO OBJECT ROTATION:** Do not rotate sofas, chairs, lamps, tables, decor, or rugs to show a more attractive side. Keep each visible object's facing direction from the main render.\n"
        )
        if is_angle_style:
            layout_lock_block = (
                "<CRITICAL: NEW CAMERA POSE + WORLD-SPACE SCENE LOCK>\n"
                "0. **NEW CAMERA POSE IS NON-NEGOTIABLE:** Reconstruct the scene from the requested new viewpoint. A pixel-aligned copy, crop, zoom, or same-camera edit is a failed output even if every object is preserved.\n"
                "1. **LOCK THE PHYSICAL SCENE:** Keep every furniture, lighting, and decor item at the same real 3D footprint, height, scale, and physical orientation in the room.\n"
                "1b. **CHANGE THE IMAGE-SPACE PROJECTION:** The camera move MUST change screen positions, visible faces, overlaps, vanishing geometry, and occlusions coherently. Do not pin objects to their source-image pixels.\n"
                "2. **NO NEW OBJECTS:** Do NOT add new objects (no extra vases, cats, books, lamps, shelves, plants, art, etc.).\n"
                "3. **NO REMOVALS:** Do NOT remove an in-frame object to simplify reconstruction. Natural out-of-frame cropping or camera occlusion is allowed.\n"
                "3b. **PRESERVE ROOM TOPOLOGY:** Keep the same walls, windows, doors, ceiling lines, floor boundaries, openings, built-ins, and object-to-room relationships.\n"
                f"{pose_lock_line}"
            )
        else:
            layout_lock_block = (
                "<CRITICAL: LAYOUT FREEZE (PRIORITY #0)>\n"
                "1. **DO NOT MOVE / REARRANGE ANYTHING:** Every existing furniture, lighting fixture, decor item, and their positions must remain EXACTLY the same as the input image.\n"
                "2. **NO NEW OBJECTS:** Do NOT add new objects (no extra vases, cats, books, lamps, shelves, plants, art, etc.).\n"
                "3. **NO REMOVALS:** Do NOT remove existing objects either.\n"
                "3b. **PRESERVE THE MAIN-SHOT LAYOUT:** Keep the target object anchored to the same physical footprint, neighboring objects, wall/floor relationship, and room geometry seen in the main render.\n"
                f"{pose_lock_line}"
            )
        final_prompt = (
            f"{scene_lock_block}\n"
            f"{target_lock_block}"
            f"{target_anchor_block}"
            f"{angle_context_block}"
            f"{style_config['prompt']}\n\n"
            f"{layout_lock_block}"
            f"{camera_lock_line}"
            "<OUTPUT REQUIREMENTS>\n"
            "1. Generate a photorealistic high-quality detail view based on the selected camera shot.\n"
            "2. Keep the overall interior style consistent with the main furnished room.\n"
            f"{output_focus_line}"
            "4. DO NOT add text, labels, logos, or watermarks.\n"
            f"OUTPUT ASPECT RATIO: {target_ratio}"
        )

        safety_settings = allow_harassment_only_safety_settings()
        source_reference_label = (
            "Furnished Main Reference (furniture truth and world-space scene source; generate the requested new camera viewpoint):"
            if is_angle_style
            else "Original Room Reality (CANVAS - DO NOT ALTER LAYOUT):"
        )
        content = [final_prompt, source_reference_label, img]
        if material_reference_img is not None:
            content += [
                "CURTAIN MATERIAL SWATCH (material/color/weave reference only; not an object or framing reference):",
                material_reference_img,
            ]
        angle_empty_room_img = None
        empty_room_path = str(style_config.get("empty_room_path") or "").strip()
        if is_angle_style and empty_room_path and os.path.exists(empty_room_path):
            try:
                with Image.open(empty_room_path) as empty_opened:
                    angle_empty_room_img = ImageOps.exif_transpose(empty_opened).convert("RGB")
                    angle_empty_room_img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                extra_imgs.append(angle_empty_room_img)
                if is_overview_angle:
                    content += [
                        "Empty Room Architecture Reference (same room topology before furnishing):",
                        angle_empty_room_img,
                    ]
            except Exception:
                angle_empty_room_img = None
        if target_crop is not None:
            content += [
                "PRIMARY TARGET IN-ROOM CROP (match this target scale and perspective):",
                target_crop,
            ]

        try:
            max_cutout_refs = 12
            max_detail_aux_cutout_refs = 2
            target_label_norm = normalize_label_for_match(style_target_label)
            is_detail_target_mode = bool(style_name.startswith("Detail:"))

            candidates = []
            cutout_source_items = [] if is_angle_style else (furniture_data or [])
            for item in cutout_source_items:
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
        max_attempts = DETAIL_ANGLE_QC_MAX_ATTEMPTS if is_angle_style and call_analysis_with_failover else 1
        if is_internal_angle and call_analysis_with_failover:
            max_attempts = min(max_attempts, DETAIL_INTERNAL_ANGLE_DIRECT_MAX_ATTEMPTS)
        last_angle_qc = None
        angle_retry_feedback = ""
        angle_pipeline_trace = (
            {
                "version": 2,
                "direct_attempts": 0,
                "guide_reference_mode": None,
                "guide_attempts": [],
                "refurnish_attempts": [],
                "refurnish_backend": None,
                "inventory_reference_mode": None,
                "locked_plate_ignored": False,
            }
            if is_internal_angle
            else None
        )

        def _clear_angle_fallback_artifacts() -> None:
            for artifact_path in list(angle_fallback_artifacts):
                _remove_file_quietly(artifact_path)
            angle_fallback_artifacts.clear()

        def _finalize_candidate_result(
            candidate_path: str,
            candidate_artifacts: set[str],
            *,
            angle_qc: dict | None,
            attempts_used: int,
            direction_fallback: bool = False,
            generation_mode: str | None = None,
        ) -> dict:
            finalized_path = candidate_path
            try:
                if is_angle_style:
                    angle_stage = (
                        "detail_side_angle"
                        if camera_mode == "side_angle" or style_name.startswith("Side Composition")
                        else "detail_high_angle"
                    )
                    corrected_path = apply_reference_relative_white_balance(
                        finalized_path,
                        reference_path=original_image_path,
                        stage_name=angle_stage,
                    ).path
                    if corrected_path != finalized_path:
                        candidate_artifacts.add(corrected_path)
                        _remove_file_quietly(finalized_path)
                        finalized_path = corrected_path

                for artifact_path in candidate_artifacts - {finalized_path}:
                    _remove_file_quietly(artifact_path)

                result = {
                    "path": finalized_path,
                    "style_name": style_config.get("name"),
                    "aspect_ratio": requested_ratio,
                    "cutout_ref_count": cutout_ref_count,
                    "cutout_ref_labels": cutout_labels,
                    "generation_mode": generation_mode
                    or ("angle_generation" if is_angle_style else "model_regeneration"),
                }
                if is_angle_style:
                    result["camera_mode"] = camera_mode or (
                        "side_angle" if style_name.startswith("Side Composition") else "overview_angle"
                    )
                    if focus_side:
                        result["focus_side"] = focus_side
                        result["requested_focus_side"] = focus_side
                    if is_side_angle:
                        inferred_side = str(
                            (angle_qc or {}).get("inferred_camera_translation") or ""
                        ).strip().lower()
                        result["camera_travel_side"] = (
                            inferred_side
                            if inferred_side in {"left", "right"}
                            else "left"
                            if focus_side == "left"
                            else "right"
                            if focus_side == "right"
                            else "requested"
                        )
                        result["camera_direction_matches"] = (angle_qc or {}).get(
                            "camera_direction_matches"
                        )
                        if direction_fallback:
                            result["angle_direction_fallback"] = True
                    result["angle_qc_attempts"] = attempts_used
                    if angle_qc is not None:
                        result["angle_qc"] = angle_qc
                    if angle_pipeline_trace is not None:
                        result["angle_pipeline_trace"] = json.loads(
                            json.dumps(angle_pipeline_trace, ensure_ascii=True)
                        )
                return result
            except Exception:
                for artifact_path in candidate_artifacts | {candidate_path, finalized_path}:
                    _remove_file_quietly(artifact_path)
                raise

        def _retry_feedback_for_qc(angle_qc: dict) -> str:
            hard_reasons = [
                str(reason)
                for reason in angle_qc.get("reject_reasons") or []
                if str(reason).strip()
            ]
            warnings = [
                str(reason)
                for reason in angle_qc.get("warnings") or []
                if str(reason).strip()
            ]
            all_reasons = hard_reasons + warnings
            retry_lines = [
                "The previous angle candidate failed the requested slot QC for: "
                + (", ".join(all_reasons) if all_reasons else "unknown angle mismatch")
                + ".",
                "Reconstruct the next candidate from a genuinely different camera pose. Do not return another pixel-aligned copy of the source frame.",
            ]
            model_reasons = [
                str(reason).strip()[:220]
                for reason in ((angle_qc.get("model_payload") or {}).get("reasons") or [])
                if str(reason).strip()
            ][:3]
            if model_reasons:
                retry_lines.append("Observed failure details: " + " | ".join(model_reasons))

            geometry_failure = any(
                reason
                in {
                    "room_topology_changed",
                    "furniture_projection_incoherent",
                    "severe_geometry_warp",
                    "background_only_rotation",
                    "large_artificial_panel",
                }
                for reason in hard_reasons
            )
            static_failure = any(
                reason in {"same_frame_or_crop", "insufficient_camera_motion"}
                for reason in hard_reasons
            )

            if is_side_angle:
                requested_side = (
                    "left"
                    if focus_side == "left"
                    else "right"
                    if focus_side == "right"
                    else "requested"
                )
                foreground_side = (
                    "right"
                    if requested_side == "left"
                    else "left"
                    if requested_side == "right"
                    else "the opposite screen direction"
                )
                observed_side = str(
                    angle_qc.get("inferred_camera_translation") or ""
                ).strip().lower()
                if (
                    {"camera_direction_mismatch", "direction_only_mismatch"} & set(warnings)
                    and observed_side in {"left", "right"}
                ):
                    retry_lines.append(
                        f"The previous camera physically moved {observed_side.upper()}. This slot requires the opposite physical "
                        f"camera-body location: {requested_side.upper()}. Do not mirror the image."
                    )
                if geometry_failure:
                    retry_lines.append(
                        f"Use a controlled 10-16% room-width move toward camera-{requested_side.upper()} with a 12-22 degree yaw; "
                        "preserve architecture and furniture before making the move more dramatic."
                    )
                elif static_failure:
                    retry_lines.append(
                        f"Use an unmistakable 18-24% room-width move toward camera-{requested_side.upper()} with a 20-30 degree yaw."
                    )
                else:
                    retry_lines.append(
                        f"Use a clear 14-20% room-width move toward camera-{requested_side.upper()} with a 15-25 degree yaw."
                    )
                retry_lines.append(
                    f"With camera-{requested_side.upper()} travel, nearby furniture must shift toward screen-{foreground_side.upper()} "
                    "relative to the far background; that parallax sign is mandatory."
                )
            elif geometry_failure:
                retry_lines.append(
                    "Raise the camera about 0.9-1.2 m and pitch downward 20-28 degrees while preserving architecture and furniture exactly."
                )
            else:
                retry_lines.append(
                    "Raise the camera about 1.2-1.6 m and pitch downward 28-38 degrees so top-plane and floor exposure visibly increase."
                )

            retry_lines.append(
                "Keep furniture count, identity, physical footprint, room topology, and architecture fixed; do not mirror, duplicate, relocate, or warp them."
            )
            return " ".join(retry_lines)

        def _materialize_fallback_response_image(response, filename_stem: str) -> tuple[str, set[str]] | None:
            if not response or not hasattr(response, "candidates") or not response.candidates:
                return None
            for part in getattr(response, "parts", []) or []:
                if not hasattr(part, "inline_data"):
                    continue
                path = os.path.join(
                    "outputs",
                    f"{filename_stem}_{time.time_ns()}_{unique_id}_{index}.png",
                )
                artifacts = {path}
                angle_fallback_artifacts.add(path)
                with open(path, "wb") as file_obj:
                    file_obj.write(part.inline_data.data)
                normalized_path = _normalize_generated_detail_ratio(
                    path,
                    requested_ratio=requested_ratio,
                )
                if normalized_path is None:
                    for artifact_path in artifacts:
                        _remove_file_quietly(artifact_path)
                    angle_fallback_artifacts.difference_update(artifacts)
                    continue
                if normalized_path != path:
                    artifacts.add(normalized_path)
                    angle_fallback_artifacts.add(normalized_path)
                    _remove_file_quietly(path)
                    path = normalized_path
                return path, artifacts
            return None

        def _retain_direction_only_candidate(
            candidate_path: str,
            candidate_artifacts: set[str],
            *,
            angle_qc: dict,
            attempts_used: int,
            generation_mode: str,
        ) -> bool:
            nonlocal salvage_candidate
            if not bool(angle_qc.get("direction_only_mismatch")):
                for artifact_path in candidate_artifacts:
                    _remove_file_quietly(artifact_path)
                angle_fallback_artifacts.difference_update(candidate_artifacts)
                return False

            motion_score = float(
                (angle_qc.get("metrics") or {}).get("camera_motion_score")
                or 0.0
            )
            confidence = float(
                (angle_qc.get("metrics") or {}).get("model_confidence")
                or 0.0
            )
            salvage_score = (0.75 * motion_score) + (0.25 * confidence)
            if salvage_candidate is not None and salvage_score <= salvage_candidate["score"]:
                for artifact_path in candidate_artifacts:
                    _remove_file_quietly(artifact_path)
                angle_fallback_artifacts.difference_update(candidate_artifacts)
                return False

            if salvage_candidate is not None:
                for artifact_path in salvage_candidate["artifacts"]:
                    _remove_file_quietly(artifact_path)
            salvage_candidate = {
                "path": candidate_path,
                "artifacts": set(candidate_artifacts),
                "angle_qc": angle_qc,
                "attempts_used": attempts_used,
                "score": salvage_score,
                "generation_mode": generation_mode,
            }
            angle_fallback_artifacts.difference_update(candidate_artifacts)
            return True

        def _refurnish_retry_feedback(angle_qc: dict) -> str:
            reasons = [
                *[
                    str(reason)
                    for reason in angle_qc.get("reject_reasons") or []
                    if str(reason).strip()
                ],
                *[
                    str(reason)
                    for reason in angle_qc.get("warnings") or []
                    if str(reason).strip()
                ],
            ]
            model_reasons = [
                str(reason).strip()[:220]
                for reason in ((angle_qc.get("model_payload") or {}).get("reasons") or [])
                if str(reason).strip()
            ][:3]
            feedback = (
                "The previous locked-plate furnishing failed strict QC for "
                + (", ".join(reasons) if reasons else "an unknown projection error")
                + ". Keep the EMPTY LOCKED CAMERA PLATE camera, architecture, vanishing points, and framing unchanged. "
                "Do not fall back to the furnished main image camera. Reproject the exact same furniture identities and "
                "world-space footprints into the locked plate with coherent visible faces, overlaps, and occlusions."
            )
            if model_reasons:
                feedback += " Observed failure details: " + " | ".join(model_reasons)
            return feedback

        def _discard_fallback_artifacts(artifacts: set[str]) -> None:
            for artifact_path in artifacts:
                _remove_file_quietly(artifact_path)
            angle_fallback_artifacts.difference_update(artifacts)

        def _guide_retry_feedback(guide_qc: dict) -> str:
            hard_reasons = [
                str(reason)
                for reason in guide_qc.get("reject_reasons") or []
                if str(reason).strip()
            ]
            warnings = [
                str(reason)
                for reason in guide_qc.get("warnings") or []
                if str(reason).strip()
            ]
            observed_side = str(
                guide_qc.get("inferred_camera_translation") or ""
            ).strip().lower()
            feedback = [
                "The previous empty camera guide failed strict camera/architecture QC for "
                + (", ".join([*hard_reasons, *warnings]) or "an unknown guide error")
                + ".",
                "Generate a new EMPTY architecture plate from the same source. Do not reuse, crop, zoom, or pixel-align the previous source camera.",
            ]
            geometry_failure = any(
                reason
                in {
                    "room_topology_changed",
                    "architecture_projection_incoherent",
                    "background_only_rotation",
                    "large_artificial_panel",
                    "severe_geometry_warp",
                }
                for reason in hard_reasons
            )
            static_failure = any(
                reason in {"same_frame_or_crop", "insufficient_camera_motion"}
                for reason in hard_reasons
            )
            if is_side_angle:
                requested_side = "left" if focus_side == "left" else "right"
                foreground_side = "right" if requested_side == "left" else "left"
                if "camera_direction_mismatch" in warnings and observed_side in {"left", "right"}:
                    feedback.append(
                        f"The previous guide moved camera-{observed_side.upper()}, but this slot requires physical camera-"
                        f"{requested_side.upper()} travel. Do not mirror the image."
                    )
                if geometry_failure:
                    feedback.append(
                        f"Use a controlled 10-16% room-width camera-{requested_side.upper()} move with 12-22 degrees of yaw."
                    )
                elif static_failure:
                    feedback.append(
                        f"Use an unmistakable 20-28% room-width camera-{requested_side.upper()} move with 25-35 degrees of yaw."
                    )
                else:
                    feedback.append(
                        f"Use a clear 16-22% room-width camera-{requested_side.upper()} move with 18-28 degrees of yaw."
                    )
                feedback.append(
                    f"Nearby architecture must shift toward screen-{foreground_side.upper()} relative to distant architecture."
                )
            elif geometry_failure:
                feedback.append(
                    "Raise the camera 0.9-1.2 m and pitch downward 20-28 degrees while preserving exact room topology."
                )
            else:
                feedback.append(
                    "Raise the camera 1.2-1.6 m and pitch downward 28-38 degrees so floor and top-plane exposure clearly increase."
                )
            return " ".join(feedback)

        def _run_two_stage_internal_angle_fallback() -> dict | None:
            nonlocal last_angle_qc, salvage_candidate

            reference_mode = "empty_room" if angle_empty_room_img is not None else "furnished_main"
            guide_source_path = (
                empty_room_path
                if reference_mode == "empty_room"
                else original_image_path
            )
            guide_source_image = angle_empty_room_img if reference_mode == "empty_room" else img
            guide_retry_feedback = ""
            selected_guide = None
            direction_only_guide = None
            if angle_pipeline_trace is not None:
                angle_pipeline_trace["guide_reference_mode"] = reference_mode

            for guide_attempt_index in range(DETAIL_INTERNAL_ANGLE_GUIDE_MAX_ATTEMPTS):
                guide_prompt = _build_angle_camera_guide_prompt(
                    style_config,
                    reference_mode=reference_mode,
                )
                if guide_retry_feedback:
                    guide_prompt += (
                        "\n\n<EMPTY-GUIDE QC RETRY FEEDBACK>\n"
                        + guide_retry_feedback
                    )
                guide_content = [
                    guide_prompt,
                    (
                        "Original Empty Room Architecture Reference (SOLE architecture source; starting pose only):"
                        if reference_mode == "empty_room"
                        else "Furnished Main Architecture Reference (SOLE source; starting pose only; remove movable contents):"
                    ),
                    guide_source_image,
                ]
                guide_response = call_gemini_with_failover(
                    model_name,
                    guide_content,
                    {
                        "timeout": max(1.0, min(DETAIL_IMAGE_REQUEST_TIMEOUT_CAP_SEC, request_timeout_sec)),
                        "aspect_ratio": requested_ratio,
                        "image_size": "4K",
                        "thinking_level": "high",
                        "include_thoughts": False,
                    },
                    safety_settings,
                    log_tag="Detail.AngleGuide",
                )
                guide_result = _materialize_fallback_response_image(
                    guide_response,
                    f"detail_angle_guide_a{guide_attempt_index + 1}",
                )
                if guide_result is None:
                    if angle_pipeline_trace is not None:
                        angle_pipeline_trace["guide_attempts"].append(
                            {
                                "attempt": guide_attempt_index + 1,
                                "materialized": False,
                            }
                        )
                    continue
                guide_path, guide_artifacts = guide_result
                try:
                    guide_qc = assess_angle_camera_guide(
                        guide_source_path,
                        guide_path,
                        camera_mode=camera_mode
                        or ("side_angle" if style_name.startswith("Side Composition") else "overview_angle"),
                        focus_side=focus_side,
                        call_analysis_with_failover=call_analysis_with_failover,
                        analysis_model_name=analysis_model_name,
                        safe_json_from_model_text=safe_json_from_model_text,
                        require_model_qc=True,
                    )
                except Exception:
                    _discard_fallback_artifacts(guide_artifacts)
                    raise

                trace_guide_qc = json.loads(json.dumps(guide_qc, ensure_ascii=True))
                if angle_pipeline_trace is not None:
                    angle_pipeline_trace["guide_attempts"].append(
                        {
                            "attempt": guide_attempt_index + 1,
                            "materialized": True,
                            "qc": trace_guide_qc,
                        }
                    )
                requested_slot_passed = bool(
                    guide_qc.get(
                        "passed_for_requested_slot",
                        guide_qc.get("passed"),
                    )
                )
                print(
                    "[DetailAngleGuideQC] "
                    f"unique_id={unique_id!r} index={index} style={style_name!r} "
                    f"guide_attempt={guide_attempt_index + 1}/{DETAIL_INTERNAL_ANGLE_GUIDE_MAX_ATTEMPTS} "
                    f"reference_mode={reference_mode!r} physical_passed={bool(guide_qc.get('passed'))} "
                    f"requested_slot_passed={requested_slot_passed} "
                    f"reasons={json.dumps([*(guide_qc.get('reject_reasons') or []), *(guide_qc.get('warnings') or [])], ensure_ascii=True)} "
                    f"metrics={json.dumps(guide_qc.get('metrics') or {}, ensure_ascii=True, sort_keys=True)} "
                    f"model={json.dumps(guide_qc.get('model_payload') or {}, ensure_ascii=True, sort_keys=True)}",
                    flush=True,
                )
                if requested_slot_passed:
                    selected_guide = {
                        "path": guide_path,
                        "artifacts": guide_artifacts,
                        "qc": guide_qc,
                        "attempt": guide_attempt_index + 1,
                    }
                    if direction_only_guide is not None:
                        _discard_fallback_artifacts(direction_only_guide["artifacts"])
                        direction_only_guide = None
                    break

                guide_retry_feedback = _guide_retry_feedback(guide_qc)
                if bool(guide_qc.get("direction_only_mismatch")):
                    motion_score = float(
                        (guide_qc.get("metrics") or {}).get("camera_motion_score")
                        or 0.0
                    )
                    confidence = float(
                        (guide_qc.get("metrics") or {}).get("model_confidence")
                        or 0.0
                    )
                    score = (0.75 * motion_score) + (0.25 * confidence)
                    if direction_only_guide is None or score > direction_only_guide["score"]:
                        if direction_only_guide is not None:
                            _discard_fallback_artifacts(direction_only_guide["artifacts"])
                        direction_only_guide = {
                            "path": guide_path,
                            "artifacts": guide_artifacts,
                            "qc": guide_qc,
                            "attempt": guide_attempt_index + 1,
                            "score": score,
                        }
                        continue
                _discard_fallback_artifacts(guide_artifacts)

            if selected_guide is None and direction_only_guide is not None:
                selected_guide = direction_only_guide
                direction_only_guide = None
            if selected_guide is None:
                return None

            guide_path = selected_guide["path"]
            guide_qc = selected_guide["qc"]
            guide_translation = str(
                guide_qc.get("inferred_camera_translation") or ""
            ).strip().lower()
            with Image.open(guide_path) as guide_opened:
                guide_img = ImageOps.exif_transpose(guide_opened).convert("RGB")
            extra_imgs.append(guide_img)

            refurnish_base_prompt = _build_angle_refurnish_prompt(
                style_config,
                locked_camera_side=guide_translation,
            )
            refurnish_retry_feedback = ""
            for fallback_attempt_index in range(DETAIL_INTERNAL_ANGLE_TWO_STAGE_MAX_ATTEMPTS):
                refurnish_prompt = refurnish_base_prompt
                if refurnish_retry_feedback:
                    refurnish_prompt += (
                        "\n\n<LOCKED-PLATE QC RETRY FEEDBACK>\n"
                        + refurnish_retry_feedback
                    )
                refurnish_backend = (
                    "main_stage2_locked_canvas"
                    if refurnish_locked_angle is not None
                    else "detail_handcrafted"
                )
                if angle_pipeline_trace is not None:
                    angle_pipeline_trace["refurnish_backend"] = refurnish_backend
                inventory_reference_mode = None
                if refurnish_locked_angle is not None:
                    stage2_result = refurnish_locked_angle(
                        guide_path=guide_path,
                        furnished_main_path=original_image_path,
                        empty_room_path=empty_room_path,
                        style_prompt=refurnish_prompt,
                        unique_id=(
                            f"{unique_id}_detail_angle_{index}_"
                            f"a{fallback_attempt_index + 1}"
                        ),
                        furniture_data=furniture_data,
                        room_dims_contract=style_config.get("room_dims_contract"),
                        geometry_contract=style_config.get("geometry_contract"),
                        scene_contract=style_config.get("scene_contract"),
                        placement_plan=style_config.get("placement_plan"),
                        timeout_sec=max(
                            1.0,
                            min(
                                DETAIL_IMAGE_REQUEST_TIMEOUT_CAP_SEC,
                                request_timeout_sec,
                            ),
                        ),
                    )
                    if isinstance(stage2_result, dict):
                        inventory_reference_mode = str(
                            stage2_result.get("inventory_reference_mode") or ""
                        ).strip() or None
                    if (
                        angle_pipeline_trace is not None
                        and inventory_reference_mode
                    ):
                        angle_pipeline_trace["inventory_reference_mode"] = (
                            inventory_reference_mode
                        )
                    stage2_path = (
                        stage2_result.get("path")
                        if isinstance(stage2_result, dict)
                        else stage2_result
                    )
                    if stage2_path:
                        stage2_path = str(stage2_path)
                    protected_paths = {
                        os.path.abspath(path)
                        for path in (
                            original_image_path,
                            guide_path,
                            empty_room_path,
                        )
                        if path
                    }
                    if (
                        stage2_path
                        and os.path.exists(stage2_path)
                        and os.path.abspath(stage2_path) not in protected_paths
                    ):
                        candidate_artifacts = {stage2_path}
                        angle_fallback_artifacts.add(stage2_path)
                        materialized = (stage2_path, candidate_artifacts)
                    else:
                        materialized = None
                else:
                    refurnish_content = [
                        refurnish_prompt,
                        "EMPTY LOCKED CAMERA PLATE (absolute camera and architecture truth):",
                        guide_img,
                        (
                            "Furnished Main Reference (furniture/world-space inventory ONLY; camera, crop, vanishing points, "
                            "perspective, and source pixel coordinates have ZERO authority):"
                        ),
                        img,
                    ]
                    refurnish_response = call_gemini_with_failover(
                        model_name,
                        refurnish_content,
                        {
                            "timeout": max(1.0, min(DETAIL_IMAGE_REQUEST_TIMEOUT_CAP_SEC, request_timeout_sec)),
                            "aspect_ratio": requested_ratio,
                            "image_size": "4K",
                            "thinking_level": "high",
                            "include_thoughts": False,
                        },
                        safety_settings,
                        log_tag="Detail.AngleRefurnish",
                    )
                    materialized = _materialize_fallback_response_image(
                        refurnish_response,
                        f"detail_angle_refurnish_a{fallback_attempt_index + 1}",
                    )
                if materialized is None:
                    if angle_pipeline_trace is not None:
                        angle_pipeline_trace["refurnish_attempts"].append(
                            {
                                "attempt": fallback_attempt_index + 1,
                                "materialized": False,
                                "guide_attempt": selected_guide["attempt"],
                                "guide_translation": guide_translation or None,
                                "backend": refurnish_backend,
                                "inventory_reference_mode": inventory_reference_mode,
                            }
                        )
                    continue
                candidate_path, candidate_artifacts = materialized
                try:
                    last_angle_qc = assess_angle_candidate(
                        original_image_path,
                        candidate_path,
                        camera_mode=camera_mode
                        or ("side_angle" if style_name.startswith("Side Composition") else "overview_angle"),
                        focus_side=focus_side,
                        call_analysis_with_failover=call_analysis_with_failover,
                        analysis_model_name=analysis_model_name,
                        safe_json_from_model_text=safe_json_from_model_text,
                        require_model_qc=True,
                    )
                except Exception:
                    _discard_fallback_artifacts(candidate_artifacts)
                    raise

                locked_plate_ignored = "same_frame_or_crop" in (
                    last_angle_qc.get("reject_reasons") or []
                )
                if angle_pipeline_trace is not None:
                    angle_pipeline_trace["locked_plate_ignored"] = bool(
                        angle_pipeline_trace["locked_plate_ignored"]
                        or locked_plate_ignored
                    )
                    angle_pipeline_trace["refurnish_attempts"].append(
                        {
                            "attempt": fallback_attempt_index + 1,
                            "materialized": True,
                            "guide_attempt": selected_guide["attempt"],
                            "guide_translation": guide_translation or None,
                            "backend": refurnish_backend,
                            "inventory_reference_mode": inventory_reference_mode,
                            "locked_plate_ignored": locked_plate_ignored,
                            "qc": json.loads(json.dumps(last_angle_qc, ensure_ascii=True)),
                        }
                    )

                total_attempts_used = max_attempts + fallback_attempt_index + 1
                requested_slot_passed = bool(
                    last_angle_qc.get(
                        "passed_for_requested_slot",
                        last_angle_qc.get("passed"),
                    )
                )
                if requested_slot_passed:
                    if salvage_candidate is not None:
                        for artifact_path in salvage_candidate["artifacts"]:
                            _remove_file_quietly(artifact_path)
                        salvage_candidate = None
                    angle_fallback_artifacts.difference_update(candidate_artifacts)
                    _clear_angle_fallback_artifacts()
                    return _finalize_candidate_result(
                        candidate_path,
                        candidate_artifacts,
                        angle_qc=last_angle_qc,
                        attempts_used=total_attempts_used,
                        generation_mode="angle_generation_two_stage",
                    )

                qc_reasons = [
                    *[
                        str(reason)
                        for reason in last_angle_qc.get("reject_reasons") or []
                    ],
                    *[
                        str(reason)
                        for reason in last_angle_qc.get("warnings") or []
                    ],
                ]
                print(
                    "[DetailAngleQC] "
                    f"pipeline='two_stage' style={style_name!r} camera_mode={camera_mode!r} "
                    f"focus_side={focus_side!r} guide_attempt={selected_guide['attempt']} "
                    f"guide_translation={guide_translation!r} "
                    f"guide_qc_passed={bool(guide_qc.get('passed'))} "
                    f"refurnish_backend={refurnish_backend!r} "
                    f"inventory_reference_mode={inventory_reference_mode!r} "
                    f"locked_plate_ignored={locked_plate_ignored} "
                    f"attempt={fallback_attempt_index + 1}/{DETAIL_INTERNAL_ANGLE_TWO_STAGE_MAX_ATTEMPTS} "
                    f"physical_passed={bool(last_angle_qc.get('passed'))} requested_slot_passed=False "
                    f"reasons={', '.join(qc_reasons) or 'unknown'} "
                    f"metrics={json.dumps(last_angle_qc.get('metrics') or {}, ensure_ascii=True, sort_keys=True)} "
                    f"model={json.dumps(last_angle_qc.get('model_payload') or {}, ensure_ascii=True, sort_keys=True)}",
                    flush=True,
                )
                refurnish_retry_feedback = _refurnish_retry_feedback(last_angle_qc)
                if locked_plate_ignored:
                    refurnish_retry_feedback += (
                        f" The validated locked plate moved camera-{guide_translation.upper() or 'AS REQUESTED'}, but the "
                        "previous furnished output reverted to the source camera. Image 1 must remain the only camera authority."
                    )
                _retain_direction_only_candidate(
                    candidate_path,
                    candidate_artifacts,
                    angle_qc=last_angle_qc,
                    attempts_used=total_attempts_used,
                    generation_mode="angle_generation_two_stage",
                )

            if salvage_candidate is not None:
                salvage_candidate["attempts_used"] = max(
                    int(salvage_candidate.get("attempts_used") or 0),
                    max_attempts + DETAIL_INTERNAL_ANGLE_TWO_STAGE_MAX_ATTEMPTS,
                )
            _clear_angle_fallback_artifacts()
            return None

        for attempt_index in range(max_attempts):
            if angle_pipeline_trace is not None:
                angle_pipeline_trace["direct_attempts"] = attempt_index + 1
            generation_content = content
            if angle_retry_feedback:
                generation_content = [
                    f"{final_prompt}\n\n<ANGLE QC RETRY FEEDBACK>\n{angle_retry_feedback}",
                    *content[1:],
                ]
            response = call_gemini_with_failover(
                model_name,
                generation_content,
                {
                    "timeout": max(1.0, min(DETAIL_IMAGE_REQUEST_TIMEOUT_CAP_SEC, request_timeout_sec)),
                    "aspect_ratio": requested_ratio,
                    **({"image_size": "4K"} if is_angle_style else {}),
                    "thinking_level": "high",
                    "include_thoughts": False,
                },
                safety_settings,
                log_tag="Detail.Generate",
            )
            if not response or not hasattr(response, "candidates") or not response.candidates:
                continue
            for part in response.parts:
                if hasattr(part, "inline_data"):
                    timestamp = int(time.time())
                    safe_style_name = "".join([c for c in style_config["name"] if c.isalnum()])[:20]
                    filename = (
                        f"detail_{timestamp}_{unique_id}_{index}_a{attempt_index + 1}_{safe_style_name}.png"
                    )
                    path = os.path.join("outputs", filename)
                    candidate_artifacts = {path}
                    with open(path, "wb") as file_obj:
                        file_obj.write(part.inline_data.data)
                    normalized_path = _normalize_generated_detail_ratio(
                        path,
                        requested_ratio=requested_ratio,
                    )
                    if normalized_path is None:
                        for artifact_path in candidate_artifacts:
                            _remove_file_quietly(artifact_path)
                        continue
                    if normalized_path != path:
                        candidate_artifacts.add(normalized_path)
                        _remove_file_quietly(path)
                        path = normalized_path
                    if is_angle_style and call_analysis_with_failover:
                        try:
                            last_angle_qc = assess_angle_candidate(
                                original_image_path,
                                path,
                                camera_mode=camera_mode or ("side_angle" if style_name.startswith("Side Composition") else "overview_angle"),
                                focus_side=focus_side,
                                call_analysis_with_failover=call_analysis_with_failover,
                                analysis_model_name=analysis_model_name,
                                safe_json_from_model_text=safe_json_from_model_text,
                                require_model_qc=True,
                            )
                        except Exception:
                            for artifact_path in candidate_artifacts:
                                _remove_file_quietly(artifact_path)
                            raise
                        requested_slot_passed = bool(
                            last_angle_qc.get(
                                "passed_for_requested_slot",
                                last_angle_qc.get("passed"),
                            )
                        )
                        if not requested_slot_passed:
                            qc_reasons = [
                                *[
                                    str(reason)
                                    for reason in last_angle_qc.get("reject_reasons") or []
                                ],
                                *[
                                    str(reason)
                                    for reason in last_angle_qc.get("warnings") or []
                                ],
                            ]
                            reasons = ", ".join(qc_reasons)
                            print(
                                "[DetailAngleQC] "
                                f"style={style_name!r} camera_mode={camera_mode!r} focus_side={focus_side!r} "
                                f"attempt={attempt_index + 1}/{max_attempts} "
                                f"physical_passed={bool(last_angle_qc.get('passed'))} requested_slot_passed=False "
                                f"reasons={reasons or 'unknown'} "
                                f"metrics={json.dumps(last_angle_qc.get('metrics') or {}, ensure_ascii=True, sort_keys=True)} "
                                f"model={json.dumps(last_angle_qc.get('model_payload') or {}, ensure_ascii=True, sort_keys=True)}",
                                flush=True,
                            )
                            angle_retry_feedback = _retry_feedback_for_qc(last_angle_qc)

                            _retain_direction_only_candidate(
                                path,
                                candidate_artifacts,
                                angle_qc=last_angle_qc,
                                attempts_used=attempt_index + 1,
                                generation_mode="angle_generation",
                            )
                            continue
                    if salvage_candidate is not None:
                        for artifact_path in salvage_candidate["artifacts"]:
                            _remove_file_quietly(artifact_path)
                        salvage_candidate = None
                    return _finalize_candidate_result(
                        path,
                        candidate_artifacts,
                        angle_qc=last_angle_qc,
                        attempts_used=attempt_index + 1,
                    )
        if is_internal_angle and call_analysis_with_failover:
            two_stage_result = _run_two_stage_internal_angle_fallback()
            if two_stage_result is not None:
                return two_stage_result
        if salvage_candidate is not None:
            selected_salvage = salvage_candidate
            salvage_candidate = None
            return _finalize_candidate_result(
                selected_salvage["path"],
                selected_salvage["artifacts"],
                angle_qc=selected_salvage["angle_qc"],
                attempts_used=max(
                    int(selected_salvage.get("attempts_used") or 0),
                    max_attempts,
                ),
                direction_fallback=True,
                generation_mode=selected_salvage.get("generation_mode"),
            )
        return None
    except Exception as exc:
        print(f"!! Detail Generation Error: {exc}", flush=True)
        return None
    finally:
        if salvage_candidate is not None:
            for artifact_path in salvage_candidate.get("artifacts") or []:
                _remove_file_quietly(artifact_path)
        for artifact_path in list(angle_fallback_artifacts):
            _remove_file_quietly(artifact_path)
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
