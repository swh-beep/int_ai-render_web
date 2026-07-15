from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from api_models import CartRenderRequest, CartSimpleBatchRequest, PresetRenderRequest
from application import job_entrypoints
from application.tracker_metadata import TRACKER_MANIFEST_FIELDS, normalize_job_result_manifest
from render_route_services import build_external_cart_batch_job, build_external_cart_job, build_external_preset_job


TRACKER_FIELD_SET = set(TRACKER_MANIFEST_FIELDS)
JOURNEY_ID = "123E4567-E89B-12D3-A456-426614174000"
CANONICAL_JOURNEY_ID = "123e4567-e89b-12d3-a456-426614174000"


def _preset_map():
    return {"preset-1": {"room": "livingroom", "style": "natural", "variant": "2"}}


def _cart_build_kwargs():
    return {
        "cart_max_items": 20,
        "apply_cart_limits": lambda items, limit: (items, []),
        "build_cart_summary": lambda items: "summary",
        "materialize_input": lambda url, prefix: "unused",
        "normalize_item_image": lambda local_path, unique_id, index: "unused",
        "resolve_image_url": lambda path, prefix=None: path,
        "build_s3_prefix": lambda audience, category, subfolder=None: f"{audience}/{category}/{subfolder or ''}",
        "build_item_target_key": lambda source, index, **kwargs: f"{source}_{index:03d}",
    }


def _fake_current_job():
    return SimpleNamespace(
        id="rq-parent-job",
        created_at=datetime(2026, 7, 14, 1, 2, 3, tzinfo=timezone.utc),
        enqueued_at=datetime(2026, 7, 14, 1, 2, 2, tzinfo=timezone.utc),
        ended_at=datetime(2026, 7, 14, 1, 5, 6, tzinfo=timezone.utc),
    )


def _fake_services(saved):
    return SimpleNamespace(
        normalize_audience=lambda audience: audience or "external",
        save_job_result=lambda job_id, result, audience=None: saved.append((job_id, result, audience)),
    )


def _tracked_preset_request(**overrides):
    data = {
        "image_url": "https://example.com/room.png",
        "preset_id": "preset-1",
        "service_source": "ai_designer",
        "client_service": "ai-consultant",
        "environment": "production",
        "is_internal": False,
        "journey_id": JOURNEY_ID,
        "request_id": "337",
        "result_id": "601",
    }
    data.update(overrides)
    return PresetRenderRequest(**data)


def test_old_untracked_payload_still_succeeds_without_enqueue_tracker_metadata():
    req = PresetRenderRequest(image_url="https://example.com/room.png", preset_id="preset-1")

    job_payload, resolved = build_external_preset_job(req, _preset_map())

    assert resolved == {"room": "livingroom", "style": "natural", "variant": "2"}
    assert "tracker_metadata" not in job_payload


def test_invalid_non_uuid_journey_is_rejected_and_valid_uuid_is_canonicalized():
    with pytest.raises(ValidationError):
        _tracked_preset_request(journey_id="not-a-uuid")

    req = _tracked_preset_request()

    assert req.journey_id == CANONICAL_JOURNEY_ID


def test_tracker_string_boundaries_are_enforced():
    req = _tracked_preset_request(client_service="x" * 80, request_id="r" * 80, result_id="s" * 80)

    assert req.client_service == "x" * 80
    assert req.request_id == "r" * 80
    assert req.result_id == "s" * 80

    with pytest.raises(ValidationError):
        _tracked_preset_request(client_service="x" * 81)
    with pytest.raises(ValidationError):
        _tracked_preset_request(request_id="r" * 81)
    with pytest.raises(ValidationError):
        _tracked_preset_request(parent_job_id="p" * 81)


def test_canonical_environment_and_service_source_are_enforced():
    for environment in ("production", "stage", "qa", "local"):
        assert _tracked_preset_request(environment=environment).environment == environment
    for service_source in ("ai_designer", "ai_consultant"):
        assert _tracked_preset_request(service_source=service_source).service_source == service_source

    for environment in ("prod", "staging", "dev", "test"):
        with pytest.raises(ValidationError):
            _tracked_preset_request(environment=environment)
    for service_source in ("admin", "internal", "external"):
        with pytest.raises(ValidationError):
            _tracked_preset_request(service_source=service_source)


def test_route_job_kind_is_authoritative_and_cannot_be_spoofed():
    req = _tracked_preset_request(job_kind="video")

    job_payload, _ = build_external_preset_job(req, _preset_map())

    assert job_payload["tracker_metadata"]["job_kind"] == "preset"


