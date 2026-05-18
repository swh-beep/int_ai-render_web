import base64
import hashlib
import hmac
import json
import math
import time
from threading import Semaphore
from typing import Callable, Optional

import requests


DEFAULT_KLING_BASE_URL = "https://api-singapore.klingai.com"
DEFAULT_KLING_MODEL_NAME = "kling-v3"
DEFAULT_KLING_MODE = "std"


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def build_kling_endpoint(base_url: str = DEFAULT_KLING_BASE_URL) -> str:
    safe_base = (base_url or DEFAULT_KLING_BASE_URL).strip().rstrip("/")
    return f"{safe_base}/v1/videos/image2video"


def encode_kling_jwt(access_key: str, secret_key: str, *, now: Optional[int] = None, ttl_sec: int = 1800) -> str:
    if not access_key:
        raise RuntimeError("KLING_ACCESS_API_KEY is not set.")
    if not secret_key:
        raise RuntimeError("KLING_SECRET_API_KEY is not set.")

    issued_at = int(time.time() if now is None else now)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": access_key,
        "exp": issued_at + ttl_sec,
        "nbf": issued_at - 5,
    }
    encoded_header = _base64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _base64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_base64url(signature)}"


def quality_to_mode(quality: str | None) -> str | None:
    normalized = (quality or "").strip().lower()
    if normalized == "720p":
        return "std"
    if normalized == "1080p":
        return "pro"
    return None


def create_kling_task(
    image_url: str,
    prompt: str,
    negative_prompt: str,
    duration: str,
    cfg_scale: float,
    *,
    access_key: str,
    secret_key: str,
    kling_endpoint: str,
    model_name: str,
    mode: str,
    video_semaphore: Semaphore,
    end_image_url: str | None = None,
    aspect_ratio: str = "9:16",
    quality: str | None = None,
    sound: str = "off",
) -> str:
    if not image_url:
        raise RuntimeError("Kling image URL is empty.")

    normalized_model = (model_name or DEFAULT_KLING_MODEL_NAME).strip().lower()
    normalized_sound = (sound or "off").strip().lower() or "off"
    normalized_mode = (quality_to_mode(quality) or mode or DEFAULT_KLING_MODE).strip().lower() or DEFAULT_KLING_MODE
    if normalized_sound == "on" and normalized_model == "kling-v2-6":
        if normalized_mode != "pro":
            raise ValueError("Kling v2.6 audio requires pro mode.")
        if end_image_url:
            raise ValueError("Kling v2.6 native audio is not available with end frame.")

    payload = {
        "model_name": (model_name or DEFAULT_KLING_MODEL_NAME).strip() or DEFAULT_KLING_MODEL_NAME,
        "image": image_url,
        "prompt": prompt or "",
        "duration": str(duration or "5"),
        "mode": normalized_mode,
        "aspect_ratio": (aspect_ratio or "9:16").strip() or "9:16",
        "sound": normalized_sound,
    }
    if end_image_url:
        payload["image_tail"] = end_image_url
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if cfg_scale is not None:
        payload["cfg_scale"] = cfg_scale

    token = encode_kling_jwt(access_key, secret_key)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    with video_semaphore:
        response = requests.post(kling_endpoint, headers=headers, json=payload, timeout=180)

    if response.status_code == 429:
        body = response.text[:500]
        raise RuntimeError(
            "Kling create rejected with 429 before task creation. "
            "Check account quota/concurrency and provider request_id if present. "
            f"Response: {body}"
        )
    if not response.ok:
        raise RuntimeError(f"Kling create failed ({response.status_code}): {response.text[:500]}")

    data = response.json()
    task_id = (
        data.get("task_id")
        or data.get("id")
        or data.get("data", {}).get("task_id")
        or data.get("data", {}).get("id")
        or data.get("result", {}).get("task_id")
        or data.get("taskId")
    )
    if not task_id:
        raise RuntimeError(f"No task_id returned from Kling create. Response: {json.dumps(data)[:300]}")

    return task_id


def _provider_message(status: str, *, clip_index: int, total_clips: int, elapsed_sec: int) -> str:
    normalized = (status or "").upper()
    phase_map = {
        "SUBMITTED": "Queued at provider",
        "CREATED": "Queued at provider",
        "PENDING": "Queued at provider",
        "QUEUED": "Queued at provider",
        "PROCESSING": "Rendering at provider",
        "IN_PROGRESS": "Rendering at provider",
        "SUCCEED": "Finalizing result",
        "COMPLETED": "Finalizing result",
        "SUCCEEDED": "Finalizing result",
        "SUCCESS": "Finalizing result",
        "DONE": "Finalizing result",
    }
    phase = phase_map.get(normalized, f"Provider status: {normalized or 'UNKNOWN'}")
    elapsed_label = f"{max(1, int(elapsed_sec // 60))}m" if elapsed_sec >= 60 else f"{elapsed_sec}s"
    return f"Clip {clip_index + 1}/{total_clips}: {phase} ({elapsed_label})"


