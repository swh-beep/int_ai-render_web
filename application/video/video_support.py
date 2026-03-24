import base64
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests


def safe_filename_from_url(url: str) -> str:
    try:
        path = urlparse(url).path
        name = os.path.basename(path)
        return name or f"clip_{uuid.uuid4().hex}.png"
    except Exception:
        return f"clip_{uuid.uuid4().hex}.png"


def download_to_path(url: str, out_path: Path) -> None:
    """
    URL이 http로 시작하면 다운로드하고,
    / 로 시작하면 로컬 파일을 복사합니다.
    """
    if url.startswith("/"):
        local_path = url.lstrip("/")
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found on server: {local_path}")
        with open(local_path, "rb") as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    with open(out_path, "wb") as file_obj:
        file_obj.write(response.content)


def run_ffmpeg(cmd: List[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffmpeg failed")


def ffmpeg_trim_speed(
    in_path: Path,
    out_path: Path,
    start_sec: float,
    dur_sec: float,
    speed: float,
    fps: int,
) -> None:
    setpts_expr = f"(PTS-STARTPTS)/{speed}" if speed and abs(speed - 1.0) > 1e-6 else "(PTS-STARTPTS)"
    vf = f"trim=start={start_sec}:duration={dur_sec},setpts={setpts_expr},fps={fps}"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(in_path),
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "10",
        "-preset",
        "veryslow",
        str(out_path),
    ]
    run_ffmpeg(cmd)


def ffprobe_wh(path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    data = json.loads(proc.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    return int(stream.get("width") or 0), int(stream.get("height") or 0)


def ffmpeg_normalize_to(in_path: Path, out_path: Path, target_w: int, target_h: int, fps: int) -> None:
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},"
        f"setsar=1,"
        f"fps={fps}"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(in_path),
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "10",
        "-preset",
        "veryslow",
        str(out_path),
    ]
    run_ffmpeg(cmd)


def clip_url_to_image_bytes(url: str) -> bytes:
    if url.startswith("data:image/"):
        try:
            _, encoded = url.split(",", 1)
            return base64.b64decode(encoded)
        except Exception:
            return base64.b64decode(url)
    if url.startswith("/"):
        local_path = url.lstrip("/")
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Image not found on server: {local_path}")
        return Path(local_path).read_bytes()
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.content


def find_static_image(prefix: str) -> Optional[Path]:
    static_dir = Path("static")
    if not static_dir.exists():
        return None
    exts = ["png", "jpg", "jpeg", "webp"]
    candidates = []
    for ext in exts:
        candidates.extend(static_dir.glob(f"{prefix}*.{ext}"))
        candidates.extend(static_dir.glob(f"{prefix.upper()}*.{ext}"))
        candidates.extend(static_dir.glob(f"{prefix.capitalize()}*.{ext}"))
    candidates = sorted(set(candidates))
    return candidates[0] if candidates else None


def ffmpeg_image_to_video(
    image_path: Path,
    out_path: Path,
    dur_sec: float,
    target_w: int,
    target_h: int,
    fps: int,
) -> None:
    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},setsar=1,fps={fps}"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-t",
        str(dur_sec),
        "-vf",
        vf,
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "10",
        "-preset",
        "veryslow",
        str(out_path),
    ]
    run_ffmpeg(cmd)


def kling_prompts_dynamic(motion: str, effect: str) -> Dict[str, str]:
    base_keep = (
        "High quality interior video, photorealistic, 8k. "
        "Keep ALL furniture and layout exactly the same as the input image. "
        "No warping, no distortion. "
    )

    motion_map = {
        "static": "Static camera shot, extremely subtle movement.",
        "orbit_r_slow": "Slow orbit rotation to the right, keeping the subject centered, smooth movement.",
        "orbit_l_slow": "Slow orbit rotation to the left, keeping the subject centered, smooth movement.",
        "orbit_r_fast": "Fast orbit rotation to the right, dynamic camera movement.",
        "orbit_l_fast": "Fast orbit rotation to the left, dynamic camera movement.",
        "zoom_in_slow": "Slow camera dolly-in at eye-level. Move straight forward without shaking or walking bob. Smooth cinematic push.",
        "zoom_out_slow": "Slow camera dolly-out at eye-level. Move straight backward without shaking or walking bob. Smooth cinematic pull.",
        "zoom_in_fast": "Fast camera dolly-in at eye-level. Rapid straight movement towards the subject.",
        "zoom_out_fast": "Fast camera dolly-out at eye-level. Rapid straight movement away from the subject.",
    }

    effect_map = {
        "none": "Natural lighting, static environment.",
        "sunlight": "Sunlight beams moving across the room, time-lapse shadow movement on the floor and furniture.",
        "lights_on": "Lighting transition: starts with lights off or dim, then lights turn on brightly. Cinematic illumination reveal.",
        "blinds": "Curtains or blinds moving gently in the wind near the window.",
        "plants": "Indoor plants and foliage swaying gently in a soft breeze.",
        "door_open": "A door, cabinet door, or glass door in the scene slowly opens.",
    }

    prompt_motion = motion_map.get(motion, motion_map["static"])
    prompt_effect = effect_map.get(effect, effect_map["none"])
    final_prompt = f"{base_keep} {prompt_motion} {prompt_effect}"
    negative_prompt = (
        "human, person, walking, shaking camera, shaky footage, "
        "changing furniture, melting objects, distorted geometry, "
        "text, watermark, logo, frame borders, low quality, cartoon"
    )
    return {"prompt": final_prompt, "negative_prompt": negative_prompt}


def image_url_to_b64(url: str) -> str:
    if url.startswith("/"):
        local_path = url.lstrip("/")
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found for b64 conversion: {local_path}")
        with open(local_path, "rb") as file_obj:
            return base64.b64encode(file_obj.read()).decode("utf-8")

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return base64.b64encode(response.content).decode("utf-8")

