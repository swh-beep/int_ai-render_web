import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi.testclient import TestClient

import main
from infrastructure.ai.gemini_client import get_qa_budget_snapshot
from shared.quality_qa_support import (
    BoardTile,
    build_review_markdown,
    build_review_sheet,
    copy_image_reference,
    create_comparison_board,
    crop_box_reference,
    ensure_dir,
    materialize_image_reference,
    slugify_token,
    write_json,
    write_text,
)


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


BASE_DIR = Path(__file__).resolve().parent
LOCALTEST_DIR = BASE_DIR.parent / "localtest_image"
QA_ROOT = BASE_DIR / "outputs" / "qa_runs"
FIXED_ROOM_DIMENSIONS = "10000 x 5500 x 3000"
DEFAULT_MAX_MODEL_CALLS = 200
DEFAULT_BUDGET_FILE = QA_ROOT / "_budget" / "gemini_calls.json"
DEFAULT_CASES = [
    "internal_main",
    "internal_detail",
    "internal_edit",
    "external_preset",
    "external_cart",
]
DEFAULT_PLACEMENT_TEXT = "Preserve architecture. Keep realistic spacing. Respect the provided furniture and real room dimensions."
DEFAULT_EDIT_INSTRUCTIONS = "Remove the main coffee table and keep the room layout otherwise unchanged."


class ValidationError(RuntimeError):
    pass


class FakeJob:
    def __init__(self, job_id: str):
        self.id = job_id


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _absolute_asset(name: str) -> str:
    path = LOCALTEST_DIR / name
    _assert(path.exists(), f"Missing test asset: {path}")
    return str(path.resolve())


def _make_sync_enqueue(fake_results: dict):
    def _sync_enqueue(func, *args, queue_name=None, **kwargs):
        job_id = f"qa-{uuid.uuid4().hex[:12]}"
        try:
            result = func(*args, **kwargs)
            fake_results[job_id] = {"status": "finished", "result": result}
        except Exception as exc:
            fake_results[job_id] = {"status": "failed", "error": str(exc)}
        return FakeJob(job_id), None

    return _sync_enqueue


def _queue_job_and_capture(client: TestClient, fake_results: dict, method: str, path: str, **kwargs) -> tuple[dict, dict]:
    response = client.request(method, path, **kwargs)
    _assert(response.status_code == 200, f"{method} {path} failed: {response.status_code} {response.text}")
    data = response.json()
    job_id = data.get("job_id")
    _assert(job_id, f"{method} {path} did not return job_id")
    result = fake_results.get(job_id)
    _assert(result is not None, f"No captured result for {method} {path}")
    _assert(result.get("status") == "finished", f"{method} {path} failed: {result}")
    return data, result


def _copy_refs(reference_map: dict[str, str | None], *, output_dir: Path, cache_dir: Path) -> dict[str, str]:
    copied: dict[str, str] = {}
    for key, reference in reference_map.items():
        copied_path = copy_image_reference(
            reference,
            repo_root=BASE_DIR,
            cache_dir=cache_dir,
            output_dir=output_dir,
            output_name=slugify_token(key),
        )
        if copied_path is not None:
            copied[key] = str(copied_path)
    return copied


def _budget_snapshot(*, budget_file: Path, max_model_calls: int) -> dict:
    snapshot = get_qa_budget_snapshot(
        budget_file=str(budget_file),
        max_calls=max_model_calls,
    )
    return {
        "budget_file": str(budget_file),
        "limit": int(snapshot.get("limit") or max_model_calls),
        "count": int(snapshot.get("count") or 0),
        "remaining": int(snapshot.get("remaining") or 0),
        "updated_at": snapshot.get("updated_at"),
    }


