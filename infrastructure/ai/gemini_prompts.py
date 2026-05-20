def build_frontal_analysis_prompt() -> str:
    return (
        "You are a Spatial Architect AI. Analyze these multiple photos of the SAME room taken from different angles.\n"
        "Your goal is to build a mental 3D model of this space to reconstruct a 'Perfect Frontal View'.\n\n"
        "OUTPUT THE FOLLOWING SPATIAL BLUEPRINT:\n"
        "1. **Anchor Elements:** Identify fixed structures (e.g., 'Large window on far wall', 'Black wall on left', 'Pillar on right').\n"
        "2. **Geometry & Materials:** Describe the ceiling (e.g., recessed, lighting type) and floor (e.g., tile reflection, pattern) in detail.\n"
        "3. **Symmetry Plan:** If we place a camera in the exact center of the room facing the main window, describe what should be seen on the Left, Center, and Right to achieve perfect symmetry.\n"
        "Output ONLY the spatial blueprint description."
    )


def build_frontal_generation_prompt(spatial_blueprint: str) -> str:
    return (
        f"TASK: Generative Space Reconstruction (Multi-View to Single Frontal View).\n"
        f"ACT AS: High-end Architectural Photographer.\n\n"
        f"<SPATIAL BLUEPRINT (SOURCE TRUTH)>\n"
        f"{spatial_blueprint}\n"
        f"--------------------------------------------------\n\n"
        "VIRTUAL CAMERA SETUP:\n"
        "- **Position:** Place the virtual camera in the DEAD CENTER of the room.\n"
        "- **Target:** Face strictly forward towards the main focal point (usually the window).\n"
        "- **Lens:** 10mm Wide-Angle Rectilinear Lens (Capture the full width, NO fish-eye distortion).\n"
        "- **Height:** Eye-level (approx 130cm).\n\n"
        "COMPOSITION RULES (STRICT SYMMETRY):\n"
        "1. **Reconstruct the Space:** Synthesize a single, coherent 1-point perspective view using features from ALL input images.\n"
        "2. **Alignment:** Vertical lines (pillars, window frames) must be perfectly vertical. Horizontal lines (floor/ceiling) must converge to a single center vanishing point.\n"
        "3. **Consistency:** Ensure the 'Black Wall' (if present) and 'Pillars' are placed correctly relative to the center view as defined in the blueprint.\n\n"
        "LIGHTING & FIDELITY:\n"
        "- **Reflections:** Render accurate reflections on the floor tiles matching the ceiling lights.\n"
        "- **Lighting:** Uniform, bright, high-end interior lighting. No dark corners.\n"
        "- **Resolution:** 8k, extremely sharp, photorealistic.\n\n"
        "NEGATIVE CONSTRAINTS:\n"
        "- Do NOT produce a collage or grid. Output ONE single image.\n"
        "- No text, watermarks, blurred textures, or distorted geometry.\n"
        "- Do not simply crop one image; SYNTHESIZE the complete view."
        "- **Zoomed in, Close-up, Cropped views.** (CRITICAL FAIL)\n"
        "- **DO NOT include text, watermark, username, interface, subtitle.**\n"
        "- Distorted pillars, curved horizon, fisheye curvature."
    )


def build_image_edit_step_prompt(
    *,
    role: str,
    task: str,
    step_focus: str,
    step_instructions: str,
    critical_rule: str,
    strict_mask_rules: str,
) -> str:
    return (
        f"ACT AS: {role}\n"
        f"TASK: {task}\n\n"
        f"<STEP FOCUS>\n{step_focus}\n"
        "Prioritize this step first, but keep the full user request in mind and do NOT contradict it.\n"
        "--------------------------------------------------\n\n"
        "<REFERENCE IMAGES>\n"
        "If provided, use them ONLY as material/shape references for the specific objects to be added or replaced.\n"
        "They are NOT a layout or framing guide; do NOT copy their composition or aspect ratio.\n"
        "--------------------------------------------------\n\n"
        "<USER INSTRUCTIONS (EXECUTE AGGRESSIVELY)>\n"
        f"{step_instructions}\n"
        "MULTI-CHANGE COMPLETION RULE: If the user asked for more than one change, the final output is invalid unless every requested change is visible together.\n"
        "--------------------------------------------------\n\n"
        "<CRITICAL RULES>\n"
        f"{critical_rule}\n"
        "4. **FRAMING LOCK (ABSOLUTE):** The output MUST match the target image's framing, composition, and camera viewpoint exactly.\n"
        "5. **ASPECT LOCK:** The output MUST keep the SAME aspect ratio as the target image. Resolution may differ.\n"
        "6. **REFERENCE ROLE:** Reference images are ONLY for object design details; they are composited into the target scene, not re-framed around.\n"
        "7. **INTEGRATION (MODERATE):** Insert reference-based objects into the scene with plausible perspective, floor contact, and soft contact shadows that match the target lighting. Avoid obvious cut-and-paste edges.\n"
        "8. **PADDING IGNORE:** If a reference contains padding/borders, ignore them and use only the object region as a style/shape guide.\n"
        "9. **MASKED EDITING:** If a mask is provided, ONLY modify the white areas. Preserve black areas exactly.\n"
        "10. **OBJECT IDENTITY LOCK:** Unless this step explicitly removes or replaces an object, preserve object identity, object count, and support surfaces.\n"
        "11. **UNCHANGED OBJECT LOCK:** Any object not explicitly targeted in the current step must stay in the same position, size, and orientation.\n"
        f"{strict_mask_rules}"
        "4. **OUTPUT:** Return a single, high-quality photorealistic image.\n"
        "5. **PHOTOREALISM ONLY:** Output must be indistinguishable from a real photograph.\n"
        "6. **NO CGI / RENDER / ILLUSTRATION:** Avoid any stylized, CGI, or illustrative look.\n"
        "7. **NO TEXT:** Do not add watermarks or text.\n"
        "8. **NO NOISE:** Do NOT add film grain or artificial noise; keep the image clean."
    )


