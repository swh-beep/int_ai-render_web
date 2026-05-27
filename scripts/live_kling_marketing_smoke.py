#!/usr/bin/env python3
"""Run a small live Kling image-to-video smoke test for /marketing assets."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
IMAGE_DIR = ROOT / "kling-test-image"
sys.path.insert(0, str(ROOT))

SMOKE_CLIPS = [
    {
        "name": "rug-drop",
        "start": IMAGE_DIR / "3.webp",
        "end": IMAGE_DIR / "4.webp",
        "duration": "5",
        "prompt": (
            "A small patterned rug suddenly appears in mid-air and drops straight down vertically onto the floor. "
            "It falls perfectly flat without any rolling or folding. As it hits the floor, it settles with a soft, "
            "heavy fabric impact and zero bounce. No horizontal movement, no sliding from the side. "
            "Realistic textile physics, 4k, cinematic lighting."
        ),
    },
    {
        "name": "dog-cushion-subtle-motion",
        "start": IMAGE_DIR / "7.webp",
        "end": IMAGE_DIR / "8.webp",
        "duration": "5",
        "prompt": (
            "The dog naturally walks over to the cushion, steps up onto it with its paws, and smoothly settles "
            "into a seated position. Keep the motion gentle and realistic: the dog slightly shifts its weight, "
            "softly lifts its head, blinks once or twice, and gently wags its tail after sitting. Show subtle "
            "muscle movement and soft cushion compression. No jumping, no exaggerated movement, no distortion "
            "of the dog or furniture. 4k, cinematic lighting."
        ),
    },
    {
        "name": "living-room-camera-glide",
        "start": IMAGE_DIR / "17.webp",
        "end": None,
        "duration": "5",
        "prompt": (
            "A smooth, cinematic first-person perspective movement exploring the living room. The camera slowly "
            "glides forward and pans gently from side to side, as if a person is walking through the space and "
            "admiring the interior. Focus on the red sofa, the artwork on the wall, the dog resting naturally, "
            "and the warm textures of the furniture. The dog may make a tiny natural movement such as a slight "
            "head turn or soft tail wag, but the scene remains stable and realistic. High-quality 4k, realistic "
            "parallax effect, stable gimbal motion."
        ),
    },
]

CLIP_SETS = {
    "smoke": SMOKE_CLIPS,
    "dog": [SMOKE_CLIPS[1]],
    "rug": [SMOKE_CLIPS[0]],
    "camera": [SMOKE_CLIPS[2]],
}


def _content_type(path: Path) -> str:
    if path.suffix.lower() == ".webp":
        return "image/webp"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "image/png"


def _upload_image(client: TestClient, *, app_module: Any, group_id: str, path: Path, role: str) -> tuple[str, str]:
    if not path.exists():
        raise FileNotFoundError(path)
    response = client.post(
        "/api/outputs/presign-upload",
        json={
            "purpose": "marketing-kling",
            "group_id": group_id,
            "asset_type": f"images/{role}",
            "files": [
                {
                    "client_id": path.stem,
                    "filename": path.name,
                    "content_type": _content_type(path),
                    "size": path.stat().st_size,
                }
            ],
        },
    )
    response.raise_for_status()
    item = response.json()["items"][0]
    upload_response = requests.put(
        item["upload_url"],
        data=path.read_bytes(),
        headers={"Content-Type": item.get("content_type") or _content_type(path)},
        timeout=180,
    )
    upload_response.raise_for_status()
    signed_read_url = app_module._get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": app_module.S3_BUCKET, "Key": item["object_key"]},
        ExpiresIn=3600,
    )
    return item["public_url"], signed_read_url


def _poll_job(client: TestClient, job_id: str, *, timeout_sec: int, interval_sec: int) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last_status = ""
    while time.time() < deadline:
        response = client.get(f"/video-mvp/status/{job_id}")
        response.raise_for_status()
        state = response.json()
        status = state.get("status", "UNKNOWN")
        progress = state.get("progress", 0)
        message = state.get("message", "")
        if f"{status}:{progress}:{message}" != last_status:
            print(f"[poll] status={status} progress={progress} message={message}")
            last_status = f"{status}:{progress}:{message}"
        if status in {"COMPLETED", "FAILED"}:
            return state
        time.sleep(interval_sec)
    raise TimeoutError(f"Timed out waiting for Kling job {job_id}")


def _switch_to_aws_profile(profile: str) -> None:
    if not profile:
        return
    os.environ["AWS_PROFILE"] = profile
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        os.environ.pop(key, None)
    import boto3  # noqa: PLC0415

    boto3.DEFAULT_SESSION = None
    boto3.setup_default_session(
        profile_name=profile,
        region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "ap-northeast-2",
    )


def run_live_smoke(*, timeout_sec: int, interval_sec: int, kling_aws_profile: str, clip_set: str, concurrency: int) -> int:
    os.environ["VIDEO_MAX_CONCURRENCY"] = str(max(1, concurrency))

    import main  # noqa: PLC0415

    group_id = f"kling-smoke-{int(time.time())}"
    client = TestClient(main.app)

    print(f"[setup] group_id={group_id}")
    items: list[dict[str, Any]] = []
    clips = CLIP_SETS[clip_set]
    for clip in clips:
        start_public_url, start_url = _upload_image(client, app_module=main, group_id=group_id, path=clip["start"], role="start")
        end_public_url = None
        end_url = None
        if clip["end"]:
            end_public_url, end_url = _upload_image(client, app_module=main, group_id=group_id, path=clip["end"], role="end")
        item = {
            "url": start_url,
            "motion": "custom",
            "effect": "none",
            "custom_motion_prompt": clip["prompt"],
            "custom_effect_prompt": None,
            "duration": clip["duration"],
        }
        if end_url:
            item["end_url"] = end_url
        items.append(item)
        print(f"[upload] {clip['name']} start_public={start_public_url}")
        print(f"[upload] {clip['name']} start_signed={start_url.split('?', 1)[0]}?...")
        if end_url:
            print(f"[upload] {clip['name']} end_public={end_public_url}")
            print(f"[upload] {clip['name']} end_signed={end_url.split('?', 1)[0]}?...")

    _switch_to_aws_profile(kling_aws_profile)
    response = client.post("/video-mvp/generate-sources", json={"items": items, "cfg_scale": 0.5, "aspect_ratio": "9:16"})
    response.raise_for_status()
    job_id = response.json()["job_id"]
    print(f"[job] {job_id}")

    final_state = _poll_job(client, job_id, timeout_sec=timeout_sec, interval_sec=interval_sec)
    print("[final]", final_state)
    if final_state.get("status") != "COMPLETED":
        return 1
    results = final_state.get("results") or []
    if len([url for url in results if url]) != len(clips):
        print(f"[error] expected {len(clips)} result URLs, got {results}")
        return 1
    for index, result in enumerate(results, start=1):
        print(f"[result {index}] {result}")
    return 0


def main_cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--interval-sec", type=int, default=10)
    parser.add_argument("--kling-aws-profile", default="default")
    parser.add_argument("--clip-set", choices=sorted(CLIP_SETS), default="smoke")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    return run_live_smoke(
        timeout_sec=args.timeout_sec,
        interval_sec=args.interval_sec,
        kling_aws_profile=args.kling_aws_profile,
        clip_set=args.clip_set,
        concurrency=args.concurrency,
    )


if __name__ == "__main__":
    raise SystemExit(main_cli())
