import base64
import os
import time
from typing import Callable

import requests


def _download_generated_image(
    url: str,
    unique_id: str,
    *,
    standardize_image: Callable[..., str],
    set_png_dpi: Callable[[str, tuple[int, int]], None],
    output_dir: str = "outputs",
) -> str | None:
    try:
        response = requests.get(url)
        if response.status_code != 200:
            return None

        timestamp = int(time.time())
        filename = f"magnific_{timestamp}_{unique_id}.png"
        raw_path = os.path.join(output_dir, filename)
        with open(raw_path, "wb") as file_obj:
            file_obj.write(response.content)

        output_path = standardize_image(raw_path, keep_ratio=True)
        set_png_dpi(output_path, (300, 300))
        return output_path
    except Exception:
        return None


def call_magnific_api(
    image_path: str,
    unique_id: str,
    start_time: float,
    *,
    magnific_api_key: str | None,
    magnific_endpoint: str,
    total_timeout_limit: float,
    standardize_image: Callable[..., str],
    set_png_dpi: Callable[[str, tuple[int, int]], None],
) -> str:
    if time.time() - start_time > total_timeout_limit:
        return image_path

    key_preview = (magnific_api_key or "")[:5]
    print(f"\n--- [Stage 4] Magnific Upscaling (Key: {key_preview}...) ---", flush=True)

    if not magnific_api_key or "your_" in magnific_api_key:
        print(">> [SKIP] API key missing. Return original.", flush=True)
        return image_path

    try:
        with open(image_path, "rb") as file_obj:
            encoded_image = base64.b64encode(file_obj.read()).decode("utf-8")

        payload = {
            "image": encoded_image,
            "scale_factor": "2x",
            "optimized_for": "films_n_photography",
            "engine": "automatic",
            "creativity": 0,
            "hdr": 0,
            "resemblance": 10,
            "fractality": 0,
            "prompt": (
                "Professional interior photography, architectural digest style, "
                "shot on Phase One XF IQ4, 100mm lens, ISO 100, f/8, "
                "neutral white daylight or existing ambient light, soft shadows, "
                "clean textures, true-to-source details, raw photo, 8k resolution. "
                "--no dust, stains, painting, drawing, cartoon, anime, illustration, plastic look, oversaturated, watermark, text, blur, distorted."
            ),
        }
        headers = {
            "x-freepik-api-key": magnific_api_key,
            "Content-Type": "application/json",
        }

        response = requests.post(magnific_endpoint, json=payload, headers=headers)
        if response.status_code != 200:
            print(f"!! [API Error] Status: {response.status_code}, Msg: {response.text}", flush=True)
            return image_path

        data = response.json()
        response_data = data.get("data", {})
        if not response_data:
            return image_path

        task_id = response_data.get("task_id")
        if task_id:
            print(f">> Task queued (ID: {task_id})...", end="", flush=True)
            while time.time() - start_time < total_timeout_limit:
                time.sleep(2)
                print(".", end="", flush=True)

                check_response = requests.get(f"{magnific_endpoint}/{task_id}", headers=headers)
                if check_response.status_code != 200:
                    continue

                status_data = check_response.json().get("data", {})
                status = status_data.get("status")
                if status == "COMPLETED":
                    print(" done!", flush=True)
                    generated_images = status_data.get("generated", [])
                    if generated_images:
                        return (
                            _download_generated_image(
                                generated_images[0],
                                unique_id,
                                standardize_image=standardize_image,
                                set_png_dpi=set_png_dpi,
                            )
                            or image_path
                        )
                    return image_path
                if status == "FAILED":
                    print(" failed.", flush=True)
                    return image_path
            return image_path

        generated_images = response_data.get("generated", [])
        if generated_images:
            return (
                _download_generated_image(
                    generated_images[0],
                    unique_id,
                    standardize_image=standardize_image,
                    set_png_dpi=set_png_dpi,
                )
                or image_path
            )

        return image_path
    except Exception:
        return image_path
