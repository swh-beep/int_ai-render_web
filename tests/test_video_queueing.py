import unittest
from unittest.mock import patch

from api_models import CompileClip, CompileRequest, SourceGenRequest, SourceItem
from application.video import job_store
from application.video.queueing import (
    build_video_status_payload,
    enqueue_compile_rq_job,
    enqueue_source_generation_rq_job,
    publish_video_state_outputs,
)


class _FakeJob:
    def __init__(self, job_id="job-123", status="queued", result=None, exc_info=None):
        self.id = job_id
        self._status = status
        self.meta = {}
        self.result = result
        self.exc_info = exc_info
        self.saved = False

    def get_status(self):
        return self._status

    def save_meta(self):
        self.saved = True


class VideoQueueingTests(unittest.TestCase):
    def test_source_generation_enqueue_uses_rq_and_sets_initial_video_meta(self):
        calls = []
        fake_job = _FakeJob()

        def enqueue_job(func, *args, queue_name=None, **kwargs):
            calls.append((func, args, queue_name, kwargs))
            return fake_job, None

        req = SourceGenRequest(items=[SourceItem(url="https://example.com/a.png", motion="static")])

        job_id, err = enqueue_source_generation_rq_job(
            req,
            enqueue_job=enqueue_job,
            queue_name="video",
            job_func=lambda payload: payload,
        )

        self.assertIsNone(err)
        self.assertEqual(job_id, "job-123")
        self.assertEqual(calls[0][2], "video")
        self.assertEqual(calls[0][1][0]["items"][0]["url"], "https://example.com/a.png")
        self.assertEqual(fake_job.meta["video_state"], {"status": "QUEUED", "progress": 0})
        self.assertTrue(fake_job.saved)

    def test_compile_enqueue_uses_rq_and_sets_initial_video_meta(self):
        calls = []
        fake_job = _FakeJob("compile-123")

        def enqueue_job(func, *args, queue_name=None, **kwargs):
            calls.append((func, args, queue_name, kwargs))
            return fake_job, None

        req = CompileRequest(clips=[CompileClip(video_url="/outputs/source.mp4", speed=1.25)])

        job_id, err = enqueue_compile_rq_job(
            req,
            enqueue_job=enqueue_job,
            queue_name="video",
            job_func=lambda payload: payload,
        )

        self.assertIsNone(err)
        self.assertEqual(job_id, "compile-123")
        self.assertEqual(calls[0][2], "video")
        self.assertEqual(calls[0][1][0]["clips"][0]["speed"], 1.25)
        self.assertEqual(fake_job.meta["video_state"], {"status": "QUEUED", "progress": 0})
        self.assertTrue(fake_job.saved)

    def test_status_payload_maps_started_rq_video_meta(self):
        job = _FakeJob("job-running", status="started")
        job.meta["video_state"] = {"status": "RUNNING", "progress": 42, "message": "Rendering"}

        payload, status_code = build_video_status_payload(
            "job-running",
            fetch_job=lambda job_id: job,
            load_memory_job=lambda job_id: None,
        )

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["status"], "RUNNING")
        self.assertEqual(payload["progress"], 42)
        self.assertEqual(payload["message"], "Rendering")

    def test_status_payload_prefers_finished_rq_result(self):
        job = _FakeJob(
            "job-done",
            status="finished",
            result={"status": "COMPLETED", "result_url": "/outputs/final.mp4", "progress": 100},
        )
        job.meta["video_state"] = {"status": "RUNNING", "progress": 80}

        payload, status_code = build_video_status_payload(
            "job-done",
            fetch_job=lambda job_id: job,
            load_memory_job=lambda job_id: None,
        )

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["result_url"], "/outputs/final.mp4")
        self.assertEqual(payload["progress"], 100)

    def test_status_payload_falls_back_to_memory_job_for_legacy_local_runs(self):
        payload, status_code = build_video_status_payload(
            "legacy",
            fetch_job=lambda job_id: None,
            load_memory_job=lambda job_id: {"status": "COMPLETED", "results": ["/outputs/a.mp4"]},
        )

        self.assertEqual(status_code, 200)
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["results"], ["/outputs/a.mp4"])

    def test_job_store_updates_current_rq_job_meta(self):
        fake_job = _FakeJob("job-rq", status="started")

        with patch.object(job_store, "get_current_job", return_value=fake_job):
            job_store.set_video_job("job-rq", {"status": "RUNNING", "progress": 0})
            job_store.update_video_job("job-rq", progress=55, message="Halfway")

        self.assertEqual(fake_job.meta["video_state"]["status"], "RUNNING")
        self.assertEqual(fake_job.meta["video_state"]["progress"], 55)
        self.assertEqual(fake_job.meta["video_state"]["job_id"], "job-rq")
        self.assertEqual(fake_job.meta["video_state"]["message"], "Halfway")
        self.assertTrue(fake_job.saved)

    def test_publish_video_state_outputs_resolves_worker_local_output_urls(self):
        resolved = []

        def resolve_output_url(url):
            resolved.append(url)
            return f"https://cdn.example/{url.rsplit('/', 1)[-1]}"

        state = {
            "status": "COMPLETED",
            "results": ["/outputs/source_a.mp4", None, "https://example.com/existing.mp4"],
            "items": [{"output_url": "/outputs/source_a.mp4"}, {"output_url": "https://example.com/existing.mp4"}],
            "result_url": "/outputs/final.mp4",
        }

        published = publish_video_state_outputs(state, resolve_output_url=resolve_output_url)

        self.assertEqual(
            published["results"],
            ["https://cdn.example/source_a.mp4", None, "https://example.com/existing.mp4"],
        )
        self.assertEqual(published["items"][0]["output_url"], "https://cdn.example/source_a.mp4")
        self.assertEqual(published["items"][1]["output_url"], "https://example.com/existing.mp4")
        self.assertEqual(published["result_url"], "https://cdn.example/final.mp4")
        self.assertEqual(resolved, ["/outputs/source_a.mp4", "/outputs/source_a.mp4", "/outputs/final.mp4"])
        self.assertEqual(state["result_url"], "/outputs/final.mp4")


if __name__ == "__main__":
    unittest.main()
