from __future__ import annotations

import copy
import json
import threading
from typing import Any


STAGING_JOB_KEY_PREFIX = "staging:render-job:"

_memory_jobs: dict[str, dict[str, Any]] = {}
_memory_lock = threading.Lock()


def _redis_key(job_id: str) -> str:
    return f"{STAGING_JOB_KEY_PREFIX}{job_id}"


def _json_default(value: Any) -> Any:
    return str(value)


def _safe_redis_get(redis_conn: Any, job_id: str) -> dict[str, Any] | None:
    if redis_conn is None:
        return None
    try:
        raw = redis_conn.get(_redis_key(job_id))
    except Exception:
        return None
    if not raw:
        return None
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _safe_redis_set(redis_conn: Any, job_id: str, state: dict[str, Any], ttl_sec: int) -> bool:
    if redis_conn is None:
        return False
    try:
        raw = json.dumps(state, ensure_ascii=False, default=_json_default)
        redis_conn.set(_redis_key(job_id), raw, ex=max(60, int(ttl_sec)))
        return True
    except Exception:
        return False


def set_staging_job_state(
    job_id: str,
    state: dict[str, Any],
    *,
    redis_conn: Any = None,
    ttl_sec: int = 86400,
) -> None:
    next_state = copy.deepcopy(state)
    next_state["job_id"] = job_id
    if _safe_redis_set(redis_conn, job_id, next_state, ttl_sec):
        return
    with _memory_lock:
        _memory_jobs[job_id] = next_state


def update_staging_job_state(
    job_id: str,
    fields: dict[str, Any],
    *,
    redis_conn: Any = None,
    ttl_sec: int = 86400,
) -> None:
    current = get_staging_job_state(job_id, redis_conn=redis_conn) or {"job_id": job_id}
    current.update(copy.deepcopy(fields))
    set_staging_job_state(job_id, current, redis_conn=redis_conn, ttl_sec=ttl_sec)


def get_staging_job_state(job_id: str, *, redis_conn: Any = None) -> dict[str, Any] | None:
    state = _safe_redis_get(redis_conn, job_id)
    if state is not None:
        state.setdefault("job_id", job_id)
        return state
    with _memory_lock:
        state = _memory_jobs.get(job_id)
        return copy.deepcopy(state) if state is not None else None
