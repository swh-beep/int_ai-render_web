import pathlib
import unittest

from fastapi.testclient import TestClient

from main import app


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class MarketingPageContractsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.marketing_html = (STATIC / "marketing.html").read_text(encoding="utf-8")

    def test_marketing_page_is_registered_and_served(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/marketing", paths)

        response = TestClient(app).get("/marketing")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Marketing", response.text)

    def test_sidebar_navigation_links_to_marketing_from_all_pages(self):
        for filename in ("index.html", "image_studio.html", "video_studio.html", "marketing.html"):
            html = (STATIC / filename).read_text(encoding="utf-8")
            self.assertIn('href="/marketing"', html)
            self.assertIn("MARKETING", html)

    def test_marketing_page_exposes_reels_mvp_sections(self):
        for marker in (
            "Marketing Reels Studio",
            'aria-label="Reels content types"',
            'data-reel-type="popup"',
            'data-reel-type="cinematic"',
            'data-reel-type="install"',
            "Campaign Brief",
            "Storyboard Preview",
            "Prompt Library",
            "Output Versions",
            "Generation Queue",
            "Final Reel",
        ):
            self.assertIn(marker, self.marketing_html)

    def test_marketing_page_exposes_required_reels_brief_fields(self):
        for marker in (
            'id="marketing-content-type"',
            'id="marketing-image-input"',
            'id="marketing-upload-count"',
            'id="marketing-image-order-list"',
            'id="marketing-cut-prompts"',
            'name="cut_prompt_1"',
            'name="cut_prompt_2"',
            'name="cut_prompt_3"',
            'id="marketing-global-prompt"',
            'id="marketing-tone"',
            'id="marketing-platform"',
            'id="marketing-audience"',
            'id="marketing-goal"',
            'id="marketing-duration"',
            'id="marketing-language"',
            'id="marketing-generate-brief"',
            'id="marketing-run-reel"',
            'id="marketing-status"',
            'id="marketing-progress"',
            'id="marketing-final-video"',
            'id="marketing-download-link"',
            'id="marketing-result-clips"',
        ):
            self.assertIn(marker, self.marketing_html)

        for option in (
            "Editorial",
            "Energetic",
            "Calm",
            "Bold",
            "Instagram",
            "TikTok",
            "YouTube Shorts",
            "15초",
            "20초",
            "30초",
            "60초",
            "한국어",
            "English",
            "日本語",
        ):
            self.assertIn(option, self.marketing_html)

    def test_marketing_page_js_orchestrates_video_pipeline_without_database_writes(self):
        js_path = STATIC / "js" / "marketing.js"
        self.assertTrue(js_path.exists(), "marketing.js should exist")
        js = js_path.read_text(encoding="utf-8")

        for marker in (
            "marketing-upload-count",
            "marketing-image-order-list",
            "marketing-add-cut",
            "marketing-generate-brief",
            "marketing-run-reel",
            "storyboard-cut-list",
            "storyboard-hook",
            "storyboard-caption",
            "storyboard-cta",
            "/api/outputs/upload",
            "/video-mvp/generate-sources",
            "/video-mvp/compile",
            "/video-mvp/status/",
            "custom_motion_prompt",
            "buildKlingPrompt",
            "compileFinalReel",
            "runMarketingReel",
            "selectedMarketingFiles",
            "renderImageOrderList",
            "moveSelectedFile",
            "removeSelectedFile",
        ):
            self.assertIn(marker, js)

        self.assertNotIn("XMLHttpRequest", js)
        for forbidden in (
            "/marketing/save",
            "/marketing/history",
            "/api/marketing",
            "indexedDB",
            "localStorage",
            "sessionStorage",
        ):
            self.assertNotIn(forbidden, js)
