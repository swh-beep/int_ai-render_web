import os
from typing import Callable


def log_render_summary(
    summary: dict,
    *,
    log_summary: bool,
    logger,
) -> None:
    if not log_summary:
        return

    reasons = []
    if summary.get("dims_fail", 0):
        reasons.append(f"Dims fail={summary.get('dims_fail', 0)}")
    if summary.get("dims_warn", 0):
        reasons.append(f"Dims warn={summary.get('dims_warn', 0)}")
    if summary.get("scalecheck_fail", 0):
        reasons.append(f"ScaleCheck fail={summary.get('scalecheck_fail', 0)}")
    if summary.get("scale_guide_skipped", 0):
        reasons.append(f"Scale guide skipped={summary.get('scale_guide_skipped', 0)}")
    if reasons:
        logger.warning("WARNING: %s", "; ".join(reasons))


def build_render_response_payload(
    *,
    std_path: str,
    step1_img: str,
    scale_guide_path: str | None,
    generated_results: list[str],
    moodboard_url: str | None,
    furniture_data: list[dict],
    volume_ranking: list[dict],
    prefix_main_user: str,
    prefix_main_empty: str,
    prefix_main_rendered: str,
    resolve_image_url: Callable[[str | None, str | None], str | None],
) -> dict:
    final_before_url = resolve_image_url(step1_img, s3_prefix_override=prefix_main_empty)

    scale_guide_url = None
    try:
        if scale_guide_path and os.path.exists(scale_guide_path):
            scale_guide_url = resolve_image_url(scale_guide_path, s3_prefix_override=prefix_main_rendered)
    except Exception:
        pass

    result_urls = [resolve_image_url(path, s3_prefix_override=prefix_main_rendered) for path in generated_results if path]
    if not result_urls and step1_img:
        result_urls = [resolve_image_url(step1_img, s3_prefix_override=prefix_main_empty)]

    return {
        "original_url": resolve_image_url(std_path, s3_prefix_override=prefix_main_user),
        "empty_room_url": final_before_url,
        "result_url": result_urls[0] if result_urls else None,
        "result_urls": result_urls,
        "moodboard_url": moodboard_url,
        "scale_guide_url": scale_guide_url,
        "furniture_data": furniture_data,
        "volume_ranking": volume_ranking,
        "message": "Complete",
    }
