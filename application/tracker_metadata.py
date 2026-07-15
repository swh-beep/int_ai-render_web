from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, field_validator


TRACKER_METADATA_FIELDS = (
    "service_source",
    "client_service",
    "environment",
    "is_internal",
    "journey_id",
    "request_id",
    "result_id",
    "parent_job_id",
    "job_kind",
)
TRACKER_MANIFEST_FIELDS = (
    "service_source",
    "client_service",
    "environment",
    "is_internal",
    "journey_id",
    "request_id",
    "result_id",
    "job_id",
    "parent_job_id",
    "job_kind",
    "terminal_status",
    "created_at_utc",
    "completed_at_utc",
    "usable_result_url_count",
    "candidate_generation_count",
)
SERVICE_SOURCE_VALUES = {"ai_designer", "ai_consultant"}
ENVIRONMENT_VALUES = {"production", "stage", "qa", "local"}
JOB_KIND_VALUES = {
    "preset",
    "cart",
    "cart_simple",
    "cart_simple_batch",
    "direct",
    "video",
    "detail",
    "empty_room",
}
TERMINAL_STATUS_VALUES = {"success", "failed", "timeout"}
_SAFE_TRACKER_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
_FIELD_MAX_LENGTHS = {
    "service_source": 13,
    "client_service": 80,
    "environment": 10,
    "journey_id": 36,
    "request_id": 80,
    "result_id": 80,
    "parent_job_id": 80,
    "job_kind": 24,
}


class TrackerMetadataRequestMixin(BaseModel):
    service_source: str | None = None
    client_service: str | None = None
    environment: str | None = None
    is_internal: bool | None = None
    journey_id: str | None = None
    request_id: str | None = None
    result_id: str | None = None
    parent_job_id: str | None = None
    job_kind: str | None = None

    @field_validator(
        "service_source",
        "client_service",
        "environment",
        "journey_id",
        "request_id",
        "result_id",
        "parent_job_id",
        "job_kind",
        mode="before",
    )
    @classmethod
    def _normalize_tracker_value(cls, value: Any, info) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("tracker metadata must be a bounded string")
        if isinstance(value, (int, float)):
            value = str(value)
        if not isinstance(value, str):
            raise ValueError("tracker metadata must be a bounded string")
        value = value.strip()
        if not value:
            return None
        max_length = _FIELD_MAX_LENGTHS.get(info.field_name, 80)
        if len(value) > max_length:
            raise ValueError(f"{info.field_name} must be at most {max_length} characters")
        if any(ch not in _SAFE_TRACKER_CHARS for ch in value):
            raise ValueError("tracker metadata contains unsupported characters")
        return value

    @field_validator("is_internal", mode="before")
    @classmethod
    def _validate_is_internal(cls, value: Any) -> bool | None:
        if value is None:
            return None
        if not isinstance(value, bool):
            raise ValueError("is_internal must be a JSON boolean")
        return value

    @field_validator("journey_id")
    @classmethod
    def _validate_journey_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise ValueError("journey_id must be a UUID string") from exc

    @field_validator("service_source")
    @classmethod
    def _validate_service_source(cls, value: str | None) -> str | None:
        if value is not None and value not in SERVICE_SOURCE_VALUES:
            raise ValueError("service_source is not allowed")
        return value

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, value: str | None) -> str | None:
        if value is not None and value not in ENVIRONMENT_VALUES:
            raise ValueError("environment is not allowed")
        return value

    @field_validator("job_kind")
    @classmethod
    def _validate_job_kind(cls, value: str | None) -> str | None:
        if value is not None and value not in JOB_KIND_VALUES:
            raise ValueError("job_kind is not allowed")
        return value


def extract_tracker_metadata(req: Any, *, default_job_kind: str | None = None) -> dict:
    metadata = {
        field: getattr(req, field, None)
        for field in TRACKER_METADATA_FIELDS
        if getattr(req, field, None) is not None
    }
    if metadata and default_job_kind:
        metadata["job_kind"] = default_job_kind
    return metadata


