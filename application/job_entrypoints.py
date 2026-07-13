from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fastapi.responses import JSONResponse
from rq import get_current_job

from application.details.detail_workflow import run_generate_details_job
from application.details.regenerate_detail_workflow import run_regenerate_single_detail_job
from application.media.frontal_view_workflow import run_frontal_view_job
from application.media.image_edit_workflow import run_image_edit_job
from application.render.empty_room_workflow import run_generate_empty_room_job
from application.render.finalize_workflow import run_finalize_job
from application.render.render_workflow import run_render_job, run_render_with_details_job
from application.render.upscale_workflow import run_upscale_job
from application.video.external_render_video_workflow import run_external_render_video_job


@dataclass
class JobEntrypointServices:
    normalize_audience: Callable[[Optional[str]], str]
    save_job_result: Callable[[str, dict, Optional[str]], Optional[str]]
    materialize_input: Callable[[str, str], str | None]
    normalize_item_image: Callable[[str, str, int], str | None]
    standardize_image: Callable[..., str]
    build_s3_prefix: Callable[..., str]
    resolve_image_url: Callable[..., str | None]
    render_room: Callable[..., Any]
    generate_empty_room: Callable[..., Any]
    call_magnific_api: Callable[..., Any]
    s3_prefix_from_url: Callable[[str], Optional[str]]
    process_image_edit_logic: Callable[..., Any]
    generate_frontal_room_from_photos: Callable[..., Any]
    log_section: Callable[[str], None]
    detect_furniture_boxes: Callable[..., Any]
    detect_item_bbox_norm: Callable[..., Any]
    canonical_category: Callable[[Optional[str]], str]
    build_item_target_key: Callable[..., str]
    analyze_cropped_item: Callable[..., Any]
    attach_volume_ranks: Callable[[list], list]
    construct_dynamic_styles: Callable[..., Any]
    generate_detail_view: Callable[..., Any]
    normalize_label_for_match: Callable[[str], str]
    volume_ranking_snapshot: Callable[[list], list]
    finalize_request_factory: Callable[..., Any]
    upscale_request_factory: Callable[..., Any]
    max_concurrency_analysis: int
    fetch_job: Callable[[str], Any]
    load_job_result: Callable[[str], dict | None]
    queue_source_generation_job: Callable[..., str]
    queue_final_compile_job: Callable[..., str]
    get_video_job: Callable[[str], dict | None]
    create_kling_task: Callable[..., str]
    poll_kling_task: Callable[..., str]
    video_target_fps: int
    video_max_concurrency: int


_SERVICES: JobEntrypointServices | None = None
_DEFERRED_CART_ITEM_MARKER = "external_cart_item_v1"
CART_SIMPLE_BATCH_MAX_WORKERS = max(1, int(os.getenv("CART_SIMPLE_BATCH_MAX_WORKERS", "3") or "3"))


def configure_job_entrypoints(services: JobEntrypointServices) -> None:
    global _SERVICES
    _SERVICES = services


def _services() -> JobEntrypointServices:
    if _SERVICES is None:
        raise RuntimeError("Job entrypoints are not configured")
    return _SERVICES


class _LocalUpload:
    def __init__(self, path: str):
        self.filename = os.path.basename(path)
        self.file = open(path, "rb")

    def close(self) -> None:
        try:
            self.file.close()
        except Exception:
            pass


def _json_from_response(resp: Any) -> dict:
    if isinstance(resp, JSONResponse):
        try:
            return json.loads(resp.body.decode("utf-8"))
        except Exception:
            return {"error": "Invalid JSON response"}
    if isinstance(resp, dict):
        return resp
    return {"result": resp}


def _persist_job_result(result: dict, audience: Optional[str] = None) -> None:
    try:
        job = get_current_job()
        if not job:
            return
        services = _services()
        normalized_audience = services.normalize_audience(audience)
        services.save_job_result(job.id, result, audience=normalized_audience)
    except Exception:
        pass


def _cleanup_temp_cart_source(local_src: str | None) -> None:
    try:
        if not local_src or not os.path.exists(local_src):
            return
        abs_src = os.path.abspath(local_src)
        abs_out = os.path.abspath("outputs") + os.sep
        if abs_src.startswith(abs_out) and os.path.basename(local_src).startswith("cart_item_"):
            os.remove(local_src)
    except Exception:
        pass


