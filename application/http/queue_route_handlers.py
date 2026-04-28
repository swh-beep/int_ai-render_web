from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse


@dataclass
class QueueRouteDependencies:
    redis_url: str | None
    local_inline_queue_enabled: bool
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
    parse_internal_render_items_form: Callable[[str, list[UploadFile]], list[dict]]
    persist_internal_room_upload: Callable[[UploadFile], str]
    persist_internal_item_uploads: Callable[[list[UploadFile]], list[str]]
    persist_internal_media_uploads: Callable[..., tuple[str, list[str], str | None]]
    build_internal_render_job_payload: Callable[[Any], dict]
    build_internal_itemized_async_render_job_payload: Callable[..., dict]
    build_image_edit_job_payload: Callable[..., dict]
    build_frontal_view_job_payload: Callable[..., dict]
    build_upscale_job_payload: Callable[[Any], dict]
    build_finalize_download_job_payload: Callable[[Any], dict]
    build_empty_room_job_payload: Callable[[Any], dict]
    build_external_preset_job: Callable[[Any, dict], tuple[dict, dict]]
    build_external_cart_job: Callable[..., tuple[dict, list[dict], list[dict]]]
    build_external_render_video_job: Callable[[Any], dict]
    build_regenerate_detail_job_payload: Callable[[Any], dict]
    build_detail_generation_job_payload: Callable[[Any], dict]
    rq_video_job_timeout: int
    job_render: Callable[..., dict]
    job_render_with_details: Callable[..., dict]
    job_generate_render_video: Callable[..., dict]
    job_image_edit: Callable[..., dict]
    job_frontal_view: Callable[..., dict]
    job_upscale: Callable[..., dict]
    job_finalize: Callable[..., dict]
    job_generate_empty_room: Callable[..., dict]
    job_regenerate_single_detail: Callable[..., dict]
    job_generate_details: Callable[..., dict]


def _redis_not_configured_response() -> JSONResponse:
    return JSONResponse(content={"error": "REDIS_URL not configured"}, status_code=500)


def _queue_backend_available(deps: QueueRouteDependencies) -> bool:
    return bool(deps.redis_url) or bool(getattr(deps, "local_inline_queue_enabled", False))


def _enqueue_or_error(job_func: Callable[..., Any], payload: dict, *, queue_name: str | None, deps: QueueRouteDependencies) -> JSONResponse:
    job, err = deps.enqueue_job(job_func, payload, queue_name=queue_name)
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return JSONResponse(content={"job_id": job.id, "status": "queued"})


def _is_external_render_job_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    return any(key in result for key in ("resolved", "cart_kept", "cart_dropped"))


def _has_external_video_source_images(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    render_payload = result.get("render") or {}
    details_payload = result.get("details") or {}

    result_url = render_payload.get("result_url")
    if isinstance(result_url, str) and result_url.strip():
        return True

    for value in render_payload.get("result_urls") or []:
        if isinstance(value, str) and value.strip():
            return True

    for row in details_payload.get("details") or []:
        if not isinstance(row, dict):
            continue
        value = row.get("url")
        if isinstance(value, str) and value.strip():
            return True
    return False


def _internal_render_allowed_exts() -> set[str]:
    raw_exts = os.getenv("OUTPUTS_ALLOWED_EXTS", ".png,.jpg,.jpeg,.webp")
    return {
        ext.strip().lower()
        for ext in raw_exts.replace(";", ",").split(",")
        if ext.strip()
    }


def _internal_render_max_bytes() -> int:
    raw_max_mb = os.getenv("OUTPUTS_UPLOAD_MAX_MB", "25").strip()
    try:
        return max(1, int(raw_max_mb)) * 1024 * 1024
    except (TypeError, ValueError):
        return 25 * 1024 * 1024


def _validate_internal_render_upload(upload: UploadFile, *, field_name: str) -> None:
    filename = upload.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in _internal_render_allowed_exts():
        allowed = ", ".join(sorted(_internal_render_allowed_exts()))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type for {field_name}. Allowed types: {allowed}",
        )

    max_bytes = _internal_render_max_bytes()
    file_obj = upload.file
    try:
        current_pos = file_obj.tell()
    except Exception:
        current_pos = 0

    try:
        file_obj.seek(0, os.SEEK_END)
        size = file_obj.tell()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to inspect {field_name}") from exc
    finally:
        try:
            file_obj.seek(current_pos, os.SEEK_SET)
        except Exception:
            pass

    if size > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"{field_name} exceeds the maximum allowed size of {max_mb}MB",
        )


