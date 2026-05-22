import os
import time
from typing import Any, Callable

from PIL import Image


def polish_main_render(
    source_path: str,
    *,
    unique_id: str,
    allow_all_safety_settings: Callable[[], Any],
    call_gemini_with_failover: Callable[..., Any],
    model_name: str,
    match_aspect_to_target: Callable[[str, str], str | None],
    logger,
    timeout_sec: float = 70.0,
) -> str | None:
    if not source_path or not os.path.exists(source_path):
        return None

    image = None
    try:
        image = Image.open(source_path)
        prompt = (
            "Retouch this as a real interior photograph, not a redraw. "
            "Only adjust exposure, white balance, contrast, shadows, highlights, and subtle lens realism. "
            "Preserve all furniture/decor shapes, edges, surface details, material texture, colors, placement, and room structure. "
            "Do not smooth, repaint, restyle, or make surfaces look clay-like, waxy, plastic, CGI, or over-airbrushed."
        )
        response = call_gemini_with_failover(
            model_name,
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
