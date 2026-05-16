from typing import Any, Callable


def _request_payload(req: Any) -> dict:
    if hasattr(req, "model_dump"):
        return req.model_dump()
    if hasattr(req, "dict"):
        return req.dict()
    return dict(req)


def _set_initial_video_meta(job: Any) -> None:
    job.meta["video_state"] = {"status": "QUEUED", "progress": 0}
    if hasattr(job, "save_meta"):
        job.save_meta()


def enqueue_source_generation_rq_job(
    req: Any,
    *,
    enqueue_job: Callable[..., tuple[Any, str | None]],
    queue_name: str | None,
    job_func: Callable[[dict], dict],
) -> tuple[str | None, str | None]:
    job, err = enqueue_job(job_func, _request_payload(req), queue_name=queue_name)
    if err:
        return None, err
    _set_initial_video_meta(job)
    return job.id, None


def enqueue_compile_rq_job(
    req: Any,
    *,
    enqueue_job: Callable[..., tuple[Any, str | None]],
    queue_name: str | None,
    job_func: Callable[[dict], dict],
) -> tuple[str | None, str | None]:
    job, err = enqueue_job(job_func, _request_payload(req), queue_name=queue_name)
    if err:
        return None, err
    _set_initial_video_meta(job)
    return job.id, None


def build_video_status_payload(
    job_id: str,
    *,
    fetch_job: Callable[[str], Any],
    load_memory_job: Callable[[str], dict | None],
) -> tuple[dict, int]:
    job = fetch_job(job_id)
    if not job:
        legacy_state = load_memory_job(job_id)
        if legacy_state is not None:
            return legacy_state, 200
        return {"status": "NOT_FOUND", "message": "Job not found"}, 404

    rq_status = _job_status(job)
    state = dict((getattr(job, "meta", {}) or {}).get("video_state") or {})

    if rq_status in {"queued", "deferred", "scheduled"}:
        state.setdefault("status", "QUEUED")
        state.setdefault("progress", 0)
    elif rq_status == "started":
        state.setdefault("status", "RUNNING")
    elif rq_status == "finished":
        if isinstance(getattr(job, "result", None), dict):
            state.update(job.result)
        state.setdefault("status", "COMPLETED")
        state.setdefault("progress", 100)
    elif rq_status == "failed":
        state.setdefault("status", "FAILED")
        state.setdefault("error", getattr(job, "exc_info", None) or "Video job failed")
    else:
        state.setdefault("status", str(rq_status or "UNKNOWN").upper())

    return state, 200


def publish_video_state_outputs(
    state: dict,
    *,
    resolve_output_url: Callable[[str], str | None],
) -> dict:
    published = dict(state)
    if isinstance(published.get("results"), list):
        published["results"] = [_publish_video_url(url, resolve_output_url) for url in published["results"]]
    if isinstance(published.get("items"), list):
        published["items"] = [_publish_video_item(item, resolve_output_url) for item in published["items"]]
    if published.get("result_url"):
        published["result_url"] = _publish_video_url(published["result_url"], resolve_output_url)
    return published


def _publish_video_item(item: Any, resolve_output_url: Callable[[str], str | None]) -> Any:
    if not isinstance(item, dict):
        return item
    published = dict(item)
    if published.get("output_url"):
        published["output_url"] = _publish_video_url(published["output_url"], resolve_output_url)
    return published


def _publish_video_url(url: Any, resolve_output_url: Callable[[str], str | None]) -> Any:
    if not isinstance(url, str) or not url.startswith("/outputs/"):
        return url
    return resolve_output_url(url) or url


def _job_status(job: Any) -> str:
    if hasattr(job, "get_status"):
        return str(job.get_status())
    return str(getattr(job, "status", "unknown"))