def handle_get_job_status(job_id: str, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
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
    items_json: str,
    item_images: list[UploadFile],
    dimensions: str,
    placement: str,
    deps: QueueRouteDependencies,
) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()

    try:
        if not item_images:
            raise HTTPException(status_code=400, detail="At least one furniture item is required")
        _validate_internal_render_upload(file, field_name="room file")
        for idx, item_image in enumerate(item_images, start=1):
            _validate_internal_render_upload(item_image, field_name=f"item image {idx}")
        item_specs = deps.parse_internal_render_items_form(items_json, item_images)
        if not item_specs or len(item_specs) != len(item_images):
            raise HTTPException(
                status_code=400,
                detail="items_json must contain at least one item and match item_images",
            )
        raw_path = deps.persist_internal_room_upload(file)
        item_paths = deps.persist_internal_item_uploads(item_images)
        payload = deps.build_internal_itemized_async_render_job_payload(
            raw_path=raw_path,
            item_specs=item_specs,
            item_paths=item_paths,
            room=room,
            style=style,
            variant=variant,
            dimensions=dimensions,
            placement=placement,
            resolve_image_url=deps.resolve_image_url,
            build_s3_prefix=deps.build_s3_prefix,
            build_item_target_key=deps.build_item_target_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return _enqueue_or_error(deps.job_render, payload, queue_name=deps.rq_queue_render, deps=deps)
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)


def handle_api_internal_render(req: Any, request: Request, *, deps: QueueRouteDependencies) -> JSONResponse:
    deps.require_role(
        request,
        {"internal"},
        deps.api_auth_disabled,
        deps.internal_api_keys,
        deps.external_api_keys,
    )
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    if not req.image_url:
        raise HTTPException(status_code=400, detail="image_url is required")
    return _enqueue_or_error(
        deps.job_render_with_details,
        deps.build_internal_render_job_payload(req),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )


def handle_generate_image_edit_async(
    *,
    input_photos: list[UploadFile],
    instructions: str,
    mode: str,
    mask: UploadFile | None,
    deps: QueueRouteDependencies,
) -> JSONResponse:
    if not _queue_backend_available(deps):
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
    if not _queue_backend_available(deps):
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
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_upscale,
        deps.build_upscale_job_payload(req),
        queue_name=deps.rq_queue_upscale,
        deps=deps,
    )


def handle_finalize_async(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_finalize,
        deps.build_finalize_download_job_payload(req),
        queue_name=deps.rq_queue_upscale,
        deps=deps,
    )


def handle_generate_empty_room_async(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_generate_empty_room,
        deps.build_empty_room_job_payload(req),
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
    if not _queue_backend_available(deps):
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
    if not _queue_backend_available(deps):
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


def handle_api_external_render_video(req: Any, request: Request, *, deps: QueueRouteDependencies) -> JSONResponse:
    deps.require_role(
        request,
        {"external"},
        deps.api_auth_disabled,
        deps.internal_api_keys,
        deps.external_api_keys,
    )
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()

    render_job_id = str(getattr(req, "render_job_id", "") or "").strip()
    if not render_job_id:
        raise HTTPException(status_code=400, detail="render_job_id is required")

    source_job = deps.fetch_job(render_job_id)
    saved_result = deps.load_job_result_s3(render_job_id)
    source_result = saved_result

    if source_job is not None:
        if bool(getattr(source_job, "is_failed", False)):
            raise HTTPException(status_code=400, detail="render_job_id failed")
        if bool(getattr(source_job, "is_finished", False)):
            source_result = getattr(source_job, "result", None) or saved_result
        elif saved_result is None:
            raise HTTPException(status_code=409, detail="render_job_id is not finished yet")
    elif saved_result is None:
        raise HTTPException(status_code=404, detail="render_job_id was not found")

    if not _is_external_render_job_result(source_result):
        raise HTTPException(status_code=403, detail="render_job_id must belong to an external render job")
    if isinstance(source_result, dict) and (source_result.get("error") or not _has_external_video_source_images(source_result)):
        raise HTTPException(status_code=400, detail="render_job_id does not have usable image results")

    try:
        job_payload = deps.build_external_render_video_job(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job, err = deps.enqueue_job(
        deps.job_generate_render_video,
        job_payload,
        queue_name=deps.rq_queue_render,
        job_timeout=int(getattr(deps, "rq_video_job_timeout", 3600) or 3600),
    )
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return JSONResponse(
        content={
            "job_id": job.id,
            "status": "queued",
            "render_job_id": job_payload["render_job_id"],
            "clip_count": job_payload["clip_count"],
        }
    )


def handle_regenerate_single_detail(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_regenerate_single_detail,
        deps.build_regenerate_detail_job_payload(req),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )


def handle_generate_details(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_generate_details,
        deps.build_detail_generation_job_payload(req),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )
