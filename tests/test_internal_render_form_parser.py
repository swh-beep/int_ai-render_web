import io
import unittest

from fastapi import UploadFile

from application.http.internal_render_form_parser import parse_internal_render_items_form


def _upload(name: str) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(b"image-bytes"))


class InternalRenderFormParserTests(unittest.TestCase):
    def test_parse_internal_render_form_items_success(self):
        items_json = (
            '[{"name":"Desk","category":"table","qty":2,'
            '"dims_mm":{"width_mm":1200,"depth_mm":600,"height_mm":750}}]'
        )
        parsed = parse_internal_render_items_form(items_json, [_upload("desk.png")])

        self.assertEqual(
            parsed,
            [
                {
                    "client_id": "item-1",
                    "name": "Desk",
                    "category": "table",
                    "qty": 2,
                    "dims_mm": {"width_mm": 1200, "depth_mm": 600, "height_mm": 750},
                    "upload_index": 0,
                }
            ],
        )

    def test_parse_internal_render_form_items_rejects_missing_dims(self):
        items_json = '[{"category":"table","qty":1,"dims_mm":{"width_mm":1200}}]'

        with self.assertRaisesRegex(ValueError, "Item 1 is missing required dims: depth_mm, height_mm"):
            parse_internal_render_items_form(items_json, [_upload("desk.png")])

    def test_parse_internal_render_form_items_allows_curtain_without_dims(self):
        items_json = '[{"client_id":"curtain-1","name":"Narcis","category":"커튼","qty":1}]'

        parsed = parse_internal_render_items_form(items_json, [_upload("swatch.png")])

        self.assertEqual(parsed[0]["category"], "커튼")
        self.assertIsNone(parsed[0]["dims_mm"])

    def test_parse_internal_render_form_items_validates_curtain_dims_when_supplied(self):
        items_json = '[{"category":"커튼","qty":1,"dims_mm":{"width_mm":1200}}]'

        with self.assertRaisesRegex(ValueError, "Item 1 is missing required dims: depth_mm, height_mm"):
            parse_internal_render_items_form(items_json, [_upload("swatch.png")])

    def test_parse_internal_render_form_items_rejects_image_count_mismatch(self):
        items_json = (
            '[{"category":"table","qty":1,'
            '"dims_mm":{"width_mm":1200,"depth_mm":600,"height_mm":750}},'
            '{"category":"chair","qty":1,'
            '"dims_mm":{"width_mm":500,"depth_mm":500,"height_mm":900}}]'
        )

        with self.assertRaisesRegex(ValueError, "item_images count must match items_json count"):
            parse_internal_render_items_form(items_json, [_upload("desk.png")])

    def test_parse_internal_render_form_items_rejects_invalid_json(self):
        with self.assertRaisesRegex(ValueError, "items_json must be valid JSON"):
            parse_internal_render_items_form("not-json", [_upload("desk.png")])

    def test_parse_internal_render_form_items_rejects_truthy_non_string_items_json(self):
        with self.assertRaisesRegex(ValueError, "items_json must be valid JSON"):
            parse_internal_render_items_form(123, [_upload("desk.png")])

    def test_parse_internal_render_form_items_treats_none_items_json_as_empty(self):
        with self.assertRaisesRegex(ValueError, "items_json must contain at least one item"):
            parse_internal_render_items_form(None, [])

    def test_parse_internal_render_form_items_rejects_missing_category(self):
        items_json = (
            '[{"name":"Desk","category":" ","qty":1,'
            '"dims_mm":{"width_mm":1200,"depth_mm":600,"height_mm":750}}]'
        )

        with self.assertRaisesRegex(ValueError, "Item 1 is missing category"):
            parse_internal_render_items_form(items_json, [_upload("desk.png")])

    def test_parse_internal_render_form_items_rejects_invalid_qty(self):
        items_json = (
            '[{"name":"Desk","category":"table","qty":0,'
            '"dims_mm":{"width_mm":1200,"depth_mm":600,"height_mm":750}}]'
        )

        with self.assertRaisesRegex(ValueError, "Item 1 has invalid qty"):
            parse_internal_render_items_form(items_json, [_upload("desk.png")])

    def test_parse_internal_render_form_items_defaults_missing_client_id(self):
        items_json = (
            '[{"name":"Desk","category":"table","qty":1,'
            '"dims_mm":{"width_mm":1200,"depth_mm":600,"height_mm":750}}]'
        )

        parsed = parse_internal_render_items_form(items_json, [_upload("desk.png")])
        self.assertEqual(parsed[0]["client_id"], "item-1")

    def test_parse_internal_render_form_items_stringifies_numeric_client_id(self):
        items_json = (
            '[{"client_id":123,"name":"Desk","category":"table","qty":1,'
            '"dims_mm":{"width_mm":1200,"depth_mm":600,"height_mm":750}}]'
        )

        parsed = parse_internal_render_items_form(items_json, [_upload("desk.png")])
        self.assertEqual(parsed[0]["client_id"], "123")

    def test_parse_internal_render_form_items_defaults_falsey_non_string_client_id(self):
        items_json = (
            '[{"client_id":0,"name":"Desk","category":"table","qty":1,'
            '"dims_mm":{"width_mm":1200,"depth_mm":600,"height_mm":750}},'
            '{"client_id":false,"name":"Chair","category":"chair","qty":1,'
            '"dims_mm":{"width_mm":500,"depth_mm":500,"height_mm":900}}]'
        )

        parsed = parse_internal_render_items_form(items_json, [_upload("desk.png"), _upload("chair.png")])
        self.assertEqual(parsed[0]["client_id"], "item-1")
        self.assertEqual(parsed[1]["client_id"], "item-2")

    def test_parse_internal_render_form_items_treats_blank_items_json_as_empty(self):
        with self.assertRaisesRegex(ValueError, "items_json must contain at least one item"):
            parse_internal_render_items_form("   ", [])
