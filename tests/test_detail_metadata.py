import unittest

from application.details.detail_style_stage import construct_dynamic_styles
from application.details.detail_result_stage import build_detail_generation_output
from application.details.regenerate_detail_resolution import (
    attach_regenerated_target_metadata,
    resolve_regeneration_style,
)


class DetailMetadataTests(unittest.TestCase):
    def test_build_detail_generation_output_attaches_target_metadata(self):
        analyzed_items = [
            {
                "label": "Accent Chair",
                "target_key": "cart_product-1_accent-chair_001",
                "source_index": 1,
                "category": "chair",
                "category_canonical": "chair",
                "box_2d": [10, 10, 20, 20],
                "source_box_2d": [1, 1, 2, 2],
                "box_source": "main_render",
                "crop_path": "/tmp/chair.png",
                "description": "Soft boucle accent chair",
                "volume_rank": 1,
                "volume_proxy": 900,
                "volume_rank_basis": "dims",
            }
        ]
        generated_paths = [
            {
                "index": 0,
                "path": "detail-output.png",
                "style_name": "Detail: Accent Chair",
                "style_target_key": "cart_product-1_accent-chair_001",
                "style_target_label": "Accent Chair",
                "cutout_ref_count": 1,
                "cutout_ref_labels": ["Accent Chair"],
            }
        ]
        output = build_detail_generation_output(
            analyzed_items=analyzed_items,
            generated_paths=generated_paths,
            materialize_input=lambda path, prefix: None,
            resolve_image_url=lambda path, prefix: f"https://cdn.example/{path}",
            prefix_detail_user="detail/user/",
            prefix_detail_rendered="detail/rendered/",
            normalize_label_for_match=lambda text: str(text).strip().lower(),
            volume_ranking_snapshot=lambda items: [{"label": items[0]["label"], "volume_rank": items[0]["volume_rank"]}],
        )
        detail = output["details"][0]
        self.assertEqual(detail["target_key"], "cart_product-1_accent-chair_001")
        self.assertEqual(detail["target_box_source"], "main_render")
        self.assertEqual(detail["target_box_2d"], [10, 10, 20, 20])
        self.assertEqual(output["furniture_data"][0]["target_key"], "cart_product-1_accent-chair_001")
        self.assertEqual(output["furniture_data"][0]["description"], "Soft boucle accent chair")
        self.assertNotIn("_normalized_label", output["furniture_data"][0])
        self.assertEqual(output["volume_ranking"], [{"label": "Accent Chair", "volume_rank": 1}])

    def test_resolve_regeneration_style_prefers_target_label(self):
        dynamic_styles = [
            {"name": "Overall Wide"},
            {"name": "Detail: Accent Chair", "target_label": "Accent Chair", "target_key": "chair-key"},
            {"name": "Detail: Sofa", "target_label": "Sofa", "target_key": "sofa-key"},
        ]
        style, resolved_by, resolved_style_index = resolve_regeneration_style(
            dynamic_styles=dynamic_styles,
            raw_style_index=0,
            req_target_key="",
            req_target_label="Accent Chair",
            style_index_mode="auto",
            normalize_label_for_match=lambda text: str(text).strip().lower(),
        )
        self.assertEqual(style["target_key"], "chair-key")
        self.assertEqual(resolved_by, "target_label")
        self.assertEqual(resolved_style_index, 2)

    def test_resolve_regeneration_style_allows_partial_target_label_match(self):
        dynamic_styles = [
            {"name": "Overall Wide"},
            {"name": "Detail: Floor Lamp", "target_label": "Floor Lamp", "target_key": "lamp-key"},
            {"name": "Detail: Sofa", "target_label": "Sofa", "target_key": "sofa-key"},
        ]
        style, resolved_by, resolved_style_index = resolve_regeneration_style(
            dynamic_styles=dynamic_styles,
            raw_style_index=0,
            req_target_key="",
            req_target_label="Lamp",
            style_index_mode="auto",
            normalize_label_for_match=lambda text: str(text).strip().lower(),
        )
        self.assertEqual(style["target_key"], "lamp-key")
        self.assertEqual(resolved_by, "target_label_partial")
        self.assertEqual(resolved_style_index, 2)

    def test_resolve_regeneration_style_auto_preserves_overall_detail_slot(self):
        dynamic_styles = [
            {"name": "High Angle Overview"},
            {"name": "Side Composition (Focus Left)"},
            {"name": "Side Composition (Focus Right)"},
            {"name": "Detail: Wardrobe", "target_label": "Wardrobe", "target_key": "wardrobe-key"},
            {"name": "Detail: Rug", "target_label": "Rug", "target_key": "rug-key"},
            {"name": "Detail: Floor Lamp", "target_label": "Floor Lamp", "target_key": "lamp-key"},
        ]
        style, resolved_by, resolved_style_index = resolve_regeneration_style(
            dynamic_styles=dynamic_styles,
            raw_style_index=4,
            req_target_key="missing-key",
            req_target_label="Missing Label",
            style_index_mode="auto",
            normalize_label_for_match=lambda text: str(text).strip().lower(),
        )
        self.assertEqual(style["target_key"], "wardrobe-key")
        self.assertEqual(resolved_by, "style_index_detail_from_overall")
        self.assertEqual(resolved_style_index, 4)

    def test_attach_regenerated_target_metadata_uses_matching_item(self):
        output = {"style_name": "Detail: Accent Chair"}
        style = {"name": "Detail: Accent Chair", "target_key": "chair-key", "target_label": "Accent Chair"}
        analyzed_items = [
            {
                "label": "Accent Chair",
                "target_key": "chair-key",
                "box_2d": [10, 10, 20, 20],
                "source_box_2d": [1, 1, 2, 2],
                "box_source": "main_render",
                "volume_rank": 2,
                "volume_proxy": 800,
            }
        ]
        enriched = attach_regenerated_target_metadata(
            output,
            style=style,
            analyzed_items=analyzed_items,
            normalize_label_for_match=lambda text: str(text).strip().lower(),
        )
        self.assertEqual(enriched["target_key"], "chair-key")
        self.assertEqual(enriched["target_box_source"], "main_render")
        self.assertEqual(enriched["resolved_target_label"], "Accent Chair")

    def test_construct_dynamic_styles_limits_overview_camera_to_natural_human_height(self):
        styles = construct_dynamic_styles([])

        overview = styles[0]
        prompt = overview["prompt"]

        self.assertEqual(overview["name"], "High Angle Overview")
        self.assertEqual(overview["ratio"], "4:5")
        self.assertEqual(styles[1]["ratio"], "4:5")
        self.assertEqual(styles[2]["ratio"], "4:5")
        self.assertIn("Moderately elevated high-angle overview", prompt)
        self.assertIn("Do NOT use bird's-eye, top-down, drone, ceiling-mounted, surveillance, or extreme overhead viewpoints.", prompt)
        self.assertIn("natural elevated in-room overview", prompt)