def _normalize_cases(selected_cases: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized = [slugify_token(case).replace("-", "_") for case in (selected_cases or DEFAULT_CASES)]
    seen = []
    for case in normalized:
        if case not in DEFAULT_CASES:
            raise ValidationError(f"Unsupported QA case: {case}")
        if case not in seen:
            seen.append(case)
    return seen


def _load_reference_internal_main(reference_run_dir: Path | None) -> dict | None:
    if reference_run_dir is None:
        return None
    suite_results_path = reference_run_dir / "suite_results.json"
    _assert(suite_results_path.exists(), f"Missing reference suite_results.json: {suite_results_path}")
    payload = json.loads(suite_results_path.read_text(encoding="utf-8"))
    reference = payload.get("internal_main")
    _assert(isinstance(reference, dict), f"Reference run missing internal_main payload: {suite_results_path}")
    result = reference.get("result") or {}
    _assert(result.get("result_url"), f"Reference internal_main missing result_url: {suite_results_path}")
    return reference


def _write_case_review(case_dir: Path, *, run_id: str, case_id: str, repeat_index: int, diversity_tags: list[str]) -> None:
    review_sheet = build_review_sheet(
        run_id=run_id,
        case_id=case_id,
        repeat_index=repeat_index,
        room_dimensions_mm=FIXED_ROOM_DIMENSIONS,
        diversity_tags=diversity_tags,
    )
    write_json(case_dir / "review_sheet.json", review_sheet)
    write_text(case_dir / "review_sheet.md", build_review_markdown(review_sheet))


def _package_internal_main(
    case_dir: Path,
    *,
    run_id: str,
    repeat_index: int,
    result: dict,
) -> dict:
    render = result.get("result") or {}
    downloads_dir = ensure_dir(case_dir / "downloads")
    cache_dir = ensure_dir(case_dir / "_cache")
    copied = _copy_refs(
        {
            "original": render.get("original_url"),
            "empty_room": render.get("empty_room_url"),
            "scale_guide": render.get("scale_guide_url"),
            "result_primary": render.get("result_url"),
            "result_variant_1": (render.get("result_urls") or [None, None, None])[0],
            "result_variant_2": (render.get("result_urls") or [None, None, None, None])[1] if len(render.get("result_urls") or []) > 1 else None,
            "result_variant_3": (render.get("result_urls") or [None, None, None, None])[2] if len(render.get("result_urls") or []) > 2 else None,
            "moodboard": render.get("moodboard_url"),
        },
        output_dir=downloads_dir,
        cache_dir=cache_dir,
    )

    board_tiles = []
    if render.get("original_url"):
        board_tiles.append(BoardTile("Original", render.get("original_url")))
    if render.get("empty_room_url"):
        board_tiles.append(BoardTile("Empty Room", render.get("empty_room_url")))
    if render.get("scale_guide_url"):
        board_tiles.append(BoardTile("Scale Guide", render.get("scale_guide_url")))
    for index, url in enumerate(render.get("result_urls") or [], start=1):
        board_tiles.append(BoardTile(f"Main Variant {index}", url))
    create_comparison_board(
        board_tiles,
        repo_root=BASE_DIR,
        cache_dir=cache_dir,
        output_path=case_dir / "board_main.png",
        columns=2,
    )

    _write_case_review(
        case_dir,
        run_id=run_id,
        case_id="internal_main",
        repeat_index=repeat_index,
        diversity_tags=["internal", "main_render", "customize", "room_dims_fixed"],
    )
    write_json(case_dir / "results.json", render)
    return copied


def _package_internal_detail(
    case_dir: Path,
    *,
    run_id: str,
    repeat_index: int,
    render_result: dict,
    detail_result: dict,
) -> dict:
    downloads_dir = ensure_dir(case_dir / "downloads")
    cache_dir = ensure_dir(case_dir / "_cache")
    render = render_result.get("result") or {}
    details = detail_result.get("result") or {}
    detail_entries = list(details.get("details") or [])
    first_detail = next(
        (
            item
            for item in detail_entries
            if isinstance(item, dict) and str(item.get("style_name") or "").startswith("Detail:")
        ),
        detail_entries[0] if detail_entries else {},
    ) or {}

    copied = _copy_refs(
        {
            "main_render": render.get("result_url"),
            "detail_target": first_detail.get("url"),
            "cutout_1": ((details.get("used_cutout_references") or [{}])[0] or {}).get("crop_url"),
            "cutout_2": ((details.get("used_cutout_references") or [{}, {}])[1] or {}).get("crop_url") if len(details.get("used_cutout_references") or []) > 1 else None,
        },
        output_dir=downloads_dir,
        cache_dir=cache_dir,
    )

    main_render_local = copy_image_reference(
        render.get("result_url"),
        repo_root=BASE_DIR,
        cache_dir=cache_dir,
        output_dir=downloads_dir,
        output_name="main_render_for_crop",
    )
    target_box = first_detail.get("target_box_2d")
    if main_render_local is not None and target_box:
        crop_box_reference(
            main_render_local,
            target_box,
            output_path=downloads_dir / "main_target_crop.png",
        )

    board_tiles = []
    if main_render_local is not None and (downloads_dir / "main_target_crop.png").exists():
        board_tiles.append(BoardTile("Main Target Crop", str(downloads_dir / "main_target_crop.png")))
    if render.get("result_url"):
        board_tiles.append(BoardTile("Main Render", render.get("result_url")))
    if first_detail.get("url"):
        board_tiles.append(BoardTile(first_detail.get("style_name") or "Target Detail", first_detail.get("url")))
    for index, item in enumerate((details.get("used_cutout_references") or [])[:2], start=1):
        if item.get("crop_url"):
            board_tiles.append(BoardTile(f"Cutout {index}", item.get("crop_url")))
    create_comparison_board(
        board_tiles,
        repo_root=BASE_DIR,
        cache_dir=cache_dir,
        output_path=case_dir / "board_detail.png",
        columns=2,
    )

    _write_case_review(
        case_dir,
        run_id=run_id,
        case_id="internal_detail",
        repeat_index=repeat_index,
        diversity_tags=["internal", "detail", "target_metadata"],
    )
    write_json(case_dir / "results.json", details)
    return copied


def _package_internal_edit(
    case_dir: Path,
    *,
    run_id: str,
    repeat_index: int,
    before_reference: str,
    edit_result: dict,
    instructions: str,
) -> dict:
    downloads_dir = ensure_dir(case_dir / "downloads")
    cache_dir = ensure_dir(case_dir / "_cache")
    result = edit_result.get("result") or {}
    after_url = ((result.get("urls") or [None])[0]) if result else None
    copied = _copy_refs(
        {
            "before": before_reference,
            "after": after_url,
        },
        output_dir=downloads_dir,
        cache_dir=cache_dir,
    )
    create_comparison_board(
        [
            BoardTile("Before", before_reference),
            BoardTile("After", after_url),
        ],
        repo_root=BASE_DIR,
        cache_dir=cache_dir,
        output_path=case_dir / "board_edit.png",
        columns=2,
    )
    _write_case_review(
        case_dir,
        run_id=run_id,
        case_id="internal_edit",
        repeat_index=repeat_index,
        diversity_tags=["internal", "edit", "prompt_adherence"],
    )
    write_json(case_dir / "results.json", {"instructions": instructions, **result})
    return copied


def _package_external_preset(
    case_dir: Path,
    *,
    run_id: str,
    repeat_index: int,
    source_reference: str,
    result: dict,
) -> dict:
    downloads_dir = ensure_dir(case_dir / "downloads")
    cache_dir = ensure_dir(case_dir / "_cache")
    payload = result.get("result") or {}
    render = payload.get("render") or {}
    copied = _copy_refs(
        {
            "source": source_reference,
            "result": render.get("result_url"),
            "empty_room": render.get("empty_room_url"),
        },
        output_dir=downloads_dir,
        cache_dir=cache_dir,
    )
    create_comparison_board(
        [
            BoardTile("Source", source_reference),
            BoardTile("Preset Result", render.get("result_url")),
        ],
        repo_root=BASE_DIR,
        cache_dir=cache_dir,
        output_path=case_dir / "board_external_preset.png",
        columns=2,
    )
    _write_case_review(
        case_dir,
        run_id=run_id,
        case_id="external_preset",
        repeat_index=repeat_index,
        diversity_tags=["external", "preset", "main_render"],
    )
    write_json(case_dir / "results.json", payload)
    return copied


def _package_external_cart(
    case_dir: Path,
    *,
    run_id: str,
    repeat_index: int,
    source_reference: str,
    product_refs: list[str],
    result: dict,
) -> dict:
    downloads_dir = ensure_dir(case_dir / "downloads")
    cache_dir = ensure_dir(case_dir / "_cache")
    payload = result.get("result") or {}
    render = payload.get("render") or {}
    copied = _copy_refs(
        {
            "source": source_reference,
            "result": render.get("result_url"),
            "product_1": product_refs[0] if len(product_refs) > 0 else None,
            "product_2": product_refs[1] if len(product_refs) > 1 else None,
            "product_3": product_refs[2] if len(product_refs) > 2 else None,
            "product_4": product_refs[3] if len(product_refs) > 3 else None,
        },
        output_dir=downloads_dir,
        cache_dir=cache_dir,
    )
    board_tiles = [BoardTile("Source", source_reference), BoardTile("Cart Result", render.get("result_url"))]
    for index, product in enumerate(product_refs, start=1):
        board_tiles.append(BoardTile(f"Product {index}", product))
    create_comparison_board(
        board_tiles,
        repo_root=BASE_DIR,
        cache_dir=cache_dir,
        output_path=case_dir / "board_external_cart.png",
        columns=2,
    )
    _write_case_review(
        case_dir,
        run_id=run_id,
        case_id="external_cart",
        repeat_index=repeat_index,
        diversity_tags=["external", "cart", "mixed_items", "room_dims_fixed"],
    )
    write_json(case_dir / "results.json", payload)
    return copied


def _run_full_suite_once(
    *,
    client: TestClient,
    fake_results: dict,
    external_api_key: str,
    room_photo: str,
    customize_moodboard: str,
    preset_products: list[str],
    runtime_cache_dir: Path,
    selected_cases: list[str],
    reference_internal_main: dict | None,
    placement_text: str,
    edit_instructions: str,
) -> dict:
    results: dict[str, dict] = {}
    need_internal_main = any(case in selected_cases for case in ["internal_main", "internal_detail", "internal_edit"])
    internal_main_result: dict = {}

    if need_internal_main and "internal_main" not in selected_cases and reference_internal_main is not None:
        internal_main_result = reference_internal_main.get("result") or {}
        _assert(internal_main_result.get("result_url"), "Reference internal main render missing result_url")
    elif need_internal_main:
        with open(room_photo, "rb") as room_fp, open(customize_moodboard, "rb") as mood_fp:
            internal_main_response, internal_main_job = _queue_job_and_capture(
                client,
                fake_results,
                "POST",
                "/async/render",
                data={
                    "room": "livingroom",
                    "style": "Customize",
                    "variant": "1",
                    "dimensions": FIXED_ROOM_DIMENSIONS,
                    "placement": placement_text,
                    "audience": "internal",
                },
                files={
                    "file": ("room_photo.png", room_fp, "image/png"),
                    "moodboard": ("customize_moodboard.png", mood_fp, "image/png"),
                },
            )
        internal_main_result = internal_main_job.get("result") or {}
        _assert(internal_main_result.get("result_url"), "Internal main render missing result_url")
        results["internal_main"] = {
            "enqueue_response": internal_main_response,
            "result": internal_main_result,
        }

    if "internal_detail" in selected_cases:
        internal_detail_response, internal_detail_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/generate-details",
            json={
                "image_url": internal_main_result.get("result_url"),
                "moodboard_url": internal_main_result.get("moodboard_url"),
                "furniture_data": internal_main_result.get("furniture_data"),
                "audience": "internal",
            },
        )
        internal_detail_result = internal_detail_job.get("result") or {}
        _assert((internal_detail_result.get("details") or []), "Internal detail render missing details")
        results["internal_detail"] = {
            "enqueue_response": internal_detail_response,
            "result": internal_detail_result,
        }

    if "internal_edit" in selected_cases:
        edit_instruction = edit_instructions
        edit_input_path = materialize_image_reference(
            internal_main_result.get("result_url"),
            repo_root=BASE_DIR,
            cache_dir=runtime_cache_dir,
        )
        _assert(edit_input_path is not None and edit_input_path.exists(), "Internal image edit could not materialize main render input")
        with open(edit_input_path, "rb") as edit_fp:
            internal_edit_response, internal_edit_job = _queue_job_and_capture(
                client,
                fake_results,
                "POST",
                "/async/generate-image-edit",
                data={"instructions": edit_instruction, "mode": "edit"},
                files=[("input_photos", ("render_result.png", edit_fp, "image/png"))],
            )
        internal_edit_result = internal_edit_job.get("result") or {}
        _assert((internal_edit_result.get("urls") or []), "Internal image edit missing urls")
        results["internal_edit"] = {
            "enqueue_response": internal_edit_response,
            "result": internal_edit_result,
            "instructions": edit_instruction,
        }

    external_headers = {"x-api-key": external_api_key}

    if "external_preset" in selected_cases:
        external_preset_response, external_preset_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/api/external/render/preset",
            headers=external_headers,
            json={
                "image_url": room_photo,
                "preset_id": "livingroom_french-modern_1",
                "dimensions": FIXED_ROOM_DIMENSIONS,
            },
        )
        external_preset_result = external_preset_job.get("result") or {}
        _assert((external_preset_result.get("render") or {}).get("result_url"), "External preset render missing result_url")
        results["external_preset"] = {
            "enqueue_response": external_preset_response,
            "result": external_preset_result,
        }

    if "external_cart" in selected_cases:
        external_cart_response, external_cart_job = _queue_job_and_capture(
            client,
            fake_results,
            "POST",
            "/api/external/render/cart",
            headers=external_headers,
            json={
                "image_url": room_photo,
                "room": "livingroom",
                "style": "French Modern",
                "variant": "1",
                "dimensions": FIXED_ROOM_DIMENSIONS,
                "items": [
                    {
                        "id": "product-1",
                        "name": "Product 1",
                        "category": "chair",
                        "image_url": preset_products[0],
                        "qty": 1,
                        "dims_mm": {"width_mm": 600, "depth_mm": 660, "height_mm": 660},
                    },
                    {
                        "id": "product-2",
                        "name": "Product 2",
                        "category": "chair",
                        "image_url": preset_products[1],
                        "qty": 1,
                        "dims_mm": {"width_mm": 600, "depth_mm": 660, "height_mm": 660},
                    },
                    {
                        "id": "product-3",
                        "name": "Product 3",
                        "category": "chair",
                        "image_url": preset_products[2],
                        "qty": 1,
                        "dims_mm": {"width_mm": 620, "depth_mm": 505, "height_mm": 800},
                    },
                    {
                        "id": "product-4",
                        "name": "Product 4",
                        "category": "sofa",
                        "image_url": preset_products[3],
                        "qty": 1,
                        "dims_mm": {"width_mm": 2790, "depth_mm": 1070, "height_mm": 700},
                    },
                ],
            },
        )
        external_cart_result = external_cart_job.get("result") or {}
        _assert((external_cart_result.get("render") or {}).get("result_url"), "External cart render missing result_url")
        results["external_cart"] = {
            "enqueue_response": external_cart_response,
            "result": external_cart_result,
        }

    return results


