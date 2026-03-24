import os
import re
import time
from typing import Any, Callable, Optional

from PIL import Image, ImageOps


def process_image_edit_logic(
    photo_paths,
    instructions,
    mode,
    unique_id,
    index,
    *,
    build_image_edit_step_prompt: Callable[..., str],
    pad_image_to_target_canvas: Callable[[Image.Image, int, int], Image.Image],
    call_gemini_with_failover: Callable[..., Any],
    model_name: str,
    match_aspect_to_target: Callable[[str, str], str | None],
    mask_path=None,
):
    img = None
    try:
        print(f"   [{mode.upper()}] Processing step with instructions: {instructions}", flush=True)

        if not photo_paths:
            return None
        target_path = photo_paths[0]
        ref_paths = photo_paths[1:7]

        try:
            with Image.open(target_path) as base_img:
                base_img.thumbnail((4096, 4096))
                img = base_img.copy()
        except Exception:
            return None

        ref_paths = [ref_path for ref_path in ref_paths if ref_path and os.path.exists(ref_path)]

        inst_lower = instructions.lower()
        wants_move = any(
            token in inst_lower
            for token in [
                "옮겨",
                "옮기",
                "이동",
                "배치",
                "배치해",
                "자리바꿔",
                "자리 바꿔",
                "배치바꿔",
                "배치 바꿔",
                "swap",
                "swap with",
                "move",
                "relocate",
                "rearrange",
                "reposition",
            ]
        )
        wants_remove = any(
            token in inst_lower
            for token in [
                "없애",
                "지워",
                "치워",
                "빼",
                "제거",
                "삭제",
                "remove",
                "delete",
                "erase",
                "take out",
            ]
        )
        wants_resize = any(
            token in inst_lower
            for token in [
                "작게",
                "크게",
                "줄여",
                "줄이",
                "늘려",
                "늘리",
                "키워",
                "축소",
                "크기",
                "shrink",
                "smaller",
                "reduce",
                "tiny",
                "enlarge",
                "bigger",
                "larger",
                "increase size",
                "decrease size",
            ]
        )
        wants_replace = any(
            token in inst_lower
            for token in [
                "바꿔",
                "바꾸",
                "교체",
                "변경",
                "대체",
                "갈아",
                "replace",
                "swap to",
                "change to",
                "substitute",
            ]
        )
        user_override = (
            "USER INSTRUCTIONS OVERRIDE ALL OTHER RULES. "
            "If any conflict exists, follow the user's request."
        )

        def _filter_instructions(step_kind: str, text: str) -> str:
            cleaned = (text or "").strip()
            if not cleaned:
                return cleaned
            step_keywords = {
                "replace": [
                    "바꿔",
                    "바꾸",
                    "교체",
                    "변경",
                    "대체",
                    "갈아",
                    "replace",
                    "swap",
                    "change",
                    "substitute",
                ],
                "remove": [
                    "없애",
                    "지워",
                    "삭제",
                    "빼",
                    "제거",
                    "없앨",
                    "remove",
                    "delete",
                    "erase",
                    "take out",
                ],
                "resize": [
                    "작게",
                    "크게",
                    "줄여",
                    "줄이",
                    "늘려",
                    "늘리",
                    "키워",
                    "축소",
                    "크기",
                    "shrink",
                    "smaller",
                    "reduce",
                    "tiny",
                    "enlarge",
                    "bigger",
                    "larger",
                    "increase size",
                    "decrease size",
                ],
                "rearrange": [
                    "옮겨",
                    "이동",
                    "배치",
                    "자리바꿔",
                    "자리 바꿔",
                    "배치바꿔",
                    "배치 바꿔",
                    "swap",
                    "swap with",
                    "move",
                    "relocate",
                    "rearrange",
                    "reposition",
                ],
            }
            parts = re.split(r"[\n\r]+|[.!?]+", cleaned)
            kept = []
            for part in parts:
                lowered = part.lower()
                if any(keyword in lowered for keyword in step_keywords.get(step_kind, [])):
                    kept.append(part.strip())
            if kept:
                return ". ".join([part for part in kept if part])
            return cleaned

        steps = []
        if mode == "edit":
            if wants_replace:
                steps.append("replace")
            if wants_remove:
                steps.append("remove")
            if wants_resize:
                steps.append("resize")
            if wants_move:
                steps.append("rearrange")
            if not steps:
                steps.append("edit")
        else:
            steps = ["decorate"]

        def _load_ref_images(target_w: int, target_h: int):
            refs = []
            for ref_path in ref_paths:
                try:
                    with Image.open(ref_path) as ref_image:
                        ref_image.thumbnail((4096, 4096))
                        padded = pad_image_to_target_canvas(ref_image.copy(), target_w, target_h)
                        refs.append(padded)
                except Exception:
                    continue
            return refs

        def _load_mask(target_w: int, target_h: int):
            if not mask_path or not os.path.exists(mask_path):
                return None
            try:
                with Image.open(mask_path) as mask_image:
                    mask_image = ImageOps.exif_transpose(mask_image)
                    if mask_image.mode != "L":
                        mask_image = mask_image.convert("L")
                    mask_image = mask_image.resize((target_w, target_h), Image.Resampling.NEAREST)
                    return mask_image.copy()
            except Exception:
                return None

        def _run_step(current_path: str, step_kind: str) -> Optional[str]:
            step_focus = ""
            if step_kind == "replace":
                step_focus = "STEP FOCUS: Replace the specified objects with the requested new designs."
            elif step_kind == "remove":
                step_focus = "STEP FOCUS: Remove the specified objects and inpaint the background cleanly."
            elif step_kind == "resize":
                step_focus = "STEP FOCUS: Resize the specified objects as requested."
            elif step_kind == "rearrange":
                step_focus = "STEP FOCUS: Reposition the specified objects to new locations."
            elif step_kind == "decorate":
                step_focus = "STEP FOCUS: Add decorations/props only."

            if step_kind == "rearrange":
                role = "Expert Interior Rearrangement Editor."
                task = "Reposition existing furniture and props per the user's request while keeping the room structure intact."
                critical_rule = (
                    "1. **REPOSITIONING ALLOWED:** You MAY move furniture to new locations as requested.\n"
                    "2. **NO NEW OBJECTS:** Do NOT invent new furniture or decor unless explicitly requested.\n"
                    "3. **KEEP ROOM STRUCTURE:** Walls, windows, doors, and architecture must remain unchanged.\n"
                    "4. **LIGHTING CONSISTENCY:** Keep lighting direction and exposure consistent with the original photo.\n"
                    "5. **NATURAL CONTACT:** Ensure furniture sits naturally on the floor with correct perspective and shadows.\n"
                    f"6. **USER PRIORITY:** {user_override}\n"
                )
            elif step_kind == "decorate":
                role = "Expert Home Stager."
                task = "Add decorations and props to the EXISTING room without changing furniture layout."
                critical_rule = (
                    "1. **ADDITIVE ONLY:** Do NOT move or remove existing large furniture.\n"
                    "2. **PROPS:** Add items like plants, cushions, rugs, lamps, books as requested.\n"
                    "3. **STYLE:** Match the lighting and shadow of the original photo perfectly.\n"
                    f"4. **USER PRIORITY:** {user_override}"
                )
            else:
                role = "Expert AI Inpainter & Scene Reconstructor."
                task = "Modify the scene by removing or replacing objects per the user's request."
                critical_rule = (
                    "1. **DESTRUCTIVE EDITING:** Remove the original object and redraw a new one when a change is requested.\n"
                    "2. **BACKGROUND INPAINT:** Recreate missing wall/floor textures seamlessly after removal.\n"
                    "3. **SCALE CHANGE:** If the user says smaller/larger, make the change clearly visible.\n"
                    "4. **COLOR/MATERIAL:** Overwrite pixel colors completely if a color/material change is requested.\n"
                    f"5. **USER PRIORITY:** {user_override}\n"
                )

            step_instructions = _filter_instructions(step_kind, instructions)
            if step_kind == "resize":
                step_instructions += " (IMPORTANT: The object MUST change size clearly. REVEAL wall/floor if shrinking.)"
            if step_kind == "remove":
                step_instructions += " (IMPORTANT: Fully remove the object and inpaint the background.)"
            if step_kind == "replace" and ref_paths:
                step_instructions += " (IMPORTANT: Use the reference image as the replacement object's design.)"

            current_img = None
            try:
                with Image.open(current_path) as base_img:
                    base_img = ImageOps.exif_transpose(base_img)
                    base_img.thumbnail((4096, 4096))
                    current_img = base_img.copy()
            except Exception:
                return None

            ref_imgs = _load_ref_images(current_img.size[0], current_img.size[1])
            current_mask = _load_mask(current_img.size[0], current_img.size[1])
            strict_mask_rules = ""
            if current_mask:
                strict_mask_rules = (
                    "<STRICT MASK COMPLIANCE>\n"
                    "- You MUST keep every pixel outside the white mask area EXACTLY identical to the target image.\n"
                    "- Do NOT alter lighting, color, or any objects outside the mask.\n"
                )

            prompt = build_image_edit_step_prompt(
                role=role,
                task=task,
                step_focus=step_focus,
                step_instructions=step_instructions,
                critical_rule=critical_rule,
                strict_mask_rules=strict_mask_rules,
            )

            content = [prompt, "Target image:", current_img]
            if current_mask:
                content.extend(["Mask image (white=edit, black=keep):", current_mask])
            for ref_index, ref_img in enumerate(ref_imgs):
                content.extend([f"Reference image {ref_index + 1}:", ref_img])

            response = call_gemini_with_failover(
                model_name,
                content,
                {"timeout": 90},
                {},
                log_tag="Edit.Generate",
            )

            for ref_img in ref_imgs:
                try:
                    ref_img.close()
                except Exception:
                    pass
            if current_mask:
                try:
                    current_mask.close()
                except Exception:
                    pass

            if response and hasattr(response, "candidates") and response.candidates:
                for part in response.parts:
                    if hasattr(part, "inline_data"):
                        timestamp = int(time.time())
                        out_filename = f"{mode}_{timestamp}_{unique_id}_{index}.png"
                        out_path = os.path.join("outputs", out_filename)
                        with open(out_path, "wb") as output_file:
                            output_file.write(part.inline_data.data)
                        final_path = match_aspect_to_target(out_path, current_path)
                        try:
                            current_img.close()
                        except Exception:
                            pass
                        return final_path

            try:
                current_img.close()
            except Exception:
                pass
            return None

        current_path = target_path
        for step_kind in steps:
            next_path = _run_step(current_path, step_kind)
            if not next_path:
                return None
            current_path = next_path

        return current_path

    except Exception as exc:
        print(f"!! {mode} Gen Error: {exc}", flush=True)
        try:
            if img:
                img.close()
        except Exception:
            pass
        return None
