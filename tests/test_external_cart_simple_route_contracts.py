import unittest
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from application import job_entrypoints


def _external_deps():
    def job_render_with_extra(payload):
        return {"render": {"result_url": "https://cdn.example/main.png"}, **(payload.get("extra") or {})}

    def require_role(request, roles, api_auth_disabled, internal_api_keys, external_api_keys):
        return None

    def enqueue_job(job_func, payload, queue_name=None, **kwargs):
        return SimpleNamespace(id="job-simple"), None

    return SimpleNamespace(
        redis_url="redis://example",
        local_inline_queue_enabled=False,
        rq_queue_render="render",
        api_auth_disabled=False,
        internal_api_keys=set(),
        external_api_keys={"external-key"},
        require_role=require_role,
        enqueue_job=enqueue_job,
        job_render_with_extra=job_render_with_extra,
        build_external_cart_job=lambda req, **kwargs: (
            {
                "render": {"audience": "external", "file_path": req.image_url},
                "extra": {
                    "cart_kept": [{"id": "chair-1", "category": "chair"}],
                    "cart_dropped": [{"id": "lamp-1", "drop_reason": "max_items_exceeded"}],
                },
            },
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
    )


class _FakeFinishedJob:
    def __init__(self, result):
        self.id = "job-simple"
        self.enqueued_at = None
        self.started_at = None
        self.ended_at = None
        self.result = result
        self.exc_info = None
        self.is_finished = True
        self.is_failed = False

    def get_status(self):
        return "finished"


class ExternalCartSimpleRouteContractsTests(unittest.TestCase):
    def test_route_surface_includes_external_cart_simple_endpoint(self):
        paths = {route.path for route in main.app.routes}
        self.assertIn("/api/external/render/cart-simple", paths)

    def test_external_cart_simple_route_response_shape_stays_stable(self):
        deps = _external_deps()

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/cart-simple",
                json={
                    "image_url": "https://example.com/room.png",
                    "items": [
                        {
                            "id": "chair-1",
                            "category": "chair",
                            "image_url": "https://example.com/chair.png",
                            "qty": 1,
                        }
                    ],
                },
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "job_id": "job-simple",
                "status": "queued",
                "cart_kept": [{"id": "chair-1", "category": "chair"}],
                "cart_dropped": [{"id": "lamp-1", "drop_reason": "max_items_exceeded"}],
            },
        )

    def test_external_cart_simple_queues_configured_wrapper(self):
        deps = _external_deps()
        enqueued = []

        def enqueue_job(job_func, payload, queue_name=None, **kwargs):
            enqueued.append((job_func, payload, queue_name))
            return SimpleNamespace(id="job-simple"), None

        deps.enqueue_job = enqueue_job

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/cart-simple",
                json={
                    "image_url": "https://example.com/room.png",
                    "items": [
                        {
                            "id": "chair-1",
                            "category": "chair",
                            "image_url": "https://example.com/chair.png",
                            "qty": 1,
                        }
                    ],
                },
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIs(enqueued[-1][0], deps.job_render_with_extra)
        self.assertEqual(enqueued[-1][2], "render")

    def test_job_render_with_extra_returns_render_and_cart_metadata_without_details(self):
        persisted = []

        with (
            patch.object(job_entrypoints, "_services", return_value=SimpleNamespace(normalize_audience=lambda audience: audience or "external")),
            patch.object(
                job_entrypoints,
                "job_render",
                return_value={
                    "original_url": "https://cdn.example/original.png",
                    "empty_room_url": "https://cdn.example/empty-room.png",
                    "result_url": "https://cdn.example/main.png",
                    "result_urls": ["https://cdn.example/main.png"],
                },
            ),
            patch.object(job_entrypoints, "_persist_job_result", side_effect=lambda payload, audience=None: persisted.append((payload, audience))),
        ):
            result = job_entrypoints.job_render_with_extra(
                {
                    "render": {"audience": "external", "file_path": "https://example.com/room.png"},
                    "extra": {
                        "cart_kept": [{"id": "chair-1", "category": "chair"}],
                        "cart_dropped": [],
                    },
                }
            )

        self.assertEqual(result["render"]["empty_room_url"], "https://cdn.example/empty-room.png")
        self.assertEqual(result["render"]["result_url"], "https://cdn.example/main.png")
        self.assertNotIn("original_url", result["render"])
        self.assertEqual(result["cart_kept"], [{"id": "chair-1", "category": "chair"}])
        self.assertNotIn("details", result)
        self.assertEqual(persisted[-1][1], "external")

    def test_job_render_preprocesses_deferred_cart_items_before_run_render_job(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local_src = os.path.join(tmpdir, "cart_item_0.png")
            normalized = os.path.join(tmpdir, "cart_item_processed.png")
            with open(local_src, "wb") as handle:
                handle.write(b"src")
            with open(normalized, "wb") as handle:
                handle.write(b"normalized")

            captured = {}

            def fake_run_render_job(payload, **kwargs):
                captured["payload"] = payload
                return {"result_url": "https://cdn.example/main.png"}

            with (
                patch.object(
                    job_entrypoints,
                    "_services",
                    return_value=SimpleNamespace(
                        materialize_input=lambda source_ref, prefix: local_src,
                        normalize_item_image=lambda local_path, unique_id, index: normalized,
                        resolve_image_url=lambda path, prefix=None: "https://cdn.example/cart_item_processed.png",
                        build_s3_prefix=lambda audience, category: f"{audience}/{category}/",
                        normalize_audience=lambda audience: audience or "external",
                        render_room=lambda **kwargs: None,
                    ),
                ),
                patch.object(job_entrypoints, "run_render_job", side_effect=fake_run_render_job),
            ):
                result = job_entrypoints.job_render(
                    {
                        "audience": "external",
                        "file_path": "https://example.com/room.png",
                        "moodboard_items": [
                            {
                                "label": "Chair",
                                "path": "https://example.com/chair.png",
                                "qty": 1,
                                "category": "decor",
                                "category_path": "Storage > Shelf",
                                "mainCategory": "Storage",
                                "subCategory": "Shelf",
                                "payload_index": 1,
                                "target_key": "cart_chair-1_001",
                                "worker_preprocess": "external_cart_item_v1",
                            }
                        ],
                    },
                    persist_result=False,
                )

        self.assertEqual(result["result_url"], "https://cdn.example/main.png")
        prepared_item = captured["payload"]["moodboard_items"][0]
        self.assertEqual(prepared_item["path"], "https://cdn.example/cart_item_processed.png")
        self.assertEqual(prepared_item["category"], "decor")
        self.assertEqual(prepared_item["category_path"], "Storage > Shelf")
        self.assertEqual(prepared_item["mainCategory"], "Storage")
        self.assertEqual(prepared_item["subCategory"], "Shelf")
        self.assertNotIn("worker_preprocess", prepared_item)
        self.assertFalse(os.path.exists(local_src))
        self.assertFalse(os.path.exists(normalized))

    def test_external_cart_simple_job_status_finished_payload_has_no_details(self):
        deps = _external_deps()
        deps.fetch_job = lambda job_id: _FakeFinishedJob(
            {
                "render": {
                    "empty_room_url": "https://cdn.example/empty-room.png",
                    "result_url": "https://cdn.example/main.png",
                    "result_urls": ["https://cdn.example/main.png"],
                },
                "cart_kept": [{"id": "chair-1", "category": "chair"}],
                "cart_dropped": [],
            }
        )
        deps.load_job_result_s3 = lambda job_id: None

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.get("/jobs/job-simple")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["render"]["empty_room_url"], "https://cdn.example/empty-room.png")
        self.assertEqual(body["result"]["render"]["result_url"], "https://cdn.example/main.png")
        self.assertEqual(body["result"]["cart_kept"][0]["id"], "chair-1")
        self.assertNotIn("details", body["result"])


if __name__ == "__main__":
    unittest.main()
