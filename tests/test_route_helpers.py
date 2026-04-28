import io
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from api_models import CartItem, CartRenderRequest, ExternalRenderVideoRequest
from preset_helpers import resolve_preset_request
from fastapi import UploadFile
from PIL import Image
from application.render.direct_item_image_prep import prepare_direct_item_image
from application.http.queue_route_handlers import handle_render_room_async
from render_route_services import build_external_cart_job, build_external_render_video_job
from request_helpers import require_role
from storage_helpers import is_allowed_download_url, resolve_image_url


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def _upload(name: str = "input.png", content: bytes = b"image-bytes") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


class RouteHelperTests(unittest.TestCase):
    def test_prepare_direct_item_image_preserves_existing_alpha_cutout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = os.path.join(tmpdir, "alpha-source.png")
            out_path = os.path.join(tmpdir, "alpha-output.png")

            image = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
            for x in range(35, 85):
                for y in range(30, 90):
                    image.putpixel((x, y), (220, 80, 80, 255))
            image.save(src_path)

            prepared_path = prepare_direct_item_image(src_path, output_path=out_path, max_size=512)

            self.assertEqual(prepared_path, out_path)
            with Image.open(out_path) as prepared:
                self.assertEqual(prepared.mode, "RGBA")
                self.assertLess(prepared.size[0], 120)
                self.assertLess(prepared.size[1], 120)
                self.assertEqual(prepared.getpixel((0, 0))[3], 0)
                self.assertEqual(prepared.getpixel((prepared.size[0] // 2, prepared.size[1] // 2))[3], 255)

    def test_prepare_direct_item_image_cuts_out_high_confidence_solid_background(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = os.path.join(tmpdir, "solid-bg-source.png")
            out_path = os.path.join(tmpdir, "solid-bg-output.png")

            image = Image.new("RGB", (160, 160), (200, 210, 225))
            for x in range(48, 112):
                for y in range(40, 118):
                    image.putpixel((x, y), (120, 45, 30))
            image.save(src_path)

            prepared_path = prepare_direct_item_image(src_path, output_path=out_path, max_size=512)

            self.assertEqual(prepared_path, out_path)
            with Image.open(out_path) as prepared:
                self.assertEqual(prepared.mode, "RGBA")
                self.assertLess(prepared.size[0], 160)
                self.assertLess(prepared.size[1], 160)
                self.assertEqual(prepared.getpixel((0, 0))[3], 0)
                self.assertEqual(prepared.getpixel((prepared.size[0] // 2, prepared.size[1] // 2))[3], 255)

    def test_prepare_direct_item_image_keeps_low_confidence_backgrounds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_path = os.path.join(tmpdir, "complex-bg-source.png")
            out_path = os.path.join(tmpdir, "complex-bg-output.png")

            image = Image.new("RGB", (140, 140))
            for x in range(140):
                for y in range(140):
                    image.putpixel((x, y), ((x * 5) % 256, (y * 3) % 256, ((x + y) * 7) % 256))
            for x in range(46, 94):
                for y in range(38, 102):
                    image.putpixel((x, y), (180, 40, 40))
            image.save(src_path)

            prepared_path = prepare_direct_item_image(src_path, output_path=out_path, max_size=512)

            self.assertEqual(prepared_path, out_path)
            with Image.open(out_path) as prepared:
                self.assertEqual(prepared.mode, "RGB")
                self.assertEqual(prepared.size, (140, 140))

    def test_require_role_rejects_missing_or_forbidden_keys(self):
        request = _FakeRequest(headers={})
        with self.assertRaisesRegex(Exception, "Invalid or missing API key"):
            require_role(
                request,
                {"external"},
                False,
                {"internal-key"},
                {"external-key"},
            )

        forbidden_request = _FakeRequest(headers={"x-api-key": "internal-key"})
        with self.assertRaisesRegex(Exception, "Forbidden"):
            require_role(
                forbidden_request,
                {"external"},
                False,
                {"internal-key"},
                {"external-key"},
            )

    def test_resolve_preset_request_merges_preset_and_request_fields(self):
        resolved = resolve_preset_request(
            {
                "preset_id": "preset-1",
                "placement": "keep the ceiling clean",
                "dimensions": "",
            },
            {
                "preset-1": {
                    "room": "livingroom",
                    "style": "natural",
                    "variant": "2",
                    "dimensions": "5000 x 4000 x 2600",
                    "placement": "preserve windows",
                }
            },
        )
        self.assertEqual(resolved["room"], "livingroom")
        self.assertEqual(resolved["style"], "natural")
        self.assertEqual(resolved["variant"], "2")
        self.assertEqual(resolved["dimensions"], "5000 x 4000 x 2600")
        self.assertEqual(resolved["placement"], "preserve windows\nkeep the ceiling clean")

    def test_build_external_cart_job_applies_limits_and_generates_target_keys(self):
        req = CartRenderRequest(
            image_url="https://example.com/room.png",
            items=[
                CartItem(id="chair-1", category="chair", image_url="https://example.com/chair.png", qty=1, name="Chair"),
                CartItem(id="sofa-1", category="sofa", image_url="https://example.com/sofa.png", qty=1, name="Sofa"),
                CartItem(id="lamp-1", category="light", image_url="https://example.com/lamp.png", qty=1, name="Lamp"),
            ],
            room="livingroom",
            style="warm modern",
        )
        job_payload, kept, dropped = build_external_cart_job(
            req,
            cart_max_items=2,
            apply_cart_limits=lambda items, limit: (items[:limit], [dict(items[2], drop_reason="max_items_exceeded", drop_index=3)]),
            build_cart_summary=lambda items: "summary",
            materialize_input=lambda url, prefix: f"C:/tmp/{prefix}.png",
            normalize_item_image=lambda local_path, unique_id, index: f"C:/tmp/norm-{index}.png",
            resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{path.split('/')[-1]}",
            build_s3_prefix=lambda audience, category: f"{audience}/{category}/",
            build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{item_id}_{index:03d}",
        )
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(dropped), 1)
        item_refs = job_payload["render"]["moodboard_items"]
        self.assertEqual(item_refs[0]["target_key"], "cart_chair-1_001")
        self.assertEqual(item_refs[1]["target_key"], "cart_sofa-1_002")

    def test_build_external_render_video_job_validates_clip_count_range(self):
        payload = build_external_render_video_job(ExternalRenderVideoRequest(render_job_id="render-job-1", clip_count=5))
        self.assertEqual(
            payload,
            {
                "render_job_id": "render-job-1",
                "clip_count": 5,
                "cfg_scale": 0.5,
                "audience": "external",
            },
        )

        with self.assertRaisesRegex(ValueError, "clip_count"):
            build_external_render_video_job(ExternalRenderVideoRequest(render_job_id="render-job-1", clip_count=3))

    def test_handle_render_room_async_uses_itemized_render_helpers(self):
        deps = MagicMock()
        deps.redis_url = "redis://example"
        deps.rq_queue_render = "render"
        deps.parse_internal_render_items_form.return_value = [{"upload_index": 0}]
        deps.persist_internal_room_upload.return_value = "outputs/raw.png"
        deps.persist_internal_item_uploads.return_value = ["outputs/item_1.png"]
        deps.build_internal_itemized_async_render_job_payload.return_value = {"render": {"audience": "internal"}}
        deps.resolve_image_url = lambda path, prefix=None: f"resolved:{path}"
        deps.build_s3_prefix = lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}"
        deps.build_item_target_key = lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}"
        deps.enqueue_job.return_value = (MagicMock(id="job-1"), None)

        response = handle_render_room_async(
            file=_upload("room.png"),
            room="livingroom",
            style="modern",
            variant="1",
            items_json='[{"category":"chair","qty":1,"dims_mm":{"width_mm":500,"depth_mm":500,"height_mm":900}}]',
            item_images=[_upload("chair.png")],
            dimensions="3000 x 3500 x 2400 mm",
            placement="keep the chair on the left",
            deps=deps,
        )

        self.assertEqual(response.status_code, 200)
        deps.parse_internal_render_items_form.assert_called_once()
        deps.persist_internal_room_upload.assert_called_once()
        deps.persist_internal_item_uploads.assert_called_once()
        deps.build_internal_itemized_async_render_job_payload.assert_called_once()
        deps.enqueue_job.assert_called_once()

    def test_resolve_image_url_respects_s3_required_for_local_outputs(self):
        with self.assertRaisesRegex(RuntimeError, "S3_REQUIRED"):
            resolve_image_url(
                "/outputs/local.png",
                None,
                s3_prefix="internal/mainrendered/",
                s3_bucket="bucket",
                aws_region="ap-northeast-2",
                s3_required=True,
                published_url_cache={},
                get_s3_client=lambda: None,
            )

    def test_is_allowed_download_url_rejects_generic_cloud_hosts_by_default(self):
        self.assertFalse(
            is_allowed_download_url(
                "https://evil-bucket.s3.amazonaws.com/file.png",
                request_host="app.example.com",
                s3_bucket="trusted-bucket",
            )
        )

    def test_is_allowed_download_url_accepts_configured_hosts_and_bucket(self):
        self.assertTrue(
            is_allowed_download_url(
                "https://trusted-bucket.s3.ap-northeast-2.amazonaws.com/file.png",
                request_host="app.example.com",
                s3_bucket="trusted-bucket",
            )
        )
        self.assertTrue(
            is_allowed_download_url(
                "https://downloads.example.com/file.png",
                request_host="app.example.com",
                s3_bucket="trusted-bucket",
                allowed_hosts={"downloads.example.com"},
            )
        )
