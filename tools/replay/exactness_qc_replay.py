import argparse
import json
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main
from api_models import CartRenderRequest, PresetRenderRequest

INTERNAL_MODES = {"internal_itemized_job_render", "internal_itemized_render"}
EXTERNAL_CART_MODES = {"external_cart_render", "external_cart_job_render"}
EXTERNAL_PRESET_MODES = {"external_preset_render", "external_preset_job_render"}


@dataclass
class ReplayCase:
    manifest_path: Path
    mode: str
    entrypoint: str
    output_dir: Path
    report_filename: str
    payload: dict[str, Any]


@dataclass
class ReplayInvocation:
    job_runner: Callable[..., dict[str, Any]]
    job_runner_name: str
    job_payload: dict[str, Any]
    payload_metadata: dict[str, Any]
    auxiliary: dict[str, Any]
    persist_result: bool | None = None


def _resolve_repo_path(path_str: str, *, base_dir: Path | None = None) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        anchor = base_dir or REPO_ROOT
        path = (anchor / path).resolve()
    return path.resolve()


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://") or value.startswith("/outputs/") or value.startswith("/assets/")


def _resolve_reference(value: Any, *, base_dir: Path) -> Any:
    if not isinstance(value, str):
        return value
    if not value.strip():
        return value
    if _looks_like_url(value):
        return value

    direct = Path(value)
    if direct.is_absolute():
        return str(direct.resolve())

    manifest_relative = (base_dir / value).resolve()
    if manifest_relative.exists():
        return str(manifest_relative)

    repo_relative = (REPO_ROOT / value).resolve()
    if repo_relative.exists():
        return str(repo_relative)

    return value


def _extract_result_urls(result: dict[str, Any] | None) -> list[str]:
    if not isinstance(result, dict):
        return []
    urls = result.get("result_urls")
    if isinstance(urls, list):
        return [str(item) for item in urls if isinstance(item, str) and item.strip()]
    single = result.get("result_url")
    if isinstance(single, str) and single.strip():
        return [single]
    return []


