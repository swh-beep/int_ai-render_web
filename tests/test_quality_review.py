import shutil
import unittest
from pathlib import Path

from PIL import Image

from shared.quality_review import (
    build_review_sheet,
    collect_report_image_refs,
    create_contact_sheet,
    ensure_dir,
    resolve_ref_to_local_path,
)


class QualityReviewTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path.cwd()
        self.outputs_dir = ensure_dir(self.repo_root / "outputs")
        self.test_root = ensure_dir(self.outputs_dir / "test_quality_review")
        self.sample_output = self.outputs_dir / "sample_quality.png"
        Image.new("RGB", (64, 64), (255, 200, 200)).save(self.sample_output, "PNG")

    def tearDown(self):
        shutil.rmtree(self.test_root, ignore_errors=True)
        if self.sample_output.exists():
            self.sample_output.unlink()

    def test_resolve_ref_to_local_path_maps_outputs_url(self):
        resolved = resolve_ref_to_local_path("/outputs/sample_quality.png", self.repo_root)
        self.assertEqual(resolved, self.sample_output)

    def test_collect_report_image_refs_extracts_expected_labels(self):
        report = {
            "results": {
                "internal_main": {
                    "original_url": "/outputs/original.png",
                    "empty_room_url": "/outputs/empty.png",
                    "scale_guide_url": "/outputs/guide.png",
                    "result_url": "/outputs/main.png",
                },
                "internal_detail": {
                    "detail_urls": ["/outputs/detail-a.png", "/outputs/detail-b.png"],
                },
                "external_cart": {
                    "result_url": "/outputs/cart.png",
                    "detail_urls": ["/outputs/cart-detail.png"],
                },
            }
        }
        labels = [label for label, _ in collect_report_image_refs(report)]
        self.assertIn("internal_main_result", labels)
        self.assertIn("internal_detail_1", labels)
        self.assertIn("external_cart_result", labels)

    def test_build_review_sheet_contains_agent_owned_qc_fields(self):
        sheet = build_review_sheet(
            suite_name="baseline",
            run_id="run_01",
            room_dimensions_text="10000 x 5500 x 3000 mm",
            manifest_path="manifest.json",
        )
        self.assertEqual(sheet["suite_name"], "baseline")
        self.assertIn("grid_leak", sheet["criteria"])
        self.assertIn("generalization_risk", sheet["criteria"])

    def test_create_contact_sheet_writes_image(self):
        out_path = self.test_root / "board.png"
        board = create_contact_sheet([("sample", self.sample_output)], out_path, columns=1)
        self.assertEqual(board, out_path)
        self.assertTrue(out_path.exists())


if __name__ == "__main__":
    unittest.main()