def _extract_status(status_payload: dict) -> str:
    data = status_payload.get("data", {})
    if isinstance(data, dict):
        return (data.get("task_status") or data.get("status") or "").upper()
    return (status_payload.get("task_status") or status_payload.get("status") or "UNKNOWN").upper()


def _extract_video_url(status_payload: dict) -> Optional[str]:
    data = status_payload.get("data", {})
    if isinstance(data, dict):
        task_result = data.get("task_result") or {}
        videos = task_result.get("videos") if isinstance(task_result, dict) else None
        if videos:
            first = videos[0]
            if isinstance(first, dict):
                return first.get("url") or first.get("video") or first.get("watermark_url")
            if isinstance(first, str):
                return first

        generated = data.get("generated", [])
        if generated:
            first = generated[0]
            if isinstance(first, dict):
                return first.get("url") or first.get("video")
            if isinstance(first, str):
                return first

        return data.get("video_url") or data.get("url") or data.get("video")

    return status_payload.get("result_url") or status_payload.get("video_url")


def _extract_error_message(status_payload: dict) -> str:
    data = status_payload.get("data", {})
    if isinstance(data, dict):
        return (
            data.get("task_status_msg")
            or data.get("error")
            or data.get("message")
            or status_payload.get("message")
            or "Unknown error"
        )
    if isinstance(data, str):
        return data
    return status_payload.get("error") or status_payload.get("message") or "Unknown error"


def poll_kling_task(
    task_id: str,
    *,
    clip_index: int,
    total_clips: int,
    access_key: str,
    secret_key: str,
    kling_endpoint: str,
    video_semaphore: Semaphore,
    update_job_status: Optional[Callable[[int, str], None]] = None,
    status_callback: Optional[Callable[[str, int, int], None]] = None,
    timeout_sec: int = 1800,
    poll_interval_sec: int = 2,
    slow_poll_interval_sec: int = 5,
    slow_after_sec: int = 180,
) -> str:
    start = time.time()
    poll_count = 0
    clip_share_percent = 90 / max(1, total_clips)
    clip_start_percent = clip_index * clip_share_percent
    status_url = f"{kling_endpoint.rstrip('/')}/{task_id}"

    while True:
        elapsed_sec = int(time.time() - start)
        if elapsed_sec > timeout_sec:
            raise RuntimeError("Kling task timeout.")

        poll_count += 1
        try:
            token = encode_kling_jwt(access_key, secret_key)
            headers = {"Authorization": f"Bearer {token}"}
            with video_semaphore:
                response = requests.get(status_url, headers=headers, timeout=60)

            if not response.ok:
                if response.status_code >= 500:
                    print(f"[Kling Warning] {response.status_code}. Retrying...", flush=True)
                    time.sleep(slow_poll_interval_sec)
                    continue
                raise RuntimeError(f"Kling status failed ({response.status_code}): {response.text[:300]}")

            status_payload = response.json()
        except requests.exceptions.RequestException as exc:
            print(f"[Network Warning] Kling polling failed temporarily: {exc}. Retrying...", flush=True)
            time.sleep(slow_poll_interval_sec)
            continue

        status = _extract_status(status_payload)
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
                f"[Kling Poll #{poll_count}] Clip {clip_index + 1}/{total_clips} Status: {status} "
                f"(Progress: {current_total_progress}%)",
                flush=True,
            )

        if update_job_status:
            update_job_status(current_total_progress, status_message)

        if status_callback:
            status_callback(status, current_total_progress, elapsed_sec)

        if status in ("SUCCEED", "COMPLETED", "SUCCEEDED", "SUCCESS", "DONE"):
            url = _extract_video_url(status_payload)
            if url:
                return url
            raise RuntimeError("Kling completed but no result URL found.")

        if status in ("FAILED", "ERROR", "CANCELLED"):
            raise RuntimeError(f"Kling task failed: {_extract_error_message(status_payload)}")

        time.sleep(slow_poll_interval_sec if elapsed_sec >= slow_after_sec else poll_interval_sec)
