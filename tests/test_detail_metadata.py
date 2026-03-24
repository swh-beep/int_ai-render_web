import unittest

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
