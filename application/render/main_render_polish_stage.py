import os
import time
from typing import Any, Callable

from PIL import Image


def polish_main_render(
    source_path: str,
    *,
    unique_id: str,
    allow_all_safety_settings: Callable[[], Any],
    call_repair_with_failover: Callable[..., Any] | None = None,
    repair_model_name: str | None = None,
    call_gemini_with_failover: Callable[..., Any] | None = None,
    model_name: str | None = None,
    match_aspect_to_target: Callable[[str, str], str | None],
    logger,
    timeout_sec: float = 70.0,
) -> str | None:
    if not source_path or not os.path.exists(source_path):
        return None

    repair_call = call_repair_with_failover or call_gemini_with_failover
    resolved_model = repair_model_name or model_name
    if repair_call is None or not resolved_model:
        return None

    image = None
    try:
        image = Image.open(source_path)
        prompt = (
            "Retouch this as a final high-end interior photograph and compositing realism pass, not a redraw. "
            "PRIMARY GOAL: make every inserted furniture/decor object look physically integrated into the room, as if photographed in-camera. "
            "Remove any Photoshop-composite look. "
            "Fix mismatched lighting direction on furniture and decor. "
            "Add or strengthen natural contact shadows where objects touch the floor, wall, table, ceiling, or another object. "
            "Add soft ambient occlusion under furniture legs, rugs, lamps, plants, chairs, tables, sofas, and wall/ceiling fixtures. "
            "Blend object edges into the room lighting; remove cutout halos, hard pasted edges, glowing rims, and sticker-like outlines. "
            "Match sharpness, grain/noise, lens softness, contrast, and local exposure between inserted objects and the room. "
            "Harmonize color temperature so objects share the same daylight/interior-light mix as the room. "
            "Match the overall tonal grade: black levels, midtone warmth, saturation, contrast curve, and color cast must be consistent across the whole scene. "
            "Keep every object in the same color-grading family as the room; no object should look separately filtered, over-sharpened, over-saturated, too blue, too warm, or locally HDR compared with its surroundings. "
            "Correct shadows so they follow the existing window/daylight direction and never contradict visible sunlight patches. "
            "Prevent objects from floating; every floor or surface item must feel grounded by believable shadow and occlusion. "
            "Improve exposure, white balance, contrast, highlights, shadows, and subtle lens realism. "
            "Make the image cleaner, more expensive, and editorial, but still natural. "
            "Preserve all furniture/decor identities, shapes, proportions, placement, colors, material textures, room structure, camera angle, and framing. "
            "Do not move, replace, resize, repaint, restyle, simplify, or add/remove any object. "
            "Do not make surfaces clay-like, waxy, plastic, CGI, overly smooth, over-airbrushed, or illustration-like. "
            "Return a single realistic interior photograph with cohesive lighting, believable cast shadows, clean material detail, and no visible compositing seams."
        )
        response = repair_call(
            resolved_model,
            [prompt, image],
            {
                "timeout": float(timeout_sec),
                "aspect_ratio": "16:9",
                "max_attempts": 1,
            },
            allow_all_safety_settings(),
            None,
            log_tag="Stage2.MainPolish",
        )
        if response and hasattr(response, "candidates") and response.candidates and hasattr(response, "parts"):
            for part in response.parts:
                if not hasattr(part, "inline_data"):
                    continue
                timestamp = int(time.time())
                raw_path = os.path.join("outputs", f"result_polish_{timestamp}_{unique_id}.png")
                with open(raw_path, "wb") as output_file:
                    output_file.write(part.inline_data.data)
                normalized_path = match_aspect_to_target(raw_path, source_path)
                return normalized_path or raw_path
    except Exception as exc:
        try:
            logger.warning(f"[MainPolish] skipped: {exc}")
        except Exception:
            pass
    finally:
        if image is not None:
            try:
                image.close()
            except Exception:
                pass
    return None
