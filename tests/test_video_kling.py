import os
import shutil
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from api_models import SourceGenRequest, SourceItem
from application.video import source_generation_workflow
from application.video.job_store import get_video_job, video_jobs, video_jobs_lock
from infrastructure.ai.kling_client import build_kling_endpoint, create_kling_task, encode_kling_jwt, poll_kling_task


class _DummyResponse:
    def __init__(self, *, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class KlingClientTests(unittest.TestCase):
    def test_build_kling_endpoint_uses_official_image_to_video_path(self):
        self.assertEqual(
            build_kling_endpoint("https://api-singapore.klingai.com/"),
            "https://api-singapore.klingai.com/v1/videos/image2video",
        )

    def test_encode_kling_jwt_uses_access_key_as_issuer(self):
        token = encode_kling_jwt("ak", "sk", now=1000)
        header, payload, signature = token.split(".")
        self.assertTrue(header)
        self.assertTrue(signature)
        padded_payload = payload + "=" * (-len(payload) % 4)
        decoded_payload = __import__("base64").urlsafe_b64decode(padded_payload.encode("ascii"))
        self.assertIn(b'"iss":"ak"', decoded_payload)
        self.assertIn(b'"exp":2800', decoded_payload)
        self.assertIn(b'"nbf":995', decoded_payload)

    @patch("infrastructure.ai.kling_client.requests.post")
    def test_create_kling_task_raises_on_rate_limit(self, mock_post):
        mock_post.return_value = _DummyResponse(
            status_code=429,
            text='{"code":429,"message":"too many requests","request_id":"req-123"}',
        )
        with self.assertRaisesRegex(RuntimeError, "429.*req-123"):
            create_kling_task(
                "img-b64",
                "prompt",
                "neg",
                "5",
                0.5,
                access_key="ak",
                secret_key="sk",
                kling_endpoint="https://api-singapore.klingai.com/v1/videos/image2video",
                model_name="kling-v3",
                mode="std",
                video_semaphore=threading.Semaphore(1),
            )

    @patch("infrastructure.ai.kling_client.requests.post")
    def test_create_kling_task_posts_official_payload_and_accepts_nested_task_id(self, mock_post):
        mock_post.return_value = _DummyResponse(status_code=200, payload={"data": {"task_id": "task-123"}})
        task_id = create_kling_task(
            "https://bucket.s3.ap-northeast-2.amazonaws.com/source.png",
            "prompt",
            "neg",
            "5",
            0.5,
            access_key="ak",
            secret_key="sk",
            kling_endpoint="https://api-singapore.klingai.com/v1/videos/image2video",
            model_name="kling-v3",
            mode="std",
            video_semaphore=threading.Semaphore(1),
        )
        self.assertEqual(task_id, "task-123")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["model_name"], "kling-v3")
        self.assertEqual(kwargs["json"]["image"], "https://bucket.s3.ap-northeast-2.amazonaws.com/source.png")
        self.assertEqual(kwargs["json"]["duration"], "5")
        self.assertEqual(kwargs["json"]["mode"], "std")
        self.assertEqual(kwargs["json"]["aspect_ratio"], "9:16")
        self.assertEqual(kwargs["json"]["sound"], "off")
        self.assertIn("Authorization", kwargs["headers"])
        self.assertNotIn("x-freepik-api-key", kwargs["headers"])

    @patch("infrastructure.ai.kling_client.requests.post")
    def test_create_kling_task_posts_landscape_aspect_ratio(self, mock_post):
        mock_post.return_value = _DummyResponse(status_code=200, payload={"data": {"task_id": "task-landscape"}})
        create_kling_task(
            "https://bucket.s3.ap-northeast-2.amazonaws.com/source.png",
            "prompt",
            "neg",
            "5",
            0.5,
            access_key="ak",
            secret_key="sk",
            kling_endpoint="https://api-singapore.klingai.com/v1/videos/image2video",
            model_name="kling-v3",
            mode="std",
            aspect_ratio="16:9",
            video_semaphore=threading.Semaphore(1),
        )
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["aspect_ratio"], "16:9")

    @patch("infrastructure.ai.kling_client.requests.post")
    def test_create_kling_task_posts_end_frame_as_image_tail_in_std_mode(self, mock_post):
        mock_post.return_value = _DummyResponse(status_code=200, payload={"data": {"task_id": "task-456"}})
        task_id = create_kling_task(
            "https://bucket.s3.ap-northeast-2.amazonaws.com/start.png",
            "prompt",
            "neg",
            "5",
            0.5,
            access_key="ak",
            secret_key="sk",
            kling_endpoint="https://api-singapore.klingai.com/v1/videos/image2video",
            model_name="kling-v3",
            mode="std",
            video_semaphore=threading.Semaphore(1),
            end_image_url="https://bucket.s3.ap-northeast-2.amazonaws.com/end.png",
        )
        self.assertEqual(task_id, "task-456")
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["image"], "https://bucket.s3.ap-northeast-2.amazonaws.com/start.png")
        self.assertEqual(kwargs["json"]["image_tail"], "https://bucket.s3.ap-northeast-2.amazonaws.com/end.png")
        self.assertEqual(kwargs["json"]["mode"], "std")

    @patch("infrastructure.ai.kling_client.requests.get")
    def test_poll_kling_task_returns_generated_url(self, mock_get):
        mock_get.side_effect = [
            _DummyResponse(status_code=200, payload={"data": {"task_status": "processing"}}),
            _DummyResponse(
                status_code=200,
                payload={
                    "data": {
                        "task_status": "succeed",
                        "task_result": {"videos": [{"url": "https://example.com/video.mp4", "duration": "5"}]},
                    }
                },
            ),
        ]
        url = poll_kling_task(
            "task-123",
            clip_index=0,
            total_clips=1,
            access_key="ak",
            secret_key="sk",
            kling_endpoint="https://api-singapore.klingai.com/v1/videos/image2video",
            video_semaphore=threading.Semaphore(1),
            update_job_status=None,
            timeout_sec=5,
        )
        self.assertEqual(url, "https://example.com/video.mp4")

    @patch("infrastructure.ai.kling_client.requests.get")
    def test_poll_kling_task_raises_on_failed_status(self, mock_get):
        mock_get.return_value = _DummyResponse(
            status_code=200,
            payload={"data": {"task_status": "failed", "task_status_msg": "bad prompt"}},
        )
        with self.assertRaisesRegex(RuntimeError, "bad prompt"):
            poll_kling_task(
                "task-123",
                clip_index=0,
                total_clips=1,
                access_key="ak",
                secret_key="sk",
                kling_endpoint="https://api-singapore.klingai.com/v1/videos/image2video",
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

    def test_source_item_duration_defaults_and_validates_allowed_values(self):
        self.assertEqual(SourceItem(url="https://example.com/image.png").duration, "5")
        self.assertEqual(SourceItem(url="https://example.com/image.png", duration="10").duration, "10")
        with self.assertRaises(ValidationError):
            SourceItem(url="https://example.com/image.png", duration="15")

    def test_source_item_accepts_optional_end_url(self):
        item = SourceItem(url="https://example.com/start.png", end_url="https://example.com/end.png")
        self.assertEqual(item.end_url, "https://example.com/end.png")

    def test_run_source_generation_job_completes_dynamic_clip(self):
        prev_cwd = os.getcwd()
        os.chdir(self.tmp_root)
        try:
            with patch.object(
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

    def test_run_source_generation_job_passes_item_duration_to_kling(self):
        prev_cwd = os.getcwd()
        os.chdir(self.tmp_root)
        try:
            captured = {}

            def create_task(image_url, prompt, negative_prompt, duration, cfg_scale, aspect_ratio=None):
                captured["image_url"] = image_url
                captured["duration"] = duration
                captured["aspect_ratio"] = aspect_ratio
                return "task-duration"

            with patch.object(
                source_generation_workflow,
                "download_to_path",
                side_effect=lambda url, out_path: Path(out_path).write_bytes(b"fake-mp4"),
            ):
                source_generation_workflow.run_source_generation_job(
                    "job-duration",
                    [SourceItem(url="https://example.com/image.png", motion="orbit_r_slow", effect="sunlight", duration="7")],
                    0.5,
                    video_target_fps=12,
                    video_max_concurrency=1,
                    create_kling_task=create_task,
                    poll_kling_task=lambda *args, **kwargs: "https://example.com/generated.mp4",
                )

            self.assertEqual(captured.get("duration"), "7")
            self.assertEqual(captured.get("image_url"), "https://example.com/image.png")
            self.assertEqual(captured.get("aspect_ratio"), "9:16")
            state = get_video_job("job-duration")
            self.assertIsNotNone(state)
            self.assertEqual(state.get("status"), "COMPLETED")
        finally:
            os.chdir(prev_cwd)

    def test_run_source_generation_job_passes_end_url_to_kling(self):
        prev_cwd = os.getcwd()
        os.chdir(self.tmp_root)
        try:
            captured = {}

            def create_task(image_url, prompt, negative_prompt, duration, cfg_scale, end_image_url=None, aspect_ratio=None):
                captured["image_url"] = image_url
                captured["end_image_url"] = end_image_url
                captured["aspect_ratio"] = aspect_ratio
                return "task-end-frame"

            with patch.object(
                source_generation_workflow,
                "download_to_path",
                side_effect=lambda url, out_path: Path(out_path).write_bytes(b"fake-mp4"),
            ):
                source_generation_workflow.run_source_generation_job(
                    "job-end-frame",
                    [
                        SourceItem(
                            url="https://example.com/start.png",
                            end_url="https://example.com/end.png",
                            motion="orbit_r_slow",
                            effect="sunlight",
                        )
                    ],
                    0.5,
                    video_target_fps=12,
                    video_max_concurrency=1,
                    create_kling_task=create_task,
                    poll_kling_task=lambda *args, **kwargs: "https://example.com/generated.mp4",
                )

            self.assertEqual(captured.get("image_url"), "https://example.com/start.png")
            self.assertEqual(captured.get("end_image_url"), "https://example.com/end.png")
            self.assertEqual(captured.get("aspect_ratio"), "9:16")
            state = get_video_job("job-end-frame")
            self.assertIsNotNone(state)
            self.assertEqual(state.get("status"), "COMPLETED")
        finally:
            os.chdir(prev_cwd)

    def test_run_source_generation_job_uses_kling_for_static_start_end_clip(self):
        prev_cwd = os.getcwd()
        os.chdir(self.tmp_root)
        try:
            captured = {}

            def create_task(image_url, prompt, negative_prompt, duration, cfg_scale, end_image_url=None, aspect_ratio=None):
                captured["image_url"] = image_url
                captured["end_image_url"] = end_image_url
                captured["aspect_ratio"] = aspect_ratio
                return "task-start-end-static"

            with patch.object(
                source_generation_workflow,
                "download_to_path",
                side_effect=lambda url, out_path: Path(out_path).write_bytes(b"fake-mp4"),
            ), patch.object(source_generation_workflow, "ffmpeg_image_to_video") as mock_static_video:
                source_generation_workflow.run_source_generation_job(
                    "job-start-end-static",
                    [SourceItem(url="https://example.com/start.png", end_url="https://example.com/end.png")],
                    0.5,
                    video_target_fps=12,
                    video_max_concurrency=1,
                    create_kling_task=create_task,
                    poll_kling_task=lambda *args, **kwargs: "https://example.com/generated.mp4",
                )

            self.assertEqual(captured.get("image_url"), "https://example.com/start.png")
            self.assertEqual(captured.get("end_image_url"), "https://example.com/end.png")
            self.assertEqual(captured.get("aspect_ratio"), "9:16")
            mock_static_video.assert_not_called()
            state = get_video_job("job-start-end-static")
            self.assertIsNotNone(state)
            self.assertEqual(state.get("status"), "COMPLETED")
        finally:
            os.chdir(prev_cwd)

    def test_run_source_generation_job_marks_failed_when_all_clips_fail(self):
        prev_cwd = os.getcwd()
        os.chdir(self.tmp_root)
        try:
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

            with patch.object(
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

    def test_queue_source_generation_job_stores_landscape_aspect_ratio(self):
        with patch.object(source_generation_workflow, "clip_url_to_image_bytes", return_value=b"fake-image"), patch.object(
            source_generation_workflow,
            "_start_source_generation_worker",
        ) as mock_start:
            job_id = source_generation_workflow.queue_source_generation_job(
                SourceGenRequest(
                    items=[SourceItem(url="https://example.com/image.png", motion="orbit_r_slow", effect="sunlight")],
                    cfg_scale=0.5,
                    aspect_ratio="16:9",
                ),
                video_target_fps=12,
                video_max_concurrency=1,
                create_kling_task=lambda *args, **kwargs: "task-123",
                poll_kling_task=lambda *args, **kwargs: "https://example.com/generated.mp4",
            )

        state = get_video_job(job_id)
        self.assertIsNotNone(state)
        self.assertEqual(state.get("aspect_ratio"), "16:9")
        mock_start.assert_called_once()
