import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from main import app


class RouteSurfaceSmokeTests(unittest.TestCase):
    def test_route_surface_has_expected_render_endpoints(self):
        paths = {route.path for route in app.routes}

        self.assertIn("/async/render", paths)
        self.assertIn("/api/external/render/cart", paths)
        self.assertIn("/api/external/render/preset", paths)
        self.assertIn("/api/external/render/video", paths)
        self.assertIn("/api/internal/render", paths)
        self.assertIn("/generate-details", paths)
        self.assertIn("/regenerate-single-detail", paths)
        self.assertIn("/api/outputs/upload-video", paths)

    def test_async_render_multipart_binding_routes_itemized_inputs(self):
        fake_deps = MagicMock()
        fake_deps.redis_url = "redis://example"
        fake_deps.rq_queue_render = "render"
        fake_deps.parse_internal_render_items_form.return_value = [
            {
                "client_id": "item-1",
                "name": "Chair",
                "category": "chair",
                "qty": 1,
                "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 900},
                "upload_index": 0,
            },
            {
                "client_id": "item-2",
                "name": "Table",
                "category": "table",
                "qty": 1,
                "dims_mm": {"width_mm": 1200, "depth_mm": 600, "height_mm": 750},
                "upload_index": 1,
            },
        ]
        fake_deps.persist_internal_room_upload.return_value = "outputs/raw_room.png"
        fake_deps.persist_internal_item_uploads.return_value = ["outputs/cart_item_1.png", "outputs/cart_item_2.png"]
        fake_deps.build_internal_itemized_async_render_job_payload.return_value = {"render": {"audience": "internal"}}
        fake_deps.resolve_image_url = lambda path, prefix=None: f"resolved:{path}"
        fake_deps.build_s3_prefix = lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}"
        fake_deps.build_item_target_key = lambda source, index, label=None, category=None, item_id=None: f"{source}_{item_id}_{index:03d}"
        fake_deps.enqueue_job.return_value = (MagicMock(id="job-123"), None)

        client = TestClient(app)

        with patch("main._queue_route_deps", return_value=fake_deps):
            response = client.post(
                "/async/render",
                data={
                    "room": "livingroom",
                    "style": "modern",
                    "variant": "1",
                    "items_json": (
                        '[{"client_id":"item-1","name":"Chair","category":"chair","qty":1,'
                        '"dims_mm":{"width_mm":500,"depth_mm":500,"height_mm":900}},'
                        '{"client_id":"item-2","name":"Table","category":"table","qty":1,'
                        '"dims_mm":{"width_mm":1200,"depth_mm":600,"height_mm":750}}]'
                    ),
                    "dimensions": "3000 x 3500 x 2400 mm",
                    "placement": "Keep the chair on the left",
                },
                files=[
                    ("file", ("room.png", b"room-bytes", "image/png")),
                    ("item_images", ("chair.png", b"chair-bytes", "image/png")),
                    ("item_images", ("table.png", b"table-bytes", "image/png")),
                ],
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"job_id": "job-123", "status": "queued"})
        fake_deps.parse_internal_render_items_form.assert_called_once()
        call_args = fake_deps.parse_internal_render_items_form.call_args
        self.assertEqual(call_args.args[0], (
            '[{"client_id":"item-1","name":"Chair","category":"chair","qty":1,'
            '"dims_mm":{"width_mm":500,"depth_mm":500,"height_mm":900}},'
            '{"client_id":"item-2","name":"Table","category":"table","qty":1,'
            '"dims_mm":{"width_mm":1200,"depth_mm":600,"height_mm":750}}]'
        ))
        self.assertEqual(len(call_args.args[1]), 2)
        self.assertEqual(call_args.args[1][0].filename, "chair.png")
        self.assertEqual(call_args.args[1][1].filename, "table.png")
        fake_deps.persist_internal_room_upload.assert_called_once()
        fake_deps.persist_internal_item_uploads.assert_called_once()
        build_call = fake_deps.build_internal_itemized_async_render_job_payload.call_args.kwargs
        self.assertEqual(build_call["room"], "livingroom")
        self.assertEqual(build_call["style"], "modern")
        self.assertEqual(build_call["variant"], "1")
        self.assertEqual(build_call["dimensions"], "3000 x 3500 x 2400 mm")
        self.assertEqual(build_call["placement"], "Keep the chair on the left")
        self.assertEqual(build_call["raw_path"], "outputs/raw_room.png")
        self.assertEqual(build_call["item_paths"], ["outputs/cart_item_1.png", "outputs/cart_item_2.png"])
        self.assertIs(build_call["resolve_image_url"], fake_deps.resolve_image_url)
        self.assertIs(build_call["build_s3_prefix"], fake_deps.build_s3_prefix)
        self.assertIs(build_call["build_item_target_key"], fake_deps.build_item_target_key)
