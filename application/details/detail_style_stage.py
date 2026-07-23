import os

from application.render.curtain_material_stage import CURTAIN_BLACKOUT_PERCENT, CURTAIN_DETAIL_MODE, CURTAIN_DETAIL_ROLE

_INTERNAL_ANGLE_STYLE_NAMES = (
    "High Angle Overview",
    "Side Composition (Focus Left)",
    "Side Composition (Focus Right)",
)


def _normalize_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        ymin, xmin, ymax, xmax = [float(v) for v in value]
    except Exception:
        return None
    if ymax <= ymin or xmax <= xmin:
        return None
    return [ymin, xmin, ymax, xmax]


def _box_iou(box_a, box_b) -> float:
    a = _normalize_box(box_a)
    b = _normalize_box(box_b)
    if not a or not b:
        return 0.0
    top = max(a[0], b[0])
    left = max(a[1], b[1])
    bottom = min(a[2], b[2])
    right = min(a[3], b[3])
    if bottom <= top or right <= left:
        return 0.0
    inter = (bottom - top) * (right - left)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def _is_full_frame_box(box) -> bool:
    normalized = _normalize_box(box)
    if not normalized:
        return False
    ymin, xmin, ymax, xmax = normalized
    return ymin <= 1 and xmin <= 1 and ymax >= 999 and xmax >= 999


_PRODUCT_BACKED_CURRENT_RENDER_BOX_SOURCES = {
    "detail_current_image_analysis",
    "selected_variant_review",
}


def _has_localized_render_box(item) -> bool:
    if not isinstance(item, dict):
        return False
    box = _normalize_box(item.get("box_2d"))
    if not box or _is_full_frame_box(box):
        return False
    box_source = str(item.get("box_source") or "").strip().lower()
    if _is_product_backed_detail_target(item):
        if box_source == "product_reference_localization":
            return str(item.get("detail_localization_status") or "").strip() == "product_reference_verified"
        return box_source in _PRODUCT_BACKED_CURRENT_RENDER_BOX_SOURCES
    return box_source not in {"item_image_full", "cached_detail_snapshot"}


