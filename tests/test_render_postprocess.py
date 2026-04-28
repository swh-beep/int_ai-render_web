import os
import shutil
import unittest
from pathlib import Path

from application.render.postprocess_support import canonical_category, category_match_family, refresh_item_boxes_from_main_render
from application.render.render_postprocess_stage import run_render_postprocess_stage


class _StubLogger:
    def info(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class RenderPostprocessTests(unittest.TestCase):
    def setUp(self):
        self.tmp_root = Path("outputs/test_artifacts")
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_refresh_item_boxes_from_main_render_matches_by_label_and_category(self):
        analyzed_items = [
            {
                "label": "Red Sofa",
                "category": "sofa",
                "category_canonical": "sofa",
                "box_2d": [1, 1, 2, 2],
            },
            {
                "label": "Dining Chair",
                "category": "chair",
                "category_canonical": "chair",
                "box_2d": [3, 3, 4, 4],
            },
        ]
        detected = [
            {"label": "Chair", "box_2d": [100, 100, 200, 200]},
            {"label": "Sofa", "box_2d": [300, 300, 400, 400]},
        ]
        render_path = self.tmp_root / "render.png"
        render_path.write_bytes(b"png")
        remapped = refresh_item_boxes_from_main_render(
            str(render_path),
            analyzed_items,
            detect_furniture_boxes=lambda *args, **kwargs: detected,
            remap_model_name="model",
            remap_detect_timeout_sec=30,
            remap_detect_retry=0,
        )
        self.assertEqual(remapped[0]["box_2d"], [300, 300, 400, 400])
        self.assertEqual(remapped[1]["box_2d"], [100, 100, 200, 200])
        self.assertEqual(remapped[0]["box_source"], "main_render")
        self.assertEqual(remapped[1]["box_source"], "main_render")

    def test_refresh_item_boxes_from_main_render_uses_family_match_without_blind_index_fallback(self):
        analyzed_items = [
            {
                "label": "Walnut Sideboard",
                "category": "storage",
                "category_canonical": "storage",
                "box_2d": [1, 1, 2, 2],
            },
            {
                "label": "Standing Reflector",
                "category": "mirror",
                "category_canonical": "mirror",
                "box_2d": [3, 3, 4, 4],
            },
        ]
        detected = [
            {"label": "Cabinet", "box_2d": [100, 100, 200, 200]},
            {"label": "Mirror", "box_2d": [300, 300, 400, 400]},
        ]
        render_path = self.tmp_root / "family-render.png"
        render_path.write_bytes(b"png")
        remapped = refresh_item_boxes_from_main_render(
            str(render_path),
            analyzed_items,
            detect_furniture_boxes=lambda *args, **kwargs: detected,
            remap_model_name="model",
            remap_detect_timeout_sec=30,
            remap_detect_retry=0,
        )
        self.assertEqual(remapped[0]["box_2d"], [100, 100, 200, 200])
        self.assertEqual(remapped[1]["box_2d"], [300, 300, 400, 400])
        self.assertEqual(remapped[0]["box_label_detected"], "Cabinet")
        self.assertEqual(remapped[1]["box_label_detected"], "Mirror")

    def test_refresh_item_boxes_from_main_render_does_not_force_sensitive_single_item_mismatch(self):
        analyzed_items = [
            {
                "label": "Standing Mirror",
                "category": "mirror",
                "category_canonical": "mirror",
                "dims_mm": {"width_mm": 700, "depth_mm": 25, "height_mm": 1800},
                "box_2d": [3, 3, 4, 4],
            },
        ]
        detected = [
            {"label": "Chair", "box_2d": [300, 300, 400, 400]},
        ]
        render_path = self.tmp_root / "sensitive-single.png"
        render_path.write_bytes(b"png")
        remapped = refresh_item_boxes_from_main_render(
            str(render_path),
            analyzed_items,
            detect_furniture_boxes=lambda *args, **kwargs: detected,
            remap_model_name="model",
            remap_detect_timeout_sec=30,
            remap_detect_retry=0,
        )
        self.assertEqual(remapped[0]["box_2d"], [3, 3, 4, 4])
        self.assertEqual(remapped[0]["box_source"], "source_reference")

    def test_refresh_item_boxes_from_main_render_records_score_for_picked_family_fallback(self):
        analyzed_items = [
            {
                "label": "Walnut Cabinet",
                "category": "storage",
                "category_canonical": "storage",
                "dims_mm": {"width_mm": 1600, "depth_mm": 450, "height_mm": 900},
                "box_2d": [1, 1, 2, 2],
            },
        ]
        detected = [
            {"label": "Cabinet", "box_2d": [100, 100, 220, 220]},
            {"label": "Mirror", "box_2d": [300, 300, 420, 420]},
        ]
        render_path = self.tmp_root / "family-score.png"
        render_path.write_bytes(b"png")
        remapped = refresh_item_boxes_from_main_render(
            str(render_path),
            analyzed_items,
            detect_furniture_boxes=lambda *args, **kwargs: detected,
            remap_model_name="model",
            remap_detect_timeout_sec=30,
            remap_detect_retry=0,
        )
        self.assertEqual(remapped[0]["box_label_detected"], "Cabinet")
        self.assertGreater(remapped[0]["box_match_score"], 0.0)

    def test_category_normalizers_support_requested_internal_taxonomy(self):
        self.assertEqual(canonical_category("거울 장식"), "mirror")
        self.assertEqual(canonical_category("수납장"), "storage")
        self.assertEqual(category_match_family("스툴"), "stool")
        self.assertEqual(category_match_family("Arc Floor Lamp"), "floor_lamp")

    def test_run_render_postprocess_stage_external_keeps_best_only_and_attaches_volume(self):
        generated_path = self.tmp_root / "candidate-c.png"
        generated_path.write_bytes(b"png")
        result = run_render_postprocess_stage(
            generated_results=["a.png", "b.png", str(generated_path)],
            full_analyzed_data=[{"label": "Chair"}],
            audience="external",
            rank_best_variant=lambda generated, items: 2,
            refresh_item_boxes_from_main_render=lambda path, items: [dict(items[0], box_source="main_render")],
            attach_volume_ranks=lambda items: [dict(items[0], volume_rank=1, volume_proxy=10)],
            volume_ranking_snapshot=lambda items: [{"label": items[0]["label"], "volume_rank": items[0]["volume_rank"]}],
            logger=_StubLogger(),
            log_brief=False,
        )
        self.assertEqual(result.generated_results, [str(generated_path)])
        self.assertEqual(result.full_analyzed_data[0]["box_source"], "main_render")
        self.assertEqual(result.full_analyzed_data[0]["volume_rank"], 1)
        self.assertEqual(result.volume_ranking, [{"label": "Chair", "volume_rank": 1}])

    def test_run_render_postprocess_stage_ranks_only_review_pass_subset(self):
        captured = {}
        generated_path_a = self.tmp_root / "candidate-a.png"
        generated_path_b = self.tmp_root / "candidate-b.png"
        generated_path_a.write_bytes(b"png")
        generated_path_b.write_bytes(b"png")

        def _rank_best_variant(candidates, items):
            captured["candidates"] = list(candidates)
            return 0

        result = run_render_postprocess_stage(
            generated_results=[str(generated_path_a), str(generated_path_b)],
            rankable_results=[str(generated_path_b)],
            full_analyzed_data=[{"label": "Chair"}],
            audience="internal",
            rank_best_variant=_rank_best_variant,
            refresh_item_boxes_from_main_render=lambda path, items: items,
            attach_volume_ranks=lambda items: items,
            volume_ranking_snapshot=lambda items: [],
            logger=_StubLogger(),
            log_brief=False,
        )

        self.assertEqual(captured["candidates"], [str(generated_path_b)])
        self.assertEqual(result.generated_results[0], str(generated_path_b))

    def test_run_render_postprocess_stage_skips_rerank_when_failed_rerank_disabled(self):
        captured = {}
        generated_path_a = self.tmp_root / "candidate-a-failed.png"
        generated_path_b = self.tmp_root / "candidate-b-failed.png"
        generated_path_a.write_bytes(b"png")
        generated_path_b.write_bytes(b"png")

        def _rank_best_variant(candidates, items):
            captured["called"] = True
            return 1

        result = run_render_postprocess_stage(
            generated_results=[str(generated_path_a), str(generated_path_b)],
            rankable_results=[str(generated_path_b)],
            full_analyzed_data=[{"label": "Chair"}],
            audience="internal",
            allow_failed_rerank=False,
            rank_best_variant=_rank_best_variant,
            refresh_item_boxes_from_main_render=lambda path, items: items,
            attach_volume_ranks=lambda items: items,
            volume_ranking_snapshot=lambda items: [],
            logger=_StubLogger(),
            log_brief=False,
        )

        self.assertNotIn("called", captured)
        self.assertEqual(result.generated_results, [str(generated_path_a), str(generated_path_b)])

    def test_run_render_postprocess_stage_can_skip_main_render_remap(self):
        generated_path = self.tmp_root / "candidate-skip-remap.png"
        generated_path.write_bytes(b"png")
        refresh_calls = {"count": 0}

        def _refresh(path, items):
            refresh_calls["count"] += 1
            return [dict(items[0], box_source="main_render")]

        result = run_render_postprocess_stage(
            generated_results=[str(generated_path)],
            full_analyzed_data=[{"label": "Chair", "box_source": "source_reference"}],
            audience="internal",
            rank_best_variant=lambda generated, items: 0,
            refresh_item_boxes_from_main_render=_refresh,
            attach_volume_ranks=lambda items: items,
            volume_ranking_snapshot=lambda items: [],
            logger=_StubLogger(),
            log_brief=False,
            skip_main_render_remap=True,
        )

        self.assertEqual(refresh_calls["count"], 0)
        self.assertEqual(result.full_analyzed_data[0]["box_source"], "source_reference")
