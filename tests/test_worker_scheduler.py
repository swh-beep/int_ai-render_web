import importlib
import os
import sys
import unittest
from unittest.mock import patch


class WorkerSchedulerTests(unittest.TestCase):
    def test_run_worker_enables_rq_scheduler(self):
        with patch.dict(
            os.environ,
            {
                "REDIS_URL": "redis://example:6379/0",
                "RQ_QUEUE_RENDER": "render",
                "RQ_QUEUE_UPSCALE": "upscale",
            },
        ):
            sys.modules.pop("worker", None)
            worker = importlib.import_module("worker")

        work_calls = []

        class FakeConnection:
            def __init__(self, conn):
                self.conn = conn

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeWorker:
            def __init__(self, queue_names):
                self.queue_names = queue_names
                self.disable_job_timeout = False
                self.death_penalty_class = None

            def work(self, **kwargs):
                work_calls.append(kwargs)
                return True

        with (
            patch.object(worker, "Connection", FakeConnection),
            patch.object(worker, "worker_class", FakeWorker),
        ):
            worker._run_worker()

        self.assertEqual(work_calls[-1]["with_scheduler"], True)


if __name__ == "__main__":
    unittest.main()
