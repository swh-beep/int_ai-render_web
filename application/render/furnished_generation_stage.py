import json
import os
import time
from typing import Any, Callable

from PIL import Image


def generate_furnished_room(
    room_path,
    style_prompt,
    ref_path,
    unique_id,
    *,
    furniture_specs=None,
    furniture_specs_json=None,
    room_dimensions=None,
    placement_instructions=None,
    scale_guide_path=None,
    primary_item=None,
    room_dims_parsed=None,
    wall_span_norm=None,
    size_hierarchy=None,
    start_time=0,
    room_planes=None,
    windows_present=None,
    room_analysis_text=None,
    enable_scale_check=False,
    total_timeout_limit: float,
    detect_windows_present: Callable[[str], bool],
    logger,
    parse_room_dimensions_mm: Callable[[str], dict],
    normalize_dims_dict: Callable[[dict], dict],
    is_two_dim_ok_label: Callable[[str], bool],
    available_dim_axes: Callable[[dict], set],
    summary_ref,
    log_brief: bool,
    log_summary: bool,
    allow_all_safety_settings: Callable[[], Any],
    call_gemini_with_failover: Callable[..., Any],
    model_name: str,
    match_aspect_to_target: Callable[[str, str], str | None],
    validate_furnished_scale: Callable[..., tuple[bool, list]],
):
    if time.time() - start_time > total_timeout_limit:
        return None
    room_img = None
    extra_imgs = []
    try:
        room_img = Image.open(room_path)
        if windows_present is None:
            windows_present = detect_windows_present(room_path)
        try:
            logger.info(f"[WindowCheck] present={bool(windows_present)} path={room_path}")
        except Exception:
            pass

        width, height = room_img.size
        is_portrait = height > width
        ratio_instruction = "PORTRAIT (4:5 Ratio)" if is_portrait else "LANDSCAPE (16:9 Ratio)"
        expected_ratio = (4 / 5) if is_portrait else (16 / 9)
        ratio_tol = 0.1
        system_instruction = "You are an expert interior designer AI."

        room_analysis_context = ""
        if room_analysis_text:
            room_analysis_context = (
                "\n<ROOM STRUCTURE & SCALE ANALYSIS (LONG)>\n"
                "Use this to preserve architecture and scale. Do NOT invent new openings.\n"
                f"{room_analysis_text}\n"
                "--------------------------------------------------\n"
            )

        specs_context = ""
        if furniture_specs:
            specs_context = (
                "\n<REFERENCE FURNITURE LIST (GUIDANCE ONLY)>\n"
                "The following list describes the items detected from the moodboard.\n"
                "Use this as a soft reference for material, color, shape, and scale cues.\n"
                "If there is any conflict, prioritize the provided furniture cutout images.\n"
                "Respect quantities exactly. If qty>1, render multiple identical instances.\n"
                "Do NOT add extra items. Do NOT omit any listed items.\n"
                "Do NOT replace any listed item with a generic substitute (no sofa instead of a desk, etc.).\n"
                f"{furniture_specs}\n"
                "--------------------------------------------------\n"
            )

        dims_table_context = ""
        try:
            if furniture_specs_json and isinstance(furniture_specs_json, dict):
                rows = []
                for it in (furniture_specs_json.get("items") or []):
                    lbl = (it.get("label") or "").strip()
                    qty = it.get("qty") or 1
                    dm = it.get("dims_mm") or {}
                    w = dm.get("width_mm")
                    d = dm.get("depth_mm")
                    h = dm.get("height_mm")
                    if any([w, d, h]):
                        qtxt = f" qty={qty}" if qty and qty > 1 else ""
                        rows.append(f"- {lbl}{qtxt}: W={w or 'null'}mm, D={d or 'null'}mm, H={h or 'null'}mm")
                if rows:
                    dims_table_context = (
                        "\n<FURNITURE DIMENSIONS TABLE (MM) - REFERENCE>\n"
                        "Use these real-world measurements as guidance. Do NOT invent new sizes.\n"
                        "Items with null W/D/H are incomplete; do NOT guess missing numbers. Use visual scale cues and keep within room limits.\n"
                        + "\n".join(rows)
                        + "\nGuidelines:\n"
                        "- No furniture item should exceed room width or room depth.\n"
                        "- Rugs/carpets: if rug width is within 10% of room width, it should visually span almost wall-to-wall.\n"
                        "- Wall storage/sideboard: if width is <= 1500mm in specs, it should NOT look like it spans most of the wall.\n"
                        "--------------------------------------------------\n"
                    )
        except Exception:
            dims_table_context = ""

        spatial_context = ""
        calculated_analysis = ""
        ratio_rules_context = ""
        incomplete_dims_context = ""
        inventory_context = ""

        try:
            _room_dims = room_dims_parsed or parse_room_dimensions_mm(room_dimensions or "")
            room_w = int(_room_dims.get("width_mm") or 0)
            room_d = int(_room_dims.get("depth_mm") or 0)
            room_h = int(_room_dims.get("height_mm") or 0)

            _primary = (
                primary_item
                or (furniture_specs_json or {}).get("primary_scale")
                or (furniture_specs_json or {}).get("primary")
                or {}
            )
            _p_dims = _primary.get("dims_mm") or {}
            p_w = int(_p_dims.get("width_mm") or 0)
            p_d = int(_p_dims.get("depth_mm") or 0)
            p_h = int(_p_dims.get("height_mm") or 0)

            if not p_w and furniture_specs_json and isinstance(furniture_specs_json, dict):
                try:
                    p_w = int(furniture_specs_json.get("max_width_mm") or 0)
                except Exception:
                    pass

            try:
                if furniture_specs_json and isinstance(furniture_specs_json, dict):
                    complete_items = []
                    incomplete_items = []
                    inventory_labels = []

                    for it in (furniture_specs_json.get("items") or []):
                        label = (it.get("label") or "").strip() or "Unknown Item"
                        inventory_labels.append(label)
                        dm = it.get("dims_mm") or {}
                        w = int(dm.get("width_mm") or 0)
                        d = int(dm.get("depth_mm") or 0)
                        h = int(dm.get("height_mm") or 0)
                        missing = []
                        if w <= 0:
                            missing.append("W")
                        if d <= 0:
                            missing.append("D")
                        if h <= 0:
                            missing.append("H")

                        allow_2d = is_two_dim_ok_label(label)
                        axes = available_dim_axes(dm)
                        if allow_2d and len(axes) >= 2:
                            missing = []
                        if missing:
                            incomplete_items.append((label, missing))
                            if log_brief:
                                print(f"[Dims] FAIL {label} missing {','.join(missing)}", flush=True)
                            try:
                                summary = summary_ref.get()
                                if isinstance(summary, dict):
                                    summary["dims_fail"] = summary.get("dims_fail", 0) + 1
                            except Exception:
                                pass
                            continue
                        complete_items.append({"label": label, "w": w, "d": d, "h": h})

                    if incomplete_items:
                        incomplete_dims_context = (
                            "\n<INCOMPLETE DIMENSIONS (DO NOT IGNORE)>\n"
                            + "\n".join([f"- {lbl}: missing {', '.join(miss)}" for lbl, miss in incomplete_items])
                            + "\nRule: Do NOT invent missing numbers, but you MUST still render these items.\n"
                            + "Estimate size from the moodboard and keep within room limits and relative proportions.\n"
                            + "--------------------------------------------------\n"
                        )

                        if inventory_labels:
                            inventory_context = (
                                "\n<ITEM INVENTORY (MUST RENDER ALL ITEMS)>\n"
                                f"Total items: {len(inventory_labels)}\n"
                                + "\n".join([f"- {lbl}" for lbl in inventory_labels])
                                + "\nRule: Every listed item must appear in the final image (exactly once unless the list says multiples).\n"
                                + "If space is tight, reduce size slightly and place items on shelves/tables or walls; do not omit.\n"
                                + "--------------------------------------------------\n"
                            )

                    def _ratio_str(value, total, cap=None):
                        if not value or not total:
                            return "n/a"
                        pct = round((value / total) * 100, 1)
                        if cap is not None and pct > cap:
                            return f"{cap:.1f}% (cap)"
                        return f"{pct:.1f}%"

                    abs_lines = []
                    abs_warn_labels = []
                    if room_w > 0 and room_d > 0 and room_h > 0:
                        for it in complete_items:
                            w = it["w"]
                            d = it["d"]
                            h = it["h"]
                            label = it["label"]
                            abs_lines.append(
                                f"- {label}: room W={_ratio_str(w, room_w, 100.0)}, D={_ratio_str(d, room_d, 100.0)}, H={_ratio_str(h, room_h, 100.0)}"
                            )
                            over = []
                            if w > room_w:
                                over.append("W")
                            if d > room_d:
                                over.append("D")
                            if h > room_h:
                                over.append("H")
                            if over:
                                abs_warn_labels.append(label)
                            try:
                                summary = summary_ref.get()
                                if isinstance(summary, dict):
                                    summary["dims_warn"] = summary.get("dims_warn", 0) + 1
                            except Exception:
                                pass
                    else:
                        if log_brief and not log_summary:
                            print("[Dims] WARN room W/D/H missing; skip absolute ratios", flush=True)
                        try:
                            summary = summary_ref.get()
                            if isinstance(summary, dict):
                                summary["dims_warn"] = summary.get("dims_warn", 0) + 1
                        except Exception:
                            pass

                    rel_lines = []
                    rel_warn_labels = []
                    primary_label = _primary.get("label", "Primary Furniture")
                    if p_w > 0 and p_d > 0 and p_h > 0:
                        for it in complete_items:
                            label = it["label"]
                            if label == primary_label:
                                continue
                            rel_w = round((it["w"] / p_w) * 100, 1)
                            rel_d = round((it["d"] / p_d) * 100, 1)
                            rel_h = round((it["h"] / p_h) * 100, 1)
                            rel_lines.append(f"- {label}: W={rel_w:.1f}%, D={rel_d:.1f}%, H={rel_h:.1f}% of {primary_label}")
                            if rel_w > 100 or rel_d > 100 or rel_h > 100:
                                rel_warn_labels.append(label)
                            try:
                                summary = summary_ref.get()
                                if isinstance(summary, dict):
                                    summary["dims_warn"] = summary.get("dims_warn", 0) + 1
                            except Exception:
                                pass
                    elif log_brief:
                        print("[Dims] WARN primary W/D/H missing; skip relative ratios", flush=True)

                    if log_brief and not log_summary:
                        if abs_warn_labels:
                            sample = ", ".join(abs_warn_labels[:3])
                            extra = len(abs_warn_labels) - 3
                            suffix = f" (+{extra} more)" if extra > 0 else ""
                            print(f"[Dims] WARN {len(abs_warn_labels)} items exceed room W/D/H: {sample}{suffix}", flush=True)
                        if rel_warn_labels:
                            sample = ", ".join(rel_warn_labels[:3])
                            extra = len(rel_warn_labels) - 3
                            suffix = f" (+{extra} more)" if extra > 0 else ""
                            print(f"[Dims] WARN {len(rel_warn_labels)} items larger than primary: {sample}{suffix}", flush=True)

                    order_w = " > ".join([x["label"] for x in sorted(complete_items, key=lambda x: x["w"], reverse=True)]) if complete_items else ""
                    order_d = " > ".join([x["label"] for x in sorted(complete_items, key=lambda x: x["d"], reverse=True)]) if complete_items else ""
                    order_h = " > ".join([x["label"] for x in sorted(complete_items, key=lambda x: x["h"], reverse=True)]) if complete_items else ""
                    height_caps = []
                    for it in complete_items:
                        if it["h"] > 0:
                            height_caps.append(f"- {it['label']}: H must be <= {it['h']}mm")

                    if abs_lines or rel_lines or order_w or order_d or order_h:
                        ratio_rules_context = "\n<CRITICAL: W/D/H RATIO RULES (ALL FURNITURE)>\nApply ratios only to items with complete W/D/H.\n"
                        if abs_lines:
                            ratio_rules_context += "ABSOLUTE RATIOS (item vs room):\n" + "\n".join(abs_lines) + "\n"
                        else:
                            ratio_rules_context += "ABSOLUTE RATIOS: room W/D/H missing or invalid.\n"
                        if rel_lines:
                            ratio_rules_context += f"RELATIVE RATIOS (item vs {primary_label}):\n" + "\n".join(rel_lines) + "\n"
                        if order_w or order_d or order_h:
                            ratio_rules_context += (
                                "DIMENSION ORDER (largest -> smallest):\n"
                                + f"- WIDTH: {order_w}\n"
                                + f"- DEPTH: {order_d}\n"
                                + f"- HEIGHT: {order_h}\n"
                            )
                        if height_caps:
                            ratio_rules_context += "HEIGHT CAPS (STRICT):\n" + "\n".join(height_caps) + "\n"
                        ratio_rules_context += "--------------------------------------------------\n"
            except Exception:
                pass

            if room_w > 0 and p_w > 0:
                occ = round((p_w / room_w) * 100, 1)
                gap_total_mm = room_w - p_w
                gap_side_mm = int(gap_total_mm / 2) if gap_total_mm > 0 else 0
                primary_d_disp = f"{p_d}mm" if p_d > 0 else "unknown"
                primary_h_disp = f"{p_h}mm" if p_h > 0 else "unknown"
                room_d_disp = f"{room_d}mm" if room_d > 0 else "unknown"
                room_h_disp = f"{room_h}mm" if room_h > 0 else "unknown"
                calculated_analysis += (
                    f"   - **PRIMARY ANCHOR:** {_primary.get('label','Primary Furniture')} "
                    f"(W {p_w}mm, D {primary_d_disp}, H {primary_h_disp})\n"
                )
                calculated_analysis += f"   - **ROOM DIMS:** W {room_w}mm, D {room_d_disp}, H {room_h_disp}\n"
                calculated_analysis += f"   - **CALCULATED GAP (WIDTH):** Total empty space width = {gap_total_mm}mm. (approx {gap_side_mm}mm on each side).\n"
                calculated_analysis += f"   - **WIDTH OCCUPANCY:** {occ}% (The furniture takes up {occ}% of the wall).\n"
                if occ > 92:
                    calculated_analysis += "   - **ACTION: WALL-TO-WALL FIT.** The furniture is almost as wide as the room. It must TOUCH the side walls or have negligible gaps.\n"
                elif occ > 80:
                    calculated_analysis += "   - **ACTION: TIGHT FIT.** The furniture dominates the wall. Leave only SMALL gaps on the sides.\n"
                else:
                    calculated_analysis += "   - **ACTION: STANDARD FIT.** Center the furniture with visible breathing room on sides.\n"

            if room_d > 0 and p_d > 0:
                depth_occ = round((p_d / room_d) * 100, 1)
                calculated_analysis += f"   - **DEPTH OCCUPANCY:** {depth_occ}% (Floor depth usage).\n"
            if room_h > 0 and p_h > 0:
                height_occ = round((p_h / room_h) * 100, 1)
                calculated_analysis += f"   - **HEIGHT OCCUPANCY:** {height_occ}% (Height usage).\n"
            if room_w <= 0 or p_w <= 0:
                calculated_analysis += "   - (No reliable W/D/H dimensions found; apply relative scaling from reference hierarchy)\n"
        except Exception:
            pass

        if room_dimensions or placement_instructions:
            spatial_context = "\n<PHYSICAL SPACE CONSTRAINTS (STRICT ADHERENCE)>\n"
            if room_dimensions:
                spatial_context += f"- **ACTUAL ROOM DIMENSIONS:** {room_dimensions}\n"
            if placement_instructions:
                spatial_context += f"- **PLACEMENT INSTRUCTIONS:** {placement_instructions}\n"
            spatial_context += (
                "**SCALING RULE:** You MUST calibrate the scale of all furniture relative to the ACTUAL ROOM DIMENSIONS provided.\n"
                f"{calculated_analysis}\n"
                "Do NOT shrink furniture to create artificial empty space. If the room is small, it should look appropriately filled.\n"
                "--------------------------------------------------\n"
            )

        size_hierarchy_hint = ""
        try:
            if size_hierarchy and isinstance(size_hierarchy, list):
                size_hierarchy_hint = " > ".join([str(x) for x in size_hierarchy if x])
            elif furniture_specs_json and isinstance(furniture_specs_json, dict):
                hierarchy = (
                    furniture_specs_json.get("size_hierarchy_scale")
                    or furniture_specs_json.get("size_hierarchy")
                    or []
                )
                if isinstance(hierarchy, list):
                    size_hierarchy_hint = " > ".join([str(x) for x in hierarchy if x])
        except Exception:
            size_hierarchy_hint = ""

        if windows_present:
            window_context = (
                "<WINDOWS DETECTED: YES>\n"
                "Curtains are the ONLY allowed extra element even if not listed.\n"
                "Add minimal floor-to-ceiling **Sheer White Chiffon Curtains** ONLY along the vertical edges of the visible window glass.\n"
                "Do NOT cover solid walls or doors. Keep coverage to outer 10-15% of the glass.\n"
                "If any window is unclear or not visible, do NOT add curtains there.\n\n"
            )
        else:
            window_context = (
                "<WINDOWS DETECTED: NO>\n"
                "Do NOT add curtains or blinds. Do NOT add or invent windows.\n\n"
            )

        user_original_prompt = (
            "IMAGE MANIPULATION TASK (Virtual Staging - Overlay Only):\n"
            "Your goal is to PLACE furniture into the EXISTING empty room image without changing the room itself.\n\n"
            "<CRITICAL: ARCHITECTURAL FREEZE (PRIORITY #1)>\n"
            "1. **DO NOT RE-GENERATE THE ROOM:** The walls, ceiling, floor pattern, and any visible openings/views must remain 100% IDENTICAL to the input image.\n"
            "2. **PERSPECTIVE LOCK:** You must use the EXACT same camera angle and perspective. Do not zoom in, do not zoom out.\n"
            "3. **DEPTH PRESERVATION:** Do not expand the room. Keep the original spatial depth.\n"
            "4. **FRAMING LOCK:** Keep the full room framing. Do NOT crop to a close-up. The ceiling and floor edges must match the input.\n"
            "5. **CORNER VISIBILITY:** Both left and right wall corners must remain visible, matching the input framing.\n\n"
            "<CRITICAL: FURNITURE COMPOSITING>\n"
            "1. **SCALE:** Fit furniture realistically within the *existing* floor space.\n"
            "2. **PLACEMENT:** Place items *on* the floor. Ensure legs touch the ground with correct contact shadows.\n"
            "3. **STYLE:** Match the intended style implied by the provided furniture items.\n"
            "4. **ONLY LISTED ITEMS:** Render only the listed items. Do NOT add extra furniture or swap designs.\n"
            f"{window_context}"
            "<CRITICAL: MATHEMATICAL SCALE ENFORCEMENT (PRIORITY #0)>\nYou are provided with ACTUAL DIMENSIONS, PRIMARY ANCHOR, and (optionally) a W/D/H SCALE GUIDE IMAGE. Do not ignore them.\nIMPORTANT: The 'PRIMARY ANCHOR' is the largest-volume movable furniture (EXCLUDING rugs/carpets).\nSIZE HIERARCHY (largest -> smallest, exclude rugs/carpets): {size_hierarchy_hint}\n\n"
            "You are provided with ACTUAL DIMENSIONS and PRE-CALCULATED RATIOS. Do not ignore them.\n"
            "1. **SPECIFIC SCALE ANALYSIS FOR THIS REQUEST:**\n"
            f"{calculated_analysis if calculated_analysis else '   - (Apply relative scaling based on provided specs)'}\n"
            "2. **RELATIVE W/D/H HIERARCHY:**\n"
            "   - You MUST maintain the visual width/depth/height hierarchy specified in the specs.\n"
            "   - Example: If Item A (H: 950mm) is taller than Item B (H: 775mm), Item A MUST be rendered taller than Item B in the image.\n"
            "3. **RATIO LOCK:**\n"
            "   - Calculate: (Furniture W/D/H) / (Room W/D/H) = Coverage ratios.\n"
            "   - Strictly follow these percentages. Do not shrink items into 'miniature' versions to create empty space.\n"
            "   - **STRICT PROHIBITION:** Do not resize items for 'vibe' or 'aesthetic balance'. Follow the NUMBERS strictly.\n"
            "4. **HEIGHT CONSISTENCY:**\n"
            "   - Do NOT make a shorter item appear taller by placing it closer to the camera.\n"
            "   - Apparent height must respect the real H ratios across all items.\n"
            "<CRITICAL: LIGHTING PRESERVATION (PRIORITY #1)>\n"
            "1. **KEEP EXISTING LIGHTING LOGIC:** Follow the input image's visible light sources and direction.\n"
            "2. **EXPOSURE RULE:** Bright and airy (not dark), while preserving highlight detail (no blown-out whites).\n"
            "3. **LIGHT DIRECTION:** Keep shadows consistent with the existing key light direction.\n"
            "4. **NO DIM ROOM:** Do NOT generate a dim, underexposed, moody, or nighttime look.\n"
            "5. **WHITE BALANCE:** Neutral white balance (around 4000~5000K). **NO warm/yellow cast.**\n"
            "6. **NO NEW OPENINGS:** Do not add new windows/doors or fake exterior light sources.\n\n"
            "<CRITICAL: PHOTOREALISTIC LIGHTING INTEGRATION (HYBRID: DAYLIGHT + ARTIFICIAL)>\n"
            "1. **LIGHTING STATE: SUBTLE SUPPORT ONLY (NEUTRAL):**\n"
            "   - **ACTION:** Keep interior fixtures ON only if they appear in the reference; no extra fixtures.\n"
            "   - **VISUALS:** Avoid visible glow/bloom halos. Lights should look realistic and restrained.\n"
            "2. **LIGHTING HIERARCHY (KEY vs. FILL):**\n"
            "   - **KEY LIGHT (DOMINANT):** Use the existing dominant light source visible in the input. Do NOT invent new openings.\n"
            "   - **FILL LIGHT (SECONDARY):** Interior lights act as gentle fill. They must NOT overpower the key light.\n"
            "3. **STRICT COLOR TEMPERATURE CONTROL (NO YELLOW):**\n"
            "   - **Target Temperature:** Use **Neutral White (4000K-5000K)** for any artificial lights to match daylight.\n"
            "   - **PROHIBITED:** No warm/tungsten/orange bulbs (2700K). No vintage/sepia cast.\n"
            "4. **SHADOW PHYSICS:**\n"
            "   - Cast soft, directional shadows driven by the existing key light direction.\n"
            "   - Use interior lights only to lift the darkest corners slightly.\n"
            "   - Shadows and light gradients must be smooth and clean; avoid blotchy noise or muddy patches on floors.\n"
            "5. **ATMOSPHERE:**\n"
            "   - Bright and airy, but never overlit. Preserve highlight detail and avoid glare.\n"
            "   - Lighting must feel natural and cohesive across all surfaces (especially floors); no artificial blotches.\n"
            "   - **OUTPUT RULE:** Return the image with furniture added, blended with the existing lighting (daylight or ambient) without introducing new openings.\n"
        )

        prompt = (
            "ACT AS: Professional Interior Photographer.\n"
            f"{room_analysis_context}\n"
            f"{specs_context}\n"
            f"{dims_table_context}\n"
            f"{incomplete_dims_context}\n"
            f"{spatial_context}\n"
            f"{inventory_context}\n"
            f"{ratio_rules_context}\n"
            f"{user_original_prompt}\n\n"
            f"<CRITICAL: OUTPUT FORMAT ENFORCEMENT -> {ratio_instruction}>\n"
            "1. **FULL BLEED CANVAS:** The output image MUST fill the entire canvas from edge to edge. **NO WHITE BARS.** NO SPLIT SCREENS.\n"
            "2. **NO TEXT OVERLAY:** Do NOT write any dimensions, labels, or watermarks on the final image. It must be a clean photo.\n"
            "3. **ASPECT RATIO LOCK (HARD):** You MUST output EXACTLY " + ratio_instruction + ". Any other ratio is invalid.\n"
            "4. **NO PORTRAIT FOR LANDSCAPE INPUTS:** If the input is landscape, output must remain landscape (16:9). Never generate portrait.\n"
            "5. **NO LANDSCAPE FOR PORTRAIT INPUTS:** If the input is portrait, output must remain portrait (4:5). Never generate landscape.\n"
            "6. **IGNORE REFERENCE RATIO:** You MUST output a " + ratio_instruction + " image. Do not mimic any reference image shape.\n"
            "7. **NO MULTI-PANEL OUTPUT:** Output must be ONE single staged room photograph only. Do NOT append catalog sheets, white inventory panels, split layouts, or include the reference image anywhere."
        ).replace("{size_hierarchy_hint}", size_hierarchy_hint or "")

        content = [prompt, "Empty Room (Target Canvas - KEEP THIS):", room_img]

        try:
            if furniture_specs_json and isinstance(furniture_specs_json, dict):
                cutouts = []
                items_for_cutout = list(furniture_specs_json.get("items") or [])

                def _cutout_scale_priority(row: dict):
                    dm = (row or {}).get("dims_mm") or {}
                    try:
                        w = int(dm.get("width_mm") or 0)
                    except Exception:
                        w = 0
                    try:
                        d = int(dm.get("depth_mm") or 0)
                    except Exception:
                        d = 0
                    try:
                        h = int(dm.get("height_mm") or 0)
                    except Exception:
                        h = 0
                    has_dims = 1 if (w > 0 or d > 0 or h > 0) else 0
                    try:
                        vol = int((row or {}).get("volume_proxy") or 0)
                    except Exception:
                        vol = 0
                    try:
                        cat = int((row or {}).get("category_score") or 0)
                    except Exception:
                        cat = 0
                    try:
                        idx = int((row or {}).get("index") or 0)
                    except Exception:
                        idx = 0
                    return (has_dims, vol, w, d, h, cat, -idx)

                items_for_cutout.sort(key=_cutout_scale_priority, reverse=True)
                items_for_cutout = items_for_cutout[:12]
                for it in items_for_cutout:
                    cp = it.get("crop_path")
                    if cp and os.path.exists(cp):
                        cutouts.append(it)
                for it in cutouts:
                    cp = it.get("crop_path")
                    lbl = (it.get("label") or "").strip() or "Item"
                    qty = int(it.get("qty") or 1)
                    if qty < 1:
                        qty = 1
                    dims = normalize_dims_dict(it.get("requested_dims_mm") or it.get("dims_mm") or {})
                    w = dims.get("width_mm")
                    d = dims.get("depth_mm")
                    h = dims.get("height_mm")
                    opts = it.get("options")
                    opts_txt = "null"
                    if isinstance(opts, (dict, list)):
                        try:
                            opts_txt = json.dumps(opts, ensure_ascii=False)
                        except Exception:
                            opts_txt = str(opts)
                    elif isinstance(opts, str) and opts.strip():
                        opts_txt = opts.strip()
                    cutout_img = Image.open(cp)
                    try:
                        cutout_img.thumbnail((512, 512), Image.Resampling.LANCZOS)
                    except Exception:
                        pass
                    extra_imgs.append(cutout_img)
                    content += [
                        (
                            "Furniture Cutout Reference (MUST MATCH EXACT DESIGN). "
                            f"Label={lbl} | Qty={qty} | W={w if w is not None else 'null'}mm "
                            f"D={d if d is not None else 'null'}mm H={h if h is not None else 'null'}mm "
                            f"| Options={opts_txt}"
                        ),
                        cutout_img,
                    ]
        except Exception:
            pass

        try:
            if scale_guide_path and os.path.exists(scale_guide_path):
                guide_img = Image.open(scale_guide_path)
                extra_imgs.append(guide_img)
                content += [
                    "SCALE GUIDE OVERLAY (fluorescent yellow 500x500mm floor grid only; DO NOT render the grid itself):",
                    guide_img,
                ]
        except Exception:
            pass

        remaining = max(30, total_timeout_limit - (time.time() - start_time))
        safety_settings = allow_all_safety_settings()

        def _render_once():
            response = call_gemini_with_failover(
                model_name,
                content,
                {"timeout": remaining},
                safety_settings,
                system_instruction,
                log_tag="Stage2.Furnish",
            )
            if response and hasattr(response, "candidates") and response.candidates and hasattr(response, "parts"):
                for part in response.parts:
                    if hasattr(part, "inline_data"):
                        timestamp = int(time.time())
                        filename = f"result_{timestamp}_{unique_id}.png"
                        path = os.path.join("outputs", filename)
                        with open(path, "wb") as output_file:
                            output_file.write(part.inline_data.data)
                        try:
                            with Image.open(path) as _chk:
                                w, h = _chk.size
                            if h <= 0:
                                return None
                            r = w / h
                            if abs(r - expected_ratio) > ratio_tol:
                                if log_brief:
                                    print(f"[RatioCheck] FAIL {w}x{h} (expected ~{expected_ratio:.4f})", flush=True)
                                return None
                        except Exception:
                            return None
                        return match_aspect_to_target(path, room_path)
            return None

        max_attempts = 3
        last_path = None
        for attempt in range(max_attempts):
            last_path = _render_once()
            if not last_path:
                continue

            if enable_scale_check and furniture_specs_json and room_dims_parsed and room_planes:
                ok, issues = validate_furnished_scale(
                    last_path,
                    furniture_specs_json,
                    room_dims_parsed,
                    room_planes,
                    primary_label=(primary_item or {}).get("label"),
                )
                if not ok:
                    if log_brief:
                        print(f"[ScaleCheck] FAIL attempt {attempt+1}/{max_attempts}: {', '.join(issues)}", flush=True)
                    else:
                        logger.warning(f"[ScaleCheck] FAIL attempt {attempt+1}/{max_attempts}: {issues}")
                    if attempt < max_attempts - 1:
                        continue
            return last_path
        return last_path
    except Exception as exc:
        print(f"!! Stage 2 에러: {exc}", flush=True)
        return None
    finally:
        for im in extra_imgs:
            try:
                im.close()
            except Exception:
                pass
        try:
            if room_img:
                room_img.close()
        except Exception:
            pass
