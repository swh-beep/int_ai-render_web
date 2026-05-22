import threading
from typing import Any, Dict, Optional

from rq import get_current_job


video_jobs: Dict[str, Dict[str, Any]] = {}
video_jobs_lock = threading.Lock()


def set_video_job(job_id: str, state: Dict[str, Any]) -> None:
    with video_jobs_lock:
        video_jobs[job_id] = dict(state)
    _sync_current_rq_job(job_id, state, replace=True)


def update_video_job(job_id: str, **fields: Any) -> None:
    with video_jobs_lock:
        if job_id not in video_jobs:
            return
        video_jobs[job_id].update(fields)
        state = dict(video_jobs[job_id])
    _sync_current_rq_job(job_id, state, replace=True)


def get_video_job(job_id: str) -> Optional[Dict[str, Any]]:
    with video_jobs_lock:
        return video_jobs.get(job_id)


def _sync_current_rq_job(job_id: str, state: Dict[str, Any], *, replace: bool) -> None:
    job = get_current_job()
    if not job or str(job.id) != str(job_id):
        return
    current = {} if replace else dict((job.meta or {}).get("video_state") or {})
    current.update(state)
    current["job_id"] = job_id
    job.meta["video_state"] = current
    job.save_meta()
