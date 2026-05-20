import os
import time
from typing import Any, Callable

from PIL import Image


def generate_frontal_room_from_photos(
    photo_paths,
    unique_id,
    index,
    *,
    build_frontal_analysis_prompt: Callable[[], str],
    build_frontal_generation_prompt: Callable[[str], str],
    call_gemini_with_failover: Callable[..., Any],
    analysis_model_name: str,
    model_name: str,
    allow_all_safety_settings: Callable[[], Any],
    standardize_image: Callable[[str], str | None],
    call_generation_with_failover: Callable[..., Any] | None = None,
):
    input_images = []
    try:
        print(
            f"   [Frontal Gen] Step 1: Analyzing {len(photo_paths)} photos with Flash (Spatial Mapping)...",
            flush=True,
        )

        for path in photo_paths:
            try:
                with Image.open(path) as img:
                    img.thumbnail((1536, 1536))
                    input_images.append(img.copy())
            except Exception:
                pass

        if not input_images:
            return None

        analysis_prompt = build_frontal_analysis_prompt()
        analysis_res = call_gemini_with_failover(
            analysis_model_name,
            [analysis_prompt] + input_images,
            {"timeout": 120},
            {},
            log_tag="Frontal.Analysis",
        )
        spatial_blueprint = (
            analysis_res.text
            if (analysis_res and getattr(analysis_res, "text", None))
            else "A modern living room with large windows and tiled floor."
        )

        print(
            "   [Frontal Gen] Step 2: Synthesizing Frontal View based on Spatial Blueprint...",
            flush=True,
        )

        generation_prompt = build_frontal_generation_prompt(spatial_blueprint)
        content_list = [generation_prompt] + input_images
        safety_settings = allow_all_safety_settings()
        generation_caller = call_generation_with_failover or call_gemini_with_failover
        response = generation_caller(
            model_name,
            content_list,
            {"timeout": 100, "aspect_ratio": "16:9", "max_attempts": 1},
            safety_settings,
            log_tag="Frontal.Generate",
        )

        if response and hasattr(response, "candidates") and response.candidates:
            for part in response.parts:
                if hasattr(part, "inline_data"):
                    timestamp = int(time.time())
                    out_filename = f"frontal_view_{timestamp}_{unique_id}_{index}.png"
                    out_path = os.path.join("outputs", out_filename)
                    with open(out_path, "wb") as output_file:
                        output_file.write(part.inline_data.data)
                    return standardize_image(out_path)
        return None

    except Exception as exc:
        print(f"!! Frontal Gen Error: {exc}", flush=True)
        return None
    finally:
        for image in input_images:
            try:
                image.close()
            except Exception:
                pass
