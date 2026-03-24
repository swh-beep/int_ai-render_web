from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse


@dataclass
class QueueRouteDependencies:
    redis_url: str | None
    rq_queue_render: str | None
    rq_queue_upscale: str | None
    cart_max_items: int
    api_auth_disabled: bool
    internal_api_keys: set[str]
    external_api_keys: set[str]
    enqueue_job: Callable[..., tuple[Any, str | None]]
    fetch_job: Callable[[str], Any]
    load_job_result_s3: Callable[[str], dict | None]
    load_preset_map: Callable[[], dict]
    require_role: Callable[..., Any]
    apply_cart_limits: Callable[[list[dict], int], tuple[list[dict], list[dict]]]
    build_cart_summary: Callable[[list[dict]], str]
    materialize_input: Callable[[str, str], str | None]
    normalize_item_image: Callable[[str, str, int], str | None]
    resolve_image_url: Callable[..., str | None]
    build_s3_prefix: Callable[..., str]
    build_item_target_key: Callable[..., str]
    persist_internal_render_uploads: Callable[..., tuple[str, str | None]]
    persist_internal_media_uploads: Callable[..., tuple[str, list[str], str | None]]
    build_internal_async_render_job_payload: Callable[..., dict]
    build_image_edit_job_payload: Callable[..., dict]
    build_frontal_view_job_payload: Callable[..., dict]
    build_upscale_job_payload: Callable[[Any], dict]
    build_finalize_download_job_payload: Callable[[Any], dict]
    build_empty_room_job_payload: Callable[[Any], dict]
    build_internal_render_job_payload: Callable[[Any], dict]
    build_external_preset_job: Callable[[Any, dict], tuple[dict, dict]]
    build_external_cart_job: Callable[..., tuple[dict, list[dict], list[dict]]]
    build_regenerate_detail_job_payload: Callable[[Any], dict]
    build_detail_generation_job_payload: Callable[[Any], dict]
    job_render: Callable[..., dict]
    job_render_with_details: Callable[..., dict]
    job_image_edit: Callable[..., dict]
    job_frontal_view: Callable[..., dict]
    job_upscale: Callable[..., dict]
    job_finalize: Callable[..., dict]
    job_generate_empty_room: Callable[..., dict]
    job_regenerate_single_detail: Callable[..., dict]
    job_generate_details: Callable[..., dict]


def _redis_not_configured_response() -> JSONResponse:
    return JSONResponse(content={"error": "REDIS_URL not configured"}, status_code=500)


def _enqueue_or_error(job_func: Callable[..., Any], payload: dict, *, queue_name: str | None, deps: QueueRouteDependencies) -> JSONResponse:
    job, err = deps.enqueue_job(job_func, payload, queue_name=queue_name)
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return JSONResponse(content={"job_id": job.id, "status": "queued"})


def handle_get_job_status(job_id: str, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not deps.redis_url:
        return _redis_not_configured_response()
    job = deps.fetch_job(job_id)
    if not job:
        saved = deps.load_job_result_s3(job_id)
        if saved is not None:
            return JSONResponse(
                content={
                    "id": job_id,
                    "status": "finished",
                    "enqueued_at": None,
                    "started_at": None,
                    "ended_at": None,
                    "result": saved,
                    "result_source": "s3",
                }
            )
        return JSONResponse(content={"error": "Job not found"}, status_code=404)

    payload = {
        "id": job.id,
        "status": job.get_status(),
        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
    }
    if job.is_finished:
        payload["result"] = job.result
        if payload["result"] is None:
            saved = deps.load_job_result_s3(job_id)
            if saved is not None:
                payload["result"] = saved
                payload["result_source"] = "s3"
    if job.is_failed:
        payload["error"] = job.exc_info
    return JSONResponse(content=payload)


def handle_render_room_async(
    *,
    file: UploadFile,
    room: str,
    style: str,
    variant: str,
    moodboard: UploadFile | None,
    dimensions: str,
    placement: str,
    deps: QueueRouteDependencies,
) -> JSONResponse:
    if not deps.redis_url:
        return _redis_not_configured_response()

    raw_path, mood_path = deps.persist_internal_render_uploads(file, moodboard)
    try:
        payload = deps.build_internal_async_render_job_payload(
            raw_path=raw_path,
            mood_path=mood_path,
            room=room,
            style=style,
            variant=variant,
            dimensions=dimensions,
            placement=placement,
            resolve_image_url=deps.resolve_image_url,
            build_s3_prefix=deps.build_s3_prefix,
        )
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)
    return _enqueue_or_error(deps.job_render, payload, queue_name=deps.rq_queue_render, deps=deps)