def _normalized_label(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _string_field(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "categoryName", "category_name", "title", "value"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
    return ""


def _category_path_leaf(value) -> str:
    text = _string_field(value)
    if not text:
        return ""
    parts = [text]
    for separator in (">", "/", "|"):
        if separator in text:
            parts = [part.strip() for part in text.split(separator)]
            break
    for part in reversed(parts):
        if part:
            return part
    return text


def _readable_category_label(value) -> str:
    text = _string_field(value)
    if not text:
        return ""
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def _category_detail_label(item) -> str:
    if not isinstance(item, dict):
        return ""
    for key in ("subCategory", "sub_category"):
        text = _readable_category_label(item.get(key))
        if text:
            return text
    text = _readable_category_label(_category_path_leaf(item.get("category_path")))
    if text:
        return text
    for key in ("mainCategory", "main_category", "category"):
        text = _readable_category_label(item.get(key))
        if text:
            return text
    return ""


def _detail_label(item) -> tuple[str, str | None]:
    original_label = str((item or {}).get("label") or "").strip()
    category_label = _category_detail_label(item)
    if category_label:
        preserved_label = original_label if original_label and _normalized_label(original_label) != _normalized_label(category_label) else None
        return category_label, preserved_label
    if original_label:
        return original_label, None
    return _readable_category_label(item.get("category_canonical")) or _detail_identity_family(item), None


_GENERIC_DECOR_DETAIL_KEYS = {
    "accessory",
    "accessories",
    "decor",
    "decoration",
    "decorative object",
    "object",
    "small accessory",
    "small decor",
    "small object",
    "shelf decor",
    "table decor",
    "tabletop decor",
    "wall decor",
}


_EXCLUDED_DETAIL_TARGET_KEYS = {
    "curtain",
    "curtains",
    "drape",
    "drapes",
    "window",
    "windows",
    "rug",
    "rugs",
    "area rug",
    "carpet",
    "carpets",
    "mat",
    "mats",
    "커튼",
    "창문",
    "러그",
    "카펫",
    "카페트",
}


def _detail_identity_family(item) -> str:
    if not isinstance(item, dict):
        return ""
    identity_profile = item.get("identity_profile") or {}
    product_identity = item.get("product_identity") or {}
    return _normalized_label(
        product_identity.get("family")
        or identity_profile.get("family")
        or item.get("category_canonical")
        or item.get("category")
        or ""
    )


def _is_generic_decor_detail_target(item) -> bool:
    if not isinstance(item, dict):
        return False
    keys = {
        _normalized_label(item.get("label")),
        _detail_identity_family(item),
    }
    return any(key in _GENERIC_DECOR_DETAIL_KEYS for key in keys if key)


def _is_excluded_detail_target(item) -> bool:
    if not isinstance(item, dict):
        return False
    keys = {
        _normalized_label(item.get("label")),
        _normalized_label(item.get("category")),
        _normalized_label(item.get("category_canonical")),
        _detail_identity_family(item),
    }
    for key in keys:
        if not key:
            continue
        if key in _EXCLUDED_DETAIL_TARGET_KEYS:
            return True
        if any(f" {excluded} " in f" {key} " for excluded in _EXCLUDED_DETAIL_TARGET_KEYS):
            return True
    return False


def _is_curtain_material_detail_target(item) -> bool:
    if not isinstance(item, dict):
        return False
    return bool(
        item.get("detail_role") == CURTAIN_DETAIL_ROLE
        and item.get("material_reference_path")
        and _normalized_label(item.get("category_canonical") or item.get("category")) == "curtain"
    )


def _construct_curtain_material_style(item: dict, detail_index: int) -> dict:
    label, original_label = _detail_label(item)
    style = {
        "name": f"Detail: {label}",
        "target_label": label,
        "target_key": item.get("target_key"),
        "source_index": item.get("source_index"),
        "detail_index": detail_index,
        "target_category": item.get("category") or "curtain",
        "target_category_canonical": "curtain",
        "detail_mode": CURTAIN_DETAIL_MODE,
        "priority_detail": True,
        "material_reference_path": item.get("material_reference_path"),
        "blackout_percent": int(item.get("blackout_percent") or CURTAIN_BLACKOUT_PERCENT),
        "prompt": (
            f"Create a generated editorial close detail of the existing curtain in this exact room, using the supplied curtain "
            f"material swatch as the absolute reference for color, weave, threads, and surface texture. Express exactly "
            f"{int(item.get('blackout_percent') or CURTAIN_BLACKOUT_PERCENT)}% blackout in the fabric opacity without darkening "
            "the room. Keep the room exposure, lighting, white balance, curtain position, curtain folds, architecture, furniture, "
            "and object layout unchanged. Choose a clear visible curtain section even when furniture overlaps the main view."
        ),
        "ratio": "4:5",
    }
    if original_label:
        style["target_product_label"] = original_label
    return style


def _is_source_backed_detail_target(item) -> bool:
    if not isinstance(item, dict):
        return False

    crop_path = str(item.get("crop_path") or "").strip()
    item_id = _normalized_label((item or {}).get("item_id"))
    target_key = _normalized_label((item or {}).get("target_key"))
    has_product_reference = bool(
        item_id.startswith("product_")
        or target_key.startswith("cart_product")
        or target_key.startswith("cart_")
        or target_key.startswith("internal_")
    )

    if _has_localized_render_box(item) and has_product_reference:
        return True

    if (
        has_product_reference
        and _normalize_box(item.get("source_box_2d")) is not None
        and not _is_full_frame_box(item.get("source_box_2d"))
    ):
        return True

    if has_product_reference and crop_path:
        return True

    return False


def _is_product_backed_detail_target(item) -> bool:
    if not isinstance(item, dict):
        return False
    target_key = _normalized_label((item or {}).get("target_key"))
    item_id = _normalized_label((item or {}).get("item_id"))
    crop_path = str((item or {}).get("crop_path") or "").strip()
    return bool(
        crop_path
        and (
            target_key.startswith("cart_")
            or target_key.startswith("cart-product")
            or target_key.startswith("cart_product")
            or target_key.startswith("internal_")
            or item_id.startswith("product_")
            or item_id.startswith("cart_")
        )
    )


def _is_duplicate_detail_target(item, accepted_items) -> bool:
    target_key = _normalized_label((item or {}).get("target_key"))
    item_id = _normalized_label((item or {}).get("item_id"))
    crop_key = _normalized_label(os.path.basename(str((item or {}).get("crop_path") or "")))
    label_key = _normalized_label((item or {}).get("label"))
    family_key = _detail_identity_family(item)
    box = (item or {}).get("box_2d")

    for accepted in accepted_items or []:
        accepted_target_key = _normalized_label((accepted or {}).get("target_key"))
        if target_key and accepted_target_key and target_key == accepted_target_key:
            return True

        accepted_item_id = _normalized_label((accepted or {}).get("item_id"))
        if item_id and accepted_item_id and item_id == accepted_item_id:
            return True

        accepted_crop_key = _normalized_label(os.path.basename(str((accepted or {}).get("crop_path") or "")))
        if crop_key and accepted_crop_key and crop_key == accepted_crop_key:
            return True

        accepted_label_key = _normalized_label((accepted or {}).get("label"))
        accepted_family_key = _detail_identity_family(accepted)
        same_label = bool(label_key and accepted_label_key and label_key == accepted_label_key)
        same_family = bool(family_key and accepted_family_key and family_key == accepted_family_key)
        both_detection_only = not _is_source_backed_detail_target(item) and not _is_source_backed_detail_target(accepted)
        if same_label and both_detection_only and (same_family or not family_key or not accepted_family_key):
            overlap = _box_iou(box, (accepted or {}).get("box_2d"))
            if overlap > 0.0:
                return overlap >= 0.35
            if _normalize_box(box) is None or _normalize_box((accepted or {}).get("box_2d")) is None:
                return True
            return False
        if (
            same_label
            and same_family
            and _box_iou(box, (accepted or {}).get("box_2d")) >= 0.45
        ):
            return True
    return False


def construct_internal_angle_styles():
    return [
        {
            "name": "High Angle Overview",
            "prompt": (
                "CAMERA POSITION: Create a genuine nearby high-angle camera move from inside the same room, "
                "as if the camera is lifted above standing eye level and pitched downward.\n"
                "VIEWPOINT REQUIRED: The output must not match the main camera or a crop of the main image. "
                "Show a measurable camera-height change, more visible top surfaces, and consistent perspective shift while preserving the same physical room.\n"
                "CAMERA TILT: Mild-to-moderate downward tilt, enough to reveal more table, sofa, rug, and floor top planes. Keep the horizon natural and the vertical lines stable.\n"
                "FORBIDDEN CAMERA: Do NOT use bird's-eye, top-down, drone, ceiling-mounted, surveillance, or extreme overhead viewpoints.\n"
                "COMPOSITION: Show the same room layout and objects from the elevated camera. Physical furniture placement, count, shape, material, and orientation must remain unchanged.\n"
                "FAILURE CONDITION: If this could be achieved by crop, zoom, or reframe from the main image, it is wrong. If architecture or furniture identity changes, it is wrong.\n"
                "OUTPUT FORMAT: Wide horizontal 16:9 angle shot, not a portrait close-up or detail crop.\n"
            ),
            "ratio": "16:9",
            "camera_mode": "overview_angle",
        },
        {
            "name": "Side Composition (Focus Left)",
            "prompt": (
                "CAMERA POSITION: Natural standing-height side-angle viewpoint after a real lateral camera move toward the LEFT side of the original room composition.\n"
                "VIEWPOINT REQUIRED: Translate the camera left and add a modest yaw back into the room. The view must show real parallax, changed occlusions, and newly visible side planes caused by camera movement.\n"
                "COMPOSITION: The left side of the same physical room should become dominant while preserving furniture identities, room architecture, lighting direction, relative placement, and world-space orientation.\n"
                "ALLOWED OCCLUSION: It is acceptable for right-side objects to be cropped out by the frame; do not relocate them to keep them visible.\n"
                "FORBIDDEN: Do NOT mirror the room, duplicate furniture, invent new furniture, or keep the exact centered source camera.\n"
                "FAILURE CONDITION: If this could be achieved by crop, zoom, or reframe from the main image, it is wrong. If only the room rotates while furniture stays front-facing, it is wrong.\n"
                "OUTPUT FORMAT: Wide horizontal 16:9 angle shot, not a portrait detail crop.\n"
            ),
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "left",
        },
        {
            "name": "Side Composition (Focus Right)",
            "prompt": (
                "CAMERA POSITION: Natural standing-height side-angle viewpoint after a real lateral camera move toward the RIGHT side of the original room composition.\n"
                "VIEWPOINT REQUIRED: Translate the camera right and add a modest yaw back into the room. The view must show real parallax, changed occlusions, and newly visible side planes caused by camera movement.\n"
                "COMPOSITION: The right side of the same physical room should become dominant while preserving furniture identities, room architecture, lighting direction, relative placement, and world-space orientation.\n"
                "ALLOWED OCCLUSION: It is acceptable for left-side objects to be cropped out by the frame; do not relocate them to keep them visible.\n"
                "FORBIDDEN: Do NOT mirror the room, duplicate furniture, invent new furniture, or keep the exact centered source camera.\n"
                "FAILURE CONDITION: If this could be achieved by crop, zoom, or reframe from the main image, it is wrong. If only the room rotates while furniture stays front-facing, it is wrong.\n"
                "OUTPUT FORMAT: Wide horizontal 16:9 angle shot, not a portrait detail crop.\n"
            ),
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "right",
        },
    ]


def with_internal_angle_styles(styles):
    current_styles = list(styles or [])
    current_names = tuple(str(style.get("name") or "") for style in current_styles[:3] if isinstance(style, dict))
    if current_names == _INTERNAL_ANGLE_STYLE_NAMES:
        return current_styles
    return [*construct_internal_angle_styles(), *current_styles]


def construct_dynamic_styles(analyzed_items):
    styles = []

    ranked_items = list(analyzed_items or [])
    try:
        ranked_items = sorted(ranked_items, key=lambda it: int((it or {}).get("volume_rank") or 10**9))
    except Exception:
        ranked_items = list(analyzed_items or [])

    curtain_material_items = [item for item in ranked_items if _is_curtain_material_detail_target(item)]
    ordinary_items = [item for item in ranked_items if not _is_curtain_material_detail_target(item)]
    localized_items = [item for item in ordinary_items if _has_localized_render_box(item)]
    detail_items = [*curtain_material_items, *(localized_items if localized_items else ordinary_items)]

    count = 0
    accepted_detail_targets = []
    for item in detail_items:
        if count >= 20:
            break
        if _is_curtain_material_detail_target(item):
            styles.append(_construct_curtain_material_style(item, count + 1))
            count += 1
            accepted_detail_targets.append(item)
            continue
        if _is_excluded_detail_target(item):
            continue
        if _is_product_backed_detail_target(item) and not _has_localized_render_box(item):
            continue
        if _is_duplicate_detail_target(item, accepted_detail_targets):
            continue

        label, original_label = _detail_label(item)

        source_backed = _is_source_backed_detail_target(item)
        style = {
            "name": f"Detail: {label}",
            "target_label": label,
            "target_key": item.get("target_key"),
            "source_index": item.get("source_index"),
            "detail_index": count + 1,
            "target_category": item.get("category") or "",
            "target_category_canonical": item.get("category_canonical") or "",
            "target_box_2d": item.get("box_2d"),
            "target_source_box_2d": item.get("source_box_2d"),
            "target_box_source": item.get("box_source"),
            "target_crop_path": item.get("crop_path"),
            "target_reference_features": item.get("reference_features"),
            "prompt": (
                f"Create a detailed editorial furniture-magazine photograph focused on the {label} area in this exact room. "
                "Use the provided main image as the sole source of truth. Preserve the furniture shape, count, placement, "
                "material, color, lighting direction, and room architecture. Do not add, remove, replace, or rearrange anything."
            ),
            "ratio": "4:5",
        }
        if original_label:
            style["target_product_label"] = original_label
        if source_backed:
            style["detail_mode"] = "product_identity_lock"
            style["prompt"] += (
                " Keep the product-backed target key, source crop, and reference feature contract locked to this same object."
            )
        else:
            style["simple_scene_detail"] = True
        styles.append(style)
        count += 1
        accepted_detail_targets.append(item)

    return styles
