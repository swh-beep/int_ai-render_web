import json
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

import main


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
ASSET_DIR = BASE_DIR.parent / "localtest_image"
REPORT_PATH = BASE_DIR / "live_validation_report.json"
DEFAULT_ROOM_DIMENSIONS_TEXT = "10000 x 5500 x 3000 mm"


class ValidationError(RuntimeError):
    pass


class FakeJob:
    def __init__(self, job_id: str):
        self.id = job_id


def _assert(condition: bool, message: str):
    if not condition:
        raise ValidationError(message)


def _count_box_sources(items: list[dict] | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("box_source") or "missing")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _absolute_asset(name: str) -> str:
    path = ASSET_DIR / name
    _assert(path.exists(), f"Missing test asset: {path}")
    return str(path.resolve())


def _make_sync_enqueue(fake_results: dict):
    def _sync_enqueue(func, *args, queue_name=None, **kwargs):
        job_id = f"live-{uuid.uuid4().hex[:12]}"
        try:
            result = func(*args, **kwargs)
            fake_results[job_id] = {"status": "finished", "result": result}
        except Exception as exc:
            fake_results[job_id] = {"status": "failed", "error": str(exc)}
        return FakeJob(job_id), None

    return _sync_enqueue


def _queue_job_and_capture(client: TestClient, fake_results: dict, method: str, path: str, **kwargs) -> tuple[dict, dict]:
    response = client.request(method, path, **kwargs)
    _assert(response.status_code == 200, f"{method} {path} failed: {response.status_code} {response.text}")
    data = response.json()
    job_id = data.get("job_id")
    _assert(job_id, f"{method} {path} did not return job_id")
    result = fake_results.get(job_id)
    _assert(result is not None, f"No captured result for {method} {path}")
    _assert(result.get("status") == "finished", f"{method} {path} failed: {result}")
    return data, result


def _poll_video_job(client: TestClient, job_id: str, timeout_sec: int = 120) -> dict:
    deadline = time.time() + timeout_sec
    last_payload = None
    while time.time() < deadline:
        response = client.get(f"/video-mvp/status/{job_id}")
        _assert(response.status_code == 200, f"GET /video-mvp/status/{job_id} failed: {response.status_code} {response.text}")
        last_payload = response.json()
        status = (last_payload.get("status") or "").upper()
        if status == "COMPLETED":
            return last_payload
        if status == "FAILED":
            raise ValidationError(f"Video job {job_id} failed: {last_payload}")
        time.sleep(1)
    raise ValidationError(f"Video job {job_id} timed out: {last_payload}")


