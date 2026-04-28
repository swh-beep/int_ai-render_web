import json
import math
import time
from threading import Semaphore
from typing import Callable, Optional

import requests


def build_kling_endpoint(model_name: str) -> str:
    safe_model = (model_name or "").strip() or "kling-v2-5-pro"
    return f"https://api.freepik.com/v1/ai/image-to-video/{safe_model}"


def create_kling_task(
    image_b64: str,
    prompt: str,
    negative_prompt: str,
    duration: str,
    cfg_scale: float,
    *,
    freepik_api_key: str,
    kling_endpoint: str,
    video_semaphore: Semaphore,
) -> str:
    if not freepik_api_key:
        raise RuntimeError("FREEPIK_API_KEY (or MAGNIFIC_API_KEY) is not set.")

    payload = {
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "duration": duration,
        "cfg_scale": cfg_scale,
        "image": image_b64,
    }
    headers = {"x-freepik-api-key": freepik_api_key, "Content-Type": "application/json"}
    with video_semaphore:
        response = requests.post(kling_endpoint, headers=headers, json=payload, timeout=180)
    if response.status_code == 429:
        raise RuntimeError("Kling/Freepik rate limit hit (429). Try again later or lower VIDEO_MAX_CONCURRENCY.")
    if not response.ok:
        raise RuntimeError(f"Kling create failed ({response.status_code}): {response.text[:500]}")

    data = response.json()
    print(f"[DEBUG] Kling API Response: {json.dumps(data, indent=2)}", flush=True)

    task_id = (
        data.get("task_id")
        or data.get("id")
        or data.get("data", {}).get("task_id")
        or data.get("data", {}).get("id")
        or data.get("result", {}).get("task_id")
        or data.get("taskId")
    )
    if not task_id:
        print(f"[ERROR] Could not find task_id. Full response keys: {list(data.keys())}", flush=True)
        raise RuntimeError(f"No task_id returned from Kling create. Response: {json.dumps(data)[:300]}")

    print(f"[SUCCESS] Task created: {task_id}", flush=True)
    return task_id


def _provider_message(status: str, *, clip_index: int, total_clips: int, elapsed_sec: int) -> str:
    phase_map = {
        "CREATED": "Queued at provider",
        "PENDING": "Queued at provider",
        "QUEUED": "Queued at provider",
        "IN_PROGRESS": "Rendering at provider",
        "PROCESSING": "Rendering at provider",
        "COMPLETED": "Finalizing result",
        "SUCCEEDED": "Finalizing result",
        "SUCCESS": "Finalizing result",
        "DONE": "Finalizing result",
    }
    phase = phase_map.get(status or "", f"Provider status: {status or 'UNKNOWN'}")
    elapsed_label = f"{max(1, int(elapsed_sec // 60))}m" if elapsed_sec >= 60 else f"{elapsed_sec}s"
    return f"Clip {clip_index + 1}/{total_clips}: {phase} ({elapsed_label})"


def poll_kling_task(
    task_id: str,
    *,
    clip_index: int,
    total_clips: int,
    freepik_api_key: str,
    kling_endpoint: str,
    video_semaphore: Semaphore,
    update_job_status: Optional[Callable[[int, str], None]] = None,
    status_callback: Optional[Callable[[str, int, int], None]] = None,
    timeout_sec: int = 1800,
    poll_interval_sec: int = 2,
    slow_poll_interval_sec: int = 5,
    slow_after_sec: int = 180,
) -> str:
    headers = {"x-freepik-api-key": freepik_api_key}
    start = time.time()
    poll_count = 0
    clip_share_percent = 90 / max(1, total_clips)
    clip_start_percent = clip_index * clip_share_percent

    while True:
        elapsed_sec = int(time.time() - start)
        if elapsed_sec > timeout_sec:
            raise RuntimeError("Kling task timeout.")

        poll_count += 1
        try:
            with video_semaphore:
                response = requests.get(f"{kling_endpoint}/{task_id}", headers=headers, timeout=60)

            if not response.ok:
                if response.status_code >= 500:
                    print(f"[Server Warning] {response.status_code}. Retrying...", flush=True)
                    time.sleep(slow_poll_interval_sec)
                    continue
                raise RuntimeError(f"Kling status failed ({response.status_code}): {response.text[:300]}")

            status_payload = response.json()
        except requests.exceptions.RequestException as exc:
            print(f"[Network Warning] Polling failed temporarily: {exc}. Retrying...", flush=True)
            time.sleep(slow_poll_interval_sec)
            continue

        data = status_payload.get("data", {})
        status = "UNKNOWN"
        if isinstance(data, dict):
            status = data.get("status", "").upper()
        elif isinstance(status_payload, dict):
            status = status_payload.get("status", "").upper()

        simulated_progress = clip_share_percent * 0.95 * (1 - math.exp(-0.05 * poll_count))
        current_total_progress = int(clip_start_percent + simulated_progress)
        status_message = _provider_message(
            status,
            clip_index=clip_index,
            total_clips=total_clips,
            elapsed_sec=elapsed_sec,
        )

        if poll_count <= 3 or poll_count % 5 == 0:
            print(
                f"[Poll #{poll_count}] Clip {clip_index + 1}/{total_clips} Status: {status} "
                f"(Progress: {current_total_progress}%)",
                flush=True,
            )

        if update_job_status:
            update_job_status(current_total_progress, status_message)

        if status_callback:
            status_callback(status, current_total_progress, elapsed_sec)

        if status in ("COMPLETED", "SUCCEEDED", "SUCCESS", "DONE"):
            print(f"[COMPLETED] Clip {clip_index + 1}/{total_clips}. Fetching URL...", flush=True)

            generated = []
            if isinstance(data, dict):
                generated = data.get("generated", [])
            elif isinstance(status_payload, dict):
                generated = status_payload.get("generated", [])

            retry_count = 0
            while not generated and retry_count < 5:
                print(f"[WAIT] Generated array empty, retrying... ({retry_count + 1}/5)", flush=True)
                time.sleep(2)
                retry_count += 1

                with video_semaphore:
                    response = requests.get(f"{kling_endpoint}/{task_id}", headers=headers, timeout=60)
                if response.ok:
                    status_payload = response.json()
                    data = status_payload.get("data", {})
                    if isinstance(data, dict):
                        generated = data.get("generated", [])
                    else:
                        generated = status_payload.get("generated", [])

            url = None
            if generated and len(generated) > 0:
                first = generated[0]
                if isinstance(first, dict):
                    url = first.get("url") or first.get("video")
                elif isinstance(first, str):
                    url = first

            if not url and isinstance(data, dict):
                url = data.get("video_url") or data.get("url") or data.get("video")

            if not url:
                url = status_payload.get("result_url") or status_payload.get("video_url")

            if url:
                print(f"[SUCCESS] Found URL: {url[:60]}...", flush=True)
                return url

            print("[ERROR] Completed but no URL. Response dump:", flush=True)
            print(json.dumps(status_payload, indent=2), flush=True)
            raise RuntimeError("Kling completed but no result URL found.")

        if status in ("FAILED", "ERROR", "CANCELLED"):
            error_msg = "Unknown error"
            if isinstance(data, dict):
                error_msg = data.get("error") or data.get("message") or error_msg
            elif isinstance(data, str):
                error_msg = data
            elif isinstance(status_payload, dict):
                error_msg = status_payload.get("error") or status_payload.get("message") or error_msg
            raise RuntimeError(f"Kling task failed: {error_msg}")

        time.sleep(slow_poll_interval_sec if elapsed_sec >= slow_after_sec else poll_interval_sec)
