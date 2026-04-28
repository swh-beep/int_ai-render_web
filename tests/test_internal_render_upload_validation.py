import io
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, UploadFile

from application.http.queue_route_handlers import handle_render_room_async


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def _deps() -> MagicMock:
    deps = MagicMock()
    deps.redis_url = "redis://example"
    deps.rq_queue_render = "render"
    deps.parse_internal_render_items_form.return_value = [{"upload_index": 0}]
    deps.persist_internal_room_upload.return_value = "outputs/raw_room.png"
    deps.persist_internal_item_uploads.return_value = ["outputs/item_1.png"]
    deps.build_internal_itemized_async_render_job_payload.return_value = {"render": {"audience": "internal"}}
    deps.resolve_image_url = lambda path, prefix=None: f"resolved:{path}"
    deps.build_s3_prefix = lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}"
    deps.build_item_target_key = lambda source, index, label=None, category=None, item_id=None: f"{source}_{item_id}_{index:03d}"
    deps.enqueue_job.return_value = (MagicMock(id="job-1"), None)
    return deps


class InternalRenderUploadValidationTests(unittest.TestCase):
    def test_handle_render_room_async_rejects_empty_item_contract_before_persistence(self):
        deps = _deps()
        deps.parse_internal_render_items_form.return_value = []

        with self.assertRaises(HTTPException) as ctx:
            handle_render_room_async(
                file=_upload("room.png", b"room-bytes"),
                room="livingroom",
                style="modern",
                variant="1",
                items_json="[]",
                item_images=[],
                dimensions="3000 x 3500 x 2400 mm",
                placement="keep the chair on the left",
                deps=deps,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("At least one furniture item is required", str(ctx.exception.detail))
        deps.parse_internal_render_items_form.assert_not_called()
        deps.persist_internal_room_upload.assert_not_called()
        deps.persist_internal_item_uploads.assert_not_called()
        deps.build_internal_itemized_async_render_job_payload.assert_not_called()
        deps.enqueue_job.assert_not_called()

    def test_handle_render_room_async_rejects_unsupported_room_file_type_before_persistence(self):
        deps = _deps()

        with self.assertRaises(HTTPException) as ctx:
            handle_render_room_async(
                file=_upload("room.gif", b"room-bytes"),
                room="livingroom",
                style="modern",
                variant="1",
                items_json='[{"category":"chair","qty":1,"dims_mm":{"width_mm":500,"depth_mm":500,"height_mm":900}}]',
                item_images=[_upload("chair.png", b"chair-bytes")],
                dimensions="3000 x 3500 x 2400 mm",
                placement="keep the chair on the left",
                deps=deps,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Unsupported file type", str(ctx.exception.detail))
        deps.parse_internal_render_items_form.assert_not_called()
        deps.persist_internal_room_upload.assert_not_called()
        deps.persist_internal_item_uploads.assert_not_called()
        deps.build_internal_itemized_async_render_job_payload.assert_not_called()
        deps.enqueue_job.assert_not_called()

    def test_handle_render_room_async_rejects_oversized_item_file_before_persistence(self):
        deps = _deps()
        large_item = _upload("chair.png", b"x" * (26 * 1024 * 1024))

        with self.assertRaises(HTTPException) as ctx:
            handle_render_room_async(
                file=_upload("room.png", b"room-bytes"),
                room="livingroom",
                style="modern",
                variant="1",
                items_json='[{"category":"chair","qty":1,"dims_mm":{"width_mm":500,"depth_mm":500,"height_mm":900}}]',
                item_images=[large_item],
                dimensions="3000 x 3500 x 2400 mm",
                placement="keep the chair on the left",
                deps=deps,
            )

        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("exceeds the maximum allowed size", str(ctx.exception.detail))
        deps.parse_internal_render_items_form.assert_not_called()
        deps.persist_internal_room_upload.assert_not_called()
        deps.persist_internal_item_uploads.assert_not_called()
        deps.build_internal_itemized_async_render_job_payload.assert_not_called()
        deps.enqueue_job.assert_not_called()