def _prepare_worker_cart_moodboard_items(payload: dict, services: JobEntrypointServices) -> dict:
    render_payload = dict(payload)
    raw_items = list(render_payload.get("moodboard_items") or [])
    if not raw_items:
        return render_payload

    has_deferred_items = any(
        isinstance(item, dict) and item.get("worker_preprocess") == _DEFERRED_CART_ITEM_MARKER
        for item in raw_items
    )
    if not has_deferred_items:
        return render_payload

    unique_id = uuid.uuid4().hex[:8]
    processed_items: list[dict] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        if item.get("worker_preprocess") != _DEFERRED_CART_ITEM_MARKER:
            processed_items.append(item)
            continue

        source_ref = item.get("path") or item.get("url")
        local_src = services.materialize_input(source_ref, f"cart_item_{index - 1}")
        norm_path = services.normalize_item_image(local_src, unique_id, index) if local_src else None
        if not norm_path:
            _cleanup_temp_cart_source(local_src)
            continue

        ref_url = services.resolve_image_url(norm_path, services.build_s3_prefix("external", "customize"))
        if isinstance(ref_url, str) and ref_url.startswith("http"):
            try:
                if os.path.exists(norm_path):
                    os.remove(norm_path)
            except Exception:
                pass
        _cleanup_temp_cart_source(local_src)

        prepared_item = dict(item)
        prepared_item["path"] = ref_url or norm_path
        prepared_item.pop("worker_preprocess", None)
        processed_items.append(prepared_item)

    if not processed_items:
        return {"error": "No valid item images after worker preprocessing"}

    render_payload["moodboard_items"] = processed_items
    return render_payload


def job_render(payload: dict, persist_result: bool = True) -> dict:
    services = _services()
    prepared_payload = _prepare_worker_cart_moodboard_items(payload, services)
    if isinstance(prepared_payload, dict) and prepared_payload.get("error"):
        return prepared_payload
    return run_render_job(
        prepared_payload,
        persist_result=persist_result,
        materialize_input=services.materialize_input,
        normalize_audience=services.normalize_audience,
        local_upload_factory=_LocalUpload,
        render_room=services.render_room,
        json_from_response=_json_from_response,
        persist_job_result=_persist_job_result,
    )


def job_render_with_extra(payload: dict) -> dict:
    services = _services()
    render_payload = payload.get("render") or {}
    extra = payload.get("extra") or {}

    audience = services.normalize_audience(render_payload.get("audience"))
    render_payload["audience"] = audience

    render_result = job_render(render_payload, persist_result=False)
    if isinstance(render_result, dict) and ("cart_kept" in extra or "cart_dropped" in extra):
        render_result.pop("original_url", None)

    if isinstance(render_result, dict) and render_result.get("error"):
        result = {"error": render_result.get("error"), "render": render_result, **extra}
    else:
        result = {"render": render_result, **extra}

    _persist_job_result(result, audience=audience)
    return result


def _generate_shared_cart_empty_room(payload: dict, services: JobEntrypointServices, audience: str) -> dict:
    variants = [row for row in (payload.get("variants") or []) if isinstance(row, dict)]
    first_render = dict((variants[0].get("render") if variants else {}) or {})
    image_url = payload.get("image_url") or first_render.get("file_path")
    local_path = services.materialize_input(image_url, "cart_simple_batch_empty_src")
    if not local_path or not os.path.exists(local_path):
        return {"error": "Input file not found"}

    try:
        std_path = services.standardize_image(local_path) or local_path
    except Exception:
        std_path = local_path

    unique_id = uuid.uuid4().hex[:8]
    start_time = time.time()
    empty_result = services.generate_empty_room(
        std_path,
        unique_id,
        start_time,
        stage_name="Cart Simple Batch: Shared Empty Gen",
        return_raw=True,
    )
    if isinstance(empty_result, tuple):
        empty_path, empty_raw_path = empty_result
    else:
        empty_path = empty_result
        empty_raw_path = empty_result

    if not empty_path or not os.path.exists(empty_path):
        return {"error": "Shared empty room generation failed"}

    empty_url = services.resolve_image_url(
        empty_path,
        services.build_s3_prefix(audience, "mainrendered", "empty"),
    )
    return {
        "empty_room_path": empty_path,
        "empty_room_raw_path": empty_raw_path or empty_path,
        "empty_room_url": empty_url or empty_path,
    }