def main_validation(
    *,
    report_path: Path | None = None,
    room_dimensions_text: str | None = None,
):
    room_photo = _absolute_asset("room_photo.png")
    customize_moodboard = _absolute_asset("customize_moodboard.png")
    preset_product_1 = _absolute_asset("preset_product_1.png")
    preset_product_2 = _absolute_asset("preset_product_2.png")
    preset_product_3 = _absolute_asset("preset_product_3.png")
    preset_product_4 = _absolute_asset("preset_product_4.png")

    if not main.PRESET_MAP_PATH:
        main.PRESET_MAP_PATH = str((BASE_DIR / "preset_map.json").resolve())
        main.PRESET_MAP_CACHE = None

    external_api_key = os.getenv("EXTERNAL_INTEA_API_KEYS", "").split(",")[0].strip()
    _assert(external_api_key, "Missing external API key in environment")

    fake_results = {}
    original_enqueue = main._enqueue_job
    original_redis_url = main.REDIS_URL
    main._enqueue_job = _make_sync_enqueue(fake_results)
    main.REDIS_URL = "sync-validation"

    room_dimensions_text = (room_dimensions_text or DEFAULT_ROOM_DIMENSIONS_TEXT).strip()
    report = {
        "mode": "sync-enqueue-route-validation",
        "room_dimensions_text": room_dimensions_text,
        "assets": {
            "room_photo": room_photo,
            "customize_moodboard": customize_moodboard,
            "preset_product_1": preset_product_1,
            "preset_product_2": preset_product_2,
            "preset_product_3": preset_product_3,
            "preset_product_4": preset_product_4,
        },
        "results": {},
    }

    try:
        client = TestClient(main.app)

        with open(room_photo, "rb") as room_fp, open(customize_moodboard, "rb") as mood_fp:
            internal_main_response, internal_main_job = _queue_job_and_capture(
                client,
                fake_results,
                "POST",
                "/async/render",
                data={
                    "room": "livingroom",
                    "style": "Customize",
                    "variant": "1",
                    "dimensions": room_dimensions_text,
                    "placement": "Preserve architecture and use the provided customized moodboard.",
                    "audience": "internal",
                },
                files={
                    "file": ("room_photo.png", room_fp, "image/png"),
                    "moodboard": ("customize_moodboard.png", mood_fp, "image/png"),
                },
            )

        internal_main_result = internal_main_job.get("result") or {}
        _assert(internal_main_result.get("result_url"), "Internal main render missing result_url")
        _assert(isinstance(internal_main_result.get("result_urls"), list) and internal_main_result.get("result_urls"), "Internal main render missing result_urls")
        _assert(isinstance(internal_main_result.get("furniture_data"), list), "Internal main render missing furniture_data list")
        report["results"]["internal_main"] = {
            "enqueue_response": internal_main_response,
            "original_url": internal_main_result.get("original_url"),
            "empty_room_url": internal_main_result.get("empty_room_url"),
            "result_url": internal_main_result.get("result_url"),
            "result_urls": list(internal_main_result.get("result_urls") or []),
            "moodboard_url": internal_main_result.get("moodboard_url"),
            "scale_guide_url": internal_main_result.get("scale_guide_url"),
            "detail_ready_furniture_count": len(internal_main_result.get("furniture_data") or []),
        }

        internal_upscale_response, internal_upscale_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/async/upscale",
            json={"image_url": internal_main_result.get("result_url")},
        )
        internal_upscale_result = internal_upscale_job.get("result") or {}
        _assert(internal_upscale_result.get("upscaled_url"), "Internal upscale missing upscaled_url")
        report["results"]["internal_upscale"] = {
            "enqueue_response": internal_upscale_response,
            "upscaled_url": internal_upscale_result.get("upscaled_url"),
        }

        internal_finalize_response, internal_finalize_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/async/finalize-download",
            json={"image_url": internal_main_result.get("result_url")},
        )
        internal_finalize_result = internal_finalize_job.get("result") or {}
        _assert(internal_finalize_result.get("upscaled_furnished"), "Internal finalize missing upscaled_furnished")
        _assert(internal_finalize_result.get("upscaled_empty"), "Internal finalize missing upscaled_empty")
        report["results"]["internal_finalize"] = {
            "enqueue_response": internal_finalize_response,
            "upscaled_furnished": internal_finalize_result.get("upscaled_furnished"),
            "upscaled_empty": internal_finalize_result.get("upscaled_empty"),
        }

        internal_empty_room_response, internal_empty_room_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/async/generate-empty-room",
            json={"image_url": room_photo},
        )
        internal_empty_room_result = internal_empty_room_job.get("result") or {}
        _assert(internal_empty_room_result.get("empty_room_url"), "Internal empty-room render missing empty_room_url")
        report["results"]["internal_empty_room"] = {
            "enqueue_response": internal_empty_room_response,
            "empty_room_url": internal_empty_room_result.get("empty_room_url"),
        }

        with open(room_photo, "rb") as frontal_fp:
            internal_frontal_response, internal_frontal_job = _queue_job_and_capture(
                client,
                fake_results,
                "POST",
                "/async/generate-frontal-view",
                files=[("input_photos", ("room_photo.png", frontal_fp, "image/png"))],
            )
        internal_frontal_result = internal_frontal_job.get("result") or {}
        internal_frontal_urls = internal_frontal_result.get("urls") or []
        _assert(internal_frontal_urls, "Internal frontal-view render missing urls")
        report["results"]["internal_frontal_view"] = {
            "enqueue_response": internal_frontal_response,
            "urls": internal_frontal_urls,
            "first_url": internal_frontal_urls[0],
        }

        with open(room_photo, "rb") as edit_fp:
            internal_image_edit_response, internal_image_edit_job = _queue_job_and_capture(
                client,
                fake_results,
                "POST",
                "/async/generate-image-edit",
                data={
                    "instructions": "Remove small visual distractions and keep the room realistic.",
                    "mode": "edit",
                },
                files=[("input_photos", ("room_photo.png", edit_fp, "image/png"))],
            )
        internal_image_edit_result = internal_image_edit_job.get("result") or {}
        internal_image_edit_urls = internal_image_edit_result.get("urls") or []
        _assert(internal_image_edit_urls, "Internal image-edit render missing urls")
        report["results"]["internal_image_edit"] = {
            "enqueue_response": internal_image_edit_response,
            "urls": internal_image_edit_urls,
            "first_url": internal_image_edit_urls[0],
        }

        with open(room_photo, "rb") as moodboard_fp:
            moodboard_response = client.post(
                "/generate-moodboard-options",
                data={"audience": "internal"},
                files={"file": ("room_photo.png", moodboard_fp, "image/png")},
            )
        _assert(
            moodboard_response.status_code == 200,
            f"POST /generate-moodboard-options failed: {moodboard_response.status_code} {moodboard_response.text}",
        )
        moodboard_result = moodboard_response.json()
        moodboard_urls = moodboard_result.get("moodboards") or []
        _assert(moodboard_urls, "Moodboard generation did not return moodboards")
        report["results"]["internal_moodboard_options"] = {
            "first_url": moodboard_urls[0],
            "urls": moodboard_urls,
            "count": len(moodboard_urls),
        }

        video_sources_response = client.post(
            "/video-mvp/generate-sources",
            json={
                "items": [
                    {
                        "url": internal_main_result.get("result_url"),
                        "motion": "static",
                        "effect": "none",
                    }
                ],
                "cfg_scale": 0.5,
            },
        )
        _assert(
            video_sources_response.status_code == 200,
            f"POST /video-mvp/generate-sources failed: {video_sources_response.status_code} {video_sources_response.text}",
        )
        video_sources_job_id = video_sources_response.json().get("job_id")
        _assert(video_sources_job_id, "POST /video-mvp/generate-sources did not return job_id")
        video_sources_status = _poll_video_job(client, video_sources_job_id)
        source_results = video_sources_status.get("results") or []
        _assert(source_results and source_results[0], "Video source generation missing result clip")
        report["results"]["internal_video_sources"] = {
            "job_id": video_sources_job_id,
            "first_result_url": source_results[0],
            "status": video_sources_status.get("status"),
        }

        original_create_kling = main._freepik_kling_create_task
        original_poll_kling = main._freepik_kling_poll
        try:
            main._freepik_kling_create_task = lambda image_b64, prompt, negative_prompt, duration, cfg_scale: "validation-task"
            main._freepik_kling_poll = (
                lambda task_id, *, clip_index, total_clips, update_job_status, timeout_sec=600: source_results[0]
            )
            video_sources_dynamic_response = client.post(
                "/video-mvp/generate-sources",
                json={
                    "items": [
                        {
                            "url": internal_main_result.get("result_url"),
                            "motion": "orbit_r_slow",
                            "effect": "sunlight",
                        }
                    ],
                    "cfg_scale": 0.5,
                },
            )
            _assert(
                video_sources_dynamic_response.status_code == 200,
                f"POST /video-mvp/generate-sources (dynamic) failed: {video_sources_dynamic_response.status_code} {video_sources_dynamic_response.text}",
            )
            video_sources_dynamic_job_id = video_sources_dynamic_response.json().get("job_id")
            _assert(video_sources_dynamic_job_id, "Dynamic video source generation did not return job_id")
            video_sources_dynamic_status = _poll_video_job(client, video_sources_dynamic_job_id)
            dynamic_results = video_sources_dynamic_status.get("results") or []
            _assert(dynamic_results and dynamic_results[0], "Dynamic video source generation missing result clip")
            report["results"]["internal_video_sources_dynamic"] = {
                "job_id": video_sources_dynamic_job_id,
                "first_result_url": dynamic_results[0],
                "status": video_sources_dynamic_status.get("status"),
            }
        finally:
            main._freepik_kling_create_task = original_create_kling
            main._freepik_kling_poll = original_poll_kling

        video_compile_response = client.post(
            "/video-mvp/compile",
            json={
                "clips": [
                    {
                        "video_url": source_results[0],
                        "speed": 1.0,
                        "trim_start": 0.0,
                        "trim_end": 5.0,
                    }
                ],
                "include_intro_outro": False,
            },
        )
        _assert(
            video_compile_response.status_code == 200,
            f"POST /video-mvp/compile failed: {video_compile_response.status_code} {video_compile_response.text}",
        )
        video_compile_job_id = video_compile_response.json().get("job_id")
        _assert(video_compile_job_id, "POST /video-mvp/compile did not return job_id")
        video_compile_status = _poll_video_job(client, video_compile_job_id)
        _assert(video_compile_status.get("result_url"), "Video compile missing result_url")
        report["results"]["internal_video_compile"] = {
            "job_id": video_compile_job_id,
            "result_url": video_compile_status.get("result_url"),
            "status": video_compile_status.get("status"),
        }

        internal_detail_response, internal_detail_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/generate-details",
            json={
                "image_url": internal_main_result.get("result_url"),
                "moodboard_url": internal_main_result.get("moodboard_url"),
                "furniture_data": internal_main_result.get("furniture_data"),
                "audience": "internal",
            },
        )
        internal_detail_result = internal_detail_job.get("result") or {}
        internal_details = internal_detail_result.get("details") or []
        _assert(len(internal_details) > 1, "Internal detail render did not return multiple detail images")
        report["results"]["internal_detail"] = {
            "enqueue_response": internal_detail_response,
            "detail_count": len(internal_details),
            "first_detail_url": internal_details[0].get("url") if internal_details else None,
            "detail_urls": [item.get("url") for item in internal_details if isinstance(item, dict) and item.get("url")],
        }

        regenerate_detail_response, regenerate_detail_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/regenerate-single-detail",
            json={
                "original_image_url": internal_main_result.get("result_url"),
                "style_index": 1,
                "style_index_mode": "detail",
                "furniture_data": internal_main_result.get("furniture_data"),
                "audience": "internal",
            },
        )
        regenerate_detail_result = regenerate_detail_job.get("result") or {}
        _assert(regenerate_detail_result.get("url"), "Regenerate single detail missing url")
        report["results"]["internal_regenerate_detail"] = {
            "enqueue_response": regenerate_detail_response,
            "url": regenerate_detail_result.get("url"),
            "resolved_by": regenerate_detail_result.get("resolved_by"),
            "resolved_style_index": regenerate_detail_result.get("resolved_style_index"),
        }

        external_headers = {"x-api-key": external_api_key}
        external_preset_response, external_preset_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/api/external/render/preset",
            headers=external_headers,
            json={
                "image_url": room_photo,
                "preset_id": "livingroom_french-modern_1",
                "dimensions": room_dimensions_text,
            },
        )
        external_preset_result = external_preset_job.get("result") or {}
        preset_render = external_preset_result.get("render") or {}
        preset_detail_payload = external_preset_result.get("details") or {}
        preset_details = preset_detail_payload.get("details") or []
        preset_furniture = preset_render.get("furniture_data") or []
        preset_volume_ranking = preset_render.get("volume_ranking") or []
        preset_furniture_boxes = preset_detail_payload.get("furniture_boxes") or []
        preset_detail_volume_ranking = preset_detail_payload.get("volume_ranking") or []
        preset_main_render_count = sum(1 for item in preset_furniture if isinstance(item, dict) and item.get("box_source") == "main_render")
        _assert(preset_render.get("result_url"), "External preset render missing result_url")
        _assert(isinstance(preset_furniture, list) and preset_furniture, "External preset render missing furniture_data")
        _assert(preset_main_render_count > 0, "External preset render missing main_render box remap evidence")
        _assert(isinstance(preset_volume_ranking, list) and preset_volume_ranking, "External preset render missing volume_ranking")
        _assert(len(preset_details) > 0, "External preset render missing detail results")
        _assert(len(preset_details) <= 9, f"External preset detail count exceeded cap: {len(preset_details)}")
        _assert(isinstance(preset_furniture_boxes, list) and preset_furniture_boxes, "External preset details missing furniture_boxes")
        _assert(isinstance(preset_detail_volume_ranking, list) and preset_detail_volume_ranking, "External preset details missing volume_ranking")
        report["results"]["external_preset"] = {
            "enqueue_response": external_preset_response,
            "result_url": preset_render.get("result_url"),
            "result_urls": list(preset_render.get("result_urls") or []),
            "empty_room_url": preset_render.get("empty_room_url"),
            "scale_guide_url": preset_render.get("scale_guide_url"),
            "detail_count": len(preset_details),
            "detail_urls": [item.get("url") for item in preset_details if isinstance(item, dict) and item.get("url")],
            "resolved": external_preset_result.get("resolved"),
            "main_render_box_count": preset_main_render_count,
            "render_box_sources": _count_box_sources(preset_furniture),
            "detail_box_sources": _count_box_sources(preset_furniture_boxes),
            "volume_ranking_count": len(preset_volume_ranking),
        }

        external_cart_response, external_cart_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/api/external/render/cart",
            headers=external_headers,
            json={
                "image_url": room_photo,
                "room": "livingroom",
                "style": "French Modern",
                "variant": "1",
                "dimensions": room_dimensions_text,
                "items": [
                    {
                        "id": "product-1",
                        "name": "Product 1",
                        "category": "chair",
                        "image_url": preset_product_1,
                        "qty": 1,
                        "dims_mm": {"width_mm": 600, "depth_mm": 660, "height_mm": 660},
                    },
                    {
                        "id": "product-2",
                        "name": "Product 2",
                        "category": "chair",
                        "image_url": preset_product_2,
                        "qty": 1,
                        "dims_mm": {"width_mm": 600, "depth_mm": 660, "height_mm": 660},
                    },
                    {
                        "id": "product-3",
                        "name": "Product 3",
                        "category": "chair",
                        "image_url": preset_product_3,
                        "qty": 1,
                        "dims_mm": {"width_mm": 620, "depth_mm": 505, "height_mm": 800},
                    },
                    {
                        "id": "product-4",
                        "name": "Product 4",
                        "category": "sofa",
                        "image_url": preset_product_4,
                        "qty": 1,
                        "dims_mm": {"width_mm": 2790, "depth_mm": 1070, "height_mm": 700},
                    },
                ],
            },
        )
        external_cart_result = external_cart_job.get("result") or {}
        cart_render = external_cart_result.get("render") or {}
        cart_detail_payload = external_cart_result.get("details") or {}
        cart_details = cart_detail_payload.get("details") or []
        cart_furniture = cart_render.get("furniture_data") or []
        cart_used_cutout_references = cart_detail_payload.get("used_cutout_references") or []
        cart_volume_ranking = cart_detail_payload.get("volume_ranking") or []
        cart_detail_targeted = [
            item for item in cart_details
            if isinstance(item, dict) and item.get("target_box_2d") and item.get("target_box_source")
        ]
        cart_cutout_cart_keys = [
            str(item.get("target_key") or "")
            for item in cart_used_cutout_references
            if isinstance(item, dict)
        ]
        _assert(cart_render.get("result_url"), "External cart render missing result_url")
        _assert(len(cart_details) > 0, "External cart render missing detail results")
        _assert(len(cart_details) <= 9, f"External cart detail count exceeded cap: {len(cart_details)}")
        _assert(isinstance(cart_furniture, list) and cart_furniture, "External cart render missing furniture_data")
        _assert(isinstance(cart_used_cutout_references, list) and cart_used_cutout_references, "External cart details missing used_cutout_references")
        _assert(any(key.startswith("cart_") for key in cart_cutout_cart_keys), "External cart used_cutout_references missing cart target keys")
        _assert(len(cart_detail_targeted) > 0, "External cart details missing target box metadata")
        _assert(isinstance(cart_volume_ranking, list) and cart_volume_ranking, "External cart details missing volume_ranking")
        report["results"]["external_cart"] = {
            "enqueue_response": external_cart_response,
            "result_url": cart_render.get("result_url"),
            "result_urls": list(cart_render.get("result_urls") or []),
            "empty_room_url": cart_render.get("empty_room_url"),
            "scale_guide_url": cart_render.get("scale_guide_url"),
            "detail_count": len(cart_details),
            "detail_urls": [item.get("url") for item in cart_details if isinstance(item, dict) and item.get("url")],
            "cart_kept_count": len(external_cart_result.get("cart_kept") or []),
            "render_box_sources": _count_box_sources(cart_furniture),
            "used_cutout_reference_count": len(cart_used_cutout_references),
            "used_cutout_target_keys": cart_cutout_cart_keys,
            "targeted_detail_count": len(cart_detail_targeted),
            "volume_ranking_count": len(cart_volume_ranking),
        }

        target_report_path = report_path or REPORT_PATH
        target_report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report
    finally:
        main._enqueue_job = original_enqueue
        main.REDIS_URL = original_redis_url


if __name__ == "__main__":
    main_validation()
