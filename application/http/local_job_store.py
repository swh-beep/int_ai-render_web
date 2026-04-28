from __future__ import annotations

import copy
import json
import threading
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional


LOCAL_JOB_STORE_PATH = Path(".local_job_state") / "render_jobs.json"

_local_jobs: Dict[str, Dict[str, Any]] = {}
_local_jobs_lock = threading.Lock()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _save_local_jobs_locked() -> None:
    LOCAL_JOB_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = LOCAL_JOB_STORE_PATH.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(_local_jobs, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    temp_path.replace(LOCAL_JOB_STORE_PATH)


def _load_local_jobs_locked() -> None:
    _local_jobs.clear()
    if not LOCAL_JOB_STORE_PATH.exists():
        return
    try:
        raw = json.loads(LOCAL_JOB_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    for job_id, state in raw.items():
        if isinstance(job_id, str) and isinstance(state, dict):
            state.setdefault("job_id", job_id)
            _local_jobs[job_id] = state


with _local_jobs_lock:
    _load_local_jobs_locked()


def clear_local_jobs() -> None:
    with _local_jobs_lock:
        _local_jobs.clear()
        _save_local_jobs_locked()


def set_local_job(job_id: str, state: Dict[str, Any]) -> None:
    with _local_jobs_lock:
        next_state = copy.deepcopy(state)
        next_state["job_id"] = job_id
        _local_jobs[job_id] = next_state
        _save_local_jobs_locked()


def update_local_job(job_id: str, **fields: Any) -> None:
    with _local_jobs_lock:
        state = _local_jobs.get(job_id)
        if state is None:
            return
        state.update(copy.deepcopy(fields))
        _save_local_jobs_locked()


def get_local_job_state(job_id: str) -> Optional[Dict[str, Any]]:
    with _local_jobs_lock:
        state = _local_jobs.get(job_id)
        return copy.deepcopy(state) if state is not None else None


@dataclass
class LocalInlineJob:
    id: str
    status: str
    enqueued_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    result: Any
    exc_info: str | None

    @property
    def is_finished(self) -> bool:
        return self.status == "finished"

    @property
    def is_failed(self) -> bool:
        return self.status == "failed"

    def get_status(self) -> str:
        return self.status


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def get_local_job(job_id: str) -> Optional[LocalInlineJob]:
    state = get_local_job_state(job_id)
    if not state:
        return None
    return LocalInlineJob(
        id=job_id,
        status=str(state.get("status") or "queued"),
        enqueued_at=_parse_dt(state.get("enqueued_at")),
        started_at=_parse_dt(state.get("started_at")),
        ended_at=_parse_dt(state.get("ended_at")),
        result=copy.deepcopy(state.get("result")),
        exc_info=state.get("exc_info"),
    )


def enqueue_local_job(job_func: Callable[..., Any], *args: Any, **kwargs: Any) -> LocalInlineJob:
    job_id = uuid.uuid4().hex
    enqueued_at = _utcnow_iso()
    set_local_job(
        job_id,
        {
            "status": "queued",
            "enqueued_at": enqueued_at,
            "started_at": None,
            "ended_at": None,
            "result": None,
            "exc_info": None,
        },
    )

    def _runner() -> None:
        update_local_job(job_id, status="started", started_at=_utcnow_iso(), exc_info=None)
        try:
            result = job_func(*args, **kwargs)
            update_local_job(
                job_id,
                status="finished",
                ended_at=_utcnow_iso(),
                result=result,
                exc_info=None,
            )
        except Exception:
            update_local_job(
                job_id,
                status="failed",
                ended_at=_utcnow_iso(),
                result=None,
                exc_info=traceback.format_exc(),
            )

    thread = threading.Thread(target=_runner, name=f"local-inline-job-{job_id[:8]}", daemon=True)
    thread.start()
    job = get_local_job(job_id)
    if job is None:
        raise RuntimeError("Failed to initialize local inline job")
    return job
