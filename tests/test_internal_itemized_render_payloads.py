import io
import os
import tempfile
import unittest
from pathlib import Path

from fastapi import UploadFile
from PIL import Image

from render_route_services import (
    build_internal_itemized_async_render_job_payload,
    persist_internal_item_uploads,
    persist_internal_room_upload,
)


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def _png_bytes(mode: str, size: tuple[int, int], painter) -> bytes:
    image = Image.new(mode, size, (0, 0, 0, 0) if "A" in mode else (255, 255, 255))
    painter(image)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class InternalItemizedRenderPayloadTests(unittest.TestCase):
    def test_persist_internal_room_upload_saves_one_file_and_returns_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                path = persist_internal_room_upload(_upload("room.png", b"room-bytes"))
            finally:
                os.chdir(cwd)

            self.assertTrue(path.startswith("outputs"))
            self.assertTrue(path.endswith("room.png"))
            saved = Path(tmpdir, path)
            self.assertTrue(saved.exists())
            self.assertEqual(saved.read_bytes(), b"room-bytes")

    def test_persist_internal_item_uploads_preserves_input_order(self):
        chair_bytes = _png_bytes("RGB", (16, 16), lambda image: image.paste((220, 220, 220), (0, 0, 16, 16)))
        table_bytes = _png_bytes("RGB", (16, 16), lambda image: image.paste((180, 180, 180), (0, 0, 16, 16)))
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                paths = persist_internal_item_uploads(
                    [
                        _upload("chair.png", chair_bytes),
                        _upload("table.png", table_bytes),
                    ]
                )
            finally:
                os.chdir(cwd)

            self.assertEqual(len(paths), 2)
            self.assertTrue(paths[0].endswith("chair.png"))
            self.assertTrue(paths[1].endswith("table.png"))
            self.assertTrue(Path(tmpdir, paths[0]).exists())
            self.assertTrue(Path(tmpdir, paths[1]).exists())

    def test_persist_internal_item_uploads_uses_cart_item_prefix_for_cleanup(self):
        chair_bytes = _png_bytes("RGB", (16, 16), lambda image: image.paste((220, 220, 220), (0, 0, 16, 16)))
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                paths = persist_internal_item_uploads([_upload("chair.png", chair_bytes)])
            finally:
                os.chdir(cwd)

            self.assertEqual(len(paths), 1)
            self.assertTrue(Path(paths[0]).name.startswith("cart_item_"))
            self.assertTrue(Path(tmpdir, paths[0]).exists())

    def test_persist_internal_item_uploads_runs_shared_prep_for_alpha_cutouts(self):
        item_bytes = _png_bytes(
            "RGBA",
            (120, 120),
            lambda image: [
                image.putpixel((x, y), (60, 120, 220, 255))
                for x in range(34, 86)
                for y in range(26, 92)
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                paths = persist_internal_item_uploads([_upload("chair.png", item_bytes)])
            finally:
                os.chdir(cwd)

            saved = Path(tmpdir, paths[0])
            with Image.open(saved) as prepared:
                self.assertEqual(prepared.mode, "RGBA")
                self.assertLess(prepared.size[0], 120)
                self.assertLess(prepared.size[1], 120)
                self.assertEqual(prepared.getpixel((0, 0))[3], 0)

    def test_build_internal_itemized_async_render_job_payload_maps_item_specs(self):
        prefixes = []

        def resolve_image_url(path, prefix=None):
            prefixes.append(prefix)
            return f"https://cdn.example/{Path(path).name}" if "room" in path else None

        payload = build_internal_itemized_async_render_job_payload(
            raw_path="outputs/raw_room.png",
            item_specs=[
                {
                    "client_id": "item-2",
                    "name": None,
                    "category": "chair",
                    "qty": 1,
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 900},
                    "upload_index": 1,
                },
                {
                    "client_id": "item-1",
                    "name": "Boucle Sofa",
                    "category": "sofa",
                    "qty": 2,
                    "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 760},
                    "upload_index": 0,
                },
            ],
            item_paths=["outputs/item_1.png", "outputs/item_2.png"],
            room="livingroom",
            style="Customize",
            variant="1",
            dimensions="3000 x 3500 x 2400 mm",
            placement="Keep the sofa on the left wall",
            resolve_image_url=resolve_image_url,
            build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
            build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{item_id}_{index:03d}",
        )

        self.assertEqual(payload["audience"], "internal")
        self.assertEqual(payload["file_path"], "https://cdn.example/raw_room.png")
        self.assertEqual(payload["room"], "livingroom")
        self.assertEqual(payload["style"], "Customize")
        self.assertEqual(payload["variant"], "1")
        self.assertEqual(payload["dimensions"], "3000 x 3500 x 2400 mm")
        self.assertEqual(payload["placement"], "Keep the sofa on the left wall")

        items = payload["moodboard_items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["label"], "chair")
        self.assertEqual(items[0]["path"], "outputs/item_2.png")
        self.assertEqual(items[0]["dims_mm"], {"width_mm": 500, "depth_mm": 500, "height_mm": 900})
        self.assertEqual(items[0]["qty"], 1)
        self.assertEqual(items[0]["category"], "chair")
        self.assertEqual(items[0]["item_id"], "item-2")
        self.assertEqual(items[0]["payload_index"], 1)
        self.assertEqual(items[0]["target_key"], "internal_item-2_001")

        self.assertEqual(items[1]["label"], "Boucle Sofa")
        self.assertEqual(items[1]["path"], "outputs/item_1.png")
        self.assertEqual(items[1]["dims_mm"], {"width_mm": 2200, "depth_mm": 950, "height_mm": 760})
        self.assertEqual(items[1]["qty"], 2)
        self.assertEqual(items[1]["category"], "sofa")
        self.assertEqual(items[1]["item_id"], "item-1")
        self.assertEqual(items[1]["payload_index"], 2)
        self.assertEqual(items[1]["target_key"], "internal_item-1_002")
        self.assertIn("internal/mainrendered/user-photos", prefixes)
        self.assertIn("internal/customize/item-images", prefixes)

    def test_build_internal_itemized_async_render_job_payload_uses_category_label_when_name_missing(self):
        payload = build_internal_itemized_async_render_job_payload(
            raw_path="outputs/raw_room.png",
            item_specs=[
                {
                    "client_id": "item-9",
                    "name": None,
                    "category": "table",
                    "qty": 1,
                    "dims_mm": {"width_mm": 800, "depth_mm": 800, "height_mm": 750},
                    "upload_index": 0,
                }
            ],
            item_paths=["outputs/item_1.png"],
            room="livingroom",
            style="Customize",
            variant="1",
            dimensions="",
            placement="",
            resolve_image_url=lambda path, prefix=None: None,
            build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
            build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{label}_{index:03d}",
        )

        self.assertEqual(payload["moodboard_items"][0]["label"], "table")

    def test_build_internal_itemized_async_render_job_payload_rejects_invalid_upload_index(self):
        calls = []

        def resolve_image_url(path, prefix=None):
            calls.append((path, prefix))
            return None

        with self.assertRaisesRegex(ValueError, "Item 1 has invalid upload_index"):
            build_internal_itemized_async_render_job_payload(
                raw_path="outputs/raw_room.png",
                item_specs=[
                    {
                        "client_id": "item-1",
                        "name": "Chair",
                        "category": "chair",
                        "qty": 1,
                        "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 900},
                        "upload_index": 9,
                    }
                ],
                item_paths=["outputs/item_1.png"],
                room="livingroom",
                style="Customize",
                variant="1",
                dimensions="",
                placement="",
                resolve_image_url=resolve_image_url,
                build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
            )
        self.assertEqual(calls, [])

    def test_build_internal_itemized_async_render_job_payload_rejects_invalid_qty(self):
        calls = []

        def resolve_image_url(path, prefix=None):
            calls.append((path, prefix))
            return None

        with self.assertRaisesRegex(ValueError, "Item 1 has invalid qty"):
            build_internal_itemized_async_render_job_payload(
                raw_path="outputs/raw_room.png",
                item_specs=[
                    {
                        "client_id": "item-1",
                        "name": "Chair",
                        "category": "chair",
                        "qty": 0,
                        "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 900},
                        "upload_index": 0,
                    }
                ],
                item_paths=["outputs/item_1.png"],
                room="livingroom",
                style="Customize",
                variant="1",
                dimensions="",
                placement="",
                resolve_image_url=resolve_image_url,
                build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
            )
        self.assertEqual(calls, [])

    def test_build_internal_itemized_async_render_job_payload_rejects_malformed_label_type(self):
        calls = []

        def resolve_image_url(path, prefix=None):
            calls.append((path, prefix))
            return None

        with self.assertRaisesRegex(ValueError, "Item 1 has invalid label"):
            build_internal_itemized_async_render_job_payload(
                raw_path="outputs/raw_room.png",
                item_specs=[
                    {
                        "client_id": "item-1",
                        "name": None,
                        "category": 123,
                        "qty": 1,
                        "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 900},
                        "upload_index": 0,
                    }
                ],
                item_paths=["outputs/item_1.png"],
                room="livingroom",
                style="Customize",
                variant="1",
                dimensions="",
                placement="",
                resolve_image_url=resolve_image_url,
                build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or 'root'}",
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
            )
        self.assertEqual(calls, [])
