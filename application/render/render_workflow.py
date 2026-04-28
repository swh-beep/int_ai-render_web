import time
from typing import Any, Callable, Optional

from application.render.render_preparation import cleanup_render_resources, prepare_render_inputs, prepare_render_resources
from application.render.render_result_stage import build_detail_payload, extract_render_result_url


def _best_effort_empty_details(message: str) -> dict:
    return {
        "details": [],
        "furniture_boxes": [],
        "used_cutout_references": [],
        "volume_ranking": [],
        "message": message,
    }


def _normalize_detail_result(details_result: Any) -> dict:
    if isinstance(details_result, dict) and isinstance(details_result.get("details"), list):
        normalized = dict(details_result)
        normalized.setdefault("furniture_boxes", [])
        normalized.setdefault("used_cutout_references", [])
        normalized.setdefault("volume_ranking", [])
        normalized.setdefault("message", "Detail views generated successfully")
        return normalized
    if isinstance(details_result, dict) and details_result.get("error"):
        return _best_effort_empty_details(str(details_result.get("error")))
    return _best_effort_empty_details("Detail generation skipped")


def run_render_job(
    payload: dict,
    *,
    persist_result: bool = True,
    materialize_input: Callable[[str | None, str], str | None],
    normalize_audience: Callable[[Optional[str]], str],
    local_upload_factory: Callable[[str], Any],
    render_room: Callable[..., Any],
    json_from_response: Callable[[Any], dict],
    persist_job_result: Callable[[dict, Optional[str]], None],
) -> dict:
    prepared = prepare_render_inputs(
        payload,
        materialize_input=materialize_input,
        normalize_audience=normalize_audience,
    )
    if isinstance(prepared, dict):
        return prepared

    resources = prepare_render_resources(
        prepared,
        materialize_input=materialize_input,
        local_upload_factory=local_upload_factory,
    )

    try:
        response = render_room(
            file=resources.file_obj,
            room=prepared.room,
            style=prepared.style,
            variant=prepared.variant,
            moodboard=resources.mood_obj,
            dimensions=prepared.dimensions,
            placement=prepared.placement,
            audience=prepared.audience,
            moodboard_items=resources.local_items,
        )
        result = json_from_response(response)
        if persist_result:
            persist_job_result(result, audience=prepared.audience)
        return result
    finally:
        cleanup_render_resources(resources)


def run_render_with_details_job(
    payload: dict,
    *,
    normalize_audience: Callable[[Optional[str]], str],
    render_job_runner: Callable[[dict, bool], dict],
    detail_job_runner: Callable[[dict], dict],
    persist_job_result: Callable[[dict, Optional[str]], None],
    total_timeout_limit_sec: float = 600.0,
    minimum_detail_budget_sec: float = 5.0,
    time_now: Callable[[], float] = time.time,
) -> dict:
    job_start_ts = float(time_now())
    absolute_deadline_ts = job_start_ts + max(1.0, float(total_timeout_limit_sec or 600.0))
    render_payload = payload.get("render") or {}
    audience = normalize_audience(render_payload.get("audience"))
    render_payload["audience"] = audience
    extra = payload.get("extra") or {}

    render_result = render_job_runner(render_payload, False)
    if "error" in render_result:
        result = {"error": render_result.get("error"), "render": render_result, **extra}
        persist_job_result(result, audience=audience)
        return result

    if isinstance(render_result, dict) and ("cart_kept" in extra or "cart_dropped" in extra):
        render_result.pop("original_url", None)

    result_url = extract_render_result_url(render_result)
    if not result_url:
        result = {"render": render_result, "details": {"error": "Result image not available"}, **extra}
        persist_job_result(result, audience=audience)
        return result

    details_payload = build_detail_payload(render_result, audience=audience)
    remaining_detail_budget_sec = max(0.0, absolute_deadline_ts - float(time_now()))
    details_payload["absolute_deadline_ts"] = absolute_deadline_ts
    details_payload["detail_budget_sec"] = remaining_detail_budget_sec
    details_payload["minimum_detail_budget_sec"] = float(minimum_detail_budget_sec)
    if remaining_detail_budget_sec < float(minimum_detail_budget_sec):
        details_result = _best_effort_empty_details("Detail generation skipped due to deadline budget exhaustion")
    else:
        details_result = _normalize_detail_result(detail_job_runner(details_payload))
    result = {"render": render_result, "details": details_result, **extra}
    persist_job_result(result, audience=audience)
    return result
