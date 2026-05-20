import unittest

from application.render.dimension_support import (
    dims_has_positive_values,
    is_rug_like,
    normalize_dims_dict,
    parse_object_dimensions_mm,
)
from application.render.furniture_specs_stage import attach_volume_ranks, build_furniture_specs_json
from application.render.postprocess_support import canonical_category


class FurnitureSpecsStageTests(unittest.TestCase):
    def test_primary_scale_prefers_wider_anchor_over_taller_storage(self):
        payload = build_furniture_specs_json(
            [
                {
                    "label": "Large Sofa",
                    "category": "sofa",
                    "requested_dims_mm": {"width_mm": 2900, "depth_mm": 1000, "height_mm": 700},
                    "description": "",
                },
                {
                    "label": "Tall Storage",
                    "category": "storage",
                    "requested_dims_mm": {"width_mm": 1500, "depth_mm": 600, "height_mm": 3000},
                    "description": "",
                },
            ],
            normalize_dims_dict=normalize_dims_dict,
            dims_has_positive_values=dims_has_positive_values,
            parse_object_dimensions_mm=parse_object_dimensions_mm,
            is_rug_like=is_rug_like,
            canonical_category=canonical_category,
        )
        self.assertEqual(payload["primary_scale"]["label"], "Large Sofa")
        self.assertEqual(payload["size_hierarchy_scale"][0], "Large Sofa")

    def test_volume_proxy_does_not_inflate_missing_height(self):
        payload = build_furniture_specs_json(
            [
                {
                    "label": "Low Cabinet",
                    "category": "storage",
                    "requested_dims_mm": {"width_mm": 800, "depth_mm": 800},
                    "description": "",
                }
            ],
            normalize_dims_dict=normalize_dims_dict,
            dims_has_positive_values=dims_has_positive_values,
            parse_object_dimensions_mm=parse_object_dimensions_mm,
            is_rug_like=is_rug_like,
            canonical_category=canonical_category,
        )
        self.assertEqual(payload["items"][0]["volume_proxy"], 640000)

    def test_attach_volume_ranks_prefers_dimension_confidence_before_category_bias(self):
        ranked = attach_volume_ranks(
            [
                {
                    "label": "Accent Lamp",
                    "category": "light",
                    "description": "",
                },
                {
                    "label": "Bench",
                    "category": "chair",
                    "requested_dims_mm": {"width_mm": 1600, "depth_mm": 450, "height_mm": 480},
                    "description": "",
                },
            ],
            normalize_dims_dict=normalize_dims_dict,
            dims_has_positive_values=dims_has_positive_values,
            parse_object_dimensions_mm=parse_object_dimensions_mm,
            canonical_category=canonical_category,
        )
        by_label = {item["label"]: item for item in ranked}
        self.assertEqual(by_label["Bench"]["volume_rank"], 1)
        self.assertGreater(by_label["Bench"]["scale_rank_confidence"], by_label["Accent Lamp"]["scale_rank_confidence"])


if __name__ == "__main__":
    unittest.main()
