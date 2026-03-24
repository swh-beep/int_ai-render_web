import os
import shutil
import unittest
from pathlib import Path

from application.render.postprocess_support import refresh_item_boxes_from_main_render
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
