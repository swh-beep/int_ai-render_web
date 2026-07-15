from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from rq.timeouts import JobTimeoutException

import main
from application import job_entrypoints


TRACKER_METADATA = {
    "service_source": "ai_designer",
    "client_service": "ai-consultant",
    "environment": "production",
    "is_internal": False,
    "journey_id": "123e4567-e89b-12d3-a456-426614174000",
    "request_id": "337",
    "result_id": "601",
    "job_kind": "cart",
}


def _tracked_payload():
    return {
        "tracker_metadata": dict(TRACKER_METADATA),
        "render": {"audience": "external"},
    }


def _fake_job(payload, *, retries_left=0, args=None):
    return SimpleNamespace(
        id="rq-failed-job",
        args=args if args is not None else (payload,),
        retries_left=retries_left,
        created_at=datetime(2026, 7, 14, 1, 2, 3, tzinfo=timezone.utc),
        enqueued_at=datetime(2026, 7, 14, 1, 2, 2, tzinfo=timezone.utc),
        ended_at=datetime(2026, 7, 14, 1, 5, 6, tzinfo=timezone.utc),
    )


def _fake_services(saved):
    return SimpleNamespace(
        normalize_audience=lambda audience: audience or "external",
        save_job_result=lambda job_id, result, audience=None: saved.append((job_id, result, audience)),
    )


def test_failure_callback_persists_tracked_failed_manifest_without_exception_text():
    saved = []

    with patch.object(job_entrypoints, "_services", return_value=_fake_services(saved)):
        job_entrypoints.persist_tracked_failure_manifest(
            _fake_job(_tracked_payload()),
            None,
            RuntimeError,
            RuntimeError("raw customer exception text"),
            None,
        )

    assert len(saved) == 1
    job_id, result, audience = saved[0]
    assert job_id == "rq-failed-job"
    assert audience == "external"
    assert result["job_id"] == "rq-failed-job"
    assert result["terminal_status"] == "failed"
    assert result["service_source"] == "ai_designer"
    assert result["journey_id"] == "123e4567-e89b-12d3-a456-426614174000"
    assert result["created_at_utc"] == "2026-07-14T01:02:03+00:00"
    assert result["completed_at_utc"] == "2026-07-14T01:05:06+00:00"
    assert result["usable_result_url_count"] == 0
    assert result["candidate_generation_count"] == 0
    assert "raw customer exception text" not in str(result)


def test_failure_callback_selects_dict_arg_with_tracker_metadata():
    saved = []

    with patch.object(job_entrypoints, "_services", return_value=_fake_services(saved)):
        job_entrypoints.persist_tracked_failure_manifest(
            _fake_job(None, args=({"render": {"audience": "internal"}}, _tracked_payload())),
            None,
            RuntimeError,
            RuntimeError("boom"),
            None,
        )

    assert len(saved) == 1
    assert saved[0][1]["service_source"] == "ai_designer"
    assert saved[0][2] == "external"


def test_failure_callback_marks_timeout_only_for_rq_timeout_exception_type():
    saved = []

    with patch.object(job_entrypoints, "_services", return_value=_fake_services(saved)):
        job_entrypoints.persist_tracked_failure_manifest(
            _fake_job(_tracked_payload()),
            None,
            JobTimeoutException,
            JobTimeoutException("rq timeout"),
            None,
        )

    assert saved[0][1]["terminal_status"] == "timeout"
    assert saved[0][1]["usable_result_url_count"] == 0
    assert saved[0][1]["candidate_generation_count"] == 0


def test_failure_callback_skips_untracked_jobs():
    saved = []

    with patch.object(job_entrypoints, "_services", return_value=_fake_services(saved)):
        job_entrypoints.persist_tracked_failure_manifest(
            _fake_job({"render": {"audience": "external"}}),
            None,
            RuntimeError,
            RuntimeError("boom"),
            None,
        )

    assert saved == []


def test_failure_callback_skips_jobs_with_retries_remaining():
    saved = []

    with patch.object(job_entrypoints, "_services", return_value=_fake_services(saved)):
        job_entrypoints.persist_tracked_failure_manifest(
            _fake_job(_tracked_payload(), retries_left=1),
            None,
            RuntimeError,
            RuntimeError("boom"),
            None,
        )

    assert saved == []


def test_enqueue_job_attaches_failure_callback_only_for_tracked_payloads():
    calls = []

    class FakeQueue:
        def enqueue(self, func, *args, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(id="job-1")

    def _job(payload):
        return payload

    with patch.object(main, "_get_rq_queue", return_value=FakeQueue()):
        job, err = main._enqueue_job(_job, _tracked_payload())
        assert err is None
        assert job.id == "job-1"

        job, err = main._enqueue_job(_job, {"render": {"audience": "external"}})
        assert err is None
        assert job.id == "job-1"

    assert calls[0]["on_failure"] is job_entrypoints.persist_tracked_failure_manifest
    assert "on_failure" not in calls[1]
