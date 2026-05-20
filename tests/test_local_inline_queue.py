import io
import json
import time
import unittest
from unittest.mock import MagicMock, patch

from fastapi import UploadFile

import main
from application.http.local_job_store import clear_local_jobs
from application.http.queue_route_handlers import handle_render_room_async


def _upload(name: str = "input.png", content: bytes = b"image-bytes") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


class LocalInlineQueueTests(unittest.TestCase):
    def setUp(self):
        clear_local_jobs()

    def test_enqueue_job_falls_back_to_local_inline_store(self):
        def _job(payload):
            return {"ok": True, "payload": payload}

        with patch.object(main, "LOCAL_INLINE_QUEUE_ENABLED", True), patch.object(main, "_get_rq_queue", return_value=None):
            job, err = main._enqueue_job(_job, {"value": 7})

            self.assertIsNone(err)
            self.assertIsNotNone(job)

            deadline = time.time() + 2.0
            fetched = None
            while time.time() < deadline:
                fetched = main._fetch_job(job.id)
                if fetched is not None and fetched.is_finished:
                    break
                time.sleep(0.05)

        self.assertIsNotNone(fetched)
        self.assertTrue(fetched.is_finished)
        self.assertEqual(fetched.result, {"ok": True, "payload": {"value": 7}})

    def test_enqueue_job_preserves_requested_job_id_for_local_inline_store(self):
        def _job(payload):
            return {"ok": True, "payload": payload}

        with patch.object(main, "LOCAL_INLINE_QUEUE_ENABLED", True), patch.object(main, "_get_rq_queue", return_value=None):
            job, err = main._enqueue_job(_job, {"value": 11}, job_id="staged-job-1")

        self.assertIsNone(err)
        self.assertEqual(job.id, "staged-job-1")

    def test_internal_async_render_allows_local_inline_queue_without_redis_url(self):
        deps = MagicMock()
        deps.redis_url = ""
        deps.local_inline_queue_enabled = True
        deps.rq_queue_render = "render"
        deps.parse_internal_render_items_form.return_value = [{"upload_index": 0}]
        deps.persist_internal_room_upload.return_value = "outputs/raw.png"
        deps.persist_internal_item_source_uploads.return_value = ["outputs/item_src_1.png"]
        deps.prepare_internal_item_upload_paths.return_value = ["outputs/item_1.png"]
        deps.build_internal_itemized_async_render_job_payload.return_value = {"render": {"audience": "internal"}}
        deps.resolve_image_url = lambda path, prefix=None: f"resolved:{path}"
        deps.build_s3_prefix = lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}"
        deps.build_item_target_key = lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}"
        deps.enqueue_job.side_effect = lambda job_func, payload, queue_name=None, **kwargs: (MagicMock(id=kwargs["job_id"]), None)
        deps.start_background_task.side_effect = lambda task: task()

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
        payload = json.loads(response.body)
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(deps.enqueue_job.call_args.kwargs["job_id"], payload["job_id"])


if __name__ == "__main__":
    unittest.main()
