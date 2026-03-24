import os
import time
import uuid
from typing import Callable, Optional


def run_generate_empty_room_job(
    payload: dict,
    *,
    normalize_audience: Callable[[Optional[str]], str],
    materialize_input: Callable[[str | None, str], str | None],
    generate_empty_room: Callable[[str, str, float], str | None],
    build_s3_prefix: Callable[[str, str, str | None], str],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    persist_job_result: Callable[[dict, Optional[str]], None],
) -> dict:
    image_url = payload.get("image_url", "")
    audience = payload.get("audience")
    aud = normalize_audience(audience)
    local_path = materialize_input(image_url, "empty_src")
    if not local_path or not os.path.exists(local_path):
        return {"error": "Input file not found"}

    start_time = time.time()
    unique_id = uuid.uuid4().hex[:8]
    empty_path = generate_empty_room(local_path, unique_id, start_time, stage_name="Direct: Empty Gen")
    if not empty_path or not os.path.exists(empty_path):
        return {"error": "Empty room generation failed"}

    prefix_empty = build_s3_prefix(aud, "mainrendered", "empty")
    empty_url = resolve_image_url(empty_path, s3_prefix_override=prefix_empty)
    result = {"empty_room_url": empty_url or empty_path}
    persist_job_result(result, audience=aud)
    return result
