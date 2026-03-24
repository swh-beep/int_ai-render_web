import os
import time
import uuid
from typing import Callable


def run_upscale_job(
    payload: dict,
    *,
    upscale_request_factory: Callable[..., object],
    materialize_input: Callable[[str | None, str], str | None],
    call_magnific_api: Callable[[str, str, float], str | None],
    s3_prefix_from_url: Callable[[str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
) -> dict:
    request = upscale_request_factory(image_url=payload.get("image_url", ""))

    local_path = materialize_input(request.image_url, "upscale")
    if not local_path or not os.path.exists(local_path):
        return {"error": "File not found"}

    final_path = call_magnific_api(local_path, uuid.uuid4().hex[:8], time.time())
    base_prefix = s3_prefix_from_url(request.image_url)
    upscaled_url = resolve_image_url(final_path, s3_prefix_override=base_prefix)
    return {"upscaled_url": upscaled_url, "message": "Success"}
