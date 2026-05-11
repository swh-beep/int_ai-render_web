import unittest
from pathlib import Path
from unittest.mock import patch

from api_models import CompileRequest, SourceGenRequest
from application.video.external_render_video_workflow import (
    _build_source_request,
    _extract_source_images,
    run_external_render_video_job,
)


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
                {"url": "https://cdn.example/detail-3.png"},
                {"url": "https://cdn.example/detail-4.png"},
                {"url": "https://cdn.example/detail-5.png"},
                {"url": "https://cdn.example/detail-6.png"},
            ]
        },
        "resolved": {
            "room": "livingroom",
            "style": "modern",
            "variant": "1",
        },
    }


class ExternalRenderVideoWorkflowTests(unittest.TestCase):
    def test_extract_source_images_uses_main_then_detail_order(self):
        self.assertEqual(
            _extract_source_images(_render_with_details_payload()),
            [
                "https://cdn.example/main-render.png",
                "https://cdn.example/detail-1.png",
                "https://cdn.example/detail-2.png",
                "https://cdn.example/detail-3.png",
                "https://cdn.example/detail-4.png",
                "https://cdn.example/detail-5.png",
                "https://cdn.example/detail-6.png",
            ],
        )

    def test_build_source_request_assigns_fixed_external_motion_pattern(self):
        req = _build_source_request(
            [
                "https://cdn.example/main-render.png",
                "https://cdn.example/detail-1.png",
                "https://cdn.example/detail-2.png",
                "https://cdn.example/detail-3.png",
                "https://cdn.example/detail-4.png",
                "https://cdn.example/detail-5.png",
                "https://cdn.example/detail-6.png",
            ],
            cfg_scale=0.5,
        )

        self.assertEqual(req.cfg_scale, 0.5)
        self.assertEqual(len(req.items), 7)
        self.assertEqual(
            [item.motion for item in req.items],
            [
                "zoom_in_slow",
                "orbit_l_slow",
                "orbit_r_slow",
                "zoom_in_slow",
                "orbit_l_slow",
                "orbit_r_slow",
                "zoom_in_slow",
            ],
        )
        self.assertEqual([item.effect for item in req.items], ["sunlight"] * 7)

    def test_run_external_render_video_job_generates_compiled_video_with_brand_cards(self):
        source_states = {
            "source-job": {
                "status": "COMPLETED",
                "results": [
                    "/outputs/source_1.mp4",
                    "/outputs/source_2.mp4",
                    "/outputs/source_3.mp4",
                    "/outputs/source_4.mp4",
                    "/outputs/source_5.mp4",
                    "/outputs/source_6.mp4",
                    "/outputs/source_7.mp4",
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

        with patch(
            "application.video.external_render_video_workflow._build_brand_card_clip",
            side_effect=["/outputs/brand_intro.mp4", "/outputs/brand_outro.mp4"],
        ):
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
        self.assertEqual(result["assembled_clip_count"], 6)
        self.assertEqual(result["video_url"], "https://cdn.example/final_compiled.mp4")
        self.assertEqual(result["intro_url"], "https://cdn.example/brand_intro.mp4")
        self.assertEqual(result["outro_url"], "https://cdn.example/brand_outro.mp4")
        self.assertEqual(
            result["clip_urls"],
            [
                "https://cdn.example/source_1.mp4",
                "https://cdn.example/source_2.mp4",
                "https://cdn.example/source_3.mp4",
                "https://cdn.example/source_4.mp4",
            ],
        )
        self.assertEqual(
            result["source_images"],
            [
                "https://cdn.example/main-render.png",
                "https://cdn.example/detail-1.png",
                "https://cdn.example/detail-2.png",
                "https://cdn.example/detail-3.png",
            ],
        )

        self.assertIsInstance(captured["source_req"], SourceGenRequest)
        self.assertEqual(len(captured["source_req"].items), 4)
        self.assertEqual(captured["source_req"].cfg_scale, 0.5)
        self.assertEqual(
            [item.motion for item in captured["source_req"].items],
            [
                "zoom_in_slow",
                "orbit_l_slow",
                "orbit_r_slow",
                "zoom_in_slow",
            ],
        )
        self.assertEqual([item.effect for item in captured["source_req"].items], ["sunlight"] * 4)

        self.assertIsInstance(captured["compile_req"], CompileRequest)
        self.assertEqual(len(captured["compile_req"].clips), 6)
        self.assertEqual(captured["compile_req"].clips[0].video_url, "/outputs/brand_intro.mp4")
        self.assertEqual(captured["compile_req"].clips[-1].video_url, "/outputs/brand_outro.mp4")
        self.assertEqual(captured["compile_req"].clips[0].trim_end, 3.0)
        self.assertEqual(captured["compile_req"].clips[-1].trim_end, 3.0)
        self.assertTrue(all(clip.trim_end == 5.0 for clip in captured["compile_req"].clips[1:-1]))
        self.assertEqual(captured["compile_req"].aspect_ratio, "16:9")
        self.assertEqual(captured["compile_req"].aspect_mode, "fill")
        self.assertEqual(captured["compile_kwargs"]["video_target_fps"], 12)

    def test_run_external_render_video_job_uses_s3_result_when_render_job_not_in_queue(self):
        source_states = {
            "source-job": {
                "status": "COMPLETED",
                "results": [
                    "/outputs/source_1.mp4",
                    "/outputs/source_2.mp4",
                    "/outputs/source_3.mp4",
                    "/outputs/source_4.mp4",
                    "/outputs/source_5.mp4",
                    "/outputs/source_6.mp4",
                    "/outputs/source_7.mp4",
                ],
            },
            "compile-job": {
                "status": "COMPLETED",
                "result_url": "/outputs/final_compiled.mp4",
            },
        }

        with patch(
            "application.video.external_render_video_workflow._build_brand_card_clip",
            side_effect=["/outputs/brand_intro.mp4", "/outputs/brand_outro.mp4"],
        ):
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
        self.assertEqual(result["clip_count"], 4)
        self.assertEqual(result["intro_url"], "https://cdn.example/brand_intro.mp4")
        self.assertEqual(result["outro_url"], "https://cdn.example/brand_outro.mp4")

    def test_run_external_render_video_job_returns_error_when_source_render_not_finished(self):
        result = run_external_render_video_job(
            {
                "render_job_id": "render-job-1",
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
