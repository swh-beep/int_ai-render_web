from __future__ import annotations

import os
import logging
import traceback
import uuid
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from application.tracker_metadata import TRACKER_MANIFEST_FIELDS
from infrastructure.ai.service_scope import INTERNAL_SCOPE, attach_ai_service_scope


logger = logging.getLogger(__name__)


EXTERNAL_CART_SIMPLE_BATCH_SETUP_ERROR = "external_cart_simple_batch_setup_failed"
EXTERNAL_CART_SIMPLE_BATCH_ENQUEUE_ERROR = "external_cart_simple_batch_enqueue_failed"
EXTERNAL_CART_SIMPLE_BATCH_SETUP_MESSAGE = "Unable to prepare cart-simple-batch render request"
EXTERNAL_CART_SIMPLE_BATCH_ENQUEUE_MESSAGE = "Unable to enqueue cart-simple-batch render request"


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
    external_scope_keys: dict[str, set[str]]
    enqueue_job: Callable[..., tuple[Any, str | None]]
    fetch_job: Callable[[str], Any]
    load_job_result_s3: Callable[[str], dict | None]
    save_job_result_s3: Callable[[str, dict, str | None], str | None]
    load_preset_map: Callable[[], dict]
    require_role: Callable[..., Any]
    require_ai_service_scope: Callable[..., str]
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
    build_external_cart_batch_job: Callable[..., tuple[dict, list[dict]]]
    build_external_render_video_job: Callable[[Any], dict]
    build_regenerate_detail_job_payload: Callable[[Any], dict]
    build_detail_generation_job_payload: Callable[[Any], dict]
    rq_video_job_timeout: int
    job_render: Callable[..., dict]
    job_render_with_details: Callable[..., dict]
    job_render_with_extra: Callable[..., dict]
    job_render_cart_simple_batch: Callable[..., dict]
    job_generate_render_video: Callable[..., dict]
    job_image_edit: Callable[..., dict]
    job_frontal_view: Callable[..., dict]
    job_upscale: Callable[..., dict]
    job_finalize: Callable[..., dict]
    job_generate_empty_room: Callable[..., dict]
    job_regenerate_single_detail: Callable[..., dict]
    job_generate_details: Callable[..., dict]
    set_staging_job: Callable[[str, dict], None] | None = None
    update_staging_job: Callable[[str, dict], None] | None = None
    get_staging_job: Callable[[str], dict | None] | None = None
    start_background_task: Callable[[Callable[[], None]], None] | None = None
    persist_internal_item_source_uploads: Callable[[list[UploadFile]], list[str]] | None = None
    prepare_internal_item_upload_paths: Callable[..., list[str]] | None = None


def _redis_not_configured_response() -> JSONResponse:
    return JSONResponse(content={"error": "REDIS_URL not configured"}, status_code=500)


def _stable_external_error_response(error_code: str, message: str, *, status_code: int = 500) -> JSONResponse:
    return JSONResponse(
        content={"error": error_code, "message": message},
        status_code=status_code,
    )


def _queue_backend_available(deps: QueueRouteDependencies) -> bool:
    return bool(deps.redis_url) or bool(getattr(deps, "local_inline_queue_enabled", False))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage_status_payload(job_id: str, state: dict) -> dict:
    payload = {
        "id": job_id,
        "status": str(state.get("status") or "queued"),
        "enqueued_at": state.get("enqueued_at"),
        "started_at": state.get("started_at"),
        "ended_at": state.get("ended_at"),
    }
    stage = state.get("stage")
    if stage:
        payload["stage"] = stage
    actual_job_id = state.get("actual_job_id")
    if actual_job_id and actual_job_id != job_id:
        payload["actual_job_id"] = actual_job_id
    if payload["status"] == "failed":
        payload["error"] = state.get("exc_info") or state.get("error") or "Staging failed"
    return payload


