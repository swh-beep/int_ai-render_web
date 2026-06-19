import unittest

from application.details.detail_style_stage import construct_dynamic_styles, construct_internal_angle_styles
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

    def test_construct_dynamic_styles_returns_only_simple_detail_targets(self):
        styles = construct_dynamic_styles(
            [
                {
                    "label": "Accent Chair",
                    "target_key": "chair-key",
                    "box_2d": [100, 100, 500, 500],
                    "box_source": "detail_current_image_analysis",
                }
            ]
        )

        self.assertEqual(len(styles), 1)
        self.assertEqual(styles[0]["name"], "Detail: Accent Chair")
        self.assertEqual(styles[0]["ratio"], "4:5")
        self.assertIs(styles[0]["simple_scene_detail"], True)
        self.assertEqual(styles[0]["target_category_canonical"], "")
        self.assertNotIn("TARGET COORDINATES", styles[0]["prompt"])

    def test_construct_dynamic_styles_uses_full_detail_contract_for_product_backed_targets(self):
        styles = construct_dynamic_styles(
            [
                {
                    "label": "Taccia Small Table Lamp",
                    "target_key": "cart_product-38173_taccia-small_011",
                    "item_id": "product_38173",
                    "category": "table_lamp",
                    "category_canonical": "table_lamp",
                    "box_2d": [422, 798, 558, 858],
                    "box_source": "detail_current_image_analysis",
                    "source_box_2d": [410, 790, 565, 865],
                    "crop_path": "outputs/taccia-reference.png",
                    "reference_features": {"silhouette_cues": ["diagonal-cut glass bowl diffuser"]},
                }
            ]
        )

        self.assertEqual(styles[0]["target_key"], "cart_product-38173_taccia-small_011")
        self.assertNotIn("simple_scene_detail", styles[0])
        self.assertEqual(styles[0]["detail_mode"], "product_identity_lock")
        self.assertEqual(styles[0]["target_crop_path"], "outputs/taccia-reference.png")
        self.assertEqual(styles[0]["target_reference_features"], {"silhouette_cues": ["diagonal-cut glass bowl diffuser"]})

    def test_construct_dynamic_styles_preserves_target_category_metadata(self):
        styles = construct_dynamic_styles(
            [
                {
                    "label": "Framed Art",
                    "target_key": "art-key",
                    "category": "wall_art",
                    "category_canonical": "decor",
                    "box_2d": [100, 100, 300, 300],
                    "box_source": "detail_current_image_analysis",
                }
            ]
        )

        self.assertEqual(styles[0]["target_category"], "wall_art")
        self.assertEqual(styles[0]["target_category_canonical"], "decor")

    def test_construct_dynamic_styles_returns_no_overview_or_side_angle_styles_without_targets(self):
        styles = construct_dynamic_styles([])

        self.assertEqual(styles, [])

    def test_construct_dynamic_styles_excludes_window_curtain_and_rug_targets(self):
        styles = construct_dynamic_styles(
            [
                {
                    "label": "Window",
                    "target_key": "window-key",
                    "category": "window",
                    "category_canonical": "window",
                    "box_2d": [10, 10, 300, 300],
                    "box_source": "detail_current_image_analysis",
                    "volume_rank": 1,
                },
                {
                    "label": "Curtains",
                    "target_key": "curtain-key",
                    "category": "curtain",
                    "category_canonical": "curtain",
                    "box_2d": [10, 310, 900, 500],
                    "box_source": "detail_current_image_analysis",
                    "volume_rank": 2,
                },
                {
                    "label": "Area Rug",
                    "target_key": "rug-key",
                    "category": "rug",
                    "category_canonical": "rug",
                    "box_2d": [700, 100, 990, 900],
                    "box_source": "detail_current_image_analysis",
                    "volume_rank": 3,
                },
                {
                    "label": "Accent Chair",
                    "target_key": "chair-key",
                    "category": "chair",
                    "category_canonical": "chair",
                    "box_2d": [400, 500, 850, 850],
                    "box_source": "detail_current_image_analysis",
                    "volume_rank": 4,
                },
            ]
        )

        self.assertEqual([style["target_label"] for style in styles], ["Accent Chair"])

    def test_construct_internal_angle_styles_returns_internal_overview_and_side_slots(self):
        styles = construct_internal_angle_styles()

        self.assertEqual(
            [style["name"] for style in styles],
            ["High Angle Overview", "Side Composition (Focus Left)", "Side Composition (Focus Right)"],
        )
        self.assertEqual([style["ratio"] for style in styles], ["16:9", "16:9", "16:9"])
        self.assertEqual(styles[0]["camera_mode"], "overview_angle")
        self.assertEqual(styles[1]["camera_mode"], "side_angle")
        self.assertEqual(styles[1]["focus_side"], "left")
        self.assertEqual(styles[2]["camera_mode"], "side_angle")
        self.assertEqual(styles[2]["focus_side"], "right")
        self.assertNotIn("simple_scene_detail", styles[0])

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

    def test_construct_dynamic_styles_deduplicates_detection_fragments_with_same_label(self):
        analyzed_items = [
            {
                "label": "Sofa",
                "target_key": "detail_sofa_001",
                "category_canonical": "main_sofa",
                "box_2d": [506, 294, 706, 709],
                "box_source": "detail_current_image_analysis",
                "volume_rank": 1,
            },
            {
                "label": "Floor Lamp",
                "target_key": "detail_floor-lamp_004",
                "category_canonical": "floor_lamp",
                "box_2d": [352, 122, 882, 230],
                "box_source": "detail_current_image_analysis",
                "volume_rank": 2,
            },
            {
                "label": "Floor Lamp",
                "target_key": "detail_floor-lamp_008",
                "category_canonical": "floor_lamp",
                "box_2d": [399, 336, 550, 353],
                "box_source": "detail_current_image_analysis",
                "volume_rank": 3,
            },
            {
                "label": "Floor Lamp",
                "target_key": "detail_floor-lamp_009",
                "category_canonical": "floor_lamp",
                "box_2d": [398, 664, 550, 682],
                "box_source": "detail_current_image_analysis",
                "volume_rank": 4,
            },
        ]

        styles = construct_dynamic_styles(analyzed_items)
        detail_targets = [style.get("target_label") for style in styles]

        self.assertEqual(detail_targets, ["Sofa", "Floor Lamp"])

    def test_construct_dynamic_styles_keeps_separate_generic_decor_targets(self):
        analyzed_items = [
            {
                "label": "Sofa",
                "target_key": "detail_sofa_001",
                "category_canonical": "sofa",
                "box_2d": [506, 294, 706, 709],
                "box_source": "detail_current_image_analysis",
                "volume_rank": 1,
            },
            {
                "label": "Decor",
                "target_key": "detail_decor_002",
                "category_canonical": "decor",
                "box_2d": [120, 100, 210, 190],
                "box_source": "detail_current_image_analysis",
                "volume_rank": 2,
            },
            {
                "label": "Decor",
                "target_key": "detail_decor_003",
                "category_canonical": "decor",
                "box_2d": [700, 780, 820, 900],
                "box_source": "detail_current_image_analysis",
                "volume_rank": 3,
            },
            {
                "label": "Decor",
                "target_key": "detail_decor_004",
                "category_canonical": "decor",
                "box_2d": [125, 105, 208, 188],
                "box_source": "detail_current_image_analysis",
                "volume_rank": 4,
            },
        ]

        styles = construct_dynamic_styles(analyzed_items)
        detail_targets = [style.get("target_label") for style in styles]

        self.assertEqual(detail_targets, ["Sofa", "Decor", "Decor"])

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

        self.assertEqual(detail_targets, ["De Sede DS-787", "Sofa", "Akari Floor Lamp", "Console Table"])

    def test_construct_dynamic_styles_does_not_prioritize_uncroppable_cached_snapshots(self):
        analyzed_items = [
            {
                "label": "De Sede DS-787",
                "target_key": "cart_product-2_desede-ds787",
                "source_index": 2,
                "category": "sofa",
                "category_canonical": "sofa",
                "crop_path": "/tmp/desede.png",
                "box_2d": [0, 0, 1000, 1000],
                "box_source": "cached_detail_snapshot",
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
                "box_source": "detail_current_image_analysis",
                "identity_profile": {"family": "sofa"},
                "volume_rank": 2,
            },
        ]

        styles = construct_dynamic_styles(analyzed_items)
        detail_targets = [style.get("target_label") for style in styles if str(style.get("name") or "").startswith("Detail:")]

        self.assertEqual(detail_targets, ["Sofa"])
