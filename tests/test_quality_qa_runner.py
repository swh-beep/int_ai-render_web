import shutil
import unittest
from pathlib import Path

from quality_qa_runner import _load_reference_internal_main, _normalize_cases, _suite_run_dir


class QualityQaRunnerTests(unittest.TestCase):
    def setUp(self):
        self.suite_root = Path("outputs/test_artifacts/qa_runner_suite").resolve()
        self.suite_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.suite_root, ignore_errors=True)

    def test_normalize_cases_keeps_dependencies_ordered(self):
        cases = _normalize_cases(["Internal Main", "internal-detail", "internal_main"])
        self.assertEqual(cases, ["internal_main", "internal_detail"])

    def test_normalize_cases_rejects_unknown_values(self):
        with self.assertRaises(Exception):
            _normalize_cases(["unsupported-case"])

    def test_suite_run_dir_nests_under_suite_root(self):
        run_id, run_dir = _suite_run_dir(self.suite_root, "Phase 0", 1)
        self.assertIn("phase-0", run_id)
        self.assertTrue(str(run_dir).startswith(str(self.suite_root)))
        self.assertTrue(run_dir.exists())

    def test_load_reference_internal_main_reads_suite_results(self):
        reference_dir = self.suite_root / "reference"
        reference_dir.mkdir(parents=True, exist_ok=True)
        (reference_dir / "suite_results.json").write_text(
            '{"internal_main": {"result": {"result_url": "/outputs/sample.png"}}}',
            encoding="utf-8",
        )
        reference = _load_reference_internal_main(reference_dir)
        self.assertEqual(reference["result"]["result_url"], "/outputs/sample.png")


if __name__ == "__main__":
    unittest.main()
