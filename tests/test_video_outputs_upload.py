import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class VideoOutputsUploadTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_video_upload_endpoint_accepts_mp4_without_widening_image_uploads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs_dir = Path(tmpdir)
            with patch.object(main, "OUTPUTS_DIR", outputs_dir), patch.object(main, "OUTPUTS_API_ENABLED", True), patch.object(main, "OUTPUTS_API_ROLE", ""), patch.object(main, "OUTPUTS_VIDEO_ALLOWED_EXTS", {".mp4", ".mov", ".webm"}), patch.object(main, "OUTPUTS_VIDEO_UPLOAD_MAX_BYTES", 1024 * 1024), patch.object(main, "OUTPUTS_VIDEO_UPLOAD_MAX_MB", 1), patch.object(main, "OUTPUTS_ALLOWED_EXTS", {".png", ".jpg", ".jpeg", ".webp"}):
                video_response = self.client.post(
                    "/api/outputs/upload-video",
                    files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
                )
                self.assertEqual(video_response.status_code, 200)
                self.assertTrue(video_response.json()["url"].endswith(".mp4"))

                image_route_response = self.client.post(
                    "/api/outputs/upload",
                    files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
                )
                self.assertEqual(image_route_response.status_code, 400)
                self.assertIn("Unsupported file type", image_route_response.text)
