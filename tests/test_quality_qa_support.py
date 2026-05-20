import shutil
import unittest
from pathlib import Path

from PIL import Image

from shared.quality_qa_support import (
    BoardTile,
    build_review_sheet,
    create_comparison_board,
    crop_box_reference,
    local_path_from_reference,
    slugify_token,
)


class QualityQaSupportTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path("outputs/test_artifacts/qa_repo").resolve()
        self.assets_dir = self.repo_root / "assets"
        self.outputs_dir = self.repo_root / "outputs"
        self.cache_dir = self.repo_root / "_cache"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.asset_image = self.assets_dir / "sample.png"
        self.output_image = self.outputs_dir / "sample.png"
        Image.new("RGB", (640, 360), "#336699").save(self.asset_image, "PNG")
        Image.new("RGB", (800, 600), "#884422").save(self.output_image, "PNG")

    def tearDown(self):
        shutil.rmtree(self.repo_root, ignore_errors=True)

    def test_slugify_token_normalizes_text(self):
        self.assertEqual(slugify_token("Main Render / Variant 1"), "main-render-variant-1")

    def test_local_path_from_reference_resolves_outputs_and_assets(self):
        asset_ref = "/assets/sample.png"
        output_ref = "/outputs/sample.png"
        self.assertEqual(local_path_from_reference(asset_ref, self.repo_root), self.asset_image)
        self.assertEqual(local_path_from_reference(output_ref, self.repo_root), self.output_image)

    def test_build_review_sheet_contains_expected_criteria(self):
        sheet = build_review_sheet(
            run_id="run-1",
            case_id="internal_main",
            repeat_index=1,
            room_dimensions_mm="10000 x 5500 x 3000",
            diversity_tags=["internal", "main_render"],
        )
        self.assertIn("grid_leak", sheet["criteria"])
        self.assertEqual(sheet["criteria"]["grid_leak"]["rating"], None)
        self.assertEqual(sheet["review_ratings"], ["clear_fail", "borderline", "acceptable", "strong"])

    def test_create_comparison_board_creates_png(self):
        output_path = self.repo_root / "board.png"
        board = create_comparison_board(
            [
                BoardTile("Asset", "/assets/sample.png"),
                BoardTile("Output", "/outputs/sample.png"),
            ],
            repo_root=self.repo_root,
            cache_dir=self.cache_dir,
            output_path=output_path,
            columns=2,
        )
        self.assertIsNotNone(board)
        self.assertTrue(output_path.exists())

    def test_create_comparison_board_marks_missing_artifacts(self):
        output_path = self.repo_root / "board_missing.png"
        board = create_comparison_board(
            [
                BoardTile("Asset", "/assets/sample.png"),
                BoardTile("Missing", "/outputs/does-not-exist.png"),
            ],
            repo_root=self.repo_root,
            cache_dir=self.cache_dir,
            output_path=output_path,
            columns=2,
        )
        self.assertIsNotNone(board)
        self.assertTrue(output_path.exists())

    def test_crop_box_reference_saves_target_crop(self):
        crop_path = self.repo_root / "crop.png"
        result = crop_box_reference(
            self.output_image,
            [250, 250, 750, 750],
            output_path=crop_path,
        )
        self.assertEqual(result, crop_path)
        self.assertTrue(crop_path.exists())


if __name__ == "__main__":
    unittest.main()
