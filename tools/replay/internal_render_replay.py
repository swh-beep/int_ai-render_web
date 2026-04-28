import argparse
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main


@dataclass
class ReplayCase:
    manifest_path: Path
    mode: str
    output_dir: Path
    entrypoint: str
    form_data: dict[str, Any]
    room_file: dict[str, Any]
    item_files_field: str
    item_files: dict[str, str]
    items_json: list[dict[str, Any]]
    report_filename: str


def _resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _require_mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Manifest must contain non-empty '{key}' object")
    return value


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"Manifest must contain non-empty '{key}' array")
    return value


def _validate_case_manifest(payload: dict[str, Any], manifest_file: Path) -> None:
    if manifest_file.suffix.lower() != ".json":
        raise ValueError("Replay case manifest must be a .json file")
    if "mode" in payload and (not isinstance(payload["mode"], str) or not payload["mode"].strip()):
        raise ValueError("Manifest mode must be a non-empty string when provided")
    if not isinstance(payload.get("entrypoint"), str) or not payload["entrypoint"].strip():
        raise ValueError("Manifest must contain 'entrypoint'")
    if not isinstance(payload.get("form_data"), dict) or not payload["form_data"]:
        raise ValueError("Manifest must contain non-empty 'form_data'")
    form_data = payload["form_data"]
    for required_key in ("room", "style", "variant"):
        value = form_data.get(required_key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Manifest form_data must contain non-empty '{required_key}'")

    room_file = _require_mapping(payload, "room_file")
    if not isinstance(room_file.get("path"), str) or not room_file["path"].strip():
        raise ValueError("Manifest room_file must contain 'path'")
    room_path = _resolve_repo_path(room_file["path"])
    if not room_path.exists():
        raise ValueError(f"Room file does not exist: {room_path}")

    item_files = _require_mapping(payload, "item_files")
    items_json = _require_list(payload, "items_json")
    client_ids: set[str] = set()
    for index, row in enumerate(items_json, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"items_json[{index}] must be an object")
        client_id = row.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise ValueError(f"items_json[{index}] must contain non-empty client_id")
        category = row.get("category")
        if not isinstance(category, str) or not category.strip():
            raise ValueError(f"items_json[{index}] must contain non-empty category")
        name = row.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError(f"items_json[{index}] name must be a string or null")
        qty = row.get("qty", 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            raise ValueError(f"items_json[{index}] qty must be a positive integer")
        dims_mm = row.get("dims_mm")
        if not isinstance(dims_mm, dict):
            raise ValueError(f"items_json[{index}] dims_mm must be an object")
        for dim_key in ("width_mm", "depth_mm", "height_mm"):
            dim_value = dims_mm.get(dim_key)
            if isinstance(dim_value, bool) or not isinstance(dim_value, int) or dim_value <= 0:
                raise ValueError(f"items_json[{index}] {dim_key} must be a positive integer")
        if client_id in client_ids:
            raise ValueError(f"Duplicate client_id in manifest items_json: {client_id}")
        client_ids.add(client_id)
        if client_id not in item_files:
            raise ValueError(f"item_files is missing client_id '{client_id}' declared in items_json")

    for client_id, path_str in item_files.items():
        if not isinstance(path_str, str) or not path_str.strip():
            raise ValueError(f"Item file path must be a non-empty string for {client_id}")
        item_path = _resolve_repo_path(path_str)
        if not item_path.exists():
            raise ValueError(f"Item file does not exist for {client_id}: {item_path}")


def load_case_manifest(manifest_path: str | Path) -> ReplayCase:
    manifest_file = _resolve_repo_path(str(manifest_path))
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    _validate_case_manifest(payload, manifest_file)

    output_dir = _resolve_repo_path(payload.get("output_dir", manifest_file.parent.as_posix()))
    room_file = dict(payload["room_file"])
    room_file["path"] = str(_resolve_repo_path(room_file["path"]))
    item_files = {
        client_id: str(_resolve_repo_path(path_str))
        for client_id, path_str in payload["item_files"].items()
    }

    return ReplayCase(
        manifest_path=manifest_file,
        mode=(payload.get("mode") or "internal_itemized_job_render").strip(),
        output_dir=output_dir,
        entrypoint=payload["entrypoint"],
        form_data=dict(payload["form_data"]),
        room_file=room_file,
        item_files_field=payload.get("item_files_field", "item_images"),
        item_files=item_files,
        items_json=list(payload["items_json"]),
        report_filename=payload.get("report_filename", "replay_report.json"),
    )


def build_replay_job_payload(case: ReplayCase) -> dict[str, Any]:
    if case.mode != "internal_itemized_job_render":
        raise ValueError(f"Unsupported replay mode: {case.mode}")

    deps = main._queue_route_deps()
    item_specs: list[dict[str, Any]] = []
    item_paths: list[str] = []
    for payload_index, row in enumerate(case.items_json, start=1):
        client_id = row["client_id"]
        item_specs.append(
            {
                "client_id": client_id,
                "name": row.get("name").strip() if isinstance(row.get("name"), str) and row.get("name").strip() else None,
                "category": row["category"].strip(),
                "qty": row.get("qty", 1),
                "dims_mm": row["dims_mm"],
                "upload_index": payload_index - 1,
            }
        )
        item_paths.append(case.item_files[client_id])

    return deps.build_internal_itemized_async_render_job_payload(
        raw_path=case.room_file["path"],
        item_specs=item_specs,
        item_paths=item_paths,
        room=case.form_data["room"].strip(),
        style=case.form_data["style"].strip(),
        variant=case.form_data["variant"].strip(),
        dimensions=str(case.form_data.get("dimensions", "") or "").strip(),
        placement=str(case.form_data.get("placement", "") or "").strip(),
        resolve_image_url=lambda local_path, s3_prefix_override=None: None,
        build_s3_prefix=deps.build_s3_prefix,
        build_item_target_key=deps.build_item_target_key,
    )


def run_case(case: ReplayCase, report_path: str | Path | None = None) -> Path:
    job_payload = None
    result = None
    script_error = None
    try:
        job_payload = build_replay_job_payload(case)
        result = main.job_render(job_payload, persist_result=False)
    except Exception:
        script_error = traceback.format_exc()

    destination = _resolve_repo_path(str(report_path)) if report_path else case.output_dir / case.report_filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "manifest_path": str(case.manifest_path),
        "mode": case.mode,
        "entrypoint": case.entrypoint,
        "job_payload": job_payload,
        "result": result,
        "script_error": script_error,
    }
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Replay an internal render case manifest through direct job execution.")
    parser.add_argument("manifest", help="Path to the replay case manifest JSON.")
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
        print(f"Replay harness error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main_cli())