def _extract_selected_result_info(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    selected_url = result.get("result_url")
    selected_filename = result.get("selected_result_filename")
    if not selected_filename and isinstance(selected_url, str) and selected_url.strip():
        selected_filename = Path(selected_url.split("?", 1)[0]).name
    return {
        "result_url": selected_url,
        "selected_result_filename": selected_filename,
        "selected_result_index": result.get("selected_result_index"),
        "selected_result_reason": result.get("selected_result_reason"),
        "empty_room_url": result.get("empty_room_url"),
        "original_url": result.get("original_url"),
    }


def _require_non_empty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Manifest must contain non-empty '{label}'")
    return value.strip()


def _require_non_empty_mapping(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Manifest must contain non-empty '{label}' object")
    return value


def _require_non_empty_list(value: Any, *, label: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Manifest must contain non-empty '{label}' array")
    return value


def _validate_internal_manifest(payload: dict[str, Any], *, manifest_path: Path) -> None:
    form_data = _require_non_empty_mapping(payload.get("form_data"), label="form_data")
    for key in ("room", "style", "variant"):
        _require_non_empty_string(form_data.get(key), label=f"form_data.{key}")

    room_file = _require_non_empty_mapping(payload.get("room_file"), label="room_file")
    room_path = _resolve_reference(room_file.get("path"), base_dir=manifest_path.parent)
    _require_non_empty_string(room_path, label="room_file.path")
    if not Path(room_path).exists():
        raise ValueError(f"Room file does not exist: {room_path}")

    item_files = _require_non_empty_mapping(payload.get("item_files"), label="item_files")
    items_json = _require_non_empty_list(payload.get("items_json"), label="items_json")

    client_ids: set[str] = set()
    for index, row in enumerate(items_json, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"items_json[{index}] must be an object")
        client_id = _require_non_empty_string(row.get("client_id"), label=f"items_json[{index}].client_id")
        _require_non_empty_string(row.get("category"), label=f"items_json[{index}].category")
        qty = row.get("qty", 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            raise ValueError(f"items_json[{index}].qty must be a positive integer")
        dims_mm = _require_non_empty_mapping(row.get("dims_mm"), label=f"items_json[{index}].dims_mm")
        for dim_key in ("width_mm", "depth_mm", "height_mm"):
            dim_value = dims_mm.get(dim_key)
            if isinstance(dim_value, bool) or not isinstance(dim_value, int) or dim_value <= 0:
                raise ValueError(f"items_json[{index}].{dim_key} must be a positive integer")
        if client_id in client_ids:
            raise ValueError(f"Duplicate client_id in manifest items_json: {client_id}")
        client_ids.add(client_id)
        if client_id not in item_files:
            raise ValueError(f"item_files is missing client_id '{client_id}' declared in items_json")
        item_path = _resolve_reference(item_files[client_id], base_dir=manifest_path.parent)
        if not Path(item_path).exists():
            raise ValueError(f"Item file does not exist for {client_id}: {item_path}")


def _validate_external_cart_manifest(payload: dict[str, Any], *, manifest_path: Path) -> None:
    request_payload = _require_non_empty_mapping(payload.get("request"), label="request")
    _require_non_empty_string(request_payload.get("image_url"), label="request.image_url")
    items = _require_non_empty_list(request_payload.get("items"), label="request.items")
    for index, row in enumerate(items, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"request.items[{index}] must be an object")
        _require_non_empty_string(row.get("id"), label=f"request.items[{index}].id")
        _require_non_empty_string(row.get("category"), label=f"request.items[{index}].category")
        _require_non_empty_string(row.get("image_url"), label=f"request.items[{index}].image_url")
        qty = row.get("qty", 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            raise ValueError(f"request.items[{index}].qty must be a positive integer")
        dims_mm = row.get("dims_mm")
        if dims_mm is not None and not isinstance(dims_mm, dict):
            raise ValueError(f"request.items[{index}].dims_mm must be an object when provided")


def _validate_external_preset_manifest(payload: dict[str, Any], *, manifest_path: Path) -> None:
    request_payload = _require_non_empty_mapping(payload.get("request"), label="request")
    _require_non_empty_string(request_payload.get("image_url"), label="request.image_url")
    preset_id = request_payload.get("preset_id")
    room = request_payload.get("room")
    style = request_payload.get("style")
    variant = request_payload.get("variant")
    if not (isinstance(preset_id, str) and preset_id.strip()) and not all(
        isinstance(value, str) and value.strip() for value in (room, style, variant)
    ):
        raise ValueError("Preset manifest must provide request.preset_id or request.room/style/variant")


def load_case_manifest(manifest_path: str | Path) -> ReplayCase:
    manifest_file = _resolve_repo_path(str(manifest_path))
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    mode = _require_non_empty_string(payload.get("mode"), label="mode")
    entrypoint = _require_non_empty_string(payload.get("entrypoint"), label="entrypoint")
    output_dir_value = payload.get("output_dir", manifest_file.parent.as_posix())
    output_dir = _resolve_repo_path(str(output_dir_value), base_dir=manifest_file.parent)
    report_filename = payload.get("report_filename", "exactness_qc_report.json")

    if mode in INTERNAL_MODES:
        _validate_internal_manifest(payload, manifest_path=manifest_file)
    elif mode in EXTERNAL_CART_MODES:
        _validate_external_cart_manifest(payload, manifest_path=manifest_file)
    elif mode in EXTERNAL_PRESET_MODES:
        _validate_external_preset_manifest(payload, manifest_path=manifest_file)
    else:
        raise ValueError(f"Unsupported replay mode: {mode}")

    return ReplayCase(
        manifest_path=manifest_file,
        mode=mode,
        entrypoint=entrypoint,
        output_dir=output_dir,
        report_filename=_require_non_empty_string(report_filename, label="report_filename"),
        payload=payload,
    )


def _build_internal_invocation(case: ReplayCase) -> ReplayInvocation:
    deps = main._queue_route_deps()
    payload = case.payload
    form_data = dict(payload["form_data"])
    room_file = dict(payload["room_file"])
    room_path = str(_resolve_reference(room_file["path"], base_dir=case.manifest_path.parent))

    item_specs: list[dict[str, Any]] = []
    item_paths: list[str] = []
    for upload_index, row in enumerate(payload["items_json"], start=1):
        client_id = row["client_id"]
        item_specs.append(
            {
                "client_id": client_id,
                "name": row.get("name"),
                "category": row["category"],
                "qty": row.get("qty", 1),
                "dims_mm": row["dims_mm"],
                "upload_index": upload_index - 1,
            }
        )
        item_paths.append(str(_resolve_reference(payload["item_files"][client_id], base_dir=case.manifest_path.parent)))

    job_payload = deps.build_internal_itemized_async_render_job_payload(
        raw_path=room_path,
        item_specs=item_specs,
        item_paths=item_paths,
        room=form_data["room"],
        style=form_data["style"],
        variant=form_data["variant"],
        dimensions=str(form_data.get("dimensions", "") or "").strip(),
        placement=str(form_data.get("placement", "") or "").strip(),
        resolve_image_url=deps.resolve_image_url,
        build_s3_prefix=deps.build_s3_prefix,
        build_item_target_key=deps.build_item_target_key,
    )
    metadata = {
        "audience": job_payload.get("audience"),
        "room": job_payload.get("room"),
        "style": job_payload.get("style"),
        "variant": job_payload.get("variant"),
        "dimensions": job_payload.get("dimensions"),
        "placement": job_payload.get("placement"),
        "item_count": len(job_payload.get("moodboard_items") or []),
        "payload_kind": "render",
    }
    return ReplayInvocation(
        job_runner=main.job_render,
        job_runner_name="job_render",
        job_payload=job_payload,
        payload_metadata=metadata,
        auxiliary={"room_file": room_file, "items_json": payload["items_json"]},
        persist_result=False,
    )


def _resolve_request_refs(value: Any, *, base_dir: Path) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_request_refs(inner, base_dir=base_dir) for key, inner in value.items()}
    if isinstance(value, list):
        return [_resolve_request_refs(inner, base_dir=base_dir) for inner in value]
    return _resolve_reference(value, base_dir=base_dir)


def _build_external_cart_invocation(case: ReplayCase) -> ReplayInvocation:
    deps = main._queue_route_deps()
    request_payload = _resolve_request_refs(case.payload["request"], base_dir=case.manifest_path.parent)
    req = CartRenderRequest.model_validate(request_payload)
    job_payload, kept, dropped = deps.build_external_cart_job(
        req,
        cart_max_items=deps.cart_max_items,
        apply_cart_limits=deps.apply_cart_limits,
        build_cart_summary=deps.build_cart_summary,
        materialize_input=deps.materialize_input,
        normalize_item_image=deps.normalize_item_image,
        resolve_image_url=deps.resolve_image_url,
        build_s3_prefix=deps.build_s3_prefix,
        build_item_target_key=deps.build_item_target_key,
    )
    render_payload = job_payload.get("render", {})
    metadata = {
        "audience": render_payload.get("audience"),
        "room": render_payload.get("room"),
        "style": render_payload.get("style"),
        "variant": render_payload.get("variant"),
        "dimensions": render_payload.get("dimensions"),
        "placement": render_payload.get("placement"),
        "item_count": len(render_payload.get("moodboard_items") or []),
        "cart_kept_count": len(kept),
        "cart_dropped_count": len(dropped),
        "payload_kind": "render_with_details",
    }
    return ReplayInvocation(
        job_runner=main.job_render_with_details,
        job_runner_name="job_render_with_details",
        job_payload=job_payload,
        payload_metadata=metadata,
        auxiliary={"request": request_payload, "cart_kept": kept, "cart_dropped": dropped},
    )


def _build_external_preset_invocation(case: ReplayCase) -> ReplayInvocation:
    deps = main._queue_route_deps()
    request_payload = _resolve_request_refs(case.payload["request"], base_dir=case.manifest_path.parent)
    req = PresetRenderRequest.model_validate(request_payload)
    job_payload, resolved = deps.build_external_preset_job(req, deps.load_preset_map())
    render_payload = job_payload.get("render", {})
    metadata = {
        "audience": render_payload.get("audience"),
        "room": render_payload.get("room"),
        "style": render_payload.get("style"),
        "variant": render_payload.get("variant"),
        "dimensions": render_payload.get("dimensions"),
        "placement": render_payload.get("placement"),
        "preset_id": request_payload.get("preset_id"),
        "resolved": resolved,
        "payload_kind": "render_with_details",
    }
    return ReplayInvocation(
        job_runner=main.job_render_with_details,
        job_runner_name="job_render_with_details",
        job_payload=job_payload,
        payload_metadata=metadata,
        auxiliary={"request": request_payload, "resolved": resolved},
    )


def build_replay_invocation(case: ReplayCase) -> ReplayInvocation:
    if case.mode in INTERNAL_MODES:
        return _build_internal_invocation(case)
    if case.mode in EXTERNAL_CART_MODES:
        return _build_external_cart_invocation(case)
    if case.mode in EXTERNAL_PRESET_MODES:
        return _build_external_preset_invocation(case)
    raise ValueError(f"Unsupported replay mode: {case.mode}")


def run_case(case: ReplayCase, report_path: str | Path | None = None) -> Path:
    build_started = time.perf_counter()
    invocation = None
    result = None
    script_error = None
    run_started = None
    run_finished = None
    try:
        invocation = build_replay_invocation(case)
        run_started = time.perf_counter()
        if invocation.persist_result is None:
            result = invocation.job_runner(invocation.job_payload)
        else:
            result = invocation.job_runner(invocation.job_payload, persist_result=invocation.persist_result)
        run_finished = time.perf_counter()
    except Exception:
        run_finished = time.perf_counter()
        script_error = traceback.format_exc()

    finished = time.perf_counter()
    destination = _resolve_repo_path(str(report_path), base_dir=case.manifest_path.parent) if report_path else case.output_dir / case.report_filename
    destination.parent.mkdir(parents=True, exist_ok=True)

    timings = {
        "build_seconds": None if invocation is None else round((run_started or finished) - build_started, 3),
        "run_seconds": None if run_started is None or run_finished is None else round(run_finished - run_started, 3),
        "total_seconds": round(finished - build_started, 3),
    }
    report = {
        "manifest_path": str(case.manifest_path),
        "mode": case.mode,
        "entrypoint": case.entrypoint,
        "timing": timings,
        "job_runner": None if invocation is None else invocation.job_runner_name,
        "job_payload_metadata": None if invocation is None else invocation.payload_metadata,
        "job_payload": None if invocation is None else invocation.job_payload,
        "auxiliary": None if invocation is None else invocation.auxiliary,
        "selected_result_info": _extract_selected_result_info(result),
        "result_urls": _extract_result_urls(result),
        "result": result,
        "script_error": script_error,
    }
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def main_cli() -> int:
    parser = argparse.ArgumentParser(
        description="Run exactness QC replay manifests for internal itemized, external cart, and external preset render modes."
    )
    parser.add_argument("manifest", help="Path to the replay manifest JSON.")
    parser.add_argument("--report-path", help="Optional report output path.")
    args = parser.parse_args()

    try:
        case = load_case_manifest(args.manifest)
        report_path = run_case(case, report_path=args.report_path)
        print(report_path)
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        if report.get("script_error") or report.get("result") is None:
            return 1
        return 0
    except Exception as exc:
        print(f"Exactness QC replay error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