def attach_tracker_metadata(payload: dict, metadata: dict | None) -> dict:
    if not metadata:
        return payload
    next_payload = dict(payload)
    next_payload["tracker_metadata"] = {
        field: metadata[field]
        for field in TRACKER_METADATA_FIELDS
        if field in metadata and metadata[field] is not None
    }
    return next_payload


def tracker_metadata_from_payload(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    metadata = payload.get("tracker_metadata")
    if not isinstance(metadata, dict):
        return {}
    return {
        field: metadata[field]
        for field in TRACKER_METADATA_FIELDS
        if field in metadata and metadata[field] is not None
    }


def build_child_tracker_metadata(
    parent_metadata: dict,
    *,
    parent_job_id: str,
    child_result_id: str | None = None,
) -> dict:
    metadata = dict(parent_metadata or {})
    metadata["parent_job_id"] = parent_job_id
    if child_result_id:
        metadata["result_id"] = child_result_id
    return metadata


def normalize_terminal_status(result: dict | None, terminal_status: str | None = None) -> str:
    if terminal_status in TERMINAL_STATUS_VALUES:
        return terminal_status
    if isinstance(result, dict):
        error_text = str(result.get("error") or "").lower()
        if "timeout" in error_text:
            return "timeout"
        if result.get("error"):
            return "failed"
    return "success"


def _iso_from_value(value: Any) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def count_usable_result_urls(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    rows = result.get("results")
    if isinstance(rows, list):
        return sum(count_usable_result_urls(row) for row in rows if isinstance(row, dict))
    render = result.get("render") if isinstance(result.get("render"), dict) else result
    urls = render.get("result_urls") if isinstance(render, dict) else None
    if isinstance(urls, list):
        return sum(1 for value in urls if isinstance(value, str) and value.strip())
    url = render.get("result_url") if isinstance(render, dict) else None
    return 1 if isinstance(url, str) and url.strip() else 0


def count_candidate_generation_urls(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    rows = result.get("results")
    if isinstance(rows, list):
        return sum(count_candidate_generation_urls(row) for row in rows if isinstance(row, dict))
    render = result.get("render") if isinstance(result.get("render"), dict) else result
    candidates = render.get("candidate_result_urls") if isinstance(render, dict) else None
    if isinstance(candidates, list):
        return sum(1 for value in candidates if isinstance(value, str) and value.strip())
    return count_usable_result_urls(result)


def normalize_job_result_manifest(
    result: dict,
    *,
    metadata: dict | None,
    job_id: str,
    terminal_status: str | None = None,
    created_at_utc: Any = None,
    completed_at_utc: Any = None,
) -> dict:
    if not isinstance(result, dict):
        result = {"result": result}
    existing_manifest = result
    created = (
        _iso_from_value(created_at_utc)
        or _iso_from_value(existing_manifest.get("created_at_utc"))
        or _now_iso()
    )
    completed = (
        _iso_from_value(completed_at_utc)
        or _iso_from_value(existing_manifest.get("completed_at_utc"))
        or _now_iso()
    )
    normalized_status = normalize_terminal_status(result, terminal_status)
    manifest = {
        "service_source": None,
        "client_service": None,
        "environment": None,
        "is_internal": None,
        "journey_id": None,
        "request_id": None,
        "result_id": None,
        "job_id": job_id,
        "parent_job_id": None,
        "job_kind": None,
        "terminal_status": normalized_status,
        "created_at_utc": created,
        "completed_at_utc": completed,
        "usable_result_url_count": 0 if normalized_status != "success" else count_usable_result_urls(result),
        "candidate_generation_count": 0 if normalized_status != "success" else count_candidate_generation_urls(result),
    }
    for field, value in (metadata or {}).items():
        if field in TRACKER_METADATA_FIELDS:
            manifest[field] = value
    if manifest["parent_job_id"] == job_id:
        manifest["parent_job_id"] = None
    next_result = {
        field: value
        for field, value in result.items()
        if field not in TRACKER_MANIFEST_FIELDS and field != "tracker_manifest"
    }
    next_result.update(manifest)
    return next_result
