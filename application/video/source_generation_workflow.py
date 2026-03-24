import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from api_models import SourceGenRequest, SourceItem
from application.video.job_store import set_video_job, update_video_job
from application.video.video_support import (
    download_to_path,
    ffmpeg_image_to_video,
    image_url_to_b64,
    kling_prompts_dynamic,
)


def _generate_raw_only(
    idx: int,
    item: SourceItem,
    job_id: str,
    out_dir: Path,
    cfg_scale: float,
    *,
    video_target_fps: int,
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
) -> Path:
    filename = f"source_{job_id}_{idx}.mp4"
    out_path = out_dir / filename

    if item.motion == "static" and item.effect == "none":
        print(f"[Clip {idx}] Static detected. Skipping Kling (Fast generation).", flush=True)
        temp_img = out_dir / f"temp_src_{job_id}_{idx}.png"
        try:
            download_to_path(item.url, temp_img)
            ffmpeg_image_to_video(temp_img, out_path, 5.0, 1080, 1920, video_target_fps)
            return out_path
        except Exception as exc:
            print(f"Static Gen Error: {exc}", flush=True)
            raise
        finally:
            if temp_img.exists():
                temp_img.unlink()

    print(f"[Clip {idx}] Kling AI Generating... ({item.motion}/{item.effect})", flush=True)
    prompts = kling_prompts_dynamic(item.motion, item.effect)
    img_b64 = image_url_to_b64(item.url)
    task_id = create_kling_task(img_b64, prompts["prompt"], prompts["negative_prompt"], "5", cfg_scale)
    video_url = poll_kling_task(
        task_id,
        clip_index=idx,
        total_clips=1,
        update_job_status=lambda progress, message: update_video_job(job_id, progress=progress, message=message),
    )
    download_to_path(video_url, out_path)
    return out_path


def run_source_generation_job(
    job_id: str,
    items: list[SourceItem],
    cfg_scale: float,
    *,
    video_target_fps: int,
    video_max_concurrency: int,
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
) -> None:
    try:
        set_video_job(job_id, {"status": "RUNNING", "message": "Initializing...", "progress": 0, "results": []})

        out_dir = Path("outputs")
        out_dir.mkdir(parents=True, exist_ok=True)

        total_steps = len(items)
        results_map = [None] * total_steps
        failures: list[dict] = []

        with ThreadPoolExecutor(max_workers=video_max_concurrency) as executor:
            future_map = {
                executor.submit(
                    _generate_raw_only,
                    i,
                    item,
                    job_id,
                    out_dir,
                    cfg_scale,
                    video_target_fps=video_target_fps,
                    create_kling_task=create_kling_task,
                    poll_kling_task=poll_kling_task,
                ): i
                for i, item in enumerate(items)
            }

            completed_count = 0
            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    path = future.result()
                    if path:
                        results_map[idx] = f"/outputs/{path.name}"
                except Exception as exc:
                    print(f"Clip {idx} failed: {exc}", flush=True)
                    results_map[idx] = None
                    failures.append({"index": idx, "error": str(exc)})

                completed_count += 1
                update_video_job(
                    job_id,
                    progress=int((completed_count / total_steps) * 100),
                    message=f"Generated {completed_count}/{total_steps} clips",
                )

        success_count = sum(1 for result in results_map if result)
        if failures and success_count == 0:
            update_video_job(
                job_id,
                status="FAILED",
                results=results_map,
                errors=failures,
                error=f"All clips failed ({len(failures)}/{total_steps})",
                message="Source generation failed.",
            )
            return

        final_fields = {
            "status": "COMPLETED",
            "results": results_map,
            "message": "Source generation complete.",
        }
        if failures:
            final_fields["errors"] = failures
            final_fields["message"] = f"Source generation complete with {len(failures)} failed clip(s)."

        update_video_job(
            job_id,
            **final_fields,
        )
    except Exception as exc:
        print(f"Source Gen Critical Error: {exc}", flush=True)
        traceback.print_exc()
        update_video_job(job_id, status="FAILED", error=str(exc))


def queue_source_generation_job(
    req: SourceGenRequest,
    *,
    video_target_fps: int,
    video_max_concurrency: int,
    create_kling_task: Callable[..., str],
    poll_kling_task: Callable[..., str],
) -> str:
    job_id = uuid.uuid4().hex
    set_video_job(job_id, {"status": "QUEUED", "progress": 0})
    threading.Thread(
        target=run_source_generation_job,
        args=(job_id, req.items, req.cfg_scale),
        kwargs={
            "video_target_fps": video_target_fps,
            "video_max_concurrency": video_max_concurrency,
            "create_kling_task": create_kling_task,
            "poll_kling_task": poll_kling_task,
        },
        daemon=True,
    ).start()
    return job_id
