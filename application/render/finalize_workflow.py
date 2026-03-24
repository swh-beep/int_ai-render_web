import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable


def run_finalize_job(
    payload: dict,
    *,
    finalize_request_factory: Callable[..., object],
    materialize_input: Callable[[str | None, str], str | None],
    generate_empty_room: Callable[[str, str, float], str | None],
    call_magnific_api: Callable[[str, str, float], str | None],
    s3_prefix_from_url: Callable[[str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
) -> dict:
    request = finalize_request_factory(image_url=payload.get("image_url", ""))

    local_path = materialize_input(request.image_url, "finalize")
    if not local_path or not os.path.exists(local_path):
        return {"error": "Original file not found"}

    unique_id = uuid.uuid4().hex[:6]
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_furnished = executor.submit(call_magnific_api, local_path, unique_id + "_upscale_furnished", start_time)
        empty_room_path = generate_empty_room(local_path, unique_id + "_final_empty", start_time, stage_name="Finalize: Empty Gen")
        future_empty = executor.submit(call_magnific_api, empty_room_path, unique_id + "_upscale_empty", start_time)
        final_furnished_path = future_furnished.result()
        final_empty_path = future_empty.result()

    base_prefix = s3_prefix_from_url(request.image_url)
    furnished_url = resolve_image_url(final_furnished_path, s3_prefix_override=base_prefix)
    empty_url = resolve_image_url(final_empty_path, s3_prefix_override=base_prefix)
    return {
        "upscaled_furnished": furnished_url,
        "upscaled_empty": empty_url,
        "message": "Success",
    }