def _suite_run_dir(suite_root: Path, suite_name: str, repeat_index: int) -> tuple[str, Path]:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_id = f"{timestamp}_{slugify_token(suite_name)}_r{repeat_index:02d}_{uuid.uuid4().hex[:6]}"
    return run_id, ensure_dir(suite_root / run_id)


def repackage_existing_run(run_dir: Path) -> dict:
    run_dir = run_dir.resolve()
    manifest_path = run_dir / "manifest.json"
    suite_results_path = run_dir / "suite_results.json"
    _assert(manifest_path.exists(), f"Missing manifest.json: {manifest_path}")
    _assert(suite_results_path.exists(), f"Missing suite_results.json: {suite_results_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    suite_result = json.loads(suite_results_path.read_text(encoding="utf-8"))
    run_id = str(manifest.get("run_id") or run_dir.name)
    repeat_index = int(manifest.get("repeat_index") or 1)
    selected_cases = _normalize_cases(list(manifest.get("cases") or suite_result.keys()))
    reference_run_dir_text = str(manifest.get("reference_run_dir") or "").strip()
    reference_internal_main = _load_reference_internal_main(Path(reference_run_dir_text)) if reference_run_dir_text else None
    room_photo = _absolute_asset("room_photo.png")
    preset_products = [
        _absolute_asset("preset_product_1.png"),
        _absolute_asset("preset_product_2.png"),
        _absolute_asset("preset_product_3.png"),
        _absolute_asset("preset_product_4.png"),
    ]

    if "internal_main" in suite_result:
        _package_internal_main(run_dir / "internal_main", run_id=run_id, repeat_index=repeat_index, result=suite_result["internal_main"])
    if "internal_detail" in suite_result:
        _package_internal_detail(
            run_dir / "internal_detail",
            run_id=run_id,
            repeat_index=repeat_index,
            render_result=suite_result.get("internal_main") or reference_internal_main or {},
            detail_result=suite_result["internal_detail"],
        )
    if "internal_edit" in suite_result:
        _package_internal_edit(
            run_dir / "internal_edit",
            run_id=run_id,
            repeat_index=repeat_index,
            before_reference=((suite_result.get("internal_main") or {}).get("result") or {}).get("result_url") or room_photo,
            edit_result=suite_result["internal_edit"],
            instructions=suite_result["internal_edit"]["instructions"],
        )
    if "external_preset" in suite_result:
        _package_external_preset(
            run_dir / "external_preset",
            run_id=run_id,
            repeat_index=repeat_index,
            source_reference=room_photo,
            result=suite_result["external_preset"],
        )
    if "external_cart" in suite_result:
        _package_external_cart(
            run_dir / "external_cart",
            run_id=run_id,
            repeat_index=repeat_index,
            source_reference=room_photo,
            product_refs=preset_products,
            result=suite_result["external_cart"],
        )

    summary = {
        "run_dir": str(run_dir),
        "run_id": run_id,
        "repeat_index": repeat_index,
        "cases": selected_cases,
        "repackaged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    write_json(run_dir / "repackage_summary.json", summary)
    return summary


def run_quality_suite(
    *,
    repeats: int,
    suite_name: str,
    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS,
    budget_file: Path = DEFAULT_BUDGET_FILE,
    selected_cases: list[str] | None = None,
    reference_run_dir: Path | None = None,
    placement_text: str = DEFAULT_PLACEMENT_TEXT,
    edit_instructions: str = DEFAULT_EDIT_INSTRUCTIONS,
) -> Path:
    room_photo = _absolute_asset("room_photo.png")
    customize_moodboard = _absolute_asset("customize_moodboard.png")
    preset_products = [
        _absolute_asset("preset_product_1.png"),
        _absolute_asset("preset_product_2.png"),
        _absolute_asset("preset_product_3.png"),
        _absolute_asset("preset_product_4.png"),
    ]

    if not main.PRESET_MAP_PATH:
        main.PRESET_MAP_PATH = str((BASE_DIR / "preset_map.json").resolve())
        main.PRESET_MAP_CACHE = None

    external_api_key = os.getenv("EXTERNAL_INTEA_API_KEYS", "").split(",")[0].strip()
    _assert(external_api_key, "Missing external API key in environment")

    selected_cases = _normalize_cases(selected_cases)
    reference_internal_main = _load_reference_internal_main(reference_run_dir)
    suite_root = ensure_dir(QA_ROOT / slugify_token(suite_name))
    runtime_cache_dir = ensure_dir(suite_root / "_runtime_cache")
    run_index = []
    budget_file = budget_file.resolve()
    ensure_dir(budget_file.parent)

    original_budget_file = os.getenv("QA_GEMINI_BUDGET_FILE")
    original_budget_limit = os.getenv("QA_GEMINI_MAX_CALLS")
    os.environ["QA_GEMINI_BUDGET_FILE"] = str(budget_file)
    os.environ["QA_GEMINI_MAX_CALLS"] = str(max(1, int(max_model_calls)))

    try:
        for repeat_index in range(1, repeats + 1):
            fake_results = {}
            original_enqueue = main._enqueue_job
            original_redis_url = main.REDIS_URL
            main._enqueue_job = _make_sync_enqueue(fake_results)
            main.REDIS_URL = "quality-qa-sync"

            budget_before = _budget_snapshot(budget_file=budget_file, max_model_calls=max_model_calls)
            if budget_before["remaining"] <= 0:
                run_index.append(
                    {
                        "repeat_index": repeat_index,
                        "status": "skipped_budget_exhausted",
                        "budget_before": budget_before,
                    }
                )
                break

            run_id, run_dir = _suite_run_dir(suite_root, suite_name, repeat_index)

            try:
                with TestClient(main.app) as client:
                    suite_result = _run_full_suite_once(
                        client=client,
                        fake_results=fake_results,
                        external_api_key=external_api_key,
                        room_photo=room_photo,
                        customize_moodboard=customize_moodboard,
                        preset_products=preset_products,
                        runtime_cache_dir=runtime_cache_dir,
                        selected_cases=selected_cases,
                        reference_internal_main=reference_internal_main,
                        placement_text=placement_text,
                        edit_instructions=edit_instructions,
                    )

                manifest = {
                    "run_id": run_id,
                    "suite_name": suite_name,
                    "repeat_index": repeat_index,
                    "room_dimensions_mm": FIXED_ROOM_DIMENSIONS,
                    "budget_before": budget_before,
                    "reference_run_dir": str(reference_run_dir) if reference_run_dir else None,
                    "placement_text": placement_text,
                    "edit_instructions": edit_instructions,
                    "assets": {
                        "room_photo": room_photo,
                        "customize_moodboard": customize_moodboard,
                        "preset_products": preset_products,
                    },
                    "cases": list(selected_cases),
                }
                write_json(run_dir / "manifest.json", manifest)
                write_json(run_dir / "suite_results.json", suite_result)

                if "internal_main" in suite_result:
                    _package_internal_main(run_dir / "internal_main", run_id=run_id, repeat_index=repeat_index, result=suite_result["internal_main"])
                if "internal_detail" in suite_result:
                    _package_internal_detail(
                        run_dir / "internal_detail",
                        run_id=run_id,
                        repeat_index=repeat_index,
                        render_result=suite_result.get("internal_main") or reference_internal_main,
                        detail_result=suite_result["internal_detail"],
                    )
                if "internal_edit" in suite_result:
                    _package_internal_edit(
                        run_dir / "internal_edit",
                        run_id=run_id,
                        repeat_index=repeat_index,
                        before_reference=((suite_result.get("internal_main") or reference_internal_main or {}).get("result") or {}).get("result_url") or room_photo,
                        edit_result=suite_result["internal_edit"],
                        instructions=suite_result["internal_edit"]["instructions"],
                    )
                if "external_preset" in suite_result:
                    _package_external_preset(
                        run_dir / "external_preset",
                        run_id=run_id,
                        repeat_index=repeat_index,
                        source_reference=room_photo,
                        result=suite_result["external_preset"],
                    )
                if "external_cart" in suite_result:
                    _package_external_cart(
                        run_dir / "external_cart",
                        run_id=run_id,
                        repeat_index=repeat_index,
                        source_reference=room_photo,
                        product_refs=preset_products,
                        result=suite_result["external_cart"],
                    )
                budget_after = _budget_snapshot(budget_file=budget_file, max_model_calls=max_model_calls)
                write_json(run_dir / "budget_snapshot.json", {"before": budget_before, "after": budget_after})
                run_index.append(
                    {
                        "run_id": run_id,
                        "run_dir": str(run_dir),
                        "status": "success",
                        "budget_before": budget_before,
                        "budget_after": budget_after,
                    }
                )
            except Exception as exc:
                budget_after = _budget_snapshot(budget_file=budget_file, max_model_calls=max_model_calls)
                write_json(
                    run_dir / "failure.json",
                    {
                        "run_id": run_id,
                        "error": str(exc),
                        "budget_before": budget_before,
                        "budget_after": budget_after,
                    },
                )
                run_index.append(
                    {
                        "run_id": run_id,
                        "run_dir": str(run_dir),
                        "status": "failed",
                        "error": str(exc),
                        "budget_before": budget_before,
                        "budget_after": budget_after,
                    }
                )
            finally:
                main._enqueue_job = original_enqueue
                main.REDIS_URL = original_redis_url
    finally:
        if original_budget_file is None:
            os.environ.pop("QA_GEMINI_BUDGET_FILE", None)
        else:
            os.environ["QA_GEMINI_BUDGET_FILE"] = original_budget_file
        if original_budget_limit is None:
            os.environ.pop("QA_GEMINI_MAX_CALLS", None)
        else:
            os.environ["QA_GEMINI_MAX_CALLS"] = original_budget_limit

    write_json(
        suite_root / "run_index.json",
        {
            "suite_name": suite_name,
            "max_model_calls": int(max_model_calls),
            "budget_file": str(budget_file),
            "cases": list(selected_cases),
            "reference_run_dir": str(reference_run_dir) if reference_run_dir else None,
            "placement_text": placement_text,
            "edit_instructions": edit_instructions,
            "runs": run_index,
            "final_budget": _budget_snapshot(budget_file=budget_file, max_model_calls=max_model_calls),
        },
    )
    return suite_root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run local quality QA packaging for render flows.")
    parser.add_argument("--repeats", type=int, default=1, help="How many repeated suite runs to execute.")
    parser.add_argument("--suite-name", default="quality-baseline", help="Name used for the qa_runs folder.")
    parser.add_argument("--max-model-calls", type=int, default=DEFAULT_MAX_MODEL_CALLS, help="Hard cap for total Gemini model calls across all QA runs.")
    parser.add_argument("--budget-file", default=str(DEFAULT_BUDGET_FILE), help="Persistent JSON file used to track cumulative model call budget.")
    parser.add_argument("--cases", nargs="*", default=list(DEFAULT_CASES), help="Subset of QA cases to run.")
    parser.add_argument("--reference-run-dir", default="", help="Existing QA run directory used to reuse internal_main for detail/edit-only validation.")
    parser.add_argument("--repackage-run-dir", default="", help="Existing QA run directory to package again without making any model calls.")
    parser.add_argument("--placement-text", default=DEFAULT_PLACEMENT_TEXT, help="Placement text used for internal main render QA runs.")
    parser.add_argument("--edit-instructions", default=DEFAULT_EDIT_INSTRUCTIONS, help="Instructions used for internal image edit QA runs.")
    return parser.parse_args()


def main_cli() -> int:
    load_dotenv(BASE_DIR / ".env")
    args = parse_args()
    if str(args.repackage_run_dir).strip():
        summary = repackage_existing_run(Path(args.repackage_run_dir))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    suite_root = run_quality_suite(
        repeats=max(1, int(args.repeats)),
        suite_name=args.suite_name,
        max_model_calls=max(1, int(args.max_model_calls)),
        budget_file=Path(args.budget_file),
        selected_cases=list(args.cases or DEFAULT_CASES),
        reference_run_dir=Path(args.reference_run_dir).resolve() if str(args.reference_run_dir).strip() else None,
        placement_text=str(args.placement_text),
        edit_instructions=str(args.edit_instructions),
    )
    print(
        json.dumps(
            {
                "suite_root": str(suite_root),
                "room_dimensions_mm": FIXED_ROOM_DIMENSIONS,
                "cases": _normalize_cases(list(args.cases or DEFAULT_CASES)),
                "reference_run_dir": str(Path(args.reference_run_dir).resolve()) if str(args.reference_run_dir).strip() else None,
                "placement_text": str(args.placement_text),
                "edit_instructions": str(args.edit_instructions),
                "budget": _budget_snapshot(
                    budget_file=Path(args.budget_file),
                    max_model_calls=max(1, int(args.max_model_calls)),
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
