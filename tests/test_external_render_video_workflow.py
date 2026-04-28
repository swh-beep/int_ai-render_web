import unittest
from pathlib import Path

from api_models import CompileRequest, SourceGenRequest
from application.video.external_render_video_workflow import run_external_render_video_job


class _FakeFinishedJob:
    def __init__(self, result):
        self.result = result
        self.is_finished = True
        self.is_failed = False
        self.exc_info = None

    def get_status(self):
        return "finished"


class _FakePendingJob:
    def __init__(self):
        self.result = None
        self.is_finished = False
        self.is_failed = False
        self.exc_info = None

    def get_status(self):
        return "queued"


def _render_with_details_payload():
    return {
        "render": {
            "result_url": "https://cdn.example/main-render.png",
            "result_urls": [
                "https://cdn.example/main-render.png",
                "https://cdn.example/main-render-alt.png",
            ],
        },
        "details": {
            "details": [
                {"url": "https://cdn.example/detail-1.png"},
                {"url": "https://cdn.example/detail-2.png"},
            ]
        },
        "resolved": {
            "room": "livingroom",
            "style": "modern",
            "variant": "1",
        },
    }


class ExternalRenderVideoWorkflowTests(unittest.TestCase):
    def test_run_external_render_video_job_generates_compiled_video_from_finished_render(self):
        source_states = {
            "source-job": {
                "status": "COMPLETED",
                "results": [
                    "/outputs/source_a.mp4",
                    "/outputs/source_b.mp4",
                    "/outputs/source_c.mp4",
                    "/outputs/source_d.mp4",
                ],
            },
            "compile-job": {
                "status": "COMPLETED",
                "result_url": "/outputs/final_compiled.mp4",
            },
        }
        captured: dict[str, object] = {}

        def queue_source_generation_job(req, **kwargs):
            captured["source_req"] = req
            captured["source_kwargs"] = kwargs
            return "source-job"

        def queue_final_compile_job(req, **kwargs):
            captured["compile_req"] = req
            captured["compile_kwargs"] = kwargs
            return "compile-job"

        result = run_external_render_video_job(
            {
                "render_job_id": "render-job-1",
                "clip_count": 4,
                "cfg_scale": 0.5,
                "audience": "external",
            },
            fetch_job=lambda job_id: _FakeFinishedJob(_render_with_details_payload()),
            load_job_result=lambda job_id: None,
            queue_source_generation_job=queue_source_generation_job,
            queue_final_compile_job=queue_final_compile_job,
            get_video_job=lambda job_id: source_states[job_id],
            resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}",
            build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
            normalize_audience=lambda audience: audience or "external",
            create_kling_task=lambda *args, **kwargs: "task-123",
            poll_kling_task=lambda *args, **kwargs: "https://cdn.example/provider-clip.mp4",
            video_target_fps=12,
            video_max_concurrency=2,
            sleep=lambda seconds: None,
        )

        self.assertEqual(result["render_job_id"], "render-job-1")
        self.assertEqual(result["clip_count"], 4)
        self.assertEqual(result["video_url"], "https://cdn.example/final_compiled.mp4")
        self.assertEqual(
            result["clip_urls"],
            [
                "https://cdn.example/source_a.mp4",
                "https://cdn.example/source_b.mp4",
                "https://cdn.example/source_c.mp4",
                "https://cdn.example/source_d.mp4",
            ],
        )
        self.assertEqual(
            result["source_images"],
            [
                "https://cdn.example/main-render.png",
                "https://cdn.example/main-render-alt.png",
                "https://cdn.example/detail-1.png",
                "https://cdn.example/detail-2.png",
            ],
        )

        self.assertIsInstance(captured["source_req"], SourceGenRequest)
        self.assertEqual(len(captured["source_req"].items), 4)
        self.assertEqual(captured["source_req"].cfg_scale, 0.5)
        self.assertIsInstance(captured["compile_req"], CompileRequest)
        self.assertEqual(len(captured["compile_req"].clips), 4)
        self.assertEqual(captured["compile_kwargs"]["video_target_fps"], 12)

    def test_run_external_render_video_job_uses_s3_result_when_render_job_not_in_queue(self):
        source_states = {
            "source-job": {
                "status": "COMPLETED",
                "results": [
                    "/outputs/source_a.mp4",
                    "/outputs/source_b.mp4",
                    "/outputs/source_c.mp4",
                    "/outputs/source_d.mp4",
                ],
            },
            "compile-job": {
                "status": "COMPLETED",
                "result_url": "/outputs/final_compiled.mp4",
            },
        }

        result = run_external_render_video_job(
            {
                "render_job_id": "render-job-1",
                "clip_count": 4,
                "cfg_scale": 0.5,
                "audience": "external",
            },
            fetch_job=lambda job_id: None,
            load_job_result=lambda job_id: _render_with_details_payload(),
            queue_source_generation_job=lambda req, **kwargs: "source-job",
            queue_final_compile_job=lambda req, **kwargs: "compile-job",
            get_video_job=lambda job_id: source_states[job_id],
            resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}",
            build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
            normalize_audience=lambda audience: audience or "external",
            create_kling_task=lambda *args, **kwargs: "task-123",
            poll_kling_task=lambda *args, **kwargs: "https://cdn.example/provider-clip.mp4",
            video_target_fps=12,
            video_max_concurrency=2,
            sleep=lambda seconds: None,
        )

        self.assertEqual(result["video_url"], "https://cdn.example/final_compiled.mp4")
        self.assertEqual(
            result["clip_urls"],
            [
                "https://cdn.example/source_a.mp4",
                "https://cdn.example/source_b.mp4",
                "https://cdn.example/source_c.mp4",
                "https://cdn.example/source_d.mp4",
            ],
        )

    def test_run_external_render_video_job_returns_error_when_source_render_not_finished(self):
        result = run_external_render_video_job(
            {
                "render_job_id": "render-job-1",
                "clip_count": 4,
                "cfg_scale": 0.5,
                "audience": "external",
            },
            fetch_job=lambda job_id: _FakePendingJob(),
            load_job_result=lambda job_id: None,
            queue_source_generation_job=lambda req, **kwargs: "source-job",
            queue_final_compile_job=lambda req, **kwargs: "compile-job",
            get_video_job=lambda job_id: None,
            resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}",
            build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
            normalize_audience=lambda audience: audience or "external",
            create_kling_task=lambda *args, **kwargs: "task-123",
            poll_kling_task=lambda *args, **kwargs: "https://cdn.example/provider-clip.mp4",
            video_target_fps=12,
            video_max_concurrency=2,
            sleep=lambda seconds: None,
        )

        self.assertEqual(result["render_job_id"], "render-job-1")
        self.assertIn("not finished", result["error"])

    def test_run_external_render_video_job_rejects_internal_source_render_payload(self):
        result = run_external_render_video_job(
            {
                "render_job_id": "render-job-1",
                "clip_count": 4,
                "cfg_scale": 0.5,
                "audience": "external",
            },
            fetch_job=lambda job_id: _FakeFinishedJob({"render": {"result_url": "https://cdn.example/internal.png"}}),
            load_job_result=lambda job_id: None,
            queue_source_generation_job=lambda req, **kwargs: "source-job",
            queue_final_compile_job=lambda req, **kwargs: "compile-job",
            get_video_job=lambda job_id: None,
            resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}",
            build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
            normalize_audience=lambda audience: audience or "external",
            create_kling_task=lambda *args, **kwargs: "task-123",
            poll_kling_task=lambda *args, **kwargs: "https://cdn.example/provider-clip.mp4",
            video_target_fps=12,
            video_max_concurrency=2,
            sleep=lambda seconds: None,
        )

        self.assertEqual(result["render_job_id"], "render-job-1")
        self.assertEqual(result["error"], "render_job_id must belong to an external render job")


if __name__ == "__main__":
    unittest.main()
