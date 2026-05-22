def construct_dynamic_styles(analyzed_items):
    styles = []
    styles.append(
        {
            "name": "High Angle Overview",
            "prompt": (
                "CAMERA POSITION: High-angle view looking down from the ceiling.\n"
                "SUBJECT: The entire room layout exactly as shown in the original image.\n"
            ),
            "ratio": "16:9",
        }
    )
    styles.append(
        {
            "name": "Side Composition (Focus Left)",
            "prompt": (
                "COMPOSITION: Asymmetrical framing focusing heavily on the LEFT SIDE of the room.\n"
                "VISUAL PRIORITY: Highlight the furniture and details located near the left wall.\n"
                "CAMERA ANGLE: Slight pan to the left, but keep the original standing position.\n"
                "CRITICAL: Do not move any furniture. Keep the exact arrangement."
            ),
            "ratio": "16:9",
        }
    )
    styles.append(
        {
            "name": "Side Composition (Focus Right)",
            "prompt": (
                "COMPOSITION: Asymmetrical framing focusing heavily on the RIGHT SIDE of the room.\n"
                "VISUAL PRIORITY: Highlight the furniture and details located near the right wall.\n"
                "CAMERA ANGLE: Slight pan to the right, but keep the original standing position.\n"
                "CRITICAL: Do not move any furniture. Keep the exact arrangement."
            ),
            "ratio": "16:9",
        }
    )

    ranked_items = list(analyzed_items or [])
    try:
        ranked_items = sorted(ranked_items, key=lambda it: int((it or {}).get("volume_rank") or 10**9))
    except Exception:
        ranked_items = list(analyzed_items or [])

    count = 0
    for item in ranked_items:
        if count >= 20:
            break

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
                "target_box_2d": item.get("box_2d"),
                "target_box_source": item.get("box_source"),
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

    return styles
