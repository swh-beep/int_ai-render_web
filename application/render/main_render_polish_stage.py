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
            "ACT AS: Senior interior photo retoucher.\n"
            "TASK: Improve photorealistic integration of the provided furnished room render.\n\n"
            "<HARD LOCKS>\n"
            "1. Keep the exact same furniture count, product identity, silhouette, scale, placement, and room layout.\n"
            "2. Keep the exact same camera angle, framing, walls, windows, doors, ceiling, and floor geometry.\n"
            "3. Do NOT add, remove, replace, move, rotate, resize, restyle, or simplify any object.\n"
            "4. Do NOT change the composition into a different shot.\n\n"
            "<ALLOWED IMPROVEMENTS ONLY>\n"
            "1. Improve lighting consistency, contact shadows, ambient occlusion, local reflections, and material blending.\n"
            "2. Improve tone balance, contrast, highlight rolloff, color temperature consistency, and natural shadow depth.\n"
            "3. Reduce pasted/composited look while preserving every object exactly.\n\n"
            "OUTPUT: Return the same image composition with cleaner photorealistic integration only."
        )
        response = repair_call(
            resolved_model,
            [prompt, image],
            {
                "timeout": float(timeout_sec),
                "aspect_ratio": "16:9",
                "thinking_level": "high",
                "include_thoughts": False,
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
