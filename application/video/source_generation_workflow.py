import hashlib
import json
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from api_models import SourceGenRequest, SourceItem
from application.video.job_store import (
    create_video_job_if_absent,
    get_video_job,
    set_video_job,
    update_video_job,
    update_video_job_item,
)
from application.video.video_support import (
    clip_url_to_image_bytes,
    download_to_path,
    ffmpeg_image_to_video,
    kling_prompts_dynamic,
)


def _aspect_dimensions(aspect_ratio: str | None, video_quality: str | None = "720p") -> tuple[int, int]:
    base_height = 1080 if video_quality == "1080p" else 720
    if aspect_ratio == "16:9":
        return int(base_height * 16 / 9), base_height
    return base_height, int(base_height * 16 / 9)


_source_worker_lock = threading.Lock()
_active_source_workers: set[str] = set()


def _claim_source_worker(job_id: str) -> bool:
    with _source_worker_lock:
        if job_id in _active_source_workers:
            return False
        _active_source_workers.add(job_id)
        return True


def _release_source_worker(job_id: str) -> None:
    with _source_worker_lock:
        _active_source_workers.discard(job_id)


def _local_output_path(output_url: str | None) -> Path | None:
    if not output_url or not output_url.startswith("/outputs/"):
        return None
    return Path(output_url.lstrip("/"))


def _result_exists(output_url: str | None) -> bool:
    local_path = _local_output_path(output_url)
    return bool(local_path and local_path.exists())


