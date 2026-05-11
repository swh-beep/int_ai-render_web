import os
import shutil
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from api_models import SourceItem
from application.video import source_generation_workflow
from application.video.job_store import get_video_job, video_jobs, video_jobs_lock
from infrastructure.ai.freepik_kling_client import (
    build_kling_endpoint,
    build_kling_status_endpoint,
    create_kling_task,
    poll_kling_task,
)


class _DummyResponse:
    def __init__(self, *, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class KlingClientTests(unittest.TestCase):
    def test_build_kling_endpoint_uses_clean_default_format(self):
        self.assertEqual(
            build_kling_endpoint("kling-v2-5-pro"),
            "https://api.freepik.com/v1/ai/image-to-video/kling-v2-5-pro",
        )

    def test_build_kling_status_endpoint_uses_kling_2_6_status_slug(self):
        self.assertEqual(
            build_kling_status_endpoint("https://api.freepik.com/v1/ai/image-to-video/kling-v2-6-pro"),
            "https://api.freepik.com/v1/ai/image-to-video/kling-v2-6",
        )
        self.assertEqual(
            build_kling_status_endpoint("https://api.freepik.com/v1/ai/image-to-video/kling-v2-5-pro"),
            "https://api.freepik.com/v1/ai/image-to-video/kling-v2-5-pro",
        )

    @patch("infrastructure.ai.freepik_kling_client.requests.post")
    def test_create_kling_task_raises_on_rate_limit(self, mock_post):
        mock_post.return_value = _DummyResponse(status_code=429, text="rate limited")
        with self.assertRaisesRegex(RuntimeError, "rate limit"):
            create_kling_task(
                "img-b64",
                "prompt",
                "neg",
                "5",
                0.5,
                freepik_api_key="key",
                kling_endpoint="https://example.com/kling",
                video_semaphore=threading.Semaphore(1),
            )

    @patch("infrastructure.ai.freepik_kling_client.requests.post")
    def test_create_kling_task_accepts_nested_task_id(self, mock_post):
        mock_post.return_value = _DummyResponse(status_code=200, payload={"data": {"task_id": "task-123"}})
        task_id = create_kling_task(
            "img-b64",
            "prompt",
            "neg",
            "5",
            0.5,
            freepik_api_key="key",
            kling_endpoint="https://example.com/kling",
            video_semaphore=threading.Semaphore(1),
        )
        self.assertEqual(task_id, "task-123")

    @patch("infrastructure.ai.freepik_kling_client.requests.get")
    def test_poll_kling_task_returns_generated_url(self, mock_get):
        mock_get.side_effect = [
            _DummyResponse(status_code=200, payload={"data": {"status": "RUNNING"}}),
            _DummyResponse(
                status_code=200,
                payload={"data": {"status": "COMPLETED", "generated": [{"url": "https://example.com/video.mp4"}]}},
            ),
        ]
        url = poll_kling_task(
            "task-123",
            clip_index=0,
            total_clips=1,
            freepik_api_key="key",
            kling_endpoint="https://example.com/kling",
            video_semaphore=threading.Semaphore(1),
            update_job_status=None,
            timeout_sec=5,
        )
        self.assertEqual(url, "https://example.com/video.mp4")

    @patch("infrastructure.ai.freepik_kling_client.requests.get")
    def test_poll_kling_task_uses_kling_2_6_status_endpoint(self, mock_get):
        mock_get.return_value = _DummyResponse(
            status_code=200,
            payload={"data": {"status": "COMPLETED", "generated": [{"url": "https://example.com/video.mp4"}]}},
        )

        url = poll_kling_task(
            "task-123",
            clip_index=0,
            total_clips=1,
            freepik_api_key="key",
            kling_endpoint="https://api.freepik.com/v1/ai/image-to-video/kling-v2-6-pro",
            video_semaphore=threading.Semaphore(1),
            update_job_status=None,
            timeout_sec=5,
        )

        self.assertEqual(url, "https://example.com/video.mp4")
        self.assertEqual(
            mock_get.call_args.args[0],
            "https://api.freepik.com/v1/ai/image-to-video/kling-v2-6/task-123",
        )

    @patch("infrastructure.ai.freepik_kling_client.requests.get")
    def test_poll_kling_task_raises_on_failed_status(self, mock_get):
        mock_get.return_value = _DummyResponse(
            status_code=200,
            payload={"data": {"status": "FAILED", "error": "bad prompt"}},
        )
        with self.assertRaisesRegex(RuntimeError, "bad prompt"):
            poll_kling_task(
                "task-123",
                clip_index=0,
                total_clips=1,
                freepik_api_key="key",
                kling_endpoint="https://example.com/kling",
                video_semaphore=threading.Semaphore(1),
                update_job_status=None,
                timeout_sec=5,
            )


class SourceGenerationWorkflowTests(unittest.TestCase):
    def setUp(self):
        with video_jobs_lock:
            video_jobs.clear()
        self.tmp_root = Path("outputs/test_artifacts_video")
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_run_source_generation_job_completes_dynamic_clip(self):
        prev_cwd = os.getcwd()
        os.chdir(self.tmp_root)
        try:
            with patch.object(source_generation_workflow, "image_url_to_b64", return_value="img-b64"), patch.object(
                source_generation_workflow,
                "download_to_path",
                side_effect=lambda url, out_path: Path(out_path).write_bytes(b"fake-mp4"),
            ):
                source_generation_workflow.run_source_generation_job(
                    "job-success",
                    [SourceItem(url="https://example.com/image.png", motion="orbit_r_slow", effect="sunlight")],
                    0.5,
                    video_target_fps=12,
                    video_max_concurrency=1,
                    create_kling_task=lambda *args, **kwargs: "task-123",
                    poll_kling_task=lambda *args, **kwargs: "https://example.com/generated.mp4",
                )
            state = get_video_job("job-success")
            self.assertIsNotNone(state)
            self.assertEqual(state.get("status"), "COMPLETED")
            self.assertEqual(state.get("results"), ["/outputs/source_job-success_0.mp4"])
        finally:
            os.chdir(prev_cwd)

    def test_run_source_generation_job_marks_failed_when_all_clips_fail(self):
        prev_cwd = os.getcwd()
        os.chdir(self.tmp_root)
        try:
            with patch.object(source_generation_workflow, "image_url_to_b64", return_value="img-b64"):
                source_generation_workflow.run_source_generation_job(
                    "job-fail",
                    [SourceItem(url="https://example.com/image.png", motion="orbit_r_slow", effect="sunlight")],
                    0.5,
                    video_target_fps=12,
                    video_max_concurrency=1,
                    create_kling_task=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("kling boom")),
                    poll_kling_task=lambda *args, **kwargs: "https://example.com/generated.mp4",
                )
            state = get_video_job("job-fail")
            self.assertIsNotNone(state)
            self.assertEqual(state.get("status"), "FAILED")
            self.assertIn("All clips failed", state.get("error") or "")
            self.assertEqual(len(state.get("errors") or []), 1)
        finally:
            os.chdir(prev_cwd)

    def test_run_source_generation_job_parallelizes_multiple_dynamic_clips(self):
        prev_cwd = os.getcwd()
        os.chdir(self.tmp_root)
        try:
            items = [
                SourceItem(url=f"https://example.com/image-{idx}.png", motion="orbit_r_slow", effect="sunlight")
                for idx in range(3)
            ]
            counters = {"active": 0, "max_active": 0, "task_index": 0}
            counter_lock = threading.Lock()

            def create_task(*args, **kwargs):
                with counter_lock:
                    counters["active"] += 1
                    counters["max_active"] = max(counters["max_active"], counters["active"])
                    counters["task_index"] += 1
                    task_id = f"task-{counters['task_index']}"
                try:
                    time.sleep(0.05)
                    return task_id
                finally:
                    with counter_lock:
                        counters["active"] -= 1

            with patch.object(source_generation_workflow, "image_url_to_b64", return_value="img-b64"), patch.object(
                source_generation_workflow,
                "download_to_path",
                side_effect=lambda url, out_path: Path(out_path).write_bytes(b"fake-mp4"),
            ):
                source_generation_workflow.run_source_generation_job(
                    "job-parallel",
                    items,
                    0.5,
                    video_target_fps=12,
                    video_max_concurrency=3,
                    create_kling_task=create_task,
                    poll_kling_task=lambda *args, **kwargs: "https://example.com/generated.mp4",
                )

            state = get_video_job("job-parallel")
            self.assertIsNotNone(state)
            self.assertEqual(state.get("status"), "COMPLETED")
            self.assertEqual(
                state.get("results"),
                [
                    "/outputs/source_job-parallel_0.mp4",
                    "/outputs/source_job-parallel_1.mp4",
                    "/outputs/source_job-parallel_2.mp4",
                ],
            )
            self.assertGreaterEqual(counters["max_active"], 2)
        finally:
            os.chdir(prev_cwd)
