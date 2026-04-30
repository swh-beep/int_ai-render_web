import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
STYLE_CSS = ROOT / "static" / "css" / "style.css"
STUDIO_CSS = ROOT / "static" / "css" / "studio-unified.css"
INDEX_HTML = ROOT / "static" / "index.html"
IMAGE_HTML = ROOT / "static" / "image_studio.html"
VIDEO_HTML = ROOT / "static" / "video_studio.html"


class SpinnerContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.style_css = STYLE_CSS.read_text(encoding="utf-8")
        cls.studio_css = STUDIO_CSS.read_text(encoding="utf-8")
        cls.index_html = INDEX_HTML.read_text(encoding="utf-8")
        cls.image_html = IMAGE_HTML.read_text(encoding="utf-8")
        cls.video_html = VIDEO_HTML.read_text(encoding="utf-8")

    def test_global_spinner_uses_shared_round_base(self):
        self.assertIn(".spinner,\n.mini-spinner,\n.vs-spinner {", self.style_css)
        for token in (
            "box-sizing: border-box;",
            "position: relative;",
            ".spinner::before,",
            ".spinner::after,",
            "border: var(--spinner-stroke) solid var(--spinner-track);",
            "border-top-color: var(--spinner-accent);",
            "animation: spin var(--spinner-speed) linear infinite;",
        ):
            self.assertIn(token, self.style_css)

    def test_html_entrypoints_expose_same_spinner_safety_override(self):
        expected_tokens = (
            ".spinner,",
            ".mini-spinner,",
            ".vs-spinner {",
            "box-sizing: border-box !important;",
            ".spinner::before,",
            ".spinner::after,",
            "border-radius: 50% !important;",
            "animation: spin 0.85s linear infinite !important;",
            "@keyframes spin {",
        )
        for html in (self.index_html, self.image_html, self.video_html):
            for token in expected_tokens:
                self.assertIn(token, html)

    def test_global_radius_override_excludes_spinner_elements(self):
        self.assertIn("body[data-page] *:not(.spinner):not(.mini-spinner):not(.vs-spinner),", self.style_css)
        self.assertIn("body[data-page] *:not(.spinner):not(.mini-spinner):not(.vs-spinner)::before,", self.style_css)
        self.assertIn("body[data-page] *:not(.spinner):not(.mini-spinner):not(.vs-spinner)::after {", self.style_css)

    def test_studio_pages_use_busted_spinner_stylesheet_versions(self):
        self.assertIn('/static/css/style.css?v=20260501_0127', self.index_html)
        self.assertIn('/static/css/style.css?v=20260501_0127', self.image_html)
        self.assertIn('/static/css/style.css?v=20260501_0127', self.video_html)
        self.assertIn('/static/css/studio-unified.css?v=20260501_0126', self.image_html)
        self.assertIn('/static/css/studio-unified.css?v=20260501_0126', self.video_html)

    def test_studio_loading_containers_use_shared_spinner_palette(self):
        self.assertIn("--spinner-track: rgba(54, 54, 54, 0.16);", self.studio_css)
        self.assertIn("--spinner-accent: #2b2b2b;", self.studio_css)
        self.assertIn('body[data-page="image-studio"] .spinner,', self.studio_css)
        self.assertIn('body[data-page="video-studio"] .spinner {', self.studio_css)
