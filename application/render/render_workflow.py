from typing import Any, Callable, Optional

from application.render.render_preparation import cleanup_render_resources, prepare_render_inputs, prepare_render_resources
from application.render.render_result_stage import build_detail_payload, extract_render_result_url


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
) -> dict:
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
    details_result = detail_job_runner(details_payload)
    result = {"render": render_result, "details": details_result, **extra}
    persist_job_result(result, audience=audience)
    return result