def build_empty_room_prompt() -> str:
    return (
        "IMAGE EDITING TASK: Extreme Cleaning & Architectural Restoration.\n\n"
        "<CRITICAL: STRUCTURAL PRESERVATION (PRIORITY #0)>\n"
        "1. **ARCHITECTURAL GRID ALIGNMENT OVERRIDES RAW CAMERA TILT:** The final empty room must look like a level architectural photograph, not a tilted phone snapshot.\n"
        "2. **VERTICAL LINE LOCK:** Wall corners, window side jambs, door frames, columns, cabinet-height structural edges, and wall-to-wall boundaries must be perfectly vertical and parallel to the image y-axis. Do not leave leaning walls, trapezoid window frames, or slanted door/window sides.\n"
        "3. **PERSPECTIVE RECTIFICATION:** Correct camera roll, pitch, keystone distortion, and wide-angle phone distortion enough to align the architectural grid. A diagonal room view is allowed, but only horizontal receding lines may converge; all true verticals must stay upright.\n"
        "4. **DO NOT CHANGE ARCHITECTURE:** Preserve only the architectural shell: room layout, walls, ceiling, floor, baseboards, trims, columns, beams, doors, windows, and openings exactly as-is after perspective rectification.\n"
        "5. **DO NOT MOVE THE CAMERA UNNECESSARILY:** Keep viewpoint, lens, viewing direction, and framing close to the input image, but perspective/grid rectification takes priority over copying the raw tilted camera angle.\n"
        "6. **DO NOT ALTER MATERIALS:** Keep wall finishes, flooring, baseboards, trims, and ceiling details unchanged.\n"
        "7. **DO NOT ALTER LIGHTING/SHADOWS:** Keep existing lighting direction and intensity consistent with the input.\n"
        "8. **DO NOT REMOVE TRUE STRUCTURE:** Strictly preserve structural elements including columns, pillars, beams, doors, windows, and architectural fireplaces. Do NOT add new openings.\n"
        "9. **VIEW PROTECTION:** If the input shows an exterior view through any opening, preserve the same view content while keeping window and door verticals plumb.\n\n"
        "10. **REMOVE ALL NON-STRUCTURAL OBJECTS:** Remove furniture, rugs, lamps, curtains, decorations, books, appliances, desks, chairs, sofas, tables, beds, mirrors, wall art, and all storage/display units.\n"
        "11. **BOOKCASE/SHELVING RULE:** Remove every bookshelf, bookcase, shelving unit, cabinet, console, wardrobe, media unit, and wall-mounted storage even if it appears attached to the wall. If unsure whether it is built-in furniture or architecture, remove it.\n\n"
        "<CRITICAL: COMPLETE ERADICATION (PRIORITY #1)>\n"
        "1. REMOVE EVERYTHING ELSE: Identify and remove ALL movable or non-structural furniture, storage, rugs, curtains, lighting fixtures, wall decor, books, electronics, and small objects.\n"
        "2. CLEAN SURFACES: The floor and walls must be perfectly empty. Remove all shadows, reflections, and traces.\n"
        "3. BARE SHELL: Restore the room to its initial construction state.\n\n"
        "OUTPUT RULE: Return a perfectly clean, empty architectural shell with all structural elements intact and all true vertical architectural lines upright. No bookshelves, cabinets, furniture, decor, rugs, curtains, lamps, desks, chairs, or electronics may remain. Do NOT add new openings."
    )


def build_moodboard_generation_prompt(base_prompt: str, furniture_specs: str | None = None) -> str:
    final_prompt = base_prompt
    if furniture_specs:
        final_prompt += f"\n\n<CONTEXT: DETECTED FURNITURE LIST>\nUse this list to ensure you capture all key items:\n{furniture_specs}"
    return final_prompt
