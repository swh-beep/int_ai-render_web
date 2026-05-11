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
            "Polish this furnished interior render so it looks like a real high-end interior magazine photograph, not an AI image. "
            "Improve natural light, shadows, reflections, material texture, depth, and tonal balance. "
            "Do not change the room structure, camera framing, furniture/decor count, shape, detail, scale, color, material, or placement."
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
