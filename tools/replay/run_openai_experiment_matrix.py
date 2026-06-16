import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from infrastructure.ai.provider_defaults import (
    resolve_provider_defaults,
    resolve_runtime_image_provider,
    resolve_runtime_model_name,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
REPLAY_SCRIPT = REPO_ROOT / "tools" / "replay" / "exactness_qc_replay.py"
DEFAULT_MANIFEST = REPO_ROOT / "tests" / "replay_cases" / "9ffde1c0_compare" / "manifest.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "tests" / "replay_cases" / "9ffde1c0_compare" / "outputs" / "matrix_runs"


EXPERIMENTS = [
    {
        "key": "existing",
        "label": "Existing",
        "env": {
            "TOTAL_TIMEOUT_LIMIT": "1800",
            "ANALYSIS_PROVIDER": "gemini",
            "ANALYSIS_MODEL_NAME": "gemini-3.5-flash",
            "DETECT_FURNITURE_MODEL_NAME": "gemini-3.5-flash",
            "ROOM_ONLY_MODEL_NAME": "gemini-3.5-flash",
            "RANK_MODEL_NAME": "gemini-3.5-flash",
            "REMAP_MODEL_NAME": "gemini-3.5-flash",
            "MAIN_IMAGE_PROVIDER": "gemini",
            "MAIN_IMAGE_MODEL_NAME": "gemini-3.1-flash-image",
            "REPAIR_IMAGE_PROVIDER": "gemini",
            "REPAIR_IMAGE_MODEL_NAME": "gemini-3.1-flash-image",
        },
    },
    {
        "key": "A",
        "label": "A_openai_analysis_gemini_image",
        "env": {
            "TOTAL_TIMEOUT_LIMIT": "1800",
            "ANALYSIS_PROVIDER": "openai",
            "ANALYSIS_MODEL_NAME": "gpt-5.4",
            "DETECT_FURNITURE_MODEL_NAME": "gpt-5.4",
            "ROOM_ONLY_MODEL_NAME": "gpt-5.4",
            "RANK_MODEL_NAME": "gpt-5.4",
            "REMAP_MODEL_NAME": "gpt-5.4",
            "OPENAI_ANALYSIS_MODEL_NAME": "gpt-5.4",
            "OPENAI_ANALYSIS_REASONING_EFFORT": "xhigh",
            "OPENAI_ANALYSIS_TIMEOUT_CAP_SEC": "25",
            "OPENAI_ANALYSIS_MAX_ATTEMPTS": "1",
            "MAIN_IMAGE_PROVIDER": "gemini",
            "MAIN_IMAGE_MODEL_NAME": "gemini-3.1-flash-image",
            "REPAIR_IMAGE_PROVIDER": "gemini",
            "REPAIR_IMAGE_MODEL_NAME": "gemini-3.1-flash-image",
            "OPENAI_IMAGE_MODEL_NAME": "gpt-image-2",
        },
    },
    {
        "key": "B",
        "label": "B_openai_analysis_gemini_main_openai_repair",
        "env": {
            "TOTAL_TIMEOUT_LIMIT": "1800",
            "ANALYSIS_PROVIDER": "openai",
            "ANALYSIS_MODEL_NAME": "gpt-5.4",
            "DETECT_FURNITURE_MODEL_NAME": "gpt-5.4",
            "ROOM_ONLY_MODEL_NAME": "gpt-5.4",
            "RANK_MODEL_NAME": "gpt-5.4",
            "REMAP_MODEL_NAME": "gpt-5.4",
            "OPENAI_ANALYSIS_MODEL_NAME": "gpt-5.4",
            "OPENAI_ANALYSIS_REASONING_EFFORT": "xhigh",
            "OPENAI_ANALYSIS_TIMEOUT_CAP_SEC": "25",
            "OPENAI_ANALYSIS_MAX_ATTEMPTS": "1",
            "MAIN_IMAGE_PROVIDER": "gemini",
            "MAIN_IMAGE_MODEL_NAME": "gemini-3.1-flash-image",
            "REPAIR_IMAGE_PROVIDER": "openai",
            "REPAIR_IMAGE_MODEL_NAME": "gpt-image-2",
            "OPENAI_IMAGE_MODEL_NAME": "gpt-image-2",
        },
    },
    {
        "key": "C",
        "label": "C_openai_analysis_openai_main_openai_repair",
        "env": {
            "TOTAL_TIMEOUT_LIMIT": "1800",
            "ANALYSIS_PROVIDER": "openai",
            "ANALYSIS_MODEL_NAME": "gpt-5.4",
            "DETECT_FURNITURE_MODEL_NAME": "gpt-5.4",
            "ROOM_ONLY_MODEL_NAME": "gpt-5.4",
            "RANK_MODEL_NAME": "gpt-5.4",
            "REMAP_MODEL_NAME": "gpt-5.4",
            "OPENAI_ANALYSIS_MODEL_NAME": "gpt-5.4",
            "OPENAI_ANALYSIS_REASONING_EFFORT": "xhigh",
            "OPENAI_ANALYSIS_TIMEOUT_CAP_SEC": "25",
            "OPENAI_ANALYSIS_MAX_ATTEMPTS": "1",
            "MAIN_IMAGE_PROVIDER": "openai",
            "MAIN_IMAGE_MODEL_NAME": "gpt-image-2",
            "REPAIR_IMAGE_PROVIDER": "openai",
            "REPAIR_IMAGE_MODEL_NAME": "gpt-image-2",
            "OPENAI_IMAGE_MODEL_NAME": "gpt-image-2",
        },
    },
]


def _resolve_repo_path(path_str: str) -> Path:
    path = Path(path_str)
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    return path.resolve()


def _load_report(report_path: Path) -> dict[str, Any]:
    return json.loads(report_path.read_text(encoding="utf-8"))


def _candidate_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    result = report.get("result") or {}
    rows = []
    for row in result.get("variant_diagnostics") or []:
        if not isinstance(row, dict):
            continue
        path = row.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        rows.append(dict(row))
    return rows


def _best_candidate_row(report: dict[str, Any]) -> dict[str, Any] | None:
    result = report.get("result") or {}
    selected_name = (result.get("selected_result_filename") or report.get("selected_result_info", {}).get("selected_result_filename") or "").strip()
    rows = _candidate_rows(report)
    if not rows:
        return None
    if selected_name:
        for row in rows:
            if Path(str(row.get("path") or "")).name == selected_name:
                return row
    return min(rows, key=lambda row: float(row.get("weighted_issue_score") or row.get("qc_issue_score") or 10**9))


def _copy_candidate_images(report: dict[str, Any], destination_dir: Path, prefix: str) -> dict[str, Any]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(_candidate_rows(report), key=lambda row: int(row.get("variant_index") or 0))
    best_row = _best_candidate_row(report)
    copied: list[dict[str, Any]] = []
    for row in rows:
        src = _resolve_repo_path(str(row["path"]))
        variant_num = int(row.get("variant_index") or 0) + 1
        is_best = best_row is not None and src.name == Path(str(best_row.get("path") or "")).name
        suffix = f"{prefix}_{'best_' if is_best else ''}v{variant_num}{src.suffix.lower() or '.png'}"
        dest = destination_dir / suffix
        shutil.copy2(src, dest)
        copied.append(
            {
                "variant_index": row.get("variant_index"),
                "source_path": str(src),
                "copied_path": str(dest),
                "weighted_issue_score": row.get("weighted_issue_score"),
                "qc_issue_score": row.get("qc_issue_score"),
                "qc_reason": row.get("qc_reason"),
                "is_best": is_best,
            }
        )
    return {
        "best_variant_index": None if best_row is None else best_row.get("variant_index"),
        "copied_images": copied,
    }


def _experiment_runtime_metadata(experiment: dict[str, Any], *, base_env: dict[str, str] | None = None) -> dict[str, Any]:
    env = dict(base_env or os.environ)
    env.update(experiment.get("env") or {})

    provider_defaults = resolve_provider_defaults(env)
    openai_api_key = str(env.get("OPENAI_API_KEY") or "")
    analysis_provider = provider_defaults.analysis_provider
    main_image_provider = resolve_runtime_image_provider(provider_defaults.main_image_provider, openai_api_key)
    repair_image_provider = resolve_runtime_image_provider(provider_defaults.repair_image_provider, openai_api_key)
    openai_analysis_model_name = (env.get("OPENAI_ANALYSIS_MODEL_NAME") or "gpt-5.4").strip() or "gpt-5.4"
    openai_image_model_name = provider_defaults.openai_image_model_name

    analysis_model_name = resolve_runtime_model_name(
        provider=analysis_provider,
        configured_model_name=env.get("ANALYSIS_MODEL_NAME"),
        default_openai_model_name=openai_analysis_model_name,
        default_gemini_model_name="gemini-3.5-flash",
    )
    main_image_model_name = resolve_runtime_model_name(
        provider=main_image_provider,
        configured_model_name=env.get("MAIN_IMAGE_MODEL_NAME"),
        default_openai_model_name=openai_image_model_name,
        default_gemini_model_name="gemini-3.1-flash-image",
    )
    repair_image_model_name = resolve_runtime_model_name(
        provider=repair_image_provider,
        configured_model_name=env.get("REPAIR_IMAGE_MODEL_NAME"),
        default_openai_model_name=openai_image_model_name,
        default_gemini_model_name="gemini-3.1-flash-image",
    )

    return {
        "analysis_provider": analysis_provider,
        "analysis_model_name": analysis_model_name,
        "analysis_reasoning_effort": env.get("OPENAI_ANALYSIS_REASONING_EFFORT"),
        "analysis_timeout_cap_sec": env.get("OPENAI_ANALYSIS_TIMEOUT_CAP_SEC"),
        "analysis_max_attempts": env.get("OPENAI_ANALYSIS_MAX_ATTEMPTS"),
        "main_image_provider": main_image_provider,
        "main_image_model_name": main_image_model_name,
        "repair_image_provider": repair_image_provider,
        "repair_image_model_name": repair_image_model_name,
        "total_timeout_limit": env.get("TOTAL_TIMEOUT_LIMIT"),
    }


def _run_experiment(experiment: dict[str, Any], manifest_path: Path, output_root: Path) -> dict[str, Any]:
    report_dir = output_root / experiment["key"]
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.json"
    stdout_path = report_dir / "stdout.log"
    stderr_path = report_dir / "stderr.log"

    env = os.environ.copy()
    env.update(experiment["env"])

    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(REPLAY_SCRIPT), str(manifest_path), "--report-path", str(report_path)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    finished = time.perf_counter()
    stdout_path.write_text(proc.stdout or "", encoding="utf-8")
    stderr_path.write_text(proc.stderr or "", encoding="utf-8")

    report = _load_report(report_path) if report_path.exists() else {}
    return {
        "experiment": experiment["key"],
        "label": experiment["label"],
        "returncode": proc.returncode,
        "wall_seconds": round(finished - started, 3),
        "report_path": str(report_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "report": report,
    }


def run_matrix(manifest_path: Path, output_root: Path, desktop_dir: Path) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    desktop_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_root / "matrix_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    else:
        summary = {
            "manifest_path": str(manifest_path),
            "output_root": str(output_root),
            "desktop_dir": str(desktop_dir),
            "runs": [],
        }

    requested_keys = {
        token.strip()
        for token in str(os.environ.get("MATRIX_EXPERIMENTS") or "").split(",")
        if token.strip()
    }
    selected_experiments = [row for row in EXPERIMENTS if not requested_keys or row["key"] in requested_keys]
    existing_runs = {str(row.get("experiment")): row for row in (summary.get("runs") or []) if isinstance(row, dict)}

    for experiment in selected_experiments:
        result = _run_experiment(experiment, manifest_path, output_root)
        artifact_info = _copy_candidate_images(
            result["report"],
            desktop_dir / experiment["key"],
            prefix=experiment["key"].lower(),
        )
        run_summary = {
            "experiment": result["experiment"],
            "label": result["label"],
            "returncode": result["returncode"],
            "wall_seconds": result["wall_seconds"],
            "report_path": result["report_path"],
            "stdout_path": result["stdout_path"],
            "stderr_path": result["stderr_path"],
            "timing": (result["report"].get("timing") or {}),
            "selected_result_info": result["report"].get("selected_result_info"),
            "artifact_info": artifact_info,
            "runtime_policy": _experiment_runtime_metadata(experiment),
        }
        existing_runs[experiment["key"]] = run_summary

    experiment_map = {row["key"]: row for row in EXPERIMENTS}
    for key, row in list(existing_runs.items()):
        experiment = experiment_map.get(key)
        if experiment and "runtime_policy" not in row:
            row["runtime_policy"] = _experiment_runtime_metadata(experiment)

    summary["runs"] = [existing_runs[key] for key in sorted(existing_runs.keys())]
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    desktop_summary_path = desktop_dir / "matrix_summary.json"
    desktop_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path


def main() -> int:
    manifest_path = DEFAULT_MANIFEST
    output_root = DEFAULT_OUTPUT_ROOT
    desktop_dir = Path(os.environ.get("MATRIX_DESKTOP_DIR") or (Path.home() / "Desktop" / "render_provider_matrix_20260415"))
    summary_path = run_matrix(manifest_path, output_root, desktop_dir)
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
