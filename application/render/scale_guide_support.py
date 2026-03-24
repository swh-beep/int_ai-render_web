import os
from typing import Any, Callable, Dict, Optional

from PIL import Image


def room_dims_valid(room_dims: dict) -> bool:
    try:
        width = int(room_dims.get("width_mm") or 0)
        depth = int(room_dims.get("depth_mm") or 0)
        height = int(room_dims.get("height_mm") or 0)
        return width > 0 and depth > 0 and height > 0
    except Exception:
        return False


def create_scale_guide_overlay_with_model(
    empty_room_path: str,
    out_path: str,
    room_dims: Optional[Dict[str, Any]] = None,
    *,
    room_dims_valid_fn: Callable[[dict], bool],
    allow_all_safety_settings: Callable[[], Any],
    call_gemini_with_failover: Callable[..., Any],
    model_name: str,
    logger,
):
    src_img = None
    try:
        if not empty_room_path or not os.path.exists(empty_room_path):
            return None

        with Image.open(empty_room_path) as img:
            src_img = img.convert("RGB")

        dims_text = "Room dimensions are not provided."
        if room_dims_valid_fn(room_dims or {}):
            room_w = int((room_dims or {}).get("width_mm") or 0)
            room_d = int((room_dims or {}).get("depth_mm") or 0)
            room_h = int((room_dims or {}).get("height_mm") or 0)
            dims_text = f"Room dimensions (W x D x H, mm): {room_w} x {room_d} x {room_h}."

        prompt = (
            "IMAGE EDIT TASK: Floor Scale Guide Overlay.\n"
            "Keep the original room photo exactly the same and only add a guide on visible floor surfaces.\n"
            f"{dims_text}\n"
            "Draw a 500mm x 500mm tile-like perspective grid in fluorescent yellow.\n"
            "Requirements:\n"
            "1) Draw the grid only on floor areas that are visible in the image.\n"
            "2) Do not draw on walls, windows, ceiling, doors, or outside the room.\n"
            "3) Preserve camera, perspective, lighting, and all architectural details.\n"
            "4) Keep lines thin and clear; no text, labels, arrows, boxes, or extra graphics.\n"
            "5) Output one clean image with this floor grid overlay only.\n"
        )

        response = call_gemini_with_failover(
            model_name,
            [prompt, src_img],
            {"timeout": 70},
            allow_all_safety_settings(),
            "You are an expert architectural image editor.",
            log_tag="ScaleGuide.NLGrid",
        )
        if response and hasattr(response, "candidates") and response.candidates and hasattr(response, "parts"):
            for part in response.parts:
                if hasattr(part, "inline_data") and getattr(part.inline_data, "data", None):
                    with open(out_path, "wb") as handle:
                        handle.write(part.inline_data.data)
                    return out_path
    except Exception as exc:
        logger.exception(f"[ScaleGuide.NLGrid] generation failed: {exc}")
    finally:
        try:
            if src_img:
                src_img.close()
        except Exception:
            pass
    return None
