import unittest

from PIL import Image

from application.details.detail_generation_stage import _build_target_crop
from application.details.detail_style_stage import construct_dynamic_styles


class DetailScaleLockTests(unittest.TestCase):
    def test_construct_dynamic_styles_carries_target_box(self):
        styles = construct_dynamic_styles(
            [
                {
                    "label": "Console",
                    "description": "Wood console",
                    "box_2d": [200, 300, 700, 800],
                    "box_source": "main_render",
                    "target_key": "detail-1-console",
                    "source_index": 1,
                    "volume_rank": 1,
                }
            ]
        )
        detail_style = next(style for style in styles if style["name"] == "Detail: Console")
        self.assertEqual(detail_style["target_box_2d"], [200, 300, 700, 800])
        self.assertEqual(detail_style["target_key"], "detail-1-console")

    def test_build_target_crop_expands_around_target_box(self):
        image = Image.new("RGB", (1000, 1000), "white")
        crop = _build_target_crop(image, [300, 300, 700, 700])
        self.assertIsNotNone(crop)
        self.assertGreater(crop.size[0], 400)
        self.assertGreater(crop.size[1], 400)
        crop.close()
        image.close()


if __name__ == "__main__":
    unittest.main()
