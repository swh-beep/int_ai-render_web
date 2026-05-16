import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api_models import CompileClip, CompileRequest
from application.video import compile_workflow
from application.video.compile_workflow import _build_video_filter
from application.video.job_store import get_video_job, video_jobs, video_jobs_lock


class CompileWorkflowTests(unittest.TestCase):
    def setUp(self):
        with video_jobs_lock:
            video_jobs.clear()

    def test_build_video_filter_defaults_to_trim_speed_scale_crop(self):
        vf = _build_video_filter(
            trim_start=0.0,
            trim_end=5.0,
            speed=1.0,
            reverse=False,
            flip_horizontal=False,
            video_target_fps=24,
            aspect_ratio="9:16",
            aspect_mode="crop",
        )

        self.assertIn("trim=start=0.0:duration=5.0", vf)
        self.assertIn("setpts=(PTS-STARTPTS)/1.0", vf)
        self.assertIn("[0:v]", vf)
        self.assertIn("[vout]", vf)
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase", vf)
        self.assertIn("crop=1080:1920", vf)
        self.assertIn("fps=24", vf)
        self.assertNotIn("reverse", vf)
        self.assertNotIn("hflip", vf)

    def test_build_video_filter_includes_reverse_and_flip_when_requested(self):
        vf = _build_video_filter(
            trim_start=1.2,
            trim_end=3.8,
            speed=0.75,
            reverse=True,
            flip_horizontal=True,
            video_target_fps=30,
            aspect_ratio="9:16",
            aspect_mode="crop",
        )

        self.assertIn("trim=start=1.2:duration=", vf)
        self.assertIn("reverse", vf)
        self.assertIn("setpts=(PTS-STARTPTS)/0.75", vf)
        self.assertIn("hflip", vf)
        self.assertTrue(vf.index("reverse") < vf.index("setpts"))
        self.assertTrue(vf.index("setpts") < vf.index("hflip"))

    def test_build_video_filter_supports_square_ratio(self):
        vf = _build_video_filter(
            trim_start=0.0,
            trim_end=5.0,
            speed=1.0,
            reverse=False,
            flip_horizontal=False,
            video_target_fps=24,
            aspect_ratio="1:1",
            aspect_mode="crop",
        )

        self.assertIn("scale=1080:1080:force_original_aspect_ratio=increase", vf)
        self.assertIn("crop=1080:1080", vf)

    def test_build_video_filter_supports_black_fill_padding(self):
        vf = _build_video_filter(
            trim_start=0.0,
            trim_end=5.0,
            speed=1.0,
            reverse=False,
            flip_horizontal=False,
            video_target_fps=24,
            aspect_ratio="16:9",
            aspect_mode="fill",
        )

        self.assertNotIn("split=2[bg][fg]", vf)
        self.assertNotIn("boxblur=12:1", vf)
        self.assertIn("pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black", vf)
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease", vf)

    def test_run_final_compile_job_publishes_worker_output_url(self):
        resolved = []

        def resolve_output_url(url):
            resolved.append(url)
            return f"https://cdn.example/{url.rsplit('/', 1)[-1]}"

        def fake_download(url, out_path):
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"source-video")

        def fake_run_ffmpeg(cmd):
            output_path = Path(cmd[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"compiled-video")

        with tempfile.TemporaryDirectory() as tmpdir:
            prev_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                with patch.object(compile_workflow, "download_to_path", side_effect=fake_download), patch.object(
                    compile_workflow, "run_ffmpeg", side_effect=fake_run_ffmpeg
                ):
                    compile_workflow.run_final_compile_job(
                        "compile-published",
                        CompileRequest(clips=[CompileClip(video_url="https://cdn.example/source.mp4")]),
                        video_target_fps=12,
                        resolve_output_url=resolve_output_url,
                    )
            finally:
                os.chdir(prev_cwd)

        state = get_video_job("compile-published")
        self.assertIsNotNone(state)
        self.assertEqual(state.get("status"), "COMPLETED")
        self.assertEqual(state.get("result_url"), "https://cdn.example/final_compile-published.mp4")
        self.assertEqual(resolved, ["/outputs/final_compile-published.mp4"])


if __name__ == "__main__":
    unittest.main()
