import os
from redis import Redis
from rq import Connection
from rq.worker import Worker, SimpleWorker
from rq.timeouts import BaseDeathPenalty

REDIS_URL = os.getenv("REDIS_URL", "").strip()
RQ_QUEUE_NAME = os.getenv("RQ_QUEUE_NAME", "default").strip() or "default"

if not REDIS_URL:
    raise SystemExit("REDIS_URL not configured")

conn = Redis.from_url(REDIS_URL)

worker_class = SimpleWorker if os.name == "nt" else Worker

with Connection(conn):
    worker = worker_class([RQ_QUEUE_NAME])
    if os.name == "nt":
        class _NoopDeathPenalty(BaseDeathPenalty):
            def __enter__(self): return self
            def __exit__(self, exc_type, exc, tb): return False
        worker.disable_job_timeout = True
        worker.death_penalty_class = _NoopDeathPenalty
    worker.work()
