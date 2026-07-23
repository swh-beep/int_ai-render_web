from typing import Callable, Optional

from application.render.white_balance_correction import apply_reference_relative_white_balance


def run_image_edit_job(
    payload: dict,
    *,
    normalize_audience: Callable[[Optional[str]], str],
    build_s3_prefix: Callable[[str, str, str | None], str],
    materialize_input: Callable[[str | None, str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    process_image_edit_logic: Callable[[list[str], str, str, str, int, str | None], str | None],
) -> dict:
    photo_paths = payload.get("photo_paths") or []
    instructions = payload.get("instructions", "")
    mode = payload.get("mode", "edit")
    unique_id = payload.get("unique_id")
    mask_path = payload.get("mask_path")
    audience = payload.get("audience")

    aud = normalize_audience(audience)
    category = "editrendered" if mode == "edit" else "decorrendered"
    prefix_user = build_s3_prefix(aud, category, "user-photos")
    prefix_rendered = build_s3_prefix(aud, category, "rendered")

    if not photo_paths:
        return {"error": "No input photos"}

    local_photos = []
    for idx, photo_path in enumerate(photo_paths):
        local_path = materialize_input(photo_path, f"edit_{idx}")
        if local_path:
            local_photos.append(local_path)
            resolve_image_url(local_path, s3_prefix_override=prefix_user)

    if not local_photos:
        return {"error": "Input file not found"}

    local_mask = materialize_input(mask_path, "mask") if mask_path else None
    result_path = process_image_edit_logic(local_photos, instructions, mode, unique_id, 1, local_mask)
    if not result_path:
        return {"error": "Failed to generate image"}

    correction_stage = "image_studio_edit" if mode == "edit" else "image_studio_decorate"
    result_path = apply_reference_relative_white_balance(
        result_path,
        reference_path=local_photos[0],
        stage_name=correction_stage,
    ).path

    result_url = resolve_image_url(result_path, s3_prefix_override=prefix_rendered)
    if not result_url:
        return {"error": "Failed to generate image"}

    return {"urls": [result_url], "message": "Success"}