def job_render_cart_simple_batch(payload: dict) -> dict:
    services = _services()
    variants = [row for row in (payload.get("variants") or []) if isinstance(row, dict)]
    audience = services.normalize_audience(payload.get("audience") or "external")
    if not variants:
        result = {"error": "variants are required"}
        _persist_job_result(result, audience=audience)
        return result

    shared_empty = _generate_shared_cart_empty_room(payload, services, audience)
    if shared_empty.get("error"):
        result = {"error": shared_empty.get("error"), "results": []}
        _persist_job_result(result, audience=audience)
        return result

    variant_count = len(variants)

    def _render_variant(indexed_variant: tuple[int, dict]) -> dict:
        fallback_index, variant = indexed_variant
        variant_index = int(variant.get("variant_index") or fallback_index)
        render_payload = dict(variant.get("render") or {})
        extra = dict(variant.get("extra") or {})
        render_payload["audience"] = audience
        render_payload.setdefault("file_path", payload.get("image_url"))
        render_payload["precomputed_empty_room_path"] = shared_empty["empty_room_path"]
        render_payload["precomputed_empty_room_raw_path"] = shared_empty["empty_room_raw_path"]
        render_payload["batch_variant_index"] = variant_index
        render_payload["batch_variant_count"] = variant_count

        render_result = job_render(render_payload, persist_result=False)
        if isinstance(render_result, dict) and render_result.get("error"):
            row = {
                "variant_index": variant_index,
                "error": render_result.get("error"),
                "render": render_result,
                **extra,
            }
        else:
            normalized_render = dict(render_result or {})
            normalized_render.pop("original_url", None)
            normalized_render["empty_room_url"] = shared_empty["empty_room_url"]
            row = {
                "variant_index": variant_index,
                "render": normalized_render,
                **extra,
            }
        return row

    worker_count = min(CART_SIMPLE_BATCH_MAX_WORKERS, variant_count)
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="cart-simple-batch") as executor:
        results = list(executor.map(_render_variant, enumerate(variants, start=1)))

    result = {
        "empty_room_url": shared_empty["empty_room_url"],
        "results": results,
    }
    _persist_job_result(result, audience=audience)
    return result


def job_render_with_details(payload: dict) -> dict:
    return run_render_with_details_job(
        payload,
        normalize_audience=_services().normalize_audience,
        render_job_runner=lambda render_payload, persist_result=False: job_render(
            render_payload,
            persist_result=persist_result,
        ),
        detail_job_runner=job_generate_details,
        persist_job_result=_persist_job_result,
    )


def job_image_edit(payload: dict) -> dict:
    services = _services()
    return run_image_edit_job(
        payload,
        normalize_audience=services.normalize_audience,
        build_s3_prefix=services.build_s3_prefix,
        materialize_input=services.materialize_input,
        resolve_image_url=services.resolve_image_url,
        process_image_edit_logic=services.process_image_edit_logic,
    )


def job_finalize(payload: dict) -> dict:
    services = _services()
    return run_finalize_job(
        payload,
        finalize_request_factory=services.finalize_request_factory,
        materialize_input=services.materialize_input,
        generate_empty_room=services.generate_empty_room,
        call_magnific_api=services.call_magnific_api,
        s3_prefix_from_url=services.s3_prefix_from_url,
        resolve_image_url=services.resolve_image_url,
    )


def job_generate_empty_room(payload: dict) -> dict:
    services = _services()
    return run_generate_empty_room_job(
        payload,
        normalize_audience=services.normalize_audience,
        materialize_input=services.materialize_input,
        generate_empty_room=services.generate_empty_room,
        build_s3_prefix=services.build_s3_prefix,
        resolve_image_url=services.resolve_image_url,
        persist_job_result=_persist_job_result,
    )


def job_upscale(payload: dict) -> dict:
    services = _services()
    return run_upscale_job(
        payload,
        upscale_request_factory=services.upscale_request_factory,
        materialize_input=services.materialize_input,
        call_magnific_api=services.call_magnific_api,
        s3_prefix_from_url=services.s3_prefix_from_url,
        resolve_image_url=services.resolve_image_url,
    )


