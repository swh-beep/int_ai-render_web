import threading
import traceback
import uuid
from pathlib import Path

from api_models import CompileRequest
from application.video.job_store import set_video_job, update_video_job
from application.video.video_support import download_to_path, run_ffmpeg, safe_filename_from_url


def run_final_compile_job(job_id: str, req: CompileRequest, *, video_target_fps: int) -> None:
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
            trim_end = min(5.0, clip.trim_end)
            if trim_end <= trim_start:
                trim_end = 5.0

            duration = trim_end - trim_start
            speed = clip.speed if clip.speed > 0.1 else 1.0
            setpts = f"(PTS-STARTPTS)/{speed}"
            vf = (
                f"trim=start={trim_start}:duration={duration},setpts={setpts},"
                f"scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,setsar=1,fps={video_target_fps}"
            )

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(local_src),
                "-vf",
                vf,
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

        update_video_job(
            job_id,
            status="COMPLETED",
            result_url=f"/outputs/{final_out.name}",
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

