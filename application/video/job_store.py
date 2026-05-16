import copy
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from rq import get_current_job


VIDEO_JOB_STORE_PATH = Path(".video_state") / "video_jobs.json"

video_jobs: Dict[str, Dict[str, Any]] = {}
video_jobs_lock = threading.Lock()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _save_video_jobs_locked() -> None:
    VIDEO_JOB_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = VIDEO_JOB_STORE_PATH.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(video_jobs, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    temp_path.replace(VIDEO_JOB_STORE_PATH)


def _local_output_path(output_url: str | None) -> Path | None:
    if not output_url or not isinstance(output_url, str) or not output_url.startswith("/outputs/"):
        return None
    return Path(output_url.lstrip("/"))


def _result_exists(output_url: str | None) -> bool:
    if output_url and isinstance(output_url, str) and output_url.startswith(("http://", "https://")):
        return True
    local_path = _local_output_path(output_url)
    return bool(local_path and local_path.exists())


def _state_rank(state: Dict[str, Any]) -> tuple[int, float]:
    status = str(state.get("status") or "").upper()
    items = state.get("items") or []
    results = state.get("results") or []
    has_complete_results = bool(results) and len(results) == len(items) and all(
        bool(result) and _result_exists(result) for result in results
    )
    has_resumable_provider_state = any(
        isinstance(item, dict) and (item.get("task_id") or item.get("provider_result_url") or _result_exists(item.get("output_url")))
        for item in items
    )

    if has_complete_results:
        return (4, float(state.get("updated_at") or state.get("created_at") or 0))
    if status in {"RUNNING", "QUEUED"} and has_resumable_provider_state:
        return (3, float(state.get("updated_at") or state.get("created_at") or 0))
    if status in {"RUNNING", "QUEUED"}:
        return (2, float(state.get("updated_at") or state.get("created_at") or 0))
    if has_resumable_provider_state:
        return (1, float(state.get("updated_at") or state.get("created_at") or 0))
    return (0, float(state.get("updated_at") or state.get("created_at") or 0))


def _load_video_jobs_locked() -> None:
    video_jobs.clear()
    if not VIDEO_JOB_STORE_PATH.exists():
        return
    try:
        raw = json.loads(VIDEO_JOB_STORE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for job_id, state in raw.items():
                if isinstance(job_id, str) and isinstance(state, dict):
                    state.setdefault("job_id", job_id)
                    video_jobs[job_id] = state
    except Exception:
        # Corrupt cache should not block the server from starting.
        return


with video_jobs_lock:
    _load_video_jobs_locked()


def set_video_job(job_id: str, state: Dict[str, Any]) -> None:
    now = time.time()
    with video_jobs_lock:
        existing = video_jobs.get(job_id, {})
        next_state = copy.deepcopy(existing)
        next_state.update(copy.deepcopy(state))
        next_state["job_id"] = job_id
        next_state.setdefault("created_at", existing.get("created_at", now))
        next_state["updated_at"] = now
        video_jobs[job_id] = next_state
        _save_video_jobs_locked()
        sync_state = copy.deepcopy(next_state)
    _sync_current_rq_job(job_id, sync_state, replace=True)


def update_video_job(job_id: str, **fields: Any) -> None:
    now = time.time()
    with video_jobs_lock:
        if job_id not in video_jobs:
            return
        video_jobs[job_id].update(copy.deepcopy(fields))
        video_jobs[job_id]["updated_at"] = now
        _save_video_jobs_locked()
        sync_state = copy.deepcopy(video_jobs[job_id])
    _sync_current_rq_job(job_id, sync_state, replace=True)


def update_video_job_item(job_id: str, index: int, **fields: Any) -> None:
    now = time.time()
    with video_jobs_lock:
        job = video_jobs.get(job_id)
        if not job:
            return
        items = job.setdefault("items", [])
        while len(items) <= index:
            items.append({})
        item_state = items[index]
        if not isinstance(item_state, dict):
            item_state = {}
            items[index] = item_state
        item_state.update(copy.deepcopy(fields))
        job["updated_at"] = now
        _save_video_jobs_locked()
        sync_state = copy.deepcopy(job)
    _sync_current_rq_job(job_id, sync_state, replace=True)


def get_video_job(job_id: str) -> Optional[Dict[str, Any]]:
    with video_jobs_lock:
        state = video_jobs.get(job_id)
        return copy.deepcopy(state) if state is not None else None

def list_video_jobs_by_request_key(
    request_key: str,
    *,
    job_type: Optional[str] = None,
    statuses: Optional[set[str]] = None,
) -> list[Dict[str, Any]]:
    with video_jobs_lock:
        matches: list[Dict[str, Any]] = []
        for state in video_jobs.values():
            if state.get("request_key") != request_key:
                continue
            if job_type and state.get("job_type") != job_type:
                continue
            if statuses and state.get("status") not in statuses:
                continue
            matches.append(state)
        return copy.deepcopy(matches)


def find_video_job_by_request_key(
    request_key: str,
    *,
    job_type: Optional[str] = None,
    statuses: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    matches = list_video_jobs_by_request_key(request_key, job_type=job_type, statuses=statuses)
    if not matches:
        return None
    best = max(matches, key=_state_rank)
    return copy.deepcopy(best)


def create_video_job_if_absent(
    job_id: str,
    state: Dict[str, Any],
    *,
    request_key: str,
    job_type: Optional[str] = None,
) -> tuple[Dict[str, Any], bool]:
    now = time.time()
    with video_jobs_lock:
        matches = []
        for existing in video_jobs.values():
            if existing.get("request_key") != request_key:
                continue
            if job_type and existing.get("job_type") != job_type:
                continue
            matches.append(existing)
        if matches:
            best = max(matches, key=_state_rank)
            return copy.deepcopy(best), False

        next_state = copy.deepcopy(state)
        next_state["job_id"] = job_id
        next_state.setdefault("created_at", now)
        next_state["updated_at"] = now
        video_jobs[job_id] = next_state
        _save_video_jobs_locked()
        return copy.deepcopy(next_state), True


def prune_video_jobs(limit: int) -> int:
    if limit <= 0:
        return 0
    with video_jobs_lock:
        if len(video_jobs) <= limit:
            return 0
        ordered = sorted(
            video_jobs.items(),
            key=lambda pair: float(pair[1].get("updated_at") or pair[1].get("created_at") or 0),
        )
        overflow = len(video_jobs) - limit
        removed = 0
        for job_id, _ in ordered[:overflow]:
            if video_jobs.pop(job_id, None) is not None:
                removed += 1
        if removed:
            _save_video_jobs_locked()
        return removed


def _sync_current_rq_job(job_id: str, state: Dict[str, Any], *, replace: bool) -> None:
    job = get_current_job()
    if not job or str(job.id) != str(job_id):
        return
    current = {} if replace else dict((job.meta or {}).get("video_state") or {})
    current.update(state)
    current["job_id"] = job_id
    job.meta["video_state"] = current
    job.save_meta()
