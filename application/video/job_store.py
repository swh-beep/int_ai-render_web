import threading
from typing import Any, Dict, Optional


video_jobs: Dict[str, Dict[str, Any]] = {}
video_jobs_lock = threading.Lock()


def set_video_job(job_id: str, state: Dict[str, Any]) -> None:
    with video_jobs_lock:
        video_jobs[job_id] = state


def update_video_job(job_id: str, **fields: Any) -> None:
    with video_jobs_lock:
        if job_id not in video_jobs:
            return
        video_jobs[job_id].update(fields)


def get_video_job(job_id: str) -> Optional[Dict[str, Any]]:
    with video_jobs_lock:
        return video_jobs.get(job_id)