def job_frontal_view(payload: dict) -> dict:
    services = _services()
    return run_frontal_view_job(
        payload,
        normalize_audience=services.normalize_audience,
        build_s3_prefix=services.build_s3_prefix,
        materialize_input=services.materialize_input,
        resolve_image_url=services.resolve_image_url,
        generate_frontal_room_from_photos=services.generate_frontal_room_from_photos,
    )


def job_generate_details(payload: dict) -> dict:
    services = _services()
    return run_generate_details_job(
        payload,
        normalize_audience=services.normalize_audience,
        build_s3_prefix=services.build_s3_prefix,
        persist_job_result=_persist_job_result,
        materialize_input=services.materialize_input,
        resolve_image_url=services.resolve_image_url,
        log_section=services.log_section,
        detect_furniture_boxes=services.detect_furniture_boxes,
        detect_item_bbox_norm=services.detect_item_bbox_norm,
        canonical_category=services.canonical_category,
        build_item_target_key=services.build_item_target_key,
        max_concurrency_analysis=services.max_concurrency_analysis,
        analyze_cropped_item=services.analyze_cropped_item,
        attach_volume_ranks=services.attach_volume_ranks,
        construct_dynamic_styles=services.construct_dynamic_styles,
        generate_detail_view=services.generate_detail_view,
        normalize_label_for_match=services.normalize_label_for_match,
        volume_ranking_snapshot=services.volume_ranking_snapshot,
    )


def job_regenerate_single_detail(payload: dict) -> dict:
    services = _services()
    return run_regenerate_single_detail_job(
        payload,
        normalize_audience=services.normalize_audience,
        build_s3_prefix=services.build_s3_prefix,
        materialize_input=services.materialize_input,
        resolve_image_url=services.resolve_image_url,
        detect_furniture_boxes=services.detect_furniture_boxes,
        canonical_category=services.canonical_category,
        build_item_target_key=services.build_item_target_key,
        max_concurrency_analysis=services.max_concurrency_analysis,
        analyze_cropped_item=services.analyze_cropped_item,
        attach_volume_ranks=services.attach_volume_ranks,
        construct_dynamic_styles=services.construct_dynamic_styles,
        normalize_label_for_match=services.normalize_label_for_match,
        generate_detail_view=services.generate_detail_view,
        volume_ranking_snapshot=services.volume_ranking_snapshot,
    )


def job_generate_render_video(payload: dict) -> dict:
    services = _services()
    result = run_external_render_video_job(
        payload,
        fetch_job=services.fetch_job,
        load_job_result=services.load_job_result,
        queue_source_generation_job=services.queue_source_generation_job,
        queue_final_compile_job=services.queue_final_compile_job,
        get_video_job=services.get_video_job,
        resolve_image_url=services.resolve_image_url,
        build_s3_prefix=services.build_s3_prefix,
        normalize_audience=services.normalize_audience,
        create_kling_task=services.create_kling_task,
        poll_kling_task=services.poll_kling_task,
        video_target_fps=services.video_target_fps,
        video_max_concurrency=services.video_max_concurrency,
    )
    _persist_job_result(result, audience=payload.get("audience"))
    return result


def finalize_download(req: Any) -> JSONResponse:
    services = _services()
    try:
        result = run_finalize_job(
            {"image_url": req.image_url},
            finalize_request_factory=services.finalize_request_factory,
            materialize_input=services.materialize_input,
            generate_empty_room=services.generate_empty_room,
            call_magnific_api=services.call_magnific_api,
            s3_prefix_from_url=services.s3_prefix_from_url,
            resolve_image_url=services.resolve_image_url,
        )
        status_code = 404 if result.get("error") == "Original file not found" else 200
        return JSONResponse(content=result, status_code=status_code)
    except Exception as exc:
        print(f"[Finalize Error] {exc}")
        traceback.print_exc()
        return JSONResponse(content={"error": str(exc)}, status_code=500)


def upscale_and_download(req: Any) -> JSONResponse:
    services = _services()
    try:
        result = run_upscale_job(
            {"image_url": req.image_url},
            upscale_request_factory=services.upscale_request_factory,
            materialize_input=services.materialize_input,
            call_magnific_api=services.call_magnific_api,
            s3_prefix_from_url=services.s3_prefix_from_url,
            resolve_image_url=services.resolve_image_url,
        )
        status_code = 404 if result.get("error") == "File not found" else 200
        return JSONResponse(content=result, status_code=status_code)
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)
