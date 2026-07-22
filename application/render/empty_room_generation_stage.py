import os
import time
from typing import Any, Callable

from PIL import Image
from shared.image_canvas import (
    get_image_size,
    image_matches_ratio,
    match_aspect_to_ratio,
    match_aspect_to_target as default_match_aspect_to_target,
)


def _is_valid_generated_image(path: str) -> bool:
    try:
        if not path or not os.path.exists(path) or os.path.getsize(path) <= 0:
            return False
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


def _normalize_empty_room_ratio(
    image_path: str,
    room_path: str,
    *,
    expected_ratio: float,
    ratio_tol: float = 0.02,
    max_crop_fraction: float = 0.20,
    match_aspect_to_target: Callable[[str, str], str | None] | None = None,
) -> str | None:
    try:
        if image_matches_ratio(image_path, expected_ratio, ratio_tol):
            return image_path

        width, height = get_image_size(image_path, exif_safe=True)
        if width <= 0 or height <= 0:
            return None

        if match_aspect_to_target is not None and match_aspect_to_target is not default_match_aspect_to_target:
            normalized_path = match_aspect_to_target(image_path, room_path)
            if normalized_path and image_matches_ratio(normalized_path, expected_ratio, ratio_tol):
                return normalized_path

        current_ratio = width / height

        if current_ratio > expected_ratio:
            retained_fraction = expected_ratio / current_ratio if current_ratio > 0 else 0.0
        else:
            retained_fraction = current_ratio / expected_ratio if expected_ratio > 0 else 0.0
        crop_fraction = max(0.0, 1.0 - retained_fraction)
        if crop_fraction > max_crop_fraction:
            return None

        return match_aspect_to_ratio(image_path, expected_ratio)
    except Exception:
        return None


def generate_empty_room(
    image_path,
    unique_id,
    start_time,
    *,
    stage_name="Stage 1",
    return_raw: bool = False,
    total_timeout_limit: float,
    log_step: Callable[[str], None],
    model_name: str,
    build_empty_room_prompt: Callable[[], str],
    allow_all_safety_settings: Callable[[], Any],
    call_image_with_failover: Callable[..., Any] | None = None,
    call_gemini_with_failover: Callable[..., Any] | None = None,
    match_aspect_to_target: Callable[[str, str], str | None],
):
    if time.time() - start_time > total_timeout_limit:
        return image_path
    log_step(f"[{stage_name}] Empty Room Generation ({model_name})")

    system_instruction = "You are an expert architectural AI."
    prompt = build_empty_room_prompt()
    safety_settings = allow_all_safety_settings()
    image_call = call_image_with_failover or call_gemini_with_failover
    if image_call is None:
        raise TypeError("generate_empty_room requires call_image_with_failover or call_gemini_with_failover")

    for try_count in range(3):
        remaining = max(10, total_timeout_limit - (time.time() - start_time))
        with Image.open(image_path) as img:
            response = image_call(
                model_name,
                [prompt, img],
                {
                    "timeout": remaining,
                    "aspect_ratio": "16:9",
                    "image_size": "4K",
                    "thinking_level": "high",
                    "include_thoughts": False,
                },
                safety_settings,
                system_instruction,
                log_tag="Stage1.EmptyRoom",
            )

        if response and hasattr(response, "candidates") and response.candidates:
            if hasattr(response, "parts") and response.parts:
                for part in response.parts:
                    if not hasattr(part, "inline_data"):
                        continue
                    print(f">> [성공] 빈 방 이미지 생성됨! ({try_count + 1}회차)", flush=True)
                    timestamp = int(time.time())
                    filename = f"empty_{timestamp}_{unique_id}.png"
                    path = os.path.join("outputs", filename)
                    with open(path, "wb") as output_file:
                        output_file.write(part.inline_data.data)
                    if not _is_valid_generated_image(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        print(f">> [Retry] invalid empty-room image on attempt {try_count + 1}", flush=True)
                        break
                    out = _normalize_empty_room_ratio(
                        path,
                        image_path,
                        expected_ratio=16 / 9,
                        match_aspect_to_target=match_aspect_to_target,
                    )
                    if not out:
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        print(f">> [Retry] unsafe empty-room aspect crop on attempt {try_count + 1}", flush=True)
                        break
                    if return_raw:
                        return (out, path)
                    if out != path:
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                    return out
            else:
                print(">> [Blocked] safety filter blocked empty-room response", flush=True)
        print(f">> [Retry] 시도 {try_count + 1} 실패. 재시도..", flush=True)

    print(">> [실패] 빈 방 생성 불가. 원본 사용.", flush=True)
    if return_raw:
        return (image_path, image_path)
    return image_path
