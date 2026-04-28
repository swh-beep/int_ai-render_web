import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime, timezone

from fastapi.testclient import TestClient

import main
from application.render.render_audience_stage import run_render_audience_stage


def _external_deps():
    def require_role(request, roles, api_auth_disabled, internal_api_keys, external_api_keys):
        return None

    def enqueue_job(job_func, payload, queue_name=None, **kwargs):
        return SimpleNamespace(id="job-xyz"), None

    return SimpleNamespace(
        redis_url="redis://example",
        local_inline_queue_enabled=False,
        rq_queue_render="render",
        api_auth_disabled=False,
        internal_api_keys=set(),
        external_api_keys={"external-key"},
        require_role=require_role,
        enqueue_job=enqueue_job,
        load_preset_map=lambda: {"preset-1": {"room": "livingroom", "style": "natural", "variant": "2"}},
        build_external_preset_job=lambda req, preset_map: (
            {"render": {"audience": "external", "image_url": req.image_url}},
            {"room": "livingroom", "style": "natural", "variant": "2"},
        ),
        build_external_cart_job=lambda req, **kwargs: (
            {"render": {"audience": "external", "image_url": req.image_url}},
            [{"id": "chair-1", "category": "chair"}],
            [{"id": "lamp-1", "drop_reason": "max_items_exceeded"}],
        ),
        cart_max_items=8,
        apply_cart_limits=lambda items, limit: (items, []),
        build_cart_summary=lambda items: "summary",
        materialize_input=lambda url, prefix: f"C:/tmp/{prefix}.png",
        normalize_item_image=lambda local_path, unique_id, index: f"C:/tmp/norm-{index}.png",
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{path}",
        build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
        build_internal_render_job_payload=lambda req: {"render": {"audience": "internal", "image_url": req.image_url}},
        job_render_with_details=lambda payload: payload,
        build_external_render_video_job=lambda req: {
            "render_job_id": req.render_job_id,
            "clip_count": req.clip_count,
            "audience": "external",
        },
        job_generate_render_video=lambda payload: payload,
        fetch_job=lambda job_id: None,
        load_job_result_s3=lambda job_id: None,
        rq_video_job_timeout=3600,
    )


class _FakeFinishedJob:
    def __init__(self, result, *, status: str = "finished", is_failed: bool = False, exc_info: str | None = None):
        now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.id = "job-xyz"
        self.enqueued_at = now
        self.started_at = now
        self.ended_at = now
        self.result = result
        self.exc_info = exc_info
        self.is_finished = not is_failed
        self.is_failed = is_failed
        self._status = status

    def get_status(self):
        return self._status


def _external_finished_result_payload():
    return {
        "render": {
            "result_url": "https://cdn.example/render.png",
            "result_urls": ["https://cdn.example/render.png"],
            "candidate_result_urls": ["https://cdn.example/render_alt.png"],
            "selected_result_reason": "internal_debug_only",
            "selected_variant_review": {"review_pass": False},
            "variant_diagnostics": [{"variant_index": 0}],
            "room_dims_contract": {"source": "explicit"},
            "geometry_contract": {"strict_scale_ready": True},
            "scene_contract": {"geometry_source": "explicit"},
            "placement_plan": {"anchor_item_key": "sofa-1"},
            "scale_plan": {"strict_scale_requested": True},
            "final_result_blocked": True,
        },
        "details": {
            "items": [],
            "selected_item_review": [{"target_key": "sofa-1"}],
        },
        "resolved": {"room": "livingroom", "style": "natural", "variant": "2"},
    }


def _external_cart_finished_result_payload():
    payload = _external_finished_result_payload()
    payload.pop("resolved", None)
    payload["cart_kept"] = [{"id": "chair-1", "category": "chair"}]
    payload["cart_dropped"] = [{"id": "lamp-1", "drop_reason": "max_items_exceeded"}]
    return payload


