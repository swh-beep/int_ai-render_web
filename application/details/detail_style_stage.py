import os


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


def _normalized_label(value) -> str:
    return " ".join(str(value or "").strip().lower().split())


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


def _is_source_backed_detail_target(item) -> bool:
    if not isinstance(item, dict):
        return False

    crop_path = str(item.get("crop_path") or "").strip()
    if crop_path:
        return True

    if _normalize_box(item.get("source_box_2d")) is not None:
        return True

    item_id = _normalized_label((item or {}).get("item_id"))
    if item_id:
        return True

    target_key = _normalized_label((item or {}).get("target_key"))
    if target_key and not target_key.startswith("detail_"):
        return True

    return False


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
        if (
            label_key
            and accepted_label_key
            and label_key == accepted_label_key
            and family_key
            and accepted_family_key
            and family_key == accepted_family_key
            and _box_iou(box, (accepted or {}).get("box_2d")) >= 0.45
        ):
            return True
    return False


def construct_dynamic_styles(analyzed_items):
    styles = []
    styles.append(
        {
            "name": "High Angle Overview",
            "prompt": (
                "CAMERA POSITION: Moderately elevated high-angle overview from just above standing eye level, "
                "as if photographed by a person holding the camera slightly above head height inside the room.\n"
                "VIEWPOINT CHANGE REQUIRED: Shift the camera laterally or backward from the source position so the output is not the same centered front-facing frame.\n"
                "CAMERA TILT: Mild downward tilt only, enough to reveal more top surfaces of furniture, floor area, and room depth. Keep the horizon natural and the vertical lines stable.\n"
                "FORBIDDEN CAMERA: Do NOT use bird's-eye, top-down, drone, ceiling-mounted, surveillance, or "
                "extreme overhead viewpoints.\n"
                "COMPOSITION: Show the entire room layout exactly as shown in the original image, but from a "
                "natural elevated in-room overview near a doorway or corner.\n"
                "FAILURE CONDITION: If the output looks like the original source frame with only tiny crop, zoom, or exposure changes, it is wrong.\n"
                "OUTPUT FORMAT: Wide horizontal 16:9 angle shot, not a portrait close-up or detail crop.\n"
            ),
            "ratio": "16:9",
            "camera_mode": "overview_angle",
        }
    )
    styles.append(
        {
            "name": "Side Composition (Focus Left)",
            "prompt": (
                "COMPOSITION: Asymmetrical side-angle framing focusing heavily on the LEFT SIDE of the room.\n"
                "CAMERA POSITION: Place the camera so it faces the LEFT wall/furniture zone at a diagonal angle.\n"
                "VIEWPOINT CHANGE REQUIRED: This must be a visibly different diagonal side-angle photograph from the source image, not the same centered straight-on shot.\n"
                "SCREEN COMPOSITION: The left-side room plane and objects located on the left half of the source image must fill about 70 percent or more of the frame.\n"
                "SUBJECT LOCK: The LEFT side of the room is the subject. The opposite/right side must be cropped out, peripheral, or background-only, and must NOT be the largest/closest foreground area.\n"
                "PARALLAX REQUIREMENT: Show clear side-plane perspective and depth on the left-side objects. A flat front-facing copy of the source is forbidden.\n"
                "FRAMING PERMISSION: It is acceptable if some opposite-side objects are outside the frame; do not keep both sides equally visible.\n"
                "FAILURE CONDITION: If the output looks like the original centered camera position with only a crop, zoom, or tiny pan, it is wrong.\n"
                "CRITICAL: Do not move any furniture. Keep the exact arrangement.\n"
                "OUTPUT FORMAT: Wide horizontal 16:9 angle shot with room context visible, not a portrait detail crop."
            ),
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "left",
        }
    )
    styles.append(
        {
            "name": "Side Composition (Focus Right)",
            "prompt": (
                "COMPOSITION: Asymmetrical side-angle framing focusing heavily on the RIGHT SIDE of the room.\n"
                "CAMERA POSITION: Place the camera so it faces the RIGHT side of the room at a diagonal angle.\n"
                "VIEWPOINT CHANGE REQUIRED: This must be a visibly different diagonal side-angle photograph from the source image, not the same centered straight-on shot.\n"
                "SCREEN COMPOSITION: The right-side room plane and objects located on the right half of the source image must fill about 70 percent or more of the frame.\n"
                "SUBJECT LOCK: The RIGHT side of the room is the subject. The opposite/left side must be cropped out, peripheral, or background-only, and must NOT be the largest/closest foreground area.\n"
                "PARALLAX REQUIREMENT: Show clear side-plane perspective and depth on the right-side objects. A flat front-facing copy of the source is forbidden.\n"
                "FRAMING PERMISSION: It is acceptable if some opposite-side objects are outside the frame; do not keep both sides equally visible.\n"
                "FAILURE CONDITION: If the output looks like the original centered camera position with only a crop, zoom, or tiny pan, it is wrong.\n"
                "CRITICAL: Do not move any furniture. Keep the exact arrangement.\n"
                "OUTPUT FORMAT: Wide horizontal 16:9 angle shot with room context visible, not a portrait detail crop."
            ),
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "right",
        }
    )

    ranked_items = list(analyzed_items or [])
    try:
        ranked_items = sorted(ranked_items, key=lambda it: int((it or {}).get("volume_rank") or 10**9))
    except Exception:
        ranked_items = list(analyzed_items or [])

    source_backed_items = [item for item in ranked_items if _is_source_backed_detail_target(item)]
    detail_items = source_backed_items if source_backed_items else ranked_items

    count = 0
    accepted_detail_targets = []
    for item in detail_items:
        if count >= 20:
            break
        if _is_duplicate_detail_target(item, accepted_detail_targets):
            continue

        label = item["label"]
        desc = item.get("description", "")
        box = item.get("box_2d", [0, 0, 1000, 1000])
        box_source = str(item.get("box_source") or "")

        use_coords = True
        try:
            if box_source == "item_image_full":
                use_coords = False
            elif isinstance(box, list) and len(box) == 4:
                ymin, xmin, ymax, xmax = [float(v) for v in box]
                if ymin <= 1 and xmin <= 1 and ymax >= 999 and xmax >= 999:
                    use_coords = False
        except Exception:
            use_coords = True

        if use_coords:
            target_coordinates_line = f"TARGET COORDINATES: Focus on area {box} (Normalized 0-1000)."
        else:
            target_coordinates_line = (
                "TARGET COORDINATES: Not available for this item (direct item image mode). "
                "Identify the matching object by design/material and keep original in-room position."
            )

        lens_type = "85mm Telephoto Lens"
        context_instruction = "Include parts of neighboring furniture to prove location."
        position_instruction = "Do NOT move this item. Shoot it exactly where it stands."

        if "rug" in label.lower() or "carpet" in label.lower():
            position_instruction = "CRITICAL: The rug MUST be UNDER the sofas/tables. Show furniture legs pressing on it."
            lens_type = "50mm Standard Lens"
        elif any(x in label.lower() for x in ["light", "lamp", "chandelier", "pendant", "sconce"]):
            position_instruction = "CRITICAL: Show the connection to the ceiling/wall. Do NOT crop the cord or chain."
            context_instruction = "ZOOM OUT significantly. You MUST show what this light is illuminating below (e.g., the table or floor). Do NOT fill the frame with just the bulb."
            lens_type = "35mm Wide Lens"

        styles.append(
            {
                "name": f"Detail: {label}",
                "target_label": label,
                "target_key": item.get("target_key"),
                "source_index": item.get("source_index"),
                "prompt": (
                    f"ACT AS: Documentary Interior Photographer.\n"
                    f"TASK: Take a candid shot of the '{label}' strictly IN-SITU.\n\n"
                    f"TARGET VISUALS: {desc}\n"
                    f"{target_coordinates_line}\n\n"
                    f"<CRITICAL: ABSOLUTE LAYOUT FREEZE>\n"
                    f"1. {position_instruction}\n"
                    f"2. {context_instruction}\n"
                    "3. **ALLOW OCCLUSION:** It is okay if the object is partially blocked. This adds realism.\n"
                    f"4. **LENS:** {lens_type}. Depth of Field is allowed, but geometry change is NOT."
                ),
                "ratio": "4:5",
            }
        )
        count += 1
        accepted_detail_targets.append(item)

    return styles

