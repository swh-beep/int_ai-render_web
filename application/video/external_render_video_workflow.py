from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from api_models import CompileClip, CompileRequest, SourceGenRequest, SourceItem
from application.video.video_support import download_to_path, ffmpeg_image_to_video


_PRIMARY_MOTION_SEQUENCE: tuple[str, ...] = (
    "zoom_in_slow",
    "orbit_l_slow",
    "orbit_r_slow",
    "zoom_in_slow",
)
_REPEATING_MOTION_SEQUENCE: tuple[str, ...] = (
    "orbit_l_slow",
    "orbit_r_slow",
    "zoom_in_slow",
)
_EXTERNAL_BRAND_CARD_SEC = 3.0
_EXTERNAL_BRAND_CARD_PATH = Path(__file__).resolve().parents[2] / "static" / "thumbnails" / "external_video_logo_card.jpg"
_EXTERNAL_BRAND_CARD_FALLBACK_PATH = Path(__file__).resolve().parents[2] / "static" / "TIOR STUDIO(Black).png"


def _is_external_render_job_result(result: dict | None) -> bool:
    if not isinstance(result, dict):
        return False
    return any(key in result for key in ("resolved", "cart_kept", "cart_dropped"))


def _render_job_result(fetch_job: Callable[[str], Any], load_job_result: Callable[[str], dict | None], render_job_id: str) -> tuple[dict | None, str | None]:
    job = fetch_job(render_job_id)
    if job is not None:
        if bool(getattr(job, "is_finished", False)):
            if isinstance(getattr(job, "result", None), dict):
                return job.result, None
            saved = load_job_result(render_job_id)
            if saved is not None:
                return saved, None
            return None, "Source render job finished without a result payload"
        if bool(getattr(job, "is_failed", False)):
            return None, getattr(job, "exc_info", None) or "Source render job failed"
        saved = load_job_result(render_job_id)
        if saved is not None:
            return saved, None
        return None, "Source render job is not finished yet"

    saved = load_job_result(render_job_id)
    if saved is not None:
        return saved, None
    return None, "Source render job was not found"


def _preferred_brand_card_path() -> Path | None:
    for candidate in (_EXTERNAL_BRAND_CARD_PATH, _EXTERNAL_BRAND_CARD_FALLBACK_PATH):
        if candidate.exists():
            return candidate
    return None


def _extract_source_images(result: dict | None) -> list[str]:
    if not isinstance(result, dict):
        return []

    render_payload = result.get("render") or {}
    details_payload = result.get("details") or {}
    primary_main = None
    if isinstance(render_payload.get("result_url"), str) and render_payload.get("result_url", "").strip():
        primary_main = render_payload["result_url"].strip()
    elif isinstance(render_payload.get("result_urls"), list):
        for value in render_payload.get("result_urls") or []:
            if isinstance(value, str) and value.strip():
                primary_main = value.strip()
                break

    detail_urls: list[str] = []
    for row in details_payload.get("details") or []:
        if not isinstance(row, dict):
            continue
        value = row.get("url")
        if isinstance(value, str) and value.strip():
            detail_urls.append(value.strip())

    ordered: list[str] = []
    if primary_main:
        ordered.append(primary_main)
    ordered.extend(detail_urls)
    return ordered


def _motion_for_external_clip(index: int) -> str:
    if index < len(_PRIMARY_MOTION_SEQUENCE):
        return _PRIMARY_MOTION_SEQUENCE[index]
    return _REPEATING_MOTION_SEQUENCE[(index - len(_PRIMARY_MOTION_SEQUENCE)) % len(_REPEATING_MOTION_SEQUENCE)]


def _build_source_request(source_images: list[str], *, cfg_scale: float) -> SourceGenRequest:
    planned_items: list[SourceItem] = []
    if not source_images:
        return SourceGenRequest(items=planned_items, cfg_scale=cfg_scale)

    for index, source_image in enumerate(source_images):
        planned_items.append(
            SourceItem(
                url=source_image,
                motion=_motion_for_external_clip(index),
                effect="sunlight",
            )
        )
    return SourceGenRequest(items=planned_items, cfg_scale=cfg_scale)


def _requested_external_clip_count(payload: dict, available_source_count: int) -> int:
    try:
        requested = int(payload.get("clip_count") or 4)
    except Exception:
        requested = 4
    requested = max(4, min(6, requested))
    return max(1, min(requested, max(0, int(available_source_count))))


