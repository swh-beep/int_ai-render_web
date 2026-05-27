import pathlib
import unittest

from fastapi.testclient import TestClient

from main import app


ROOT = pathlib.Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
STUDIO_APP = ROOT / "studio-app"


class MarketingPageContractsTests(unittest.TestCase):
    def test_marketing_page_is_registered_and_served(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/marketing", paths)

        response = TestClient(app).get("/marketing")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn('id="root"', response.text)
        self.assertIn("/marketing/assets/", response.text)
        self.assertNotIn("/app/assets/", response.text)

    def test_sidebar_navigation_links_to_marketing_from_all_pages(self):
        for filename in ("index.html", "image_studio.html", "video_studio.html"):
            html = (STATIC / filename).read_text(encoding="utf-8")
            self.assertIn('href="/marketing"', html)
            self.assertIn("MARKETING", html)

    def test_vite_marketing_page_source_exposes_reels_sections(self):
        marketing_source = (STUDIO_APP / "src" / "pages" / "MarketingPage.tsx").read_text(encoding="utf-8")

        for marker in (
            "Marketing Studio",
            "1. 생성 전",
            "2. 비디오 확인",
            "3. 최종 합치기",
            "Global prompt",
            "공용 히스토리",
            "Final Reel",
        ):
            self.assertIn(marker, marketing_source)

    def test_vite_marketing_page_orchestrates_video_pipeline_without_legacy_static_ui(self):
        marketing_source = (STUDIO_APP / "src" / "pages" / "MarketingPage.tsx").read_text(encoding="utf-8")
        self.assertFalse((STATIC / "marketing.html").exists())
        self.assertFalse((STATIC / "js" / "marketing.js").exists())

        for marker in (
            "createMarketingReelGroup",
            "requestSourceGeneration",
            "requestMarketingCompile",
            "buildKlingPrompt",
            "uploadOutputImageAssets",
            "patchMarketingFinalResult",
        ):
            self.assertIn(marker, marketing_source)

        for forbidden in (
            "/marketing/save",
            "/marketing/history",
            "indexedDB",
            "localStorage",
            "sessionStorage",
        ):
            self.assertNotIn(forbidden, marketing_source)
