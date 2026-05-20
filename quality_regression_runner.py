from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import live_validate_render_flows
from shared.quality_review import (
    build_review_sheet,
    collect_report_image_refs,
    create_contact_sheet,
    ensure_dir,
    materialize_image_ref,
    write_json,
)


BASE_DIR = Path(__file__).resolve().parent
QA_ROOT = BASE_DIR / "outputs" / "qa_runs"


def _build_suite_dir(suite_name: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ensure_dir(QA_ROOT / f"{timestamp}_{suite_name}")


def _materialize_refs(report: dict, run_dir: Path) -> list[tuple[str, Path]]:
    refs = collect_report_image_refs(report)
    images_dir = ensure_dir(run_dir / "images")
    materialized: list[tuple[str, Path]] = []
    for index, (label, ref) in enumerate(refs, start=1):
        local_path = materialize_image_ref(ref, BASE_DIR, images_dir, f"{index:02d}_{label}")
        if local_path:
            materialized.append((label, local_path))
    return materialized


def _group_board_items(materialized: list[tuple[str, Path]], prefix: str) -> list[tuple[str, Path]]:
    return [(label, path) for label, path in materialized if label.startswith(prefix)]


def run_quality_suite(*, suite_name: str, repeats: int, room_dimensions_text: str) -> dict:
    suite_dir = _build_suite_dir(suite_name)
    runs = []

    for repeat_index in range(1, repeats + 1):
        run_id = f"run_{repeat_index:02d}"
        run_dir = ensure_dir(suite_dir / run_id)
        report_path = run_dir / "live_validation_report.json"
        report = live_validate_render_flows.main_validation(
            report_path=report_path,
            room_dimensions_text=room_dimensions_text,
        )

        materialized = _materialize_refs(report, run_dir)
        boards_dir = ensure_dir(run_dir / "boards")
        create_contact_sheet(materialized, boards_dir / "all_outputs.png", columns=2)
        create_contact_sheet(_group_board_items(materialized, "internal_main"), boards_dir / "internal_main.png", columns=2)
        create_contact_sheet(_group_board_items(materialized, "internal_detail"), boards_dir / "internal_detail.png", columns=2)
        create_contact_sheet(_group_board_items(materialized, "external_cart"), boards_dir / "external_cart.png", columns=2)

        manifest = {
            "suite_name": suite_name,
            "run_id": run_id,
            "room_dimensions_text": room_dimensions_text,
            "report_path": str(report_path),
            "image_labels": [label for label, _ in materialized],
            "result_keys": sorted(((report or {}).get("results") or {}).keys()),
        }
        manifest_path = write_json(run_dir / "manifest.json", manifest)
        review_sheet = build_review_sheet(
            suite_name=suite_name,
            run_id=run_id,
            room_dimensions_text=room_dimensions_text,
            manifest_path=str(manifest_path),
        )
        write_json(run_dir / "review_sheet.json", review_sheet)

        runs.append(
            {
                "run_id": run_id,
                "report_path": str(report_path),
                "manifest_path": str(manifest_path),
                "image_count": len(materialized),
            }
        )

    suite_summary = {
        "suite_name": suite_name,
        "generated_at": datetime.now().isoformat(),
        "repeats": repeats,
        "room_dimensions_text": room_dimensions_text,
        "suite_dir": str(suite_dir),
        "runs": runs,
    }
    write_json(suite_dir / "suite_summary.json", suite_summary)
    return suite_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run repeated quality validation and package QA artifacts.")
    parser.add_argument("--suite-name", default="baseline", help="Name of the QA suite directory.")
    parser.add_argument("--repeats", type=int, default=3, help="Number of repeated validation runs.")
    parser.add_argument(
        "--room-dimensions",
        default=live_validate_render_flows.DEFAULT_ROOM_DIMENSIONS_TEXT,
        help="Room dimensions text passed into the validation suite.",
    )
    args = parser.parse_args()
    summary = run_quality_suite(
        suite_name=args.suite_name,
        repeats=max(1, int(args.repeats)),
        room_dimensions_text=str(args.room_dimensions).strip(),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
