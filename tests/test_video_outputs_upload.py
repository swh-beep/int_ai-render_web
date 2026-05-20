import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class VideoOutputsUploadTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_image_upload_returns_worker_accessible_published_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs_dir = Path(tmpdir)
            calls = []

            def fake_resolve(local_path, s3_prefix_override=None):
                calls.append((local_path, s3_prefix_override))
                return f"https://cdn.example/{Path(local_path).name}"

            with patch.object(main, "OUTPUTS_DIR", outputs_dir), patch.object(main, "OUTPUTS_API_ENABLED", True), patch.object(main, "OUTPUTS_API_ROLE", ""), patch.object(main, "S3_REQUIRED", True), patch.object(main, "resolve_image_url", side_effect=fake_resolve):
                response = self.client.post(
                    "/api/outputs/upload",
                    files={"file": ("room.png", b"image-bytes", "image/png")},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["url"].startswith("https://cdn.example/"))
            self.assertTrue(payload["local_url"].startswith("/outputs/"))
            self.assertTrue(calls)
            self.assertIn("videorendered/uploads", calls[0][1])

    def test_outputs_list_publishes_urls_for_worker_access(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs_dir = Path(tmpdir)
            (outputs_dir / "existing.png").write_bytes(b"image-bytes")

            def fake_resolve(local_path, s3_prefix_override=None):
                return f"https://cdn.example/{Path(local_path).name}"

            with patch.object(main, "OUTPUTS_DIR", outputs_dir), patch.object(main, "OUTPUTS_API_ENABLED", True), patch.object(main, "OUTPUTS_API_ROLE", ""), patch.object(main, "S3_REQUIRED", True), patch.object(main, "resolve_image_url", side_effect=fake_resolve):
                response = self.client.get("/api/outputs/list")

            self.assertEqual(response.status_code, 200)
            item = response.json()["items"][0]
            self.assertEqual(item["url"], "https://cdn.example/existing.png")
            self.assertEqual(item["local_url"], "/outputs/existing.png")

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

    def test_presigned_image_upload_returns_public_s3_url(self):
        fake_s3 = SimpleNamespace(generate_presigned_url=lambda *args, **kwargs: "https://s3-upload.example/presigned")
        with patch.object(main, "OUTPUTS_API_ENABLED", True), patch.object(main, "OUTPUTS_API_ROLE", ""), patch.object(
            main,
            "OUTPUTS_ALLOWED_EXTS",
            {".png", ".jpg", ".jpeg", ".webp"},
        ), patch.object(main, "OUTPUTS_UPLOAD_MAX_BYTES", 1024 * 1024), patch.object(
            main,
            "OUTPUTS_UPLOAD_MAX_MB",
            1,
        ), patch.object(main, "S3_BUCKET", "public-bucket"), patch.object(
            main,
            "AWS_REGION",
            "ap-northeast-2",
        ), patch.object(main, "S3_PREFIX", ""), patch.object(
            main,
            "OUTPUTS_S3_PREFIX",
            "outputs/",
        ), patch.object(main, "PUBLIC_ASSET_BASE_URL", ""), patch.object(
            main,
            "_get_s3_client",
            return_value=fake_s3,
        ):
            response = self.client.post(
                "/api/outputs/presign-upload",
                json={"filename": "room.png", "content_type": "image/png", "size": 128},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["upload_url"], "https://s3-upload.example/presigned")
        self.assertEqual(payload["read_url"], "https://s3-upload.example/presigned")
        self.assertTrue(payload["object_key"].startswith("outputs/upload_"))
        self.assertTrue(payload["public_url"].startswith("https://public-bucket.s3.ap-northeast-2.amazonaws.com/outputs/upload_"))

    def test_presigned_image_upload_accepts_multiple_files(self):
        def fake_presign(_operation, Params, **_kwargs):
            return f"https://s3-upload.example/{Params['Key']}"

        fake_s3 = SimpleNamespace(generate_presigned_url=fake_presign)
        with patch.object(main, "OUTPUTS_API_ENABLED", True), patch.object(main, "OUTPUTS_API_ROLE", ""), patch.object(
            main,
            "OUTPUTS_ALLOWED_EXTS",
            {".png", ".jpg", ".jpeg", ".webp"},
        ), patch.object(main, "OUTPUTS_UPLOAD_MAX_BYTES", 1024 * 1024), patch.object(
            main,
            "OUTPUTS_UPLOAD_MAX_MB",
            1,
        ), patch.object(main, "OUTPUTS_PRESIGNED_UPLOAD_MAX_FILES", 10), patch.object(
            main,
            "S3_BUCKET",
            "public-bucket",
        ), patch.object(main, "AWS_REGION", "ap-northeast-2"), patch.object(
            main,
            "S3_PREFIX",
            "",
        ), patch.object(main, "OUTPUTS_S3_PREFIX", "outputs/"), patch.object(
            main,
            "PUBLIC_ASSET_BASE_URL",
            "",
        ), patch.object(main, "_get_s3_client", return_value=fake_s3):
            response = self.client.post(
                "/api/outputs/presign-upload",
                json={
                    "files": [
                        {"client_id": "first", "filename": "room-a.png", "content_type": "image/png", "size": 128},
                        {"client_id": "second", "filename": "room-b.jpg", "content_type": "image/jpeg", "size": 256},
                    ]
                },
            )

        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual([item["client_id"] for item in items], ["first", "second"])
        self.assertTrue(items[0]["object_key"].startswith("outputs/upload_"))
        self.assertIn("room-a.png", items[0]["object_key"])
        self.assertTrue(items[0]["read_url"].startswith("https://s3-upload.example/outputs/upload_"))
        self.assertTrue(items[1]["public_url"].startswith("https://public-bucket.s3.ap-northeast-2.amazonaws.com/outputs/upload_"))

    def test_presigned_image_upload_accepts_folder_suffix(self):
        captured_keys = []

        def fake_presign(operation, Params, **_kwargs):
            if operation == "put_object":
                captured_keys.append(Params["Key"])
            return f"https://s3-upload.example/{Params['Key']}"

        fake_s3 = SimpleNamespace(generate_presigned_url=fake_presign)
        with patch.object(main, "OUTPUTS_API_ENABLED", True), patch.object(main, "OUTPUTS_API_ROLE", ""), patch.object(
            main,
            "OUTPUTS_ALLOWED_EXTS",
            {".png", ".jpg", ".jpeg", ".webp"},
        ), patch.object(main, "OUTPUTS_UPLOAD_MAX_BYTES", 1024 * 1024), patch.object(
            main,
            "OUTPUTS_UPLOAD_MAX_MB",
            1,
        ), patch.object(main, "S3_BUCKET", "public-bucket"), patch.object(
            main,
            "AWS_REGION",
            "ap-northeast-2",
        ), patch.object(main, "S3_PREFIX", ""), patch.object(
            main,
            "OUTPUTS_S3_PREFIX",
            "outputs/",
        ), patch.object(main, "PUBLIC_ASSET_BASE_URL", ""), patch.object(
            main,
            "_get_s3_client",
            return_value=fake_s3,
        ):
            response = self.client.post(
                "/api/outputs/presign-upload",
                json={
                    "folder_suffix": "../marketing video",
                    "files": [{"client_id": "first", "filename": "room.png", "content_type": "image/png", "size": 128}],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured_keys), 1)
        self.assertTrue(captured_keys[0].startswith("outputs/marketing-video/upload_"))
        self.assertTrue(response.json()["items"][0]["public_url"].startswith("https://public-bucket.s3.ap-northeast-2.amazonaws.com/outputs/marketing-video/upload_"))

    def test_presigned_marketing_kling_images_use_group_folder_without_outputs_prefix(self):
        def fake_presign(_operation, Params, **_kwargs):
            return f"https://s3-upload.example/{Params['Key']}"

        fake_s3 = SimpleNamespace(generate_presigned_url=fake_presign)
        with patch.object(main, "OUTPUTS_API_ENABLED", True), patch.object(main, "OUTPUTS_API_ROLE", ""), patch.object(
            main,
            "OUTPUTS_ALLOWED_EXTS",
            {".png", ".jpg", ".jpeg", ".webp"},
        ), patch.object(main, "OUTPUTS_UPLOAD_MAX_BYTES", 1024 * 1024), patch.object(
            main,
            "OUTPUTS_UPLOAD_MAX_MB",
            1,
        ), patch.object(main, "S3_BUCKET", "public-bucket"), patch.object(
            main,
            "AWS_REGION",
            "ap-northeast-2",
        ), patch.object(main, "S3_PREFIX", "env-prefix/"), patch.object(
            main,
            "MARKETING_KLING_S3_PREFIX",
            "marketing-kling/",
        ), patch.object(main, "PUBLIC_ASSET_BASE_URL", ""), patch.object(
            main,
            "_get_s3_client",
            return_value=fake_s3,
        ):
            response = self.client.post(
                "/api/outputs/presign-upload",
                json={
                    "purpose": "marketing-kling",
                    "group_id": "group-1",
                    "asset_type": "images",
                    "files": [{"filename": "room.png", "content_type": "image/png", "size": 128}],
                },
            )

        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertTrue(item["object_key"].startswith("marketing-kling/group-1/images/upload_"))
        self.assertTrue(item["read_url"].startswith("https://s3-upload.example/marketing-kling/group-1/images/upload_"))
        self.assertTrue(item["public_url"].startswith("https://public-bucket.s3.ap-northeast-2.amazonaws.com/marketing-kling/group-1/images/upload_"))

    def test_presigned_marketing_kling_images_accept_start_and_end_subfolders(self):
        captured_keys = []

        def fake_presign(operation, Params, **_kwargs):
            if operation == "put_object":
                captured_keys.append(Params["Key"])
            return f"https://s3-upload.example/{Params['Key']}"

        fake_s3 = SimpleNamespace(generate_presigned_url=fake_presign)
        with patch.object(main, "OUTPUTS_API_ENABLED", True), patch.object(main, "OUTPUTS_API_ROLE", ""), patch.object(
            main,
            "OUTPUTS_ALLOWED_EXTS",
            {".png", ".jpg", ".jpeg", ".webp"},
        ), patch.object(main, "OUTPUTS_UPLOAD_MAX_BYTES", 1024 * 1024), patch.object(
            main,
            "OUTPUTS_UPLOAD_MAX_MB",
            1,
        ), patch.object(main, "S3_BUCKET", "public-bucket"), patch.object(
            main,
            "AWS_REGION",
            "ap-northeast-2",
        ), patch.object(main, "S3_PREFIX", "env-prefix/"), patch.object(
            main,
            "MARKETING_KLING_S3_PREFIX",
            "marketing-kling/",
        ), patch.object(main, "PUBLIC_ASSET_BASE_URL", ""), patch.object(
            main,
            "_get_s3_client",
            return_value=fake_s3,
        ):
            response = self.client.post(
                "/api/outputs/presign-upload",
                json={
                    "purpose": "marketing-kling",
                    "group_id": "group-1",
                    "files": [
                        {"filename": "start.png", "content_type": "image/png", "size": 128, "asset_type": "images/start"},
                        {"filename": "end.png", "content_type": "image/png", "size": 128, "asset_type": "images/end"},
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured_keys), 2)
        self.assertTrue(captured_keys[0].startswith("marketing-kling/group-1/images/start/upload_"))
        self.assertTrue(captured_keys[1].startswith("marketing-kling/group-1/images/end/upload_"))

    def test_publish_existing_output_video_to_marketing_kling_videos_folder(self):
        captured = {}

        def fake_upload_file(local_path, bucket, key, ExtraArgs=None):
            captured["local_path"] = local_path
            captured["bucket"] = bucket
            captured["key"] = key
            captured["extra"] = ExtraArgs

        fake_s3 = SimpleNamespace(upload_file=fake_upload_file)
        with tempfile.TemporaryDirectory() as tmpdir:
            outputs_dir = Path(tmpdir)
            (outputs_dir / "clip.mp4").write_bytes(b"video")
            with patch.object(main, "OUTPUTS_DIR", outputs_dir), patch.object(main, "OUTPUTS_API_ENABLED", True), patch.object(
                main,
                "OUTPUTS_API_ROLE",
                "",
            ), patch.object(main, "OUTPUTS_VIDEO_ALLOWED_EXTS", {".mp4", ".mov", ".webm"}), patch.object(
                main,
                "S3_BUCKET",
                "public-bucket",
            ), patch.object(main, "AWS_REGION", "ap-northeast-2"), patch.object(
                main,
                "S3_PREFIX",
                "",
            ), patch.object(main, "MARKETING_KLING_S3_PREFIX", "marketing-kling/"), patch.object(
                main,
                "PUBLIC_ASSET_BASE_URL",
                "",
            ), patch.object(main, "_get_s3_client", return_value=fake_s3):
                response = self.client.post(
                    "/api/outputs/publish",
                    json={
                        "url": "/outputs/clip.mp4",
                        "purpose": "marketing-kling",
                        "group_id": "group-1",
                        "asset_type": "videos",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["bucket"], "public-bucket")
        self.assertTrue(captured["key"].startswith("marketing-kling/group-1/videos/upload_"))
        self.assertTrue(response.json()["public_url"].startswith("https://public-bucket.s3.ap-northeast-2.amazonaws.com/marketing-kling/group-1/videos/upload_"))