def test_batch_variant_tracker_override_cannot_cross_wire_parent_journey():
    req = CartSimpleBatchRequest(
        image_url="https://example.com/room.png",
        variants=[
            {
                "items": [{"id": "chair-1", "category": "chair", "image_url": "https://example.com/chair.png"}],
                "journey_id": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "service_source": "ai_consultant",
            "environment": "qa",
            "is_internal": True,
            }
        ],
        service_source="ai_designer",
        client_service="ai-consultant",
        environment="production",
        journey_id=JOURNEY_ID,
        request_id="337",
        result_id="601",
        is_internal=False,
    )

    job_payload, _ = build_external_cart_batch_job(req, **_cart_build_kwargs())

    assert job_payload["tracker_metadata"]["journey_id"] == CANONICAL_JOURNEY_ID
    assert job_payload["tracker_metadata"]["service_source"] == "ai_designer"
    assert job_payload["tracker_metadata"]["environment"] == "production"
    assert job_payload["tracker_metadata"]["is_internal"] is False
    assert "tracker_metadata" not in job_payload["variants"][0]


def test_tracked_request_metadata_propagates_to_enqueue_payload_with_canonical_values():
    req = _tracked_preset_request()

    job_payload, _ = build_external_preset_job(req, _preset_map())

    assert job_payload["tracker_metadata"] == {
        "service_source": "ai_designer",
        "client_service": "ai-consultant",
        "environment": "production",
        "is_internal": False,
        "journey_id": CANONICAL_JOURNEY_ID,
        "request_id": "337",
        "result_id": "601",
        "job_kind": "preset",
    }


def test_persisted_manifest_fields_are_top_level_without_nested_tracker_manifest():
    saved = []
    result = {
        "tracker_manifest": {"job_id": "nested-spoof"},
        "job_id": "top-level-spoof",
        "terminal_status": "failed",
        "created_at_utc": "not-a-date",
        "completed_at_utc": "not-a-date",
        "render": {
            "result_urls": ["https://cdn.example/final.png"],
            "candidate_result_urls": [
                "https://cdn.example/candidate-1.png",
                "https://cdn.example/candidate-2.png",
                "https://cdn.example/candidate-3.png",
            ],
        },
    }

    with (
        patch.object(job_entrypoints, "get_current_job", return_value=_fake_current_job()),
        patch.object(job_entrypoints, "_services", return_value=_fake_services(saved)),
    ):
        job_entrypoints._persist_job_result(
            result,
            audience="external",
            metadata={"service_source": "ai_designer", "environment": "production", "job_kind": "cart"},
        )

    persisted = saved[0][1]
    assert TRACKER_FIELD_SET.issubset(persisted.keys())
    assert set(persisted).intersection(TRACKER_FIELD_SET) == TRACKER_FIELD_SET
    assert "tracker_manifest" not in persisted
    assert persisted["job_id"] == "rq-parent-job"
    assert persisted["terminal_status"] == "success"
    assert persisted["created_at_utc"] == "2026-07-14T01:02:03+00:00"
    assert persisted["completed_at_utc"] == "2026-07-14T01:05:06+00:00"
    assert persisted["service_source"] == "ai_designer"
    assert persisted["usable_result_url_count"] == 1
    assert persisted["candidate_generation_count"] == 3


def test_untracked_manifest_has_null_optional_top_level_tracker_fields():
    normalized = normalize_job_result_manifest(
        {"render": {"result_urls": ["https://cdn.example/final.png"]}},
        metadata={},
        job_id="job-1",
        created_at_utc="2026-07-14T00:00:00+00:00",
        completed_at_utc="2026-07-14T00:01:00+00:00",
    )

    assert normalized["service_source"] is None
    assert normalized["client_service"] is None
    assert normalized["environment"] is None
    assert normalized["is_internal"] is None
    assert normalized["job_id"] == "job-1"
    assert "tracker_manifest" not in normalized


def test_authoritative_rq_fields_override_result_fields_and_invalid_existing_dates():
    saved = []
    result = {
        "job_id": "spoofed",
        "terminal_status": "failed",
        "created_at_utc": "invalid-date",
        "completed_at_utc": "invalid-date",
        "render": {"result_urls": ["https://cdn.example/final.png"]},
    }

    with (
        patch.object(job_entrypoints, "get_current_job", return_value=_fake_current_job()),
        patch.object(job_entrypoints, "_services", return_value=_fake_services(saved)),
    ):
        job_entrypoints._persist_job_result(result, audience="external", metadata={"job_kind": "cart"})

    persisted = saved[0][1]
    assert persisted["job_id"] == "rq-parent-job"
    assert persisted["terminal_status"] == "success"
    assert persisted["created_at_utc"] == "2026-07-14T01:02:03+00:00"
    assert persisted["completed_at_utc"] == "2026-07-14T01:05:06+00:00"


