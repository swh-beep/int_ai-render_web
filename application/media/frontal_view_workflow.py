from typing import Callable, Optional


def run_frontal_view_job(
    payload: dict,
    *,
    normalize_audience: Callable[[Optional[str]], str],
    build_s3_prefix: Callable[[str, str, str | None], str],
    materialize_input: Callable[[str | None, str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    generate_frontal_room_from_photos: Callable[[list[str], str, int], str | None],
) -> dict:
    photo_paths = payload.get("photo_paths") or []
    unique_id = payload.get("unique_id")
    audience = payload.get("audience")

    aud = normalize_audience(audience)
    prefix_user = build_s3_prefix(aud, "realphotorendered", "user-photos")
    prefix_rendered = build_s3_prefix(aud, "realphotorendered", "rendered")

    if not photo_paths:
        return {"error": "No input photos"}

    local_photos = []
    for idx, photo_path in enumerate(photo_paths):
        local_path = materialize_input(photo_path, f"frontal_{idx}")
        if local_path:
            local_photos.append(local_path)
            resolve_image_url(local_path, s3_prefix_override=prefix_user)

    if not local_photos:
        return {"error": "Input file not found"}

    result_path = generate_frontal_room_from_photos(local_photos, unique_id, 1)
    if not result_path:
        return {"error": "Failed to generate images"}

    result_url = resolve_image_url(result_path, s3_prefix_override=prefix_rendered)
    if not result_url:
        return {"error": "Failed to generate images"}

    return {"urls": [result_url], "message": "Success"}
