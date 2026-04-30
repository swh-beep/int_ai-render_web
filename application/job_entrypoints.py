from __future__ import annotations

import json
import os
import traceback
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


def job_render(payload: dict, persist_result: bool = True) -> dict:
    services = _services()
    return run_render_job(
        payload,
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
