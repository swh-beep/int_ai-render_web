import unittest
import os
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from application import job_entrypoints


def _external_deps():
    def job_render_with_extra(payload):
        return {"render": {"result_url": "https://cdn.example/main.png"}, **(payload.get("extra") or {})}

    def job_render_cart_simple_batch(payload):
        return {
            "empty_room_url": "https://cdn.example/empty-room.png",
            "results": [
                {
                    "variant_index": 1,
                    "render": {"result_url": "https://cdn.example/main-1.png"},
                    "cart_kept": [{"id": "chair-1", "category": "chair"}],
                    "cart_dropped": [],
                }
            ],
        }

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
        job_render_cart_simple_batch=job_render_cart_simple_batch,
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
        build_external_cart_batch_job=lambda req, **kwargs: (
            {
                "audience": "external",
                "image_url": req.image_url,
                "variants": [
                    {
                        "variant_index": 1,
                        "render": {"audience": "external", "file_path": req.image_url},
                        "extra": {"cart_kept": [{"id": "chair-1", "category": "chair"}], "cart_dropped": []},
                    }
                ],
            },
            [{"variant_index": 1, "cart_kept": [{"id": "chair-1", "category": "chair"}], "cart_dropped": []}],
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

    def test_route_surface_includes_external_cart_simple_batch_endpoint(self):
        paths = {route.path for route in main.app.routes}
        self.assertIn("/api/external/render/cart-simple-batch", paths)

    def test_external_cart_simple_batch_route_queues_batch_wrapper(self):
        deps = _external_deps()
        enqueued = []

        def enqueue_job(job_func, payload, queue_name=None, **kwargs):
            enqueued.append((job_func, payload, queue_name))
            return SimpleNamespace(id="job-batch"), None

        deps.enqueue_job = enqueue_job

        with patch.object(main, "_queue_route_deps", return_value=deps):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/cart-simple-batch",
                json={
                    "image_url": "https://example.com/room.png",
                    "variants": [
                        {
                            "items": [
                                {
                                    "id": "chair-1",
                                    "category": "chair",
                                    "image_url": "https://example.com/chair.png",
                                    "qty": 1,
                                }
                            ]
                        }
                    ],
                },
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "job_id": "job-batch",
                "status": "queued",
                "variants": [
                    {
                        "variant_index": 1,
                        "cart_kept": [{"id": "chair-1", "category": "chair"}],
                        "cart_dropped": [],
                    }
                ],
            },
        )
        self.assertIs(enqueued[-1][0], deps.job_render_cart_simple_batch)
        self.assertEqual(enqueued[-1][2], "render")

    def test_external_cart_simple_batch_setup_failure_response_hides_internal_text(self):
        deps = _external_deps()
        secret_text = "db password=super-secret traceback marker"

        def build_external_cart_batch_job(req, **kwargs):
            raise RuntimeError(secret_text)

        deps.build_external_cart_batch_job = build_external_cart_batch_job

        with (
            patch.object(main, "_queue_route_deps", return_value=deps),
            self.assertLogs("application.http.queue_route_handlers", level="ERROR") as logs,
        ):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/cart-simple-batch",
                json={
                    "image_url": "https://example.com/room.png",
                    "variants": [
                        {
                            "items": [
                                {
                                    "id": "chair-1",
                                    "category": "chair",
                                    "image_url": "https://example.com/chair.png",
                                    "qty": 1,
                                }
                            ]
                        }
                    ],
                },
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {
                "error": "external_cart_simple_batch_setup_failed",
                "message": "Unable to prepare cart-simple-batch render request",
            },
        )
        self.assertNotIn("super-secret", response.text)
        self.assertNotIn("traceback", response.text.lower())
        self.assertIn(secret_text, "\n".join(logs.output))

    def test_external_cart_simple_batch_enqueue_failure_response_hides_internal_text(self):
        deps = _external_deps()
        secret_text = "redis://:super-secret@example internal queue refused"

        def enqueue_job(job_func, payload, queue_name=None, **kwargs):
            return None, secret_text

        deps.enqueue_job = enqueue_job

        with (
            patch.object(main, "_queue_route_deps", return_value=deps),
            self.assertLogs("application.http.queue_route_handlers", level="ERROR") as logs,
        ):
            client = TestClient(main.app)
            response = client.post(
                "/api/external/render/cart-simple-batch",
                json={
                    "image_url": "https://example.com/room.png",
                    "variants": [
                        {
                            "items": [
                                {
                                    "id": "chair-1",
                                    "category": "chair",
                                    "image_url": "https://example.com/chair.png",
                                    "qty": 1,
                                }
                            ]
                        }
                    ],
                },
                headers={"x-api-key": "external-key"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json(),
            {
                "error": "external_cart_simple_batch_enqueue_failed",
                "message": "Unable to enqueue cart-simple-batch render request",
            },
        )
        self.assertNotIn("super-secret", response.text)
        self.assertNotIn("redis://", response.text)
        self.assertIn(secret_text, "\n".join(logs.output))

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

    def test_job_render_cart_simple_batch_generates_empty_once_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, "room.png")
            std_path = os.path.join(tmpdir, "room-std.png")
            empty_path = os.path.join(tmpdir, "empty.png")
            empty_raw_path = os.path.join(tmpdir, "empty-raw.png")
            for path in (source_path, std_path, empty_path, empty_raw_path):
                with open(path, "wb") as handle:
                    handle.write(b"img")

            empty_calls = []
            render_calls = []
            persisted = []

            def fake_generate_empty_room(path, unique_id, start_time, **kwargs):
                empty_calls.append((path, unique_id, kwargs))
                return empty_path, empty_raw_path

            def fake_job_render(payload, persist_result=True):
                render_calls.append(dict(payload))
                variant_index = payload["batch_variant_index"]
                return {
                    "original_url": f"https://cdn.example/original-{variant_index}.png",
                    "empty_room_url": "https://cdn.example/external/mainrendered/empty/empty.png",
                    "result_url": f"https://cdn.example/main-{variant_index}.png",
                    "result_urls": [f"https://cdn.example/main-{variant_index}.png"],
                }

            with (
                patch.object(
                    job_entrypoints,
                    "_services",
                    return_value=SimpleNamespace(
                        normalize_audience=lambda audience: audience or "external",
                        materialize_input=lambda source_ref, prefix: source_path,
                        standardize_image=lambda path: std_path,
                        generate_empty_room=fake_generate_empty_room,
                        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{prefix}/{os.path.basename(path)}",
                        build_s3_prefix=lambda audience, category, subfolder=None: "/".join(
                            [part for part in [audience, category, subfolder] if part]
                        ),
                    ),
                ),
                patch.object(job_entrypoints, "job_render", side_effect=fake_job_render),
                patch.object(
                    job_entrypoints,
                    "_persist_job_result",
                    side_effect=lambda payload, audience=None: persisted.append((payload, audience)),
                ),
            ):
                result = job_entrypoints.job_render_cart_simple_batch(
                    {
                        "audience": "external",
                        "image_url": "https://example.com/room.png",
                        "variants": [
                            {
                                "variant_index": 1,
                                "render": {"audience": "external", "file_path": "https://example.com/room.png"},
                                "extra": {"cart_kept": [{"id": "chair-1"}], "cart_dropped": []},
                            },
                            {
                                "variant_index": 2,
                                "render": {"audience": "external", "file_path": "https://example.com/room.png"},
                                "extra": {"cart_kept": [{"id": "sofa-1"}], "cart_dropped": []},
                            },
                        ],
                    }
                )

        self.assertEqual(len(empty_calls), 1)
        self.assertEqual(len(render_calls), 2)
        self.assertEqual({call["precomputed_empty_room_path"] for call in render_calls}, {empty_path})
        self.assertEqual({call["precomputed_empty_room_raw_path"] for call in render_calls}, {empty_raw_path})
        self.assertEqual(result["empty_room_url"], "https://cdn.example/external/mainrendered/empty/empty.png")
        self.assertEqual([row["variant_index"] for row in result["results"]], [1, 2])
        self.assertNotIn("original_url", result["results"][0]["render"])
        self.assertEqual(persisted[-1][1], "external")

    def test_job_render_cart_simple_batch_runs_variants_concurrently_and_preserves_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, "room.png")
            std_path = os.path.join(tmpdir, "room-std.png")
            empty_path = os.path.join(tmpdir, "empty.png")
            empty_raw_path = os.path.join(tmpdir, "empty-raw.png")
            for path in (source_path, std_path, empty_path, empty_raw_path):
                with open(path, "wb") as handle:
                    handle.write(b"img")

            lock = threading.Lock()
            all_started = threading.Event()
            active_calls = 0
            max_active_calls = 0
            completion_order = []

            def fake_job_render(payload, persist_result=True):
                nonlocal active_calls, max_active_calls
                variant_index = payload["batch_variant_index"]
                with lock:
                    active_calls += 1
                    max_active_calls = max(max_active_calls, active_calls)
                    if active_calls == 3:
                        all_started.set()

                all_started.wait(timeout=1.0)
                threading.Event().wait({1: 0.06, 2: 0.03, 3: 0.0}[variant_index])

                with lock:
                    completion_order.append(variant_index)
                    active_calls -= 1

                return {
                    "original_url": f"https://cdn.example/original-{variant_index}.png",
                    "result_url": f"https://cdn.example/main-{variant_index}.png",
                    "result_urls": [f"https://cdn.example/main-{variant_index}.png"],
                }

            with (
                patch.object(
                    job_entrypoints,
                    "_services",
                    return_value=SimpleNamespace(
                        normalize_audience=lambda audience: audience or "external",
                        materialize_input=lambda source_ref, prefix: source_path,
                        standardize_image=lambda path: std_path,
                        generate_empty_room=lambda *args, **kwargs: (empty_path, empty_raw_path),
                        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{prefix}/{os.path.basename(path)}",
                        build_s3_prefix=lambda audience, category, subfolder=None: "/".join(
                            [part for part in [audience, category, subfolder] if part]
                        ),
                    ),
                ),
                patch.object(job_entrypoints, "job_render", side_effect=fake_job_render),
                patch.object(job_entrypoints, "_persist_job_result"),
                patch.object(job_entrypoints, "CART_SIMPLE_BATCH_MAX_WORKERS", 3),
            ):
                result = job_entrypoints.job_render_cart_simple_batch(
                    {
                        "audience": "external",
                        "image_url": "https://example.com/room.png",
                        "variants": [
                            {"variant_index": index, "render": {"file_path": "https://example.com/room.png"}}
                            for index in range(1, 4)
                        ],
                    }
                )

        self.assertEqual(max_active_calls, 3)
        self.assertEqual(completion_order, [3, 2, 1])
        self.assertEqual([row["variant_index"] for row in result["results"]], [1, 2, 3])

    def test_job_render_cart_simple_batch_passes_artifact_context_to_variants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, "room.png")
            std_path = os.path.join(tmpdir, "room-std.png")
            empty_path = os.path.join(tmpdir, "empty.png")
            empty_raw_path = os.path.join(tmpdir, "empty-raw.png")
            for path in (source_path, std_path, empty_path, empty_raw_path):
                with open(path, "wb") as handle:
                    handle.write(b"img")

            render_calls = []

            def fake_job_render(payload, persist_result=True):
                render_calls.append(dict(payload))
                return {
                    "original_url": "https://cdn.example/original.png",
                    "empty_room_url": "https://cdn.example/old-empty.png",
                    "result_url": "https://cdn.example/main.png",
                    "result_urls": ["https://cdn.example/main.png"],
                    "artifact_manifest": {
                        "root_prefix": "external/mainrendered/2026/06/25/batch-job/",
                    },
                }

            with (
                patch.object(
                    job_entrypoints,
                    "_services",
                    return_value=SimpleNamespace(
                        normalize_audience=lambda audience: audience or "external",
                        materialize_input=lambda source_ref, prefix: source_path,
                        standardize_image=lambda path: std_path,
                        generate_empty_room=lambda *args, **kwargs: (empty_path, empty_raw_path),
                        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{prefix}/{os.path.basename(path)}",
                        build_s3_prefix=lambda audience, category, subfolder=None: "/".join(
                            [part for part in [audience, category, subfolder] if part]
                        ),
                    ),
                ),
                patch.object(job_entrypoints, "job_render", side_effect=fake_job_render),
                patch.object(job_entrypoints, "_persist_job_result"),
            ):
                result = job_entrypoints.job_render_cart_simple_batch(
                    {
                        "audience": "external",
                        "image_url": "https://example.com/room.png",
                        "artifact_job_id": "batch-job",
                        "artifact_created_at": "2026-06-25T04:37:46Z",
                        "variants": [
                            {"variant_index": 1, "render": {"file_path": "https://example.com/room.png"}},
                            {"variant_index": 2, "render": {"file_path": "https://example.com/room.png"}},
                        ],
                    }
                )

        self.assertEqual(len(render_calls), 2)
        self.assertEqual({call["artifact_job_id"] for call in render_calls}, {"batch-job"})
        self.assertEqual({call["artifact_created_at"] for call in render_calls}, {"2026-06-25T04:37:46Z"})
        self.assertIn("external/mainrendered/2026/06/25/batch-job/empty", result["empty_room_url"])
        self.assertEqual(
            {row["render"]["artifact_manifest"]["root_prefix"] for row in result["results"]},
            {"external/mainrendered/2026/06/25/batch-job/"},
        )

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

    def test_job_render_preprocesses_curtain_as_full_frame_material_swatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            local_src = os.path.join(tmpdir, "curtain-source.png")
            normalized = os.path.join(tmpdir, "curtain-material.png")
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
                        normalize_item_image=lambda *args: (_ for _ in ()).throw(AssertionError("object cutout prep must not run")),
                        normalize_material_swatch=lambda local_path, unique_id, index: normalized,
                        resolve_image_url=lambda path, prefix=None: "https://cdn.example/curtain-material.png",
                        build_s3_prefix=lambda audience, category: f"{audience}/{category}/",
                        normalize_audience=lambda audience: audience or "external",
                        render_room=lambda **kwargs: None,
                    ),
                ),
                patch.object(job_entrypoints, "run_render_job", side_effect=fake_run_render_job),
            ):
                job_entrypoints.job_render(
                    {
                        "audience": "external",
                        "file_path": "https://example.com/room.png",
                        "moodboard_items": [
                            {
                                "label": "Curtain",
                                "path": "https://example.com/curtain.png",
                                "category": "curtain",
                                "target_key": "cart_curtain-1_001",
                                "worker_preprocess": "external_cart_item_v1",
                            }
                        ],
                    },
                    persist_result=False,
                )

        prepared_item = captured["payload"]["moodboard_items"][0]
        self.assertEqual(prepared_item["path"], "https://cdn.example/curtain-material.png")
        self.assertNotIn("worker_preprocess", prepared_item)

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
