import os
import shutil
import unittest
from pathlib import Path

from infrastructure.ai.gemini_client import _reserve_qa_budget_call, get_qa_budget_snapshot


class GeminiBudgetTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path("outputs/test_artifacts/gemini_budget").resolve()
        self.budget_file = self.test_root / "budget.json"
        self.original_budget_file = os.environ.get("QA_GEMINI_BUDGET_FILE")
        self.original_max_calls = os.environ.get("QA_GEMINI_MAX_CALLS")
        self.test_root.mkdir(parents=True, exist_ok=True)
        os.environ["QA_GEMINI_BUDGET_FILE"] = str(self.budget_file)
        os.environ["QA_GEMINI_MAX_CALLS"] = "2"

    def tearDown(self):
        if self.original_budget_file is None:
            os.environ.pop("QA_GEMINI_BUDGET_FILE", None)
        else:
            os.environ["QA_GEMINI_BUDGET_FILE"] = self.original_budget_file
        if self.original_max_calls is None:
            os.environ.pop("QA_GEMINI_MAX_CALLS", None)
        else:
            os.environ["QA_GEMINI_MAX_CALLS"] = self.original_max_calls
        shutil.rmtree(self.test_root, ignore_errors=True)

    def test_budget_snapshot_initializes_and_tracks_count(self):
        initial = get_qa_budget_snapshot()
        self.assertTrue(initial["enabled"])
        self.assertEqual(initial["count"], 0)
        self.assertEqual(initial["remaining"], 2)

        first = _reserve_qa_budget_call(model_name="test-model", log_tag="phase0")
        second = _reserve_qa_budget_call(model_name="test-model", log_tag="phase0")
        self.assertEqual(first["count"], 1)
        self.assertEqual(second["count"], 2)

        final = get_qa_budget_snapshot()
        self.assertEqual(final["count"], 2)
        self.assertEqual(final["remaining"], 0)
        self.assertEqual(len(final["events"]), 2)

    def test_budget_raises_when_limit_is_exceeded(self):
        _reserve_qa_budget_call(model_name="test-model", log_tag="phase0")
        _reserve_qa_budget_call(model_name="test-model", log_tag="phase0")
        with self.assertRaises(RuntimeError):
            _reserve_qa_budget_call(model_name="test-model", log_tag="phase0")


if __name__ == "__main__":
    unittest.main()
