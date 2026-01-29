import os
from redis import Redis
from rq import Connection
from rq.worker import Worker, SimpleWorker
from rq.timeouts import BaseDeathPenalty

REDIS_URL = os.getenv("REDIS_URL", "").strip()

def _split_queue_names(val: str) -> list[str]:
    return [p.strip() for p in val.replace(";", ",").split(",") if p.strip()]

RQ_QUEUE_NAME = os.getenv("RQ_QUEUE_NAME", "").strip()
RQ_QUEUE_RENDER = os.getenv("RQ_QUEUE_RENDER", "").strip()
RQ_QUEUE_UPSCALE = os.getenv("RQ_QUEUE_UPSCALE", "").strip()

queue_names = []
if RQ_QUEUE_NAME:
    queue_names.extend(_split_queue_names(RQ_QUEUE_NAME))
if RQ_QUEUE_RENDER:
    queue_names.append(RQ_QUEUE_RENDER)
if RQ_QUEUE_UPSCALE:
    queue_names.append(RQ_QUEUE_UPSCALE)

if not queue_names:
    queue_names = ["default"]

# De-duplicate while preserving order.
seen = set()
queue_names = [q for q in queue_names if not (q in seen or seen.add(q))]

if not REDIS_URL:
    raise SystemExit("REDIS_URL not configured")

conn = Redis.from_url(REDIS_URL)

worker_class = SimpleWorker if os.name == "nt" else Worker

with Connection(conn):
    worker = worker_class(queue_names)
    if os.name == "nt":
        class _NoopDeathPenalty(BaseDeathPenalty):
            def __enter__(self): return self
            def __exit__(self, exc_type, exc, tb): return False
        worker.disable_job_timeout = True
        worker.death_penalty_class = _NoopDeathPenalty
    worker.work()