def _saved_result_can_finish_stale_job(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("error"):
        return True
    terminal_keys = {
        "render",
        "empty_room_url",
        "upscaled_url",
        "final_url",
        "finalized_url",
        "video_url",
        "clip_urls",
        "source_images",
        "frontal_url",
    }
    return any(key in result for key in terminal_keys)


def _select_present_fields(payload: dict, field_names: tuple[str, ...]) -> dict:
    return {field: payload[field] for field in field_names if field in payload}


def _compact_render_payload(render: dict) -> dict:
    compact_render = _select_present_fields(
        render,
        (
            "empty_room_url",
            "result_url",
            "result_urls",
            "original_url",
            "message",
            "error",
        ),
    )
    furniture_data = render.get("furniture_data")
    if isinstance(furniture_data, list):
        compact_render["furniture_data"] = [
            _select_present_fields(
                item,
                (
                    "label",
                    "description",
                    "box_2d",
                    "qty",
                    "options",
                    "requested_dims_mm",
                    "item_id",
                    "itemId",
                    "cart_item_id",
                    "cartItemId",
                ),
            )
            for item in furniture_data
            if isinstance(item, dict)
        ]
    return compact_render


def _compact_render_job_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    render = result.get("render")
    if not isinstance(render, dict):
        batch_results = result.get("results")
        if not isinstance(batch_results, list):
            return result
        compact_rows = []
        changed = False
        for row in batch_results:
            if isinstance(row, dict) and isinstance(row.get("render"), dict):
                compact_row = dict(row)
                compact_row["render"] = _compact_render_payload(row["render"])
                compact_rows.append(compact_row)
                changed = True
            else:
                compact_rows.append(row)
        if not changed:
            return result
        compact_batch = dict(result)
        compact_batch["results"] = compact_rows
        return compact_batch

    compact_render = _compact_render_payload(render)

    compact_result = {
        field: result[field]
        for field in TRACKER_MANIFEST_FIELDS
        if field in result
    }
    compact_result["render"] = compact_render
    details = result.get("details")
    if isinstance(details, dict):
        detail_items = details.get("details")
        if isinstance(detail_items, list):
            compact_result["details"] = {
                "details": [
                    _select_present_fields(item, ("index", "url"))
                    for item in detail_items
                    if isinstance(item, dict)
                ]
            }

    for field in ("cart_kept", "cart_dropped", "error", "message"):
        if field in result:
            compact_result[field] = result[field]
    return compact_result


def _compact_job_status_payload(payload: dict, *, compact: bool) -> dict:
    if not compact or "result" not in payload:
        return payload
    compact_result = _compact_render_job_result(payload["result"])
    if compact_result is payload["result"]:
        return payload
    compact_payload = dict(payload)
    compact_payload["result"] = compact_result
    compact_payload["result_compacted"] = True
    return compact_payload


def _safe_failed_error(saved: dict | None) -> str:
    if isinstance(saved, dict) and saved.get("terminal_status") == "timeout":
        return "render_job_timeout"
    return "render_job_failed"


def _manifest_identity_fields(body: Any) -> dict:
    return {
        "service_source": body.service_source,
        "client_service": body.client_service,
        "environment": body.environment,
        "is_internal": body.is_internal,
        "journey_id": body.journey_id,
        "request_id": body.request_id,
        "job_kind": body.job_kind,
    }


def _validate_manifest_identity(job_id: str, manifest: dict, body: Any) -> None:
    if manifest.get("terminal_status") != "success":
        raise HTTPException(status_code=409, detail="tracker manifest is not successful")
    if manifest.get("job_id") != job_id:
        raise HTTPException(status_code=409, detail="tracker manifest identity mismatch")
    for field, expected in _manifest_identity_fields(body).items():
        if manifest.get(field) != expected:
            raise HTTPException(status_code=409, detail="tracker manifest identity mismatch")


def _set_staging_job(deps: QueueRouteDependencies, job_id: str, state: dict) -> None:
    setter = getattr(deps, "set_staging_job", None)
    if callable(setter):
        setter(job_id, state)


def _update_staging_job(deps: QueueRouteDependencies, job_id: str, **fields: Any) -> None:
    updater = getattr(deps, "update_staging_job", None)
    if callable(updater):
        updater(job_id, fields)


def _get_staging_job(deps: QueueRouteDependencies, job_id: str) -> dict | None:
    getter = getattr(deps, "get_staging_job", None)
    if callable(getter):
        state = getter(job_id)
        return state if isinstance(state, dict) else None
    return None


def _start_background_task(deps: QueueRouteDependencies, task: Callable[[], None]) -> None:
    starter = getattr(deps, "start_background_task", None)
    if callable(starter):
        starter(task)
    else:
        task()


def _enqueue_or_error(job_func: Callable[..., Any], payload: dict, *, queue_name: str | None, deps: QueueRouteDependencies) -> JSONResponse:
    job, err = deps.enqueue_job(job_func, payload, queue_name=queue_name)
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return JSONResponse(content={"job_id": job.id, "status": "queued"})


def _internal_payload(payload: dict) -> dict:
    return attach_ai_service_scope(payload, INTERNAL_SCOPE)


def _scope_from_request(request: Request, *, deps: QueueRouteDependencies) -> str:
    resolver = getattr(deps, "require_ai_service_scope", None)
    if not callable(resolver):
        raise HTTPException(status_code=500, detail="AI service scope resolver is not configured")
    return deps.require_ai_service_scope(
        request,
        {"external"},
        deps.api_auth_disabled,
        deps.internal_api_keys,
        deps.external_api_keys,
        getattr(deps, "external_scope_keys", {}),
    )


def _external_payload(payload: dict, scope: str) -> dict:
    return attach_ai_service_scope(payload, scope)


def _is_external_render_job_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    return any(key in result for key in ("resolved", "cart_kept", "cart_dropped"))


def _is_external_cart_render_job_result(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    return "cart_kept" in result or "cart_dropped" in result


def _external_video_disabled_reason(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    if result.get("video_enabled") is not False:
        return None
    reason = result.get("video_disabled_reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    return "Video generation is disabled for this render job"


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


def _count_external_video_source_images(result: Any) -> int:
    if not isinstance(result, dict):
        return 0

    render_payload = result.get("render") or {}
    details_payload = result.get("details") or {}

    primary_count = 0
    result_url = render_payload.get("result_url")
    if isinstance(result_url, str) and result_url.strip():
        primary_count = 1
    else:
        for value in render_payload.get("result_urls") or []:
            if isinstance(value, str) and value.strip():
                primary_count = 1
                break

    detail_count = 0
    for row in details_payload.get("details") or []:
        if not isinstance(row, dict):
            continue
        value = row.get("url")
        if isinstance(value, str) and value.strip():
            detail_count += 1

    return primary_count + detail_count


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


def _stage_internal_render_publish_and_enqueue(
    *,
    job_id: str,
    raw_path: str,
    item_specs: list[dict],
    item_source_paths: list[str],
    room: str,
    style: str,
    variant: str,
    dimensions: str,
    placement: str,
    deps: QueueRouteDependencies,
) -> None:
    try:
        _update_staging_job(
            deps,
            job_id,
            status="queued",
            stage="preparing_inputs",
            started_at=_utcnow_iso(),
            error=None,
            exc_info=None,
        )
        prepare_item_paths = getattr(deps, "prepare_internal_item_upload_paths", None)
        if not callable(prepare_item_paths):
            raise RuntimeError("prepare_internal_item_upload_paths is not configured")
        item_paths = prepare_item_paths(item_source_paths, item_specs=item_specs)

        _update_staging_job(deps, job_id, status="queued", stage="publishing_inputs")
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
            publish_inputs=True,
        )
        payload = _internal_payload(payload)

        _update_staging_job(deps, job_id, status="queued", stage="enqueueing")
        job, err = deps.enqueue_job(deps.job_render, payload, queue_name=deps.rq_queue_render, job_id=job_id)
        if err:
            raise RuntimeError(err)
        actual_job_id = str(getattr(job, "id", job_id) or job_id)
        _update_staging_job(
            deps,
            job_id,
            status="queued",
            stage="enqueued",
            actual_job_id=actual_job_id,
        )
    except Exception as exc:
        _update_staging_job(
            deps,
            job_id,
            status="failed",
            stage="failed",
            ended_at=_utcnow_iso(),
            error=str(exc),
            exc_info=traceback.format_exc(),
        )


def handle_get_job_status(job_id: str, *, deps: QueueRouteDependencies, compact: bool = False) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    job = deps.fetch_job(job_id)
    if not job:
        staged = _get_staging_job(deps, job_id)
        if staged is not None:
            return JSONResponse(content=_stage_status_payload(job_id, staged))
        saved = deps.load_job_result_s3(job_id)
        if saved is not None:
            return JSONResponse(
                content=_compact_job_status_payload({
                    "id": job_id,
                    "status": "finished",
                    "enqueued_at": None,
                    "started_at": None,
                    "ended_at": None,
                    "result": saved,
                    "result_source": "s3",
                }, compact=compact)
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
        saved = deps.load_job_result_s3(job_id)
        if saved is not None:
            payload["result"] = saved
            payload["result_source"] = "s3"
        else:
            payload["result"] = job.result
    elif not job.is_failed:
        saved = deps.load_job_result_s3(job_id)
        if _saved_result_can_finish_stale_job(saved):
            payload["status"] = "finished"
            payload["result"] = saved
            payload["result_source"] = "s3"
            payload["stale_job_status"] = job.get_status()
    if job.is_failed:
        saved = deps.load_job_result_s3(job_id)
        if saved is not None:
            payload["result"] = saved
            payload["result_source"] = "s3"
        payload["error"] = _safe_failed_error(saved)
    return JSONResponse(content=_compact_job_status_payload(payload, compact=compact))


def handle_patch_external_tracker_manifest(req: Any, request: Request, job_id: str, *, deps: QueueRouteDependencies) -> JSONResponse:
    deps.require_role(
        request,
        {"external"},
        deps.api_auth_disabled,
        deps.internal_api_keys,
        deps.external_api_keys,
    )

    manifest = deps.load_job_result_s3(job_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="tracker manifest not found")
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=409, detail="tracker manifest is invalid")

    _validate_manifest_identity(job_id, manifest, req)
    existing_result_id = manifest.get("result_id")
    if existing_result_id is None:
        updated = dict(manifest)
        updated["result_id"] = req.result_id
    elif existing_result_id == req.result_id:
        updated = manifest
    else:
        raise HTTPException(status_code=409, detail="tracker manifest result_id conflict")

    try:
        saved_path = deps.save_job_result_s3(job_id, updated, "external")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="tracker manifest save failed") from exc
    if not saved_path:
        raise HTTPException(status_code=502, detail="tracker manifest save failed")
    return JSONResponse(content={"result_source": "s3", "result": updated})


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
        persist_item_sources = getattr(deps, "persist_internal_item_source_uploads", None)
        if not callable(persist_item_sources):
            raise RuntimeError("persist_internal_item_source_uploads is not configured")
        raw_path = deps.persist_internal_room_upload(file)
        item_source_paths = persist_item_sources(item_images)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)

    job_id = uuid.uuid4().hex
    now = _utcnow_iso()
    _set_staging_job(
        deps,
        job_id,
        {
            "status": "queued",
            "stage": "staged",
            "enqueued_at": now,
            "started_at": None,
            "ended_at": None,
            "error": None,
            "exc_info": None,
        },
    )

    def _task() -> None:
        _stage_internal_render_publish_and_enqueue(
            job_id=job_id,
            raw_path=raw_path,
            item_specs=item_specs,
            item_source_paths=item_source_paths,
            room=room,
            style=style,
            variant=variant,
            dimensions=dimensions,
            placement=placement,
            deps=deps,
        )

    _start_background_task(deps, _task)
    return JSONResponse(content={"job_id": job_id, "status": "queued"})


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
        _internal_payload(deps.build_internal_render_job_payload(req)),
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
    return _enqueue_or_error(deps.job_image_edit, _internal_payload(payload), queue_name=deps.rq_queue_render, deps=deps)


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
    return _enqueue_or_error(deps.job_frontal_view, _internal_payload(payload), queue_name=deps.rq_queue_render, deps=deps)


def handle_upscale_async(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_upscale,
        _internal_payload(deps.build_upscale_job_payload(req)),
        queue_name=deps.rq_queue_upscale,
        deps=deps,
    )


def handle_finalize_async(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_finalize,
        _internal_payload(deps.build_finalize_download_job_payload(req)),
        queue_name=deps.rq_queue_upscale,
        deps=deps,
    )


def handle_generate_empty_room_async(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_generate_empty_room,
        _internal_payload(deps.build_empty_room_job_payload(req)),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )


def handle_api_external_render_preset(req: Any, request: Request, *, deps: QueueRouteDependencies) -> JSONResponse:
    ai_scope = _scope_from_request(request, deps=deps)
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    if not req.image_url:
        raise HTTPException(status_code=400, detail="image_url is required")

    try:
        job_payload, resolved = deps.build_external_preset_job(req, deps.load_preset_map())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job, err = deps.enqueue_job(deps.job_render_with_details, _external_payload(job_payload, ai_scope), queue_name=deps.rq_queue_render)
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
    ai_scope = _scope_from_request(request, deps=deps)
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

    job, err = deps.enqueue_job(deps.job_render_with_details, _external_payload(job_payload, ai_scope), queue_name=deps.rq_queue_render)
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return JSONResponse(content={"job_id": job.id, "status": "queued", "cart_kept": kept, "cart_dropped": dropped})


def handle_api_external_render_cart_simple(req: Any, request: Request, *, deps: QueueRouteDependencies) -> JSONResponse:
    ai_scope = _scope_from_request(request, deps=deps)
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    if not req.image_url:
        raise HTTPException(status_code=400, detail="image_url is required")
    if not req.items:
        raise HTTPException(status_code=400, detail="items are required")

    try:
        job_payload, kept, dropped = deps.build_external_cart_job(
            req,
            default_job_kind="cart_simple",
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

    job, err = deps.enqueue_job(deps.job_render_with_extra, _external_payload(job_payload, ai_scope), queue_name=deps.rq_queue_render)
    if err:
        return JSONResponse(content={"error": err}, status_code=500)
    return JSONResponse(content={"job_id": job.id, "status": "queued", "cart_kept": kept, "cart_dropped": dropped})


def handle_api_external_render_cart_simple_batch(req: Any, request: Request, *, deps: QueueRouteDependencies) -> JSONResponse:
    ai_scope = _scope_from_request(request, deps=deps)
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    if not req.image_url:
        raise HTTPException(status_code=400, detail="image_url is required")
    if not req.variants:
        raise HTTPException(status_code=400, detail="variants are required")

    try:
        job_payload, variants = deps.build_external_cart_batch_job(
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
    except Exception:
        logger.exception("External cart-simple-batch setup failed")
        return _stable_external_error_response(
            EXTERNAL_CART_SIMPLE_BATCH_SETUP_ERROR,
            EXTERNAL_CART_SIMPLE_BATCH_SETUP_MESSAGE,
        )

    try:
        job, err = deps.enqueue_job(deps.job_render_cart_simple_batch, _external_payload(job_payload, ai_scope), queue_name=deps.rq_queue_render)
    except Exception:
        logger.exception("External cart-simple-batch enqueue failed")
        return _stable_external_error_response(
            EXTERNAL_CART_SIMPLE_BATCH_ENQUEUE_ERROR,
            EXTERNAL_CART_SIMPLE_BATCH_ENQUEUE_MESSAGE,
        )
    if err:
        logger.error("External cart-simple-batch enqueue failed: %s", err)
        return _stable_external_error_response(
            EXTERNAL_CART_SIMPLE_BATCH_ENQUEUE_ERROR,
            EXTERNAL_CART_SIMPLE_BATCH_ENQUEUE_MESSAGE,
        )
    return JSONResponse(content={"job_id": job.id, "status": "queued", "variants": variants})


def handle_api_external_render_video(req: Any, request: Request, *, deps: QueueRouteDependencies) -> JSONResponse:
    ai_scope = _scope_from_request(request, deps=deps)
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
    if _is_external_cart_render_job_result(source_result):
        raise HTTPException(status_code=400, detail="cart render jobs do not generate video")
    disabled_reason = _external_video_disabled_reason(source_result)
    if disabled_reason:
        raise HTTPException(status_code=400, detail=disabled_reason)
    if isinstance(source_result, dict) and (source_result.get("error") or not _has_external_video_source_images(source_result)):
        raise HTTPException(status_code=400, detail="render_job_id does not have usable image results")

    try:
        job_payload = deps.build_external_render_video_job(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job, err = deps.enqueue_job(
        deps.job_generate_render_video,
        _external_payload(job_payload, ai_scope),
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
            "clip_count": min(
                int(job_payload.get("clip_count") or 7),
                _count_external_video_source_images(source_result),
            ),
        }
    )


def handle_regenerate_single_detail(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_regenerate_single_detail,
        _internal_payload(deps.build_regenerate_detail_job_payload(req)),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )


def handle_generate_details(req: Any, *, deps: QueueRouteDependencies) -> JSONResponse:
    if not _queue_backend_available(deps):
        return _redis_not_configured_response()
    return _enqueue_or_error(
        deps.job_generate_details,
        _internal_payload(deps.build_detail_generation_job_payload(req)),
        queue_name=deps.rq_queue_render,
        deps=deps,
    )
