import unittest

from api_models import CartItem, CartRenderRequest
from preset_helpers import resolve_preset_request
from render_route_services import build_external_cart_job
from request_helpers import require_role
from storage_helpers import is_allowed_download_url, resolve_image_url


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


class RouteHelperTests(unittest.TestCase):
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