class ExternalRouteContractsTests(unittest.TestCase):
    def test_external_audience_must_keep_scale_check_disabled(self):
        result = run_render_audience_stage(
            audience=None,
            normalize_audience=lambda aud: "external" if aud is None else aud,
            build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
        )

        self.assertEqual(result.audience, "external")
        self.assertFalse(result.enable_scale_check)

    def test_external_preset_route_response_shape_stays_stable(self):
        deps = _external_deps()

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/preset",
                json={
                    "image_url": "https://example.com/room.png",
                    "preset_id": "preset-1",
                    "dimensions": "5000 x 4000 x 2600",
                    "placement": "keep the window clear",
                },
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "job_id": "job-xyz",
                "status": "queued",
                "resolved": {
                    "room": "livingroom",
                    "style": "natural",
                    "variant": "2",
                },
            },
        )

    def test_external_cart_route_response_shape_stays_stable(self):
        deps = _external_deps()

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/cart",
                json={
                    "image_url": "https://example.com/room.png",
                    "items": [
                        {
                            "id": "chair-1",
                            "category": "chair",
                            "image_url": "https://example.com/chair.png",
                            "qty": 1,
                            "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 900},
                        }
                    ],
                    "room": "livingroom",
                    "style": "warm modern",
                    "dimensions": "5000 x 4000 x 2600",
                    "placement": "keep the chair near the window",
                },
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "job_id": "job-xyz",
                "status": "queued",
                "cart_kept": [{"id": "chair-1", "category": "chair"}],
                "cart_dropped": [{"id": "lamp-1", "drop_reason": "max_items_exceeded"}],
            },
        )

    def test_external_render_video_route_response_shape_stays_stable(self):
        deps = _external_deps()
        deps.fetch_job = lambda job_id: _FakeFinishedJob(_external_finished_result_payload())
        deps.enqueue_job = lambda job_func, payload, queue_name=None, **kwargs: (SimpleNamespace(id="job-video"), None)

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/video",
                json={"render_job_id": "job-xyz", "clip_count": 4},
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "job_id": "job-video",
                "status": "queued",
                "render_job_id": "job-xyz",
                "clip_count": 4,
            },
        )

    def test_external_render_video_route_rejects_internal_source_job(self):
        deps = _external_deps()
        deps.fetch_job = lambda job_id: _FakeFinishedJob({"render": {"result_url": "https://cdn.example/internal.png"}})

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/video",
                json={"render_job_id": "job-internal", "clip_count": 4},
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "render_job_id must belong to an external render job"})

    def test_external_render_video_route_rejects_failed_external_source_job(self):
        deps = _external_deps()
        deps.fetch_job = lambda job_id: _FakeFinishedJob(
            {
                "error": "upstream render failed",
                "render": {"error": "upstream render failed"},
                "resolved": {"room": "livingroom", "style": "natural", "variant": "2"},
            }
        )

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/video",
                json={"render_job_id": "job-failed", "clip_count": 4},
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"detail": "render_job_id does not have usable image results"})

    def test_external_preset_job_status_finished_payload_stays_stable(self):
        deps = _external_deps()
        deps.fetch_job = lambda job_id: _FakeFinishedJob(_external_finished_result_payload())
        deps.load_job_result_s3 = lambda job_id: None

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.get("/jobs/job-xyz")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "finished")
        self.assertEqual(body["result"]["resolved"]["room"], "livingroom")
        self.assertEqual(body["result"]["render"]["selected_result_reason"], "internal_debug_only")
        self.assertFalse(body["result"]["render"]["selected_variant_review"]["review_pass"])
        self.assertEqual(body["result"]["render"]["candidate_result_urls"], ["https://cdn.example/render_alt.png"])
        self.assertTrue(body["result"]["render"]["final_result_blocked"])
        self.assertEqual(body["result"]["render"]["room_dims_contract"]["source"], "explicit")
        self.assertTrue(body["result"]["render"]["geometry_contract"]["strict_scale_ready"])
        self.assertEqual(body["result"]["render"]["scene_contract"]["geometry_source"], "explicit")
        self.assertEqual(body["result"]["render"]["placement_plan"]["anchor_item_key"], "sofa-1")
        self.assertTrue(body["result"]["render"]["scale_plan"]["strict_scale_requested"])
        self.assertEqual(body["result"]["details"]["selected_item_review"][0]["target_key"], "sofa-1")

    def test_external_preset_job_status_finished_payload_from_s3_stays_stable(self):
        deps = _external_deps()
        deps.fetch_job = lambda job_id: None
        deps.load_job_result_s3 = lambda job_id: _external_finished_result_payload()

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.get("/jobs/job-xyz")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result_source"], "s3")
        self.assertEqual(body["result"]["render"]["selected_result_reason"], "internal_debug_only")
        self.assertEqual(body["result"]["render"]["candidate_result_urls"], ["https://cdn.example/render_alt.png"])
        self.assertTrue(body["result"]["render"]["final_result_blocked"])
        self.assertEqual(body["result"]["render"]["room_dims_contract"]["source"], "explicit")
        self.assertTrue(body["result"]["render"]["geometry_contract"]["strict_scale_ready"])
        self.assertEqual(body["result"]["details"]["selected_item_review"][0]["target_key"], "sofa-1")

    def test_external_cart_job_status_finished_payload_stays_stable(self):
        deps = _external_deps()
        deps.fetch_job = lambda job_id: _FakeFinishedJob(_external_cart_finished_result_payload())
        deps.load_job_result_s3 = lambda job_id: None

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.get("/jobs/job-xyz")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["cart_kept"][0]["id"], "chair-1")
        self.assertEqual(body["result"]["render"]["selected_result_reason"], "internal_debug_only")
        self.assertEqual(body["result"]["render"]["candidate_result_urls"], ["https://cdn.example/render_alt.png"])
        self.assertTrue(body["result"]["render"]["final_result_blocked"])
        self.assertTrue(body["result"]["render"]["geometry_contract"]["strict_scale_ready"])
        self.assertEqual(body["result"]["render"]["scene_contract"]["geometry_source"], "explicit")
        self.assertEqual(body["result"]["details"]["selected_item_review"][0]["target_key"], "sofa-1")

    def test_external_cart_job_status_finished_payload_from_s3_stays_stable(self):
        deps = _external_deps()
        deps.fetch_job = lambda job_id: None
        deps.load_job_result_s3 = lambda job_id: _external_cart_finished_result_payload()

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.get("/jobs/job-xyz")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result_source"], "s3")
        self.assertEqual(body["result"]["render"]["selected_result_reason"], "internal_debug_only")
        self.assertEqual(body["result"]["render"]["candidate_result_urls"], ["https://cdn.example/render_alt.png"])
        self.assertTrue(body["result"]["render"]["final_result_blocked"])
        self.assertTrue(body["result"]["render"]["geometry_contract"]["strict_scale_ready"])
        self.assertEqual(body["result"]["render"]["placement_plan"]["anchor_item_key"], "sofa-1")
        self.assertEqual(body["result"]["details"]["selected_item_review"][0]["target_key"], "sofa-1")


if __name__ == "__main__":
    unittest.main()
