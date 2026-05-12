import pathlib
import re
import unittest

from fastapi.testclient import TestClient

from main import app


ROOT = pathlib.Path(__file__).resolve().parents[1]
STUDIO_DIST = ROOT / "studio-app" / "dist"


class StudioAppRouteContractsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_static_studio_routes_remain_served_from_static_html(self):
        route_markers = {
            "/marketing": "Marketing Reels Studio",
            "/image-studio": 'data-page="image-studio"',
            "/video-studio": 'data-page="video-studio"',
        }

        for route, marker in route_markers.items():
            with self.subTest(route=route):
                response = self.client.get(route)

                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn(marker, response.text)

    def test_app_routes_return_vite_index_without_replacing_static_routes(self):
        for route in (
            "/app/marketing",
            "/app/image-studio",
            "/app/image-studio/generate-real-photo",
            "/app/image-studio/edit-image",
            "/app/image-studio/decorate-image",
            "/app/video-studio",
            "/app/video-studio/create-video-clips",
            "/app/video-studio/assemble-full-video",
            "/app/video-studio/post-production",
        ):
            with self.subTest(route=route):
                response = self.client.get(route)

                self.assertEqual(response.status_code, 200)
                self.assertIn("text/html", response.headers["content-type"])
                self.assertIn('id="root"', response.text)
                self.assertIn("/app/assets/", response.text)

    def test_app_assets_are_served_from_vite_build_output(self):
        index_html = STUDIO_DIST / "index.html"
        self.assertTrue(index_html.exists(), "studio-app must be built before route contract tests")
        html = index_html.read_text(encoding="utf-8")
        match = re.search(r'src="(/app/assets/[^"]+\.js)"', html)
        self.assertIsNotNone(match, "Vite index should reference a built JS asset")

        response = self.client.get(match.group(1))

        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
