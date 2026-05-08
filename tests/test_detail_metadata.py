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
                "style_ratio": "4:5",
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
        self.assertEqual(detail["aspect_ratio"], "4:5")
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
        self.assertEqual(overview["ratio"], "16:9")
        self.assertEqual(styles[1]["ratio"], "16:9")
        self.assertEqual(styles[2]["ratio"], "16:9")
        self.assertIn("Moderately elevated high-angle overview", prompt)
        self.assertIn("Shift the camera laterally or backward from the source position", prompt)
        self.assertIn("reveal more top surfaces of furniture, floor area, and room depth", prompt)
        self.assertIn("Do NOT use bird's-eye, top-down, drone, ceiling-mounted, surveillance, or extreme overhead viewpoints.", prompt)
        self.assertIn("natural elevated in-room overview", prompt)
        self.assertIn("If the output looks like the original source frame", prompt)
        self.assertIn("Wide horizontal 16:9 angle shot", prompt)

    def test_construct_dynamic_styles_requires_visible_side_angle_viewpoint_change(self):
        styles = construct_dynamic_styles([])

        left_prompt = styles[1]["prompt"]
        right_prompt = styles[2]["prompt"]

        self.assertEqual(styles[1]["name"], "Side Composition (Focus Left)")
        self.assertEqual(styles[2]["name"], "Side Composition (Focus Right)")
        self.assertEqual(styles[1]["camera_mode"], "side_angle")
        self.assertEqual(styles[2]["camera_mode"], "side_angle")
        self.assertEqual(styles[1]["focus_side"], "left")
        self.assertEqual(styles[2]["focus_side"], "right")
        self.assertIn("faces the LEFT wall/furniture zone", left_prompt)
        self.assertIn("left half of the source image must fill about 70 percent or more", left_prompt)
        self.assertIn("opposite/right side must be cropped out", left_prompt)
        self.assertIn("faces the RIGHT side of the room", right_prompt)
        self.assertIn("right half of the source image must fill about 70 percent or more", right_prompt)
        self.assertIn("opposite/left side must be cropped out", right_prompt)
        self.assertIn("visibly different diagonal side-angle photograph", left_prompt)
        self.assertIn("visibly different diagonal side-angle photograph", right_prompt)
        self.assertIn("PARALLAX REQUIREMENT", left_prompt)
        self.assertIn("PARALLAX REQUIREMENT", right_prompt)
        self.assertIn("FRAMING PERMISSION", left_prompt)
        self.assertIn("FRAMING PERMISSION", right_prompt)
        self.assertIn("flat front-facing copy of the source is forbidden", left_prompt)
        self.assertIn("flat front-facing copy of the source is forbidden", right_prompt)
        self.assertNotIn("keep the original standing position", left_prompt.lower())
        self.assertNotIn("keep the original standing position", right_prompt.lower())

    def test_construct_dynamic_styles_deduplicates_same_product_detail_targets(self):
        analyzed_items = [
            {
                "label": "Sofa",
                "target_key": "sofa-primary",
                "category": "sofa",
                "category_canonical": "sofa",
                "crop_path": "/tmp/sofa.png",
                "box_2d": [100, 100, 700, 700],
                "identity_profile": {"family": "sofa"},
            },
            {
                "label": "Sofa",
                "target_key": "detail_2_sofa",
                "category": "sofa",
                "category_canonical": "sofa",
                "box_2d": [120, 120, 690, 690],
                "identity_profile": {"family": "sofa"},
            },
            {
                "label": "Floor Lamp",
                "target_key": "lamp-1",
                "category": "light",
                "category_canonical": "floor_lamp",
                "box_2d": [720, 760, 980, 920],
                "identity_profile": {"family": "floor_lamp"},
            },
        ]

        styles = construct_dynamic_styles(analyzed_items)
        detail_styles = [style for style in styles if str(style.get("name") or "").startswith("Detail:")]
        sofa_styles = [style for style in detail_styles if style.get("target_label") == "Sofa"]

        self.assertEqual(len(sofa_styles), 1)
        self.assertEqual(len(detail_styles), 2)
        self.assertEqual(detail_styles[0]["target_label"], "Sofa")
        self.assertEqual(detail_styles[1]["target_label"], "Floor Lamp")

    def test_construct_dynamic_styles_prefers_source_backed_products_over_generic_fresh_detections(self):
        analyzed_items = [
            {
                "label": "De Sede DS-787",
                "target_key": "cart_product-2_desede-ds787",
                "source_index": 2,
                "category": "sofa",
                "category_canonical": "sofa",
                "crop_path": "/tmp/desede.png",
                "box_2d": [120, 80, 700, 620],
                "source_box_2d": [120, 80, 700, 620],
                "identity_profile": {"family": "sofa"},
                "volume_rank": 1,
            },
            {
                "label": "Sofa",
                "target_key": "detail_2_sofa",
                "source_index": 99,
                "category": "sofa",
                "category_canonical": "sofa",
                "box_2d": [140, 100, 690, 610],
                "identity_profile": {"family": "sofa"},
                "volume_rank": 2,
            },
            {
                "label": "Akari Floor Lamp",
                "target_key": "cart_product-5_akari-floor-lamp",
                "source_index": 5,
                "category": "floor_lamp",
                "category_canonical": "floor_lamp",
                "crop_path": "/tmp/akari.png",
                "box_2d": [80, 760, 540, 930],
                "source_box_2d": [80, 760, 540, 930],
                "identity_profile": {"family": "floor_lamp"},
                "volume_rank": 3,
            },
            {
                "label": "Console Table",
                "target_key": "detail_8_console-table",
                "source_index": 100,
                "category": "console_table",
                "category_canonical": "console_table",
                "box_2d": [260, 640, 720, 980],
                "identity_profile": {"family": "table"},
                "volume_rank": 4,
            },
        ]

        styles = construct_dynamic_styles(analyzed_items)
        detail_targets = [style.get("target_label") for style in styles if str(style.get("name") or "").startswith("Detail:")]

        self.assertEqual(detail_targets, ["De Sede DS-787", "Akari Floor Lamp"])
        self.assertNotIn("Sofa", detail_targets)
        self.assertNotIn("Console Table", detail_targets)
