import unittest

from application.video.compile_workflow import _build_video_filter


class CompileWorkflowTests(unittest.TestCase):
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

    def test_build_video_filter_supports_blur_fill_mode(self):
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

        self.assertIn("split=2[bg][fg]", vf)
        self.assertIn("boxblur=12:1", vf)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", vf)
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease", vf)


if __name__ == "__main__":
    unittest.main()