def test_terminal_status_ignores_arbitrary_result_status_but_honors_controlled_argument():
    spoofed = normalize_job_result_manifest(
        {"terminal_status": "failed", "render": {"result_urls": ["https://cdn.example/final.png"]}},
        metadata={},
        job_id="job-1",
        created_at_utc="2026-07-14T00:00:00+00:00",
        completed_at_utc="2026-07-14T00:01:00+00:00",
    )
    timeout = normalize_job_result_manifest(
        {"terminal_status": "success"},
        metadata={},
        job_id="job-2",
        terminal_status="timeout",
        created_at_utc="2026-07-14T00:00:00+00:00",
        completed_at_utc="2026-07-14T00:01:00+00:00",
    )

    assert spoofed["terminal_status"] == "success"
    assert timeout["terminal_status"] == "timeout"
    assert timeout["usable_result_url_count"] == 0


def test_batch_parent_and_children_are_flattened_without_fake_result_ids():
    saved = []
    result = {
        "empty_room_url": "https://cdn.example/empty.png",
        "results": [
            {
                "variant_index": 1,
                "render": {
                    "result_urls": ["https://cdn.example/final-1.png"],
                    "candidate_result_urls": ["https://cdn.example/c1.png", "https://cdn.example/c2.png"],
                },
            },
            {
                "variant_index": 2,
                "render": {
                    "result_urls": ["https://cdn.example/final-2.png"],
                    "candidate_result_urls": ["https://cdn.example/c3.png", "https://cdn.example/c4.png"],
                },
            },
        ],
    }

    with (
        patch.object(job_entrypoints, "get_current_job", return_value=_fake_current_job()),
        patch.object(job_entrypoints, "_services", return_value=_fake_services(saved)),
    ):
        job_entrypoints._persist_job_result(
            result,
            audience="external",
            metadata={"service_source": "ai_designer", "job_kind": "cart_simple_batch", "result_id": "601"},
        )

    persisted = saved[0][1]
    children = persisted["results"]
    assert persisted["job_id"] == "rq-parent-job"
    assert persisted["parent_job_id"] is None
    assert persisted["result_id"] == "601"
    assert persisted["usable_result_url_count"] == 2
    assert persisted["candidate_generation_count"] == 4
    assert [row["job_id"] for row in children] == [None, None]
    assert [row["parent_job_id"] for row in children] == ["rq-parent-job", "rq-parent-job"]
    assert [row["result_id"] for row in children] == [None, None]
    assert all("tracker_manifest" not in row for row in children)


def test_is_internal_requires_actual_json_boolean():
    assert _tracked_preset_request(is_internal=True).is_internal is True
    assert _tracked_preset_request(is_internal=False).is_internal is False
    for value in ("true", "false", 1, 0):
        with pytest.raises(ValidationError):
            _tracked_preset_request(is_internal=value)


def test_failure_and_timeout_manifest_normalization_do_not_invent_counts():
    failed = normalize_job_result_manifest(
        {"error": "render failed", "render": {"result_urls": ["https://cdn.example/ignored.png"]}},
        metadata={"job_kind": "cart"},
        job_id="job-failed",
        created_at_utc="2026-07-14T00:00:00+00:00",
        completed_at_utc="2026-07-14T00:01:00+00:00",
    )
    timeout = normalize_job_result_manifest(
        {"error": "worker timeout after 1800 seconds"},
        metadata={"job_kind": "cart"},
        job_id="job-timeout",
        created_at_utc="2026-07-14T00:00:00+00:00",
        completed_at_utc="2026-07-14T00:01:00+00:00",
    )

    assert failed["terminal_status"] == "failed"
    assert failed["usable_result_url_count"] == 0
    assert timeout["terminal_status"] == "timeout"
    assert timeout["candidate_generation_count"] == 0


def test_route_validation_rejects_invalid_tracker_payload_before_enqueue():
    client = TestClient(main.app)
    response = client.post(
        "/api/external/render/preset",
        json={
            "image_url": "https://example.com/room.png",
            "preset_id": "preset-1",
            "service_source": "external",
            "environment": "prod",
            "journey_id": "not-a-uuid",
        },
        headers={"x-api-key": "external-key"},
    )

    assert response.status_code == 422
