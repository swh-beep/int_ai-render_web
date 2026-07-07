import argparse
import json
import os
import shutil
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main
from application.http.local_job_store import clear_local_jobs


DEFAULT_MANIFEST = REPO_ROOT / "tests" / "replay_cases" / "9ffde1c0_compare" / "manifest.json"
DEFAULT_JOB_TIMEOUT_SEC = int(os.getenv("LIVE_MATRIX_JOB_TIMEOUT_SEC", "3600") or "3600")
DEFAULT_POLL_INTERVAL_SEC = float(os.getenv("LIVE_MATRIX_POLL_INTERVAL_SEC", "2.0") or "2.0")
DEFAULT_RUN_COUNT = int(os.getenv("LIVE_MATRIX_RUN_COUNT", "3") or "3")
DEFAULT_VIDEO_CLIP_COUNT = 4
DEFAULT_VIDEO_CFG_SCALE = 0.5
DEFAULT_VIDEO_TIMEOUT_SEC = int(os.getenv("LIVE_MATRIX_VIDEO_TIMEOUT_SEC", str(DEFAULT_JOB_TIMEOUT_SEC)) or str(DEFAULT_JOB_TIMEOUT_SEC))
DEFAULT_OUTPUT_FOLDER_PREFIX = "live_test_render_engine_matrix"
EXTERNAL_HEADERS = {"x-api-key": os.getenv("LIVE_MATRIX_DUMMY_API_KEY", "local-live-test")}
ARTIFACT_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".mp4",
    ".mov",
    ".webm",
    ".avi",
    ".mkv",
    ".json",
}


@dataclass
class FixtureManifest:
    manifest_path: Path
    form_data: dict[str, Any]
    room_file: dict[str, Any]
    item_files_field: str
    item_files: dict[str, str]
    items_json: list[dict[str, Any]]


class MatrixError(RuntimeError):
    pass


def _resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _load_manifest(manifest_path: str | Path) -> FixtureManifest:
    manifest_file = _resolve_repo_path(str(manifest_path))
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(payload.get("form_data"), dict):
        raise MatrixError("Manifest form_data is required")
    if not isinstance(payload.get("room_file"), dict):
        raise MatrixError("Manifest room_file is required")
    if not isinstance(payload.get("item_files"), dict):
        raise MatrixError("Manifest item_files is required")
    if not isinstance(payload.get("items_json"), list) or not payload["items_json"]:
        raise MatrixError("Manifest items_json must be a non-empty list")

    room_file = dict(payload["room_file"])
    room_file["path"] = str(_resolve_repo_path(room_file["path"]))
    item_files = {client_id: str(_resolve_repo_path(path_str)) for client_id, path_str in payload["item_files"].items()}

    return FixtureManifest(
        manifest_path=manifest_file,
        form_data=dict(payload["form_data"]),
        room_file=room_file,
        item_files_field=str(payload.get("item_files_field") or "item_images"),
        item_files=item_files,
        items_json=list(payload["items_json"]),
    )


def _configure_main_for_local_queue() -> None:
    main.LOCAL_INLINE_QUEUE_ENABLED = True
    main.API_AUTH_DISABLED = True
    main.REDIS_URL = ""
    clear_local_jobs()
    if not getattr(main, "PRESET_MAP_PATH", ""):
        preset_map_path = (REPO_ROOT / "preset_map.json").resolve()
        if not preset_map_path.exists():
            raise MatrixError(f"preset_map.json not found: {preset_map_path}")
        main.PRESET_MAP_PATH = str(preset_map_path)
    main.PRESET_MAP_CACHE = None


def _desktop_output_root() -> Path:
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return desktop / f"{DEFAULT_OUTPUT_FOLDER_PREFIX}_{timestamp}"


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sanitize_token(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value.strip())
    return safe.strip("._") or "value"


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _seconds_between(start: str | None, end: str | None) -> float | None:
    start_dt = _parse_iso_datetime(start)
    end_dt = _parse_iso_datetime(end)
    if start_dt is None or end_dt is None:
        return None
    return max(0.0, (end_dt - start_dt).total_seconds())