def handle_generate_image_edit_async(
    *,
    input_photos: list[UploadFile],
    instructions: str,
    mode: str,
    mask: UploadFile | None,
    deps: QueueRouteDependencies,
) -> JSONResponse:
    if not deps.redis_url:
        return _redis_not_configured_response()

    unique_id, saved_photo_paths, mask_path = deps.persist_internal_media_uploads(
        input_photos,
        prefix="src",
        mode=mode,
        mask=mask,
    )
    try:
        payload = deps.build_image_edit_job_payload(
            saved_photo_paths=saved_photo_paths,
            instructions=instructions,
            mode=mode,
            unique_id=unique_id,
            mask_path=mask_path,
            resolve_image_url=deps.resolve_image_url,
            build_s3_prefix=deps.build_s3_prefix,
        )
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)
    return _enqueue_or_error(deps.job_image_edit, payload, queue_name=deps.rq_queue_render, deps=deps)


def handle_generate_frontal_view_async(
    *,
    input_photos: list[UploadFile],
    deps: QueueRouteDependencies,
) -> JSONResponse:
    if not deps.redis_url:
        return _redis_not_configured_response()

    unique_id, saved_photo_paths, _ = deps.persist_internal_media_uploads(input_photos, prefix="src")
    try:
        payload = deps.build_frontal_view_job_payload(
            saved_photo_paths=saved_photo_paths,
            unique_id=unique_id,
            resolve_image_url=deps.resolve_image_url,
            build_s3_prefix=deps.build_s3_prefix,
        )
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)
    return _enqueue_or_error(deps.job_frontal_view, payload, queue_name=deps.rq_queue_render, deps=deps)


def handle_upscale_async(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not deps.redis_url:
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_upscale,
        deps.build_upscale_job_payload(req),
        queue_name=deps.rq_queue_upscale,
        deps=deps,
    )


def handle_finalize_async(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not deps.redis_url:
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_finalize,
        deps.build_finalize_download_job_payload(req),
        queue_name=deps.rq_queue_upscale,
        deps=deps,
    )


def handle_generate_empty_room_async(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not deps.redis_url:
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_generate_empty_room,
        deps.build_empty_room_job_payload(req),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )


def handle_api_internal_render(req: Any, request: Request, *, deps: QueueRouteDependencies) -> JSONResponse:
    deps.require_role(
        request,
        {"internal"},
        deps.api_auth_disabled,
        deps.internal_api_keys,
        deps.external_api_keys,
    )
    if not deps.redis_url:
        return _redis_not_configured_response()
    if not req.image_url:
        raise HTTPException(status_code=400, detail="image_url is required")
    return _enqueue_or_error(
        deps.job_render_with_details,
        deps.build_internal_render_job_payload(req),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )


def handle_api_external_render_preset(req: Any, request: Request, *, deps: QueueRouteDependencies) -> JSONResponse:
    deps.require_role(
        request,
        {"external"},
        deps.api_auth_disabled,
        deps.internal_api_keys,
        deps.external_api_keys,
    )
    if not deps.redis_url:
        return _redis_not_configured_response()
    if not req.image_url:
        raise HTTPException(status_code=400, detail="image_url is required")

    try:
        job_payload, resolved = deps.build_external_preset_job(req, deps.load_preset_map())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job, err = deps.enqueue_job(deps.job_render_with_details, job_payload, queue_name=deps.rq_queue_render)
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return JSONResponse(
        content={
            "job_id": job.id,
            "status": "queued",
            "resolved": {
                "room": resolved["room"],
                "style": resolved["style"],
                "variant": resolved["variant"],
            },
        }
    )


def handle_api_external_render_cart(req: Any, request: Request, *, deps: QueueRouteDependencies) -> JSONResponse:
    deps.require_role(
        request,
        {"external"},
        deps.api_auth_disabled,
        deps.internal_api_keys,
        deps.external_api_keys,
    )
    if not deps.redis_url:
        return _redis_not_configured_response()
    if not req.image_url:
        raise HTTPException(status_code=400, detail="image_url is required")
    if not req.items:
        raise HTTPException(status_code=400, detail="items are required")

    try:
        job_payload, kept, dropped = deps.build_external_cart_job(
            req,
            cart_max_items=deps.cart_max_items,
            apply_cart_limits=deps.apply_cart_limits,
            build_cart_summary=deps.build_cart_summary,
            materialize_input=deps.materialize_input,
            normalize_item_image=deps.normalize_item_image,
            resolve_image_url=deps.resolve_image_url,
            build_s3_prefix=deps.build_s3_prefix,
            build_item_target_key=deps.build_item_target_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)

    job, err = deps.enqueue_job(deps.job_render_with_details, job_payload, queue_name=deps.rq_queue_render)
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return JSONResponse(content={"job_id": job.id, "status": "queued", "cart_kept": kept, "cart_dropped": dropped})


def handle_regenerate_single_detail(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not deps.redis_url:
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_regenerate_single_detail,
        deps.build_regenerate_detail_job_payload(req),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )


def handle_generate_details(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not deps.redis_url:
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_generate_details,
        deps.build_detail_generation_job_payload(req),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )
