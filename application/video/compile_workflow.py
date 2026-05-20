import threading
import traceback
import uuid
import subprocess
from pathlib import Path
from typing import Callable

from api_models import CompileRequest
from application.video.job_store import set_video_job, update_video_job
from application.video.video_support import download_to_path, run_ffmpeg, safe_filename_from_url


def _resolve_aspect_dimensions(aspect_ratio: str, video_quality: str = "1080p") -> tuple[int, int]:
    base_height = 720 if video_quality == "720p" else 1080
    ratio_map = {
        "16:9": (int(base_height * 16 / 9), base_height),
        "1:1": (base_height, base_height),
        "4:5": (base_height, int(base_height * 5 / 4)),
        "9:16": (base_height, int(base_height * 16 / 9)),
    }
    return ratio_map.get(aspect_ratio or "9:16", ratio_map["9:16"])


def _build_video_filter(
    *,
    trim_start: float,
    trim_end: float,
    speed: float,
    reverse: bool,
    flip_horizontal: bool,
    video_target_fps: int,
    aspect_ratio: str,
    aspect_mode: str,
    video_quality: str = "1080p",
) -> str:
    duration = trim_end - trim_start
    safe_speed = speed if speed > 0.1 else 1.0
    target_w, target_h = _resolve_aspect_dimensions(aspect_ratio, video_quality)
    filter_steps = [f"trim=start={trim_start}:duration={duration}"]
    if reverse:
        filter_steps.append("reverse")
    filter_steps.append(f"setpts=(PTS-STARTPTS)/{safe_speed}")
    if flip_horizontal:
        filter_steps.append("hflip")
    base_chain = ",".join(filter_steps)
    safe_mode = (aspect_mode or "crop").strip().lower()
    if safe_mode == "fill":
        return (
            f"[0:v]{base_chain},"
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={video_target_fps}[vout]"
        )
    return (
        f"[0:v]{base_chain},"
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},setsar=1,fps={video_target_fps}[vout]"
    )


def _atempo_steps(speed: float) -> list[str]:
    safe_speed = speed if speed > 0.1 else 1.0
    safe_speed = max(0.5, min(float(safe_speed), 2.0))
    if abs(safe_speed - 1.0) <= 0.001:
        return []
    return [f"atempo={safe_speed:g}"]


def _build_audio_filter(*, trim_start: float, trim_end: float, speed: float, reverse: bool) -> str:
    duration = trim_end - trim_start
    steps = [f"atrim=start={trim_start}:duration={duration}", "asetpts=PTS-STARTPTS"]
    if reverse:
        steps.append("areverse")
    steps.extend(_atempo_steps(speed))
    return f"[0:a]{','.join(steps)}[aout]"


def _clip_output_duration(clip) -> float:
    trim_start = max(0.0, float(clip.trim_start))
    trim_end = max(trim_start + 0.1, float(clip.trim_end))
    safe_speed = float(clip.speed) if float(clip.speed) > 0.1 else 1.0
    return max(0.1, (trim_end - trim_start) / safe_speed)


def _build_process_clip_command(
    *,
    local_src: Path,
    final_path: Path,
    clip,
    video_filter: str,
    audio_filter: str | None,
    has_audio: bool,
    preserve_audio: bool,
) -> list[str]:
    if preserve_audio and has_audio and audio_filter:
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(local_src),
            "-filter_complex",
            f"{video_filter};{audio_filter}",
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryslow",
            "-crf",
            "10",
            str(final_path),
        ]

    if preserve_audio:
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(local_src),
            "-f",
            "lavfi",
            "-t",
            f"{_clip_output_duration(clip):g}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex",
            video_filter,
            "-map",
            "[vout]",
            "-map",
            "1:a",
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryslow",
            "-crf",
            "10",
            str(final_path),
        ]

    return [
        "ffmpeg",
        "-y",
        "-i",
        str(local_src),
        "-filter_complex",
        video_filter,
        "-map",
        "[vout]",
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryslow",
        "-crf",
        "10",
        str(final_path),
    ]


def _has_audio_stream(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return "audio" in result.stdout


def _publish_local_output_url(
    local_url: str,
    resolve_output_url: Callable[[str], str | None] | None,
) -> str:
    if not resolve_output_url:
        return local_url
    return resolve_output_url(local_url) or local_url


def run_final_compile_job(
    job_id: str,
    req: CompileRequest,
    *,
    video_target_fps: int,
    resolve_output_url: Callable[[str], str | None] | None = None,
) -> None:
    try:
        set_video_job(job_id, {"status": "RUNNING", "message": "Compiling...", "progress": 0})

        out_dir = Path("outputs")
        processed_paths = []
        total_clips = len(req.clips)

        for i, clip in enumerate(req.clips):
            if not clip.video_url:
                continue

            src_name = safe_filename_from_url(clip.video_url)
            local_src = out_dir / src_name
            if not local_src.exists():
                download_to_path(clip.video_url, local_src)

            final_path = out_dir / f"proc_{job_id}_{i}.mp4"

            trim_start = max(0.0, clip.trim_start)
            trim_end = max(trim_start + 0.1, clip.trim_end)
            if trim_end <= trim_start:
                trim_end = trim_start + 5.0

            vf = _build_video_filter(
                trim_start=trim_start,
                trim_end=trim_end,
                speed=clip.speed,
                reverse=clip.reverse,
                flip_horizontal=clip.flip_horizontal,
                video_target_fps=video_target_fps,
                aspect_ratio=req.aspect_ratio,
                aspect_mode=req.aspect_mode,
                video_quality=req.video_quality,
            )

            has_audio = _has_audio_stream(local_src) if req.preserve_audio else False
            af = (
                _build_audio_filter(
                    trim_start=trim_start,
                    trim_end=trim_end,
                    speed=clip.speed,
                    reverse=clip.reverse,
                )
                if req.preserve_audio and has_audio
                else None
            )
            cmd = _build_process_clip_command(
                local_src=local_src,
                final_path=final_path,
                clip=clip,
                video_filter=vf,
                audio_filter=af,
                has_audio=has_audio,
                preserve_audio=req.preserve_audio,
            )
            run_ffmpeg(cmd)
            processed_paths.append(final_path)

            update_video_job(job_id, progress=int(((i + 1) / total_clips) * 80))

        if not processed_paths:
            raise RuntimeError("No clips to merge")

        list_file = out_dir / f"list_{job_id}.txt"
        with open(list_file, "w", encoding="utf-8") as file_obj:
            for path in processed_paths:
                file_obj.write(f"file '{path.resolve().as_posix()}'\n")

        final_out = out_dir / f"final_{job_id}.mp4"
        run_ffmpeg(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(final_out)]
        )

        local_url = f"/outputs/{final_out.name}"
        result_url = _publish_local_output_url(local_url, resolve_output_url)

        update_video_job(
            job_id,
            status="COMPLETED",
            result_url=result_url,
            progress=100,
        )
    except Exception as exc:
        print(f"Compile Error: {exc}", flush=True)
        traceback.print_exc()
        update_video_job(job_id, status="FAILED", error=str(exc))


def queue_final_compile_job(req: CompileRequest, *, video_target_fps: int) -> str:
    job_id = uuid.uuid4().hex
    set_video_job(job_id, {"status": "QUEUED", "progress": 0})
    threading.Thread(
        target=run_final_compile_job,
        args=(job_id, req),
        kwargs={"video_target_fps": video_target_fps},
    ).start()
    return job_id