def _poll_video_job_until_terminal(
    job_id: str,
    *,
    get_video_job: Callable[[str], dict | None],
    timeout_sec: float,
    time_now: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict:
    deadline = float(time_now()) + max(1.0, float(timeout_sec))
    last_state: dict | None = None
    while float(time_now()) < deadline:
        state = get_video_job(job_id) or {}
        last_state = state
        status = str(state.get("status") or "").upper()
        if status in {"COMPLETED", "FAILED"}:
            return state
        sleep(1.0)

    return {
        "status": "FAILED",
        "error": f"Timed out waiting for video job {job_id}",
        "last_state": last_state,
    }


def _resolve_artifact_url(
    artifact: str | None,
    *,
    audience: str,
    subfolder: str,
    resolve_image_url: Callable[[str | None, str | None], str | None],
    build_s3_prefix: Callable[[str, str, str | None], str],
) -> str | None:
    if not artifact:
        return None
    if artifact.startswith("http://") or artifact.startswith("https://"):
        return artifact

    local_path = artifact.lstrip("/") if artifact.startswith("/") else artifact
    prefix = build_s3_prefix(audience, "videorendered", subfolder)
    return resolve_image_url(local_path, prefix) or artifact


def _build_static_fallback_clips(
    render_job_id: str,
    *,
    source_images: list[str],
    clip_count: int,
    video_target_fps: int,
) -> list[str]:
    if not source_images:
        return []

    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    fallback_results: list[str] = []

    for index in range(max(1, int(clip_count))):
        source_image = source_images[index % len(source_images)]
        temp_image = out_dir / f"fallback_{render_job_id}_{index}.png"
        output_video = out_dir / f"fallback_{render_job_id}_{index}.mp4"
        try:
            download_to_path(source_image, temp_image)
            ffmpeg_image_to_video(temp_image, output_video, 5.0, 1080, 1920, video_target_fps)
            fallback_results.append(f"/outputs/{output_video.name}")
        finally:
            if temp_image.exists():
                temp_image.unlink()
    return fallback_results


def _build_brand_card_clip(
    render_job_id: str,
    *,
    position: str,
    video_target_fps: int,
) -> str | None:
    card_path = _preferred_brand_card_path()
    if card_path is None:
        return None
    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    output_video = out_dir / f"external_brand_{render_job_id}_{position}.mp4"
    if not output_video.exists():
        ffmpeg_image_to_video(card_path, output_video, _EXTERNAL_BRAND_CARD_SEC, 1080, 1920, video_target_fps)
    return f"/outputs/{output_video.name}"


def _supplement_missing_clips(
    existing_results: list[str],
    *,
    render_job_id: str,
    source_images: list[str],
    requested_clip_count: int,
    video_target_fps: int,
) -> list[str]:
    if len(existing_results) >= requested_clip_count:
        return list(existing_results[:requested_clip_count])

    needed = requested_clip_count - len(existing_results)
    fallback_results = _build_static_fallback_clips(
        render_job_id,
        source_images=source_images,
        clip_count=needed,
        video_target_fps=video_target_fps,
    )
    return list(existing_results) + fallback_results


def run_external_render_video_job(
    payload: dict,
    *,
    fetch_job: Callable[[str], Any],
    load_job_result: Callable[[str], dict | None],
    queue_source_generation_job: Callable[..., str],
    queue_final_compile_job: Callable[..., str],
    get_video_job: Callable[[str], dict | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    build_s3_prefix: Callable[[str, str, str | None], str],
    normalize_audience: Callable[[str | None], str],
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
    video_target_fps: int,
    video_max_concurrency: int,
    time_now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    source_timeout_sec: float = 1800.0,
    compile_timeout_sec: float = 900.0,
) -> dict:
    render_job_id = str(payload.get("render_job_id") or "").strip()
    audience = normalize_audience(payload.get("audience"))
    cfg_scale = float(payload.get("cfg_scale") or 0.5)

    if not render_job_id:
        return {"error": "render_job_id is required", "render_job_id": render_job_id}

    render_result, render_error = _render_job_result(fetch_job, load_job_result, render_job_id)
    if render_error:
        return {"error": render_error, "render_job_id": render_job_id}
    if not _is_external_render_job_result(render_result):
        return {
            "error": "render_job_id must belong to an external render job",
            "render_job_id": render_job_id,
        }

    source_images = _extract_source_images(render_result)
    if not source_images:
        return {
            "error": "No source images were available from the render job",
            "render_job_id": render_job_id,
            "source_images": [],
            "clip_urls": [],
        }

    requested_clip_count = _requested_external_clip_count(payload, len(source_images))
    planned_source_images = list(source_images[:requested_clip_count])
    source_req = _build_source_request(planned_source_images, cfg_scale=cfg_scale)
    source_job_id = queue_source_generation_job(
        source_req,
        video_target_fps=video_target_fps,
        video_max_concurrency=video_max_concurrency,
        create_kling_task=create_kling_task,
        poll_kling_task=poll_kling_task,
    )
    source_state = _poll_video_job_until_terminal(
        source_job_id,
        get_video_job=get_video_job,
        timeout_sec=source_timeout_sec,
        time_now=time_now,
        sleep=sleep,
    )

    source_results = [
        result
        for result in (source_state.get("results") or [])
        if isinstance(result, str) and result.strip()
    ]
    if len(source_results) > requested_clip_count:
        source_results = source_results[:requested_clip_count]
    fallback_used = False
    if not source_results:
        fallback_results = _build_static_fallback_clips(
            render_job_id,
            source_images=planned_source_images,
            clip_count=requested_clip_count,
            video_target_fps=video_target_fps,
        )
        if not fallback_results:
            return {
                "error": source_state.get("error") or "Video source generation failed",
                "render_job_id": render_job_id,
                "source_images": planned_source_images,
                "clip_urls": [],
            }
        source_results = fallback_results
        fallback_used = True
    elif len(source_results) < requested_clip_count:
        source_results = _supplement_missing_clips(
            source_results,
            render_job_id=render_job_id,
            source_images=planned_source_images,
            requested_clip_count=requested_clip_count,
            video_target_fps=video_target_fps,
        )
        fallback_used = True

    intro_artifact = _build_brand_card_clip(render_job_id, position="intro", video_target_fps=video_target_fps)
    outro_artifact = _build_brand_card_clip(render_job_id, position="outro", video_target_fps=video_target_fps)
    compile_clip_urls: list[str] = []
    if intro_artifact:
        compile_clip_urls.append(intro_artifact)
    compile_clip_urls.extend(source_results)
    if outro_artifact:
        compile_clip_urls.append(outro_artifact)

    compile_req = CompileRequest(
        clips=[
            CompileClip(
                video_url=clip_url,
                speed=1.0,
                trim_start=0.0,
                trim_end=_EXTERNAL_BRAND_CARD_SEC if clip_url in {intro_artifact, outro_artifact} else 5.0,
            )
            for clip_url in compile_clip_urls
        ],
        include_intro_outro=False,
        aspect_ratio="16:9",
        aspect_mode="fill",
    )
    compile_job_id = queue_final_compile_job(compile_req, video_target_fps=video_target_fps)
    compile_state = _poll_video_job_until_terminal(
        compile_job_id,
        get_video_job=get_video_job,
        timeout_sec=compile_timeout_sec,
        time_now=time_now,
        sleep=sleep,
    )

    final_artifact = compile_state.get("result_url")
    if not final_artifact:
        return {
            "error": compile_state.get("error") or "Video compile failed",
            "render_job_id": render_job_id,
            "source_images": planned_source_images,
            "clip_urls": [
                resolved
                for resolved in (
                    _resolve_artifact_url(
                        clip_url,
                        audience=audience,
                        subfolder="clips",
                        resolve_image_url=resolve_image_url,
                        build_s3_prefix=build_s3_prefix,
                    )
                    for clip_url in source_results
                )
                if resolved
            ],
            "clip_count": len(source_results),
            "intro_url": _resolve_artifact_url(
                intro_artifact,
                audience=audience,
                subfolder="clips",
                resolve_image_url=resolve_image_url,
                build_s3_prefix=build_s3_prefix,
            ) if intro_artifact else None,
            "outro_url": _resolve_artifact_url(
                outro_artifact,
                audience=audience,
                subfolder="clips",
                resolve_image_url=resolve_image_url,
                build_s3_prefix=build_s3_prefix,
            ) if outro_artifact else None,
        }

    clip_urls = [
        resolved
        for resolved in (
            _resolve_artifact_url(
                clip_url,
                audience=audience,
                subfolder="clips",
                resolve_image_url=resolve_image_url,
                build_s3_prefix=build_s3_prefix,
            )
            for clip_url in source_results
        )
        if resolved
    ]
    intro_url = _resolve_artifact_url(
        intro_artifact,
        audience=audience,
        subfolder="clips",
        resolve_image_url=resolve_image_url,
        build_s3_prefix=build_s3_prefix,
    ) if intro_artifact else None
    outro_url = _resolve_artifact_url(
        outro_artifact,
        audience=audience,
        subfolder="clips",
        resolve_image_url=resolve_image_url,
        build_s3_prefix=build_s3_prefix,
    ) if outro_artifact else None
    video_url = _resolve_artifact_url(
        final_artifact,
        audience=audience,
        subfolder="final",
        resolve_image_url=resolve_image_url,
        build_s3_prefix=build_s3_prefix,
    )

    result = {
        "render_job_id": render_job_id,
        "source_images": planned_source_images,
        "clip_urls": clip_urls,
        "clip_count": len(clip_urls),
        "video_url": video_url,
        "intro_url": intro_url,
        "outro_url": outro_url,
        "assembled_clip_count": len(clip_urls) + (1 if intro_url else 0) + (1 if outro_url else 0),
    }
    if fallback_used:
        result["fallback_used"] = True
    return result
