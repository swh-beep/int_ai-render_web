import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "static" / "video_studio.html"
JS_PATH = ROOT / "static" / "js" / "video_studio.js"
CSS_PATH = ROOT / "static" / "css" / "studio-unified.css"


class VideoStudioStaticContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.js = JS_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")

    def test_clip_workspace_exposes_preview_and_clear_controls(self):
        for marker in (
            'id="clip-ref-drop-zone"',
            'id="clip-upload-preview"',
            'id="clip-ref-remove-all"',
        ):
            self.assertIn(marker, self.html)

        self.assertIn(".clip-clear-btn", self.css)
        self.assertIn("clip-upload-box.has-preview", self.css)

    def test_assemble_workspace_exposes_upload_and_compile_surface(self):
        for marker in (
            'id="full-ref-drop-zone"',
            'id="full-ref-preview-container"',
            'id="full-ref-remove-all"',
            'id="full-generate-btn"',
            'id="assemble-monitor-canvas"',
            'id="assemble-monitor-video"',
            'id="assemble-play-toggle"',
            'id="assemble-delete-btn"',
            'id="assemble-trim-btn"',
            'id="assemble-reverse-btn"',
            'id="assemble-flip-btn"',
            'id="assemble-ratio-group"',
            'id="assemble-timeline-track"',
            'id="assemble-inspector-form"',
            'id="assemble-trim-card"',
            'id="assemble-reverse-state"',
            'id="assemble-flip-state"',
            'id="assemble-fit-mode-group"',
            'id="assemble-monitor-video-backdrop"',
        ):
            self.assertIn(marker, self.html)

        self.assertIn("/api/outputs/upload-video", self.js)
        self.assertIn("/video-mvp/compile", self.js)
        self.assertIn("flip_horizontal", self.js)
        self.assertIn("aspect_ratio", self.js)
        self.assertIn("aspect_mode", self.js)
        self.assertIn("toggleActiveAssembleFlag", self.js)
        self.assertIn("bindAssembleWorkspace()", self.js)
        self.assertIn("renderAssembleWorkspace()", self.js)
        self.assertIn("assemble_full_video_card.avif", self.css)
        self.assertIn(".assemble-studio-grid", self.css)
        self.assertIn(".assemble-ratio-btn", self.css)
        self.assertIn(".assemble-fit-btn", self.css)
        self.assertNotIn('id="full-result-container"', self.html)