def _collect_candidate_artifacts(payload: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def _visit(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                _visit(child)
            return
        if isinstance(value, list):
            for child in value:
                _visit(child)
            return
        if not isinstance(value, str):
            return

        token = value.strip()
        if not token or token in seen:
            return

        lowered = token.lower()
        parsed = urlparse(token)
        parsed_suffix = Path(parsed.path).suffix.lower()
        token_path = Path(token)

        is_http = parsed.scheme in {"http", "https"} and bool(parsed.netloc)
        is_outputs_url = token.startswith("/outputs/") or token.startswith("/assets/")
        is_relative_outputs = lowered.startswith("outputs/") or lowered.startswith("outputs\\")
        is_output_path = token_path.is_absolute() and str(token_path).lower().startswith(str((REPO_ROOT / "outputs").resolve()).lower())
        is_named_artifact = parsed_suffix in ARTIFACT_EXTENSIONS or token_path.suffix.lower() in ARTIFACT_EXTENSIONS

        if is_http or is_outputs_url or is_relative_outputs or is_output_path:
            if is_named_artifact or is_outputs_url or is_relative_outputs or is_output_path:
                seen.add(token)
                found.append(token)

    _visit(payload)
    return found


def _copy_stream_to_path(response: requests.Response, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)


def _artifact_destination_name(source: str, index: int) -> str:
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        basename = Path(parsed.path).name
    else:
        basename = Path(source.replace("\\", "/")).name
    if not basename:
        basename = f"artifact_{index:03d}.bin"
    return f"{index:03d}_{basename}"


def _copy_artifact(source: str, destination_dir: Path) -> dict[str, Any]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / _artifact_destination_name(source, index=len(list(destination_dir.glob("*"))) + 1)
    parsed = urlparse(source)

    if parsed.scheme in {"http", "https"}:
        response = requests.get(source, stream=True, timeout=120)
        response.raise_for_status()
        _copy_stream_to_path(response, destination)
        return {"source": source, "copied_to": str(destination), "mode": "http"}

    if source.startswith("/outputs/") or source.startswith("/assets/"):
        local_source = REPO_ROOT / source.lstrip("/").replace("/", os.sep)
    elif source.lower().startswith("outputs/") or source.lower().startswith("outputs\\"):
        local_source = REPO_ROOT / Path(source)
    else:
        local_source = Path(source)

    local_source = local_source.resolve()
    if not local_source.exists():
        raise FileNotFoundError(f"Artifact source does not exist: {source}")
    shutil.copy2(local_source, destination)
    return {"source": source, "copied_to": str(destination), "mode": "local"}


def _copy_artifacts_from_payload(payload: Any, destination_dir: Path) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for source in _collect_candidate_artifacts(payload):
        try:
            copied.append(_copy_artifact(source, destination_dir))
        except Exception as exc:
            copied.append({"source": source, "error": str(exc)})
    return copied


def _poll_job(client: TestClient, job_id: str, timeout_sec: int, poll_interval_sec: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    last_payload: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}")
        if response.status_code == 404:
            time.sleep(poll_interval_sec)
            continue
        if response.status_code != 200:
            raise MatrixError(f"GET /jobs/{job_id} failed: {response.status_code} {response.text}")
        last_payload = response.json()
        status = str(last_payload.get("status") or "").lower()
        if status in {"finished", "failed"}:
            return last_payload
        time.sleep(poll_interval_sec)
    raise TimeoutError(f"Timed out waiting for job {job_id}: {last_payload}")


def _summarize_timings(request_started: float, request_finished: float, final_status: dict[str, Any]) -> dict[str, Any]:
    enqueued_at = final_status.get("enqueued_at")
    started_at = final_status.get("started_at")
    ended_at = final_status.get("ended_at")
    return {
        "job_id_latency_sec": round(max(0.0, request_finished - request_started), 3),
        "total_duration_sec": round(max(0.0, time.monotonic() - request_started), 3),
        "queue_wait_sec": _seconds_between(enqueued_at, started_at),
        "job_run_sec": _seconds_between(started_at, ended_at),
        "job_end_to_end_sec": _seconds_between(enqueued_at, ended_at),
    }


def _primary_render_url(render_result: dict[str, Any]) -> str | None:
    result_url = render_result.get("result_url")
    if isinstance(result_url, str) and result_url.strip():
        return result_url.strip()
    result_urls = render_result.get("result_urls") or []
    if isinstance(result_urls, list):
        for value in result_urls:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _render_block_reason(render_result: Any, *, require_furniture_data: bool = False) -> str | None:
    if not isinstance(render_result, dict):
        return "render result is not a dict"
    if render_result.get("error"):
        return f"render error: {render_result.get('error')}"
    if render_result.get("final_result_blocked") is True:
        return "render final_result_blocked=true"
    if str(render_result.get("message") or "").strip().lower() == "qc blocked final selection":
        return "render message indicates QC blocked final selection"

    primary_url = _primary_render_url(render_result)
    if not primary_url:
        return "render result_url/result_urls is missing"
    empty_room_url = render_result.get("empty_room_url")
    if isinstance(empty_room_url, str) and empty_room_url.strip() and primary_url == empty_room_url.strip():
        return "render primary result is the empty-room fallback"

    if require_furniture_data and not render_result.get("furniture_data"):
        return "render furniture_data is missing"
    return None


def _is_finished_success(row: dict[str, Any]) -> bool:
    status = row.get("job_status") or {}
    return (
        str(status.get("status") or "").lower() == "finished"
        and not status.get("error")
        and isinstance(status.get("result"), dict)
        and not status["result"].get("error")
    )


def _external_video_block_reason(row: dict[str, Any]) -> str | None:
    if not _is_finished_success(row):
        return "source render job did not finish successfully"
    source_result = row["job_status"]["result"]
    if isinstance(source_result, dict) and source_result.get("video_enabled") is False:
        reason = source_result.get("video_disabled_reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
        return "video generation is disabled for this render job"
    render_result = source_result.get("render") if isinstance(source_result, dict) else None
    reason = _render_block_reason(render_result)
    if reason:
        return reason
    details = source_result.get("details") or {}
    detail_urls = [
        item.get("url")
        for item in details.get("details") or []
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item.get("url").strip()
    ]
    if not _primary_render_url(render_result) and not detail_urls:
        return "source render has no usable video images"
    return None


def _failure_result(case_name: str, run_dir: Path, request_payload: Any, error: str, started_at: float | None = None) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    final_status = {"status": "failed", "error": error, "result": None}
    timings = {
        "job_id_latency_sec": None,
        "total_duration_sec": round(max(0.0, time.monotonic() - started_at), 3) if started_at is not None else None,
        "queue_wait_sec": None,
        "job_run_sec": None,
        "job_end_to_end_sec": None,
    }
    _json_dump(run_dir / "request.json", request_payload)
    _json_dump(run_dir / "job_status.json", final_status)
    _json_dump(run_dir / "error.json", {"error": error})
    return {
        "case": case_name,
        "run_dir": str(run_dir),
        "request": request_payload,
        "enqueue_response": {},
        "job_status": final_status,
        "copied_artifacts": [],
        "timings": timings,
    }


def _assert_response_ok(response, path: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise MatrixError(f"{path} failed: {response.status_code} {response.text}")
    payload = response.json()
    if not payload.get("job_id"):
        raise MatrixError(f"{path} did not return job_id: {payload}")
    return payload


def _build_internal_async_render_request(
    manifest: FixtureManifest,
) -> tuple[dict[str, Any], list[tuple[str, tuple[str, Any, str]]], ExitStack]:
    data = {
        "room": str(manifest.form_data.get("room") or ""),
        "style": str(manifest.form_data.get("style") or ""),
        "variant": str(manifest.form_data.get("variant") or ""),
        "items_json": json.dumps(manifest.items_json, ensure_ascii=False),
        "dimensions": str(manifest.form_data.get("dimensions") or ""),
        "placement": str(manifest.form_data.get("placement") or ""),
    }
    files: list[tuple[str, tuple[str, Any, str]]] = []
    stack = ExitStack()
    room_handle = stack.enter_context(open(manifest.room_file["path"], "rb"))
    room_name = Path(manifest.room_file["path"]).name
    room_content_type = str(manifest.room_file.get("content_type") or "application/octet-stream")
    files.append((str(manifest.room_file.get("field") or "file"), (room_name, room_handle, room_content_type)))
    for item in manifest.items_json:
        client_id = item["client_id"]
        item_path = manifest.item_files[client_id]
        item_handle = stack.enter_context(open(item_path, "rb"))
        files.append((manifest.item_files_field, (Path(item_path).name, item_handle, "application/octet-stream")))
    return data, files, stack


def _build_external_cart_items(manifest: FixtureManifest) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in manifest.items_json:
        items.append(
            {
                "id": row["client_id"],
                "name": row.get("name"),
                "category": row["category"],
                "image_url": manifest.item_files[row["client_id"]],
                "qty": int(row.get("qty", 1) or 1),
                "dims_mm": row.get("dims_mm"),
                "options": row.get("options"),
            }
        )
    return items


def _preset_sort_key(preset_id: str) -> tuple[str, int]:
    tail = preset_id.rsplit("_", 1)
    if len(tail) == 2 and tail[1].isdigit():
        return tail[0], int(tail[1])
    return preset_id, 0


def _choose_preset_id(manifest: FixtureManifest) -> str:
    override = os.getenv("LIVE_MATRIX_PRESET_ID", "").strip()
    preset_map_path = _resolve_repo_path(main.PRESET_MAP_PATH)
    preset_map = json.loads(preset_map_path.read_text(encoding="utf-8"))
    if not isinstance(preset_map, dict) or not preset_map:
        raise MatrixError(f"Preset map is empty or invalid: {preset_map_path}")
    if override:
        if override not in preset_map:
            raise MatrixError(f"LIVE_MATRIX_PRESET_ID not found in preset_map.json: {override}")
        return override

    desired_room = str(manifest.form_data.get("room") or "").strip().lower()
    candidates = [
        preset_id
        for preset_id, row in preset_map.items()
        if isinstance(row, dict) and str(row.get("room") or "").strip().lower() == desired_room
    ]
    if not candidates:
        candidates = list(preset_map.keys())
    return sorted(candidates, key=_preset_sort_key)[0]


def _save_run_files(run_dir: Path, request_payload: Any, enqueue_payload: Any, final_status: Any, copied_artifacts: Any) -> None:
    _json_dump(run_dir / "request.json", request_payload)
    _json_dump(run_dir / "enqueue_response.json", enqueue_payload)
    _json_dump(run_dir / "job_status.json", final_status)
    _json_dump(run_dir / "artifacts.json", copied_artifacts)


def _run_async_render(client: TestClient, manifest: FixtureManifest, run_dir: Path, timeout_sec: int, poll_interval_sec: float) -> dict[str, Any]:
    data, files, stack = _build_internal_async_render_request(manifest)
    request_started = time.monotonic()
    try:
        with stack:
            response = client.post("/async/render", data=data, files=files)
    finally:
        request_finished = time.monotonic()
    enqueue_payload = _assert_response_ok(response, "/async/render")
    final_status = _poll_job(client, enqueue_payload["job_id"], timeout_sec=timeout_sec, poll_interval_sec=poll_interval_sec)
    timings = _summarize_timings(request_started, request_finished, final_status)
    copied_artifacts = _copy_artifacts_from_payload(final_status.get("result"), run_dir / "artifacts")
    _save_run_files(run_dir, data | {"manifest": str(manifest.manifest_path)}, enqueue_payload, final_status, copied_artifacts)
    return {
        "case": "internal_main_render",
        "run_dir": str(run_dir),
        "request": data,
        "enqueue_response": enqueue_payload,
        "job_status": final_status,
        "copied_artifacts": copied_artifacts,
        "timings": timings,
    }


def _run_generate_details(
    client: TestClient,
    *,
    seed_result: dict[str, Any],
    run_dir: Path,
    timeout_sec: int,
    poll_interval_sec: float,
    case_name: str = "internal_generate_details",
) -> dict[str, Any]:
    image_url = seed_result.get("result_url")
    if not image_url:
        result_urls = seed_result.get("result_urls") or []
        if isinstance(result_urls, list) and result_urls:
            image_url = result_urls[0]
    if not image_url:
        raise MatrixError("Detail generation seed is missing result_url/result_urls")
    if not seed_result.get("furniture_data"):
        raise MatrixError("Detail generation seed is missing furniture_data")
    request_payload = {
        "image_url": image_url,
        "furniture_data": seed_result.get("furniture_data"),
        "audience": "internal",
        "simple_generation_mode": True,
    }
    request_started = time.monotonic()
    response = client.post("/generate-details", json=request_payload)
    request_finished = time.monotonic()
    enqueue_payload = _assert_response_ok(response, "/generate-details")
    final_status = _poll_job(client, enqueue_payload["job_id"], timeout_sec=timeout_sec, poll_interval_sec=poll_interval_sec)
    timings = _summarize_timings(request_started, request_finished, final_status)
    copied_artifacts = _copy_artifacts_from_payload(final_status.get("result"), run_dir / "artifacts")
    _save_run_files(run_dir, request_payload, enqueue_payload, final_status, copied_artifacts)
    return {
        "case": case_name,
        "run_dir": str(run_dir),
        "request": request_payload,
        "enqueue_response": enqueue_payload,
        "job_status": final_status,
        "copied_artifacts": copied_artifacts,
        "timings": timings,
    }


def _run_json_job(
    client: TestClient,
    *,
    path: str,
    request_payload: dict[str, Any],
    run_dir: Path,
    timeout_sec: int,
    poll_interval_sec: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_started = time.monotonic()
    response = client.post(path, json=request_payload, headers=headers or {})
    request_finished = time.monotonic()
    enqueue_payload = _assert_response_ok(response, path)
    final_status = _poll_job(client, enqueue_payload["job_id"], timeout_sec=timeout_sec, poll_interval_sec=poll_interval_sec)
    timings = _summarize_timings(request_started, request_finished, final_status)
    copied_artifacts = _copy_artifacts_from_payload(final_status.get("result"), run_dir / "artifacts")
    _save_run_files(run_dir, request_payload, enqueue_payload, final_status, copied_artifacts)
    return {
        "case": path,
        "run_dir": str(run_dir),
        "request": request_payload,
        "enqueue_response": enqueue_payload,
        "job_status": final_status,
        "copied_artifacts": copied_artifacts,
        "timings": timings,
    }


def _run_external_video(
    client: TestClient,
    *,
    render_job_id: str,
    run_dir: Path,
    timeout_sec: int,
    poll_interval_sec: float,
) -> dict[str, Any]:
    request_payload = {
        "render_job_id": render_job_id,
        "clip_count": DEFAULT_VIDEO_CLIP_COUNT,
        "cfg_scale": DEFAULT_VIDEO_CFG_SCALE,
    }
    return _run_json_job(
        client,
        path="/api/external/render/video",
        request_payload=request_payload,
        run_dir=run_dir,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
        headers=EXTERNAL_HEADERS,
    )


def _build_summary(
    results: dict[str, list[dict[str, Any]]],
    output_root: Path,
    manifest: FixtureManifest,
    preset_id: str,
    *,
    timeout_sec: int,
    poll_interval_sec: float,
) -> dict[str, Any]:
    summary_cases: dict[str, list[dict[str, Any]]] = {}
    for case_name, rows in results.items():
        summary_rows: list[dict[str, Any]] = []
        for row in rows:
            job_status = row.get("job_status") or {}
            timings = row.get("timings") or {}
            summary_rows.append(
                {
                    "run_dir": row.get("run_dir"),
                    "job_id": (row.get("enqueue_response") or {}).get("job_id"),
                    "status": job_status.get("status"),
                    "job_id_latency_sec": timings.get("job_id_latency_sec"),
                    "total_duration_sec": timings.get("total_duration_sec"),
                    "queue_wait_sec": timings.get("queue_wait_sec"),
                    "job_run_sec": timings.get("job_run_sec"),
                    "job_end_to_end_sec": timings.get("job_end_to_end_sec"),
                    "result_error": (job_status.get("result") or {}).get("error") if isinstance(job_status.get("result"), dict) else None,
                    "job_error": job_status.get("error"),
                    "artifact_count": len(row.get("copied_artifacts") or []),
                }
            )
        summary_cases[case_name] = summary_rows

    return {
        "manifest_path": str(manifest.manifest_path),
        "output_root": str(output_root),
        "preset_id": preset_id,
        "job_timeout_sec": timeout_sec,
        "poll_interval_sec": poll_interval_sec,
        "results": summary_cases,
    }


def run_matrix(
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    output_root: str | Path | None = None,
    run_count: int = DEFAULT_RUN_COUNT,
    timeout_sec: int = DEFAULT_JOB_TIMEOUT_SEC,
    poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
) -> tuple[Path, dict[str, Any]]:
    if run_count < 1:
        raise MatrixError("run_count must be at least 1")

    manifest = _load_manifest(manifest_path)
    _configure_main_for_local_queue()
    preset_id = _choose_preset_id(manifest)
    output_dir = _resolve_repo_path(str(output_root)) if output_root else _desktop_output_root()
    output_dir.mkdir(parents=True, exist_ok=True)

    detailed_results: dict[str, list[dict[str, Any]]] = {
        "internal_main_render": [],
        "internal_main_detail": [],
        "internal_detail_only": [],
        "external_render_cart": [],
        "external_render_cart_video": [],
        "external_render_preset": [],
        "external_render_preset_video": [],
        "external_render_cart_simple": [],
    }

    cart_items = _build_external_cart_items(manifest)
    if int(getattr(main, "CART_MAX_ITEMS", len(cart_items)) or len(cart_items)) < len(cart_items):
        raise MatrixError(
            f"CART_MAX_ITEMS={getattr(main, 'CART_MAX_ITEMS', None)} would drop fixture items; "
            f"need at least {len(cart_items)} for the 10-item cart test"
        )
    base_room_image_url = manifest.room_file["path"]
    external_cart_payload = {
        "image_url": base_room_image_url,
        "items": cart_items,
        "room": str(manifest.form_data.get("room") or ""),
        "style": str(manifest.form_data.get("style") or ""),
        "variant": str(manifest.form_data.get("variant") or ""),
        "dimensions": str(manifest.form_data.get("dimensions") or ""),
        "placement": str(manifest.form_data.get("placement") or ""),
    }
    external_preset_payload = {
        "image_url": base_room_image_url,
        "preset_id": preset_id,
    }

    with TestClient(main.app) as client:
        for run_index in range(1, run_count + 1):
            main_run_dir = output_dir / "internal_main_detail" / f"run_{run_index:02d}" / "main"
            try:
                main_result = _run_async_render(
                    client,
                    manifest=manifest,
                    run_dir=main_run_dir,
                    timeout_sec=timeout_sec,
                    poll_interval_sec=poll_interval_sec,
                )
            except Exception as exc:
                main_result = _failure_result(
                    "internal_main_render",
                    main_run_dir,
                    {"manifest": str(manifest.manifest_path)},
                    str(exc),
                )
            detailed_results["internal_main_render"].append(main_result)

            render_payload = (main_result.get("job_status") or {}).get("result")
            details_run_dir = output_dir / "internal_main_detail" / f"run_{run_index:02d}" / "details"
            block_reason = _render_block_reason(render_payload, require_furniture_data=True)
            if block_reason:
                detailed_results["internal_main_detail"].append(
                    _failure_result(
                        "internal_main_detail",
                        details_run_dir,
                        {"seed_run": run_index},
                        f"Skipped because internal main render is not usable: {block_reason}",
                    )
                )
            else:
                try:
                    detailed_results["internal_main_detail"].append(
                        _run_generate_details(
                            client,
                            seed_result=render_payload,
                            run_dir=details_run_dir,
                            timeout_sec=timeout_sec,
                            poll_interval_sec=poll_interval_sec,
                            case_name="internal_main_detail",
                        )
                    )
                except Exception as exc:
                    detailed_results["internal_main_detail"].append(
                        _failure_result("internal_main_detail", details_run_dir, {"seed_run": run_index}, str(exc))
                    )

        successful_internal_main = [
            row
            for row in detailed_results["internal_main_render"]
            if _is_finished_success(row)
            and _render_block_reason(row["job_status"]["result"], require_furniture_data=True) is None
        ]
        detail_seed_result = successful_internal_main[0]["job_status"]["result"] if successful_internal_main else None

        for run_index in range(1, run_count + 1):
            run_dir = output_dir / "internal_detail_only" / f"run_{run_index:02d}"
            if detail_seed_result is None:
                detailed_results["internal_detail_only"].append(
                    _failure_result(
                        "internal_detail_only",
                        run_dir,
                        {"seed": "first successful internal main render"},
                        "Skipped because no usable internal main render seed was available",
                    )
                )
            else:
                try:
                    detailed_results["internal_detail_only"].append(
                        _run_generate_details(
                            client,
                            seed_result=detail_seed_result,
                            run_dir=run_dir,
                            timeout_sec=timeout_sec,
                            poll_interval_sec=poll_interval_sec,
                            case_name="internal_detail_only",
                        )
                    )
                except Exception as exc:
                    detailed_results["internal_detail_only"].append(
                        _failure_result("internal_detail_only", run_dir, {"seed": "first successful internal main render"}, str(exc))
                    )

        for run_index in range(1, run_count + 1):
            run_dir = output_dir / "external_render_cart" / f"run_{run_index:02d}"
            try:
                render_result = _run_json_job(
                    client,
                    path="/api/external/render/cart",
                    request_payload=external_cart_payload,
                    run_dir=run_dir / "render",
                    timeout_sec=timeout_sec,
                    poll_interval_sec=poll_interval_sec,
                    headers=EXTERNAL_HEADERS,
                )
            except Exception as exc:
                render_result = _failure_result("external_render_cart", run_dir / "render", external_cart_payload, str(exc))
            detailed_results["external_render_cart"].append(render_result)
            render_job_id = (render_result.get("enqueue_response") or {}).get("job_id")
            video_block_reason = _external_video_block_reason(render_result)
            if render_job_id and not video_block_reason:
                try:
                    detailed_results["external_render_cart_video"].append(
                        _run_external_video(
                            client,
                            render_job_id=render_job_id,
                            run_dir=run_dir / "video",
                            timeout_sec=DEFAULT_VIDEO_TIMEOUT_SEC,
                            poll_interval_sec=poll_interval_sec,
                        )
                    )
                except Exception as exc:
                    detailed_results["external_render_cart_video"].append(
                        _failure_result("external_render_cart_video", run_dir / "video", {"render_job_id": render_job_id}, str(exc))
                    )
            else:
                detailed_results["external_render_cart_video"].append(
                    _failure_result(
                        "external_render_cart_video",
                        run_dir / "video",
                        {"render_job_id": render_job_id},
                        f"Skipped because cart render is not usable for video: {video_block_reason or 'missing render job id'}",
                    )
                )

        for run_index in range(1, run_count + 1):
            run_dir = output_dir / "external_render_preset" / f"run_{run_index:02d}"
            try:
                render_result = _run_json_job(
                    client,
                    path="/api/external/render/preset",
                    request_payload=external_preset_payload,
                    run_dir=run_dir / "render",
                    timeout_sec=timeout_sec,
                    poll_interval_sec=poll_interval_sec,
                    headers=EXTERNAL_HEADERS,
                )
            except Exception as exc:
                render_result = _failure_result("external_render_preset", run_dir / "render", external_preset_payload, str(exc))
            detailed_results["external_render_preset"].append(render_result)
            render_job_id = (render_result.get("enqueue_response") or {}).get("job_id")
            video_block_reason = _external_video_block_reason(render_result)
            if render_job_id and not video_block_reason:
                try:
                    detailed_results["external_render_preset_video"].append(
                        _run_external_video(
                            client,
                            render_job_id=render_job_id,
                            run_dir=run_dir / "video",
                            timeout_sec=DEFAULT_VIDEO_TIMEOUT_SEC,
                            poll_interval_sec=poll_interval_sec,
                        )
                    )
                except Exception as exc:
                    detailed_results["external_render_preset_video"].append(
                        _failure_result("external_render_preset_video", run_dir / "video", {"render_job_id": render_job_id}, str(exc))
                    )
            else:
                detailed_results["external_render_preset_video"].append(
                    _failure_result(
                        "external_render_preset_video",
                        run_dir / "video",
                        {"render_job_id": render_job_id},
                        f"Skipped because preset render is not usable for video: {video_block_reason or 'missing render job id'}",
                    )
                )

        for run_index in range(1, run_count + 1):
            run_dir = output_dir / "external_render_cart_simple" / f"run_{run_index:02d}"
            try:
                detailed_results["external_render_cart_simple"].append(
                    _run_json_job(
                        client,
                        path="/api/external/render/cart-simple",
                        request_payload=external_cart_payload,
                        run_dir=run_dir,
                        timeout_sec=timeout_sec,
                        poll_interval_sec=poll_interval_sec,
                        headers=EXTERNAL_HEADERS,
                    )
                )
            except Exception as exc:
                detailed_results["external_render_cart_simple"].append(
                    _failure_result("external_render_cart_simple", run_dir, external_cart_payload, str(exc))
                )

    summary = _build_summary(
        detailed_results,
        output_dir,
        manifest,
        preset_id,
        timeout_sec=timeout_sec,
        poll_interval_sec=poll_interval_sec,
    )
    _json_dump(output_dir / "results.json", detailed_results)
    _json_dump(output_dir / "summary.json", summary)
    return output_dir, summary


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Run the live FastAPI route matrix against local inline queue jobs.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to the replay manifest JSON.")
    parser.add_argument("--output-root", help="Optional absolute or repo-relative output directory. Default is a timestamped Desktop folder.")
    parser.add_argument("--run-count", type=int, default=DEFAULT_RUN_COUNT, help="Number of runs per route variant.")
    parser.add_argument("--timeout-sec", type=int, default=DEFAULT_JOB_TIMEOUT_SEC, help="Per-job timeout in seconds.")
    parser.add_argument("--poll-interval-sec", type=float, default=DEFAULT_POLL_INTERVAL_SEC, help="Polling interval in seconds.")
    args = parser.parse_args()

    try:
        output_dir, summary = run_matrix(
            manifest_path=args.manifest,
            output_root=args.output_root,
            run_count=args.run_count,
            timeout_sec=args.timeout_sec,
            poll_interval_sec=args.poll_interval_sec,
        )
        print(json.dumps({"output_dir": str(output_dir), "summary_path": str(output_dir / "summary.json"), "summary": summary}, ensure_ascii=False, indent=2))
        failures = []
        for case_rows in summary.get("results", {}).values():
            for row in case_rows:
                status = str(row.get("status") or "").lower()
                if status != "finished" or row.get("job_error") or row.get("result_error"):
                    failures.append(row)
        return 1 if failures else 0
    except Exception as exc:
        print(f"Live matrix orchestration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