def _normalize_prompt(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _build_clip_state(job_id: str, idx: int, item: SourceItem, source_hash: str) -> dict:
    output_name = f"source_{job_id}_{idx}.mp4"
    return {
        "index": idx,
        "url": item.url,
        "end_url": item.end_url,
        "motion": item.motion,
        "effect": item.effect,
        "duration": item.duration,
        "custom_motion_prompt": _normalize_prompt(item.custom_motion_prompt),
        "custom_effect_prompt": _normalize_prompt(item.custom_effect_prompt),
        "source_hash": source_hash,
        "task_id": None,
        "provider_status": None,
        "provider_result_url": None,
        "output_name": output_name,
        "output_url": f"/outputs/{output_name}",
        "status": "PENDING",
        "attempt_count": 0,
        "last_error": None,
    }


def _build_request_key(req: SourceGenRequest, *, job_id: str) -> tuple[str, list[dict]]:
    fingerprint_payload = {
        "aspect_ratio": req.aspect_ratio or "9:16",
        "cfg_scale": round(float(req.cfg_scale), 4),
        "video_quality": req.video_quality or "720p",
        "sound": req.sound or "off",
        "items": [],
    }
    clip_states: list[dict] = []

    for idx, item in enumerate(req.items):
        source_hash = hashlib.sha256(clip_url_to_image_bytes(item.url)).hexdigest()
        motion = item.motion or "static"
        effect = item.effect or "none"
        custom_motion_prompt = _normalize_prompt(item.custom_motion_prompt)
        custom_effect_prompt = _normalize_prompt(item.custom_effect_prompt)
        duration = item.duration or "5"

        fingerprint_payload["items"].append(
            {
                "source_hash": source_hash,
                "end_url": item.end_url or "",
                "motion": motion,
                "effect": effect,
                "duration": duration,
                "custom_motion_prompt": custom_motion_prompt or "",
                "custom_effect_prompt": custom_effect_prompt or "",
            }
        )

        clip_states.append(
            _build_clip_state(
                job_id,
                idx,
                SourceItem(
                    url=item.url,
                    end_url=item.end_url,
                    motion=motion,
                    effect=effect,
                    custom_motion_prompt=custom_motion_prompt,
                    custom_effect_prompt=custom_effect_prompt,
                    duration=duration,
                ),
                source_hash,
            )
        )

    request_key = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return request_key, clip_states


def _job_ready_to_reuse(job: dict) -> bool:
    results = job.get("results") or []
    items = job.get("items") or []
    if not results or len(results) != len(items):
        return False
    return all(bool(result) and _result_exists(result) for result in results)


def _job_has_resumable_state(job: dict) -> bool:
    for item in job.get("items") or []:
        if item.get("task_id") or item.get("provider_result_url") or _result_exists(item.get("output_url")):
            return True
    return False


def _update_clip_progress(job_id: str, idx: int, status: str, progress: int, elapsed_sec: int) -> None:
    update_video_job_item(
        job_id,
        idx,
        provider_status=status,
        last_polled_elapsed_sec=elapsed_sec,
    )


def _coerce_progress(value: int | float | None) -> int:
    if value is None:
        return 0
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _update_job_progress_monotonic(job_id: str, *, progress: int | float | None, message: str | None = None) -> None:
    current_job = get_video_job(job_id) or {}
    current_progress = _coerce_progress(current_job.get("progress"))
    requested_progress = _coerce_progress(progress)
    next_progress = max(current_progress, requested_progress)

    fields = {"progress": next_progress}
    if message and requested_progress >= current_progress:
        fields["message"] = message

    update_video_job(job_id, **fields)


def _source_worker_count(total_clips: int, requested_max: int) -> int:
    if total_clips <= 0:
        return 1
    return max(1, min(total_clips, max(1, int(requested_max or 1))))


def _process_clip_by_index(
    job_id: str,
    idx: int,
    *,
    total_clips: int,
    cfg_scale: float,
    aspect_ratio: str,
    video_quality: str,
    sound: str,
    video_target_fps: int,
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
) -> Path:
    current_job = get_video_job(job_id) or {}
    current_items = current_job.get("items") or []
    if idx >= len(current_items):
        raise IndexError(f"Clip index {idx} is out of range for job {job_id}")

    return _process_clip(
        job_id,
        idx,
        current_items[idx],
        total_clips=total_clips,
        cfg_scale=cfg_scale,
        aspect_ratio=aspect_ratio,
        video_quality=video_quality,
        sound=sound,
        video_target_fps=video_target_fps,
        create_kling_task=create_kling_task,
        poll_kling_task=poll_kling_task,
    )


def _materialize_clip(item_state: dict) -> SourceItem:
    return SourceItem(
        url=item_state["url"],
        end_url=item_state.get("end_url"),
        motion=item_state.get("motion") or "static",
        effect=item_state.get("effect") or "none",
        custom_motion_prompt=item_state.get("custom_motion_prompt"),
        custom_effect_prompt=item_state.get("custom_effect_prompt"),
        duration=item_state.get("duration") or "5",
    )


def _seed_direct_job_state(
    job_id: str,
    items: list[SourceItem],
    cfg_scale: float,
    aspect_ratio: str = "9:16",
    video_quality: str = "720p",
    sound: str = "off",
) -> None:
    clip_states = [
        _build_clip_state(
            job_id,
            idx,
            SourceItem(
                url=item.url,
                end_url=item.end_url,
                motion=item.motion or "static",
                effect=item.effect or "none",
                custom_motion_prompt=_normalize_prompt(item.custom_motion_prompt),
                custom_effect_prompt=_normalize_prompt(item.custom_effect_prompt),
                duration=item.duration or "5",
            ),
            hashlib.sha256(
                json.dumps(
                    {
                        "url": item.url,
                        "end_url": item.end_url or "",
                        "motion": item.motion or "static",
                        "effect": item.effect or "none",
                        "duration": item.duration or "5",
                        "custom_motion_prompt": _normalize_prompt(item.custom_motion_prompt) or "",
                        "custom_effect_prompt": _normalize_prompt(item.custom_effect_prompt) or "",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        )
        for idx, item in enumerate(items)
    ]
    set_video_job(
        job_id,
        {
            "job_type": "source_generation",
            "cfg_scale": float(cfg_scale),
            "aspect_ratio": aspect_ratio or "9:16",
            "video_quality": video_quality or "720p",
            "sound": sound or "off",
            "status": "QUEUED",
            "progress": 0,
            "message": "Queued for generation...",
            "results": [None] * len(clip_states),
            "errors": [],
            "items": clip_states,
        },
    )


def _process_static_clip(
    job_id: str,
    idx: int,
    item: SourceItem,
    out_path: Path,
    *,
    video_target_fps: int,
    aspect_ratio: str,
    video_quality: str,
) -> Path:
    update_video_job_item(job_id, idx, status="PROCESSING", provider_status="LOCAL_STATIC", last_error=None)
    temp_img = out_path.parent / f"temp_src_{job_id}_{idx}.png"
    try:
        download_to_path(item.url, temp_img)
        target_w, target_h = _aspect_dimensions(aspect_ratio, video_quality)
        ffmpeg_image_to_video(temp_img, out_path, float(item.duration or "5"), target_w, target_h, video_target_fps)
        update_video_job_item(job_id, idx, status="COMPLETED", output_url=f"/outputs/{out_path.name}")
        return out_path
    finally:
        if temp_img.exists():
            temp_img.unlink()


def _process_ai_clip(
    job_id: str,
    idx: int,
    item: SourceItem,
    out_path: Path,
    *,
    total_clips: int,
    cfg_scale: float,
    aspect_ratio: str,
    video_quality: str,
    sound: str,
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
) -> Path:
    current_job = get_video_job(job_id) or {}
    item_state = ((current_job.get("items") or []) + [{}])[idx]
    provider_result_url = item_state.get("provider_result_url")

    if provider_result_url:
        update_video_job_item(job_id, idx, status="DOWNLOADING", last_error=None)
        download_to_path(provider_result_url, out_path)
        update_video_job_item(job_id, idx, status="COMPLETED", output_url=f"/outputs/{out_path.name}")
        return out_path

    task_id = item_state.get("task_id")
    if not task_id:
        prompts = kling_prompts_dynamic(
            item.motion,
            item.effect,
            custom_motion_prompt=item.custom_motion_prompt,
            custom_effect_prompt=item.custom_effect_prompt,
        )
        attempt_count = int(item_state.get("attempt_count") or 0) + 1
        task_kwargs = {
            "aspect_ratio": aspect_ratio or "9:16",
            "quality": video_quality or "720p",
            "sound": sound or "off",
        }
        if item.end_url:
            task_kwargs["end_image_url"] = item.end_url
        task_id = create_kling_task(
            item.url,
            prompts["prompt"],
            prompts["negative_prompt"],
            item.duration or "5",
            cfg_scale,
            **task_kwargs,
        )
        update_video_job_item(
            job_id,
            idx,
            task_id=task_id,
            attempt_count=attempt_count,
            status="RUNNING",
            provider_status="CREATED",
            last_error=None,
        )

    video_url = poll_kling_task(
        task_id,
        clip_index=idx,
        total_clips=total_clips,
        update_job_status=lambda progress, message: _update_job_progress_monotonic(
            job_id,
            progress=progress,
            message=message,
        ),
        status_callback=lambda status, progress, elapsed_sec: _update_clip_progress(
            job_id,
            idx,
            status,
            progress,
            elapsed_sec,
        ),
    )

    update_video_job_item(
        job_id,
        idx,
        provider_status="COMPLETED",
        provider_result_url=video_url,
        status="DOWNLOADING",
    )
    download_to_path(video_url, out_path)
    update_video_job_item(job_id, idx, status="COMPLETED", output_url=f"/outputs/{out_path.name}")
    return out_path


def _process_clip(
    job_id: str,
    idx: int,
    item_state: dict,
    *,
    total_clips: int,
    cfg_scale: float,
    aspect_ratio: str,
    video_quality: str,
    sound: str,
    video_target_fps: int,
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
) -> Path:
    out_dir = Path("outputs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / item_state["output_name"]

    if out_path.exists():
        update_video_job_item(job_id, idx, status="COMPLETED", output_url=f"/outputs/{out_path.name}")
        return out_path

    item = _materialize_clip(item_state)
    is_static = (
        item.motion == "static"
        and item.effect == "none"
        and not item.end_url
        and not item.custom_motion_prompt
        and not item.custom_effect_prompt
    )

    if is_static:
        return _process_static_clip(
            job_id,
            idx,
            item,
            out_path,
            video_target_fps=video_target_fps,
            aspect_ratio=aspect_ratio,
            video_quality=video_quality,
        )

    return _process_ai_clip(
        job_id,
        idx,
        item,
        out_path,
        total_clips=total_clips,
        cfg_scale=cfg_scale,
        aspect_ratio=aspect_ratio,
        video_quality=video_quality,
        sound=sound,
        create_kling_task=create_kling_task,
        poll_kling_task=poll_kling_task,
    )


def run_source_generation_job(
    job_id: str,
    items: list[SourceItem] | None = None,
    cfg_scale: float | None = None,
    sound: str | None = None,
    *,
    video_target_fps: int,
    video_max_concurrency: int,
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
) -> None:
    try:
        job = get_video_job(job_id)
        if not job and items is not None:
            _seed_direct_job_state(
                job_id,
                items,
                float(cfg_scale if cfg_scale is not None else 0.5),
                sound=sound or "off",
            )
            job = get_video_job(job_id)
        if not job:
            return

        item_states = job.get("items") or []
        total_clips = len(item_states)
        cfg_scale = float(job.get("cfg_scale") or 0.5)
        aspect_ratio = job.get("aspect_ratio") or "9:16"
        video_quality = job.get("video_quality") or "720p"
        sound = job.get("sound") or "off"
        worker_count = _source_worker_count(total_clips, video_max_concurrency)

        update_video_job(
            job_id,
            status="RUNNING",
            message=f"Preparing clip generation ({worker_count} workers)...",
            progress=int(job.get("progress") or 0),
            errors=[],
        )

        results_map = list(job.get("results") or [None] * total_clips)
        failures: list[dict] = []

        if total_clips == 0:
            update_video_job(
                job_id,
                status="COMPLETED",
                results=[],
                message="No clips requested.",
                progress=100,
            )
            return

        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix=f"source-{job_id[:8]}") as executor:
            future_map = {
                executor.submit(
                    _process_clip_by_index,
                    job_id,
                    idx,
                    total_clips=total_clips,
                    cfg_scale=cfg_scale,
                    aspect_ratio=aspect_ratio,
                    video_quality=video_quality,
                    sound=sound,
                    video_target_fps=video_target_fps,
                    create_kling_task=create_kling_task,
                    poll_kling_task=poll_kling_task,
                ): idx
                for idx in range(total_clips)
            }

            for future in as_completed(future_map):
                idx = future_map[future]
                current_job = get_video_job(job_id) or {}

                try:
                    path = future.result()
                    results_map[idx] = f"/outputs/{path.name}"
                    completed_count = sum(1 for result in results_map if result)
                    requested_progress = int((completed_count / max(1, total_clips)) * 100)
                    update_video_job(
                        job_id,
                        results=results_map,
                        progress=max(requested_progress, _coerce_progress(current_job.get("progress"))),
                        message=f"Generated {completed_count}/{total_clips} clips",
                    )
                except Exception as exc:
                    error_text = str(exc)
                    print(f"Clip {idx} failed: {error_text}", flush=True)
                    traceback.print_exc()
                    update_video_job_item(job_id, idx, status="FAILED", last_error=error_text)
                    results_map[idx] = None
                    failures.append({"index": idx, "error": error_text})

        success_count = sum(1 for result in results_map if result)
        if failures and success_count == 0:
            update_video_job(
                job_id,
                status="FAILED",
                results=results_map,
                errors=failures,
                error=f"All clips failed. First error: {failures[0]['error']}",
                message="Source generation failed.",
                progress=0,
            )
            return

        final_fields = {
            "status": "COMPLETED",
            "results": results_map,
            "message": "Source generation complete.",
            "progress": 100,
        }
        if failures:
            final_fields["errors"] = failures
            final_fields["message"] = f"Source generation complete with {len(failures)} failed clip(s)."

        update_video_job(job_id, **final_fields)
    except Exception as exc:
        print(f"Source Gen Critical Error: {exc}", flush=True)
        traceback.print_exc()
        update_video_job(job_id, status="FAILED", error=str(exc), message="Source generation failed.", progress=0)
    finally:
        _release_source_worker(job_id)


def _start_source_generation_worker(
    job_id: str,
    *,
    video_target_fps: int,
    video_max_concurrency: int,
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
) -> None:
    if not _claim_source_worker(job_id):
        return
    threading.Thread(
        target=run_source_generation_job,
        kwargs={
            "job_id": job_id,
            "video_target_fps": video_target_fps,
            "video_max_concurrency": video_max_concurrency,
            "create_kling_task": create_kling_task,
            "poll_kling_task": poll_kling_task,
        },
        daemon=True,
    ).start()


def queue_source_generation_job(
    req: SourceGenRequest,
    *,
    video_target_fps: int,
    video_max_concurrency: int,
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
) -> str:
    candidate_job_id = uuid.uuid4().hex
    request_key, clip_states = _build_request_key(req, job_id=candidate_job_id)
    initial_state = {
        "job_type": "source_generation",
        "request_key": request_key,
        "cfg_scale": float(req.cfg_scale),
        "aspect_ratio": req.aspect_ratio or "9:16",
        "video_quality": req.video_quality or "720p",
        "sound": req.sound or "off",
        "status": "QUEUED",
        "progress": 0,
        "message": "Queued for generation...",
        "results": [None] * len(clip_states),
        "errors": [],
        "items": clip_states,
    }
    selected_state, created = create_video_job_if_absent(
        candidate_job_id,
        initial_state,
        request_key=request_key,
        job_type="source_generation",
    )

    if not created:
        existing_job_id = selected_state["job_id"]
        if _job_ready_to_reuse(selected_state):
            update_video_job(
                existing_job_id,
                status="COMPLETED",
                message="Reusing existing generated clips.",
                progress=100,
            )
            return existing_job_id

        if selected_state.get("status") in {"QUEUED", "RUNNING"} or _job_has_resumable_state(selected_state):
            update_video_job(
                existing_job_id,
                status="RUNNING",
                error=None,
                message="Resuming existing clip generation...",
            )
            _start_source_generation_worker(
                existing_job_id,
                video_target_fps=video_target_fps,
                video_max_concurrency=video_max_concurrency,
                create_kling_task=create_kling_task,
                poll_kling_task=poll_kling_task,
            )
            return existing_job_id

        job_id = existing_job_id
        rebuilt_items = []
        for idx, state in enumerate(clip_states):
            rebuilt = dict(state)
            rebuilt["output_name"] = f"source_{job_id}_{idx}.mp4"
            rebuilt["output_url"] = f"/outputs/{rebuilt['output_name']}"
            rebuilt_items.append(rebuilt)
        set_video_job(
            job_id,
            {
                **initial_state,
                "items": rebuilt_items,
                "results": [None] * len(rebuilt_items),
                "status": "QUEUED",
                "progress": 0,
                "message": "Queued for generation...",
                "errors": [],
                "error": None,
            },
        )
    else:
        job_id = candidate_job_id

    _start_source_generation_worker(
        job_id,
        video_target_fps=video_target_fps,
        video_max_concurrency=video_max_concurrency,
        create_kling_task=create_kling_task,
        poll_kling_task=poll_kling_task,
    )
    return job_id
