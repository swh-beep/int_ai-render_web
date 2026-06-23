from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["S3_REQUIRED"] = "0"
os.environ["S3_BUCKET"] = ""
os.environ["AWS_REGION"] = ""
os.environ.setdefault("USE_S3_MOODBOARD", "0")
os.environ.setdefault("LOG_BRIEF", "1")

import main  # noqa: E402
from api_models import CartSimpleBatchRequest  # noqa: E402
from application import job_entrypoints  # noqa: E402
from request_helpers import apply_cart_limits, build_cart_summary  # noqa: E402


ASSET_DIR = ROOT / "outputs" / "cart_simple_batch_local_test" / "ascii_assets_3155"
OUT_DIR = ROOT / "outputs" / "cart_simple_batch_live_test"


def item(
    item_id: str,
    category: str,
    filename: str,
    name: str,
    dims_mm: dict[str, int],
) -> dict:
    return {
        "id": item_id,
        "category": category,
        "image_url": str(ASSET_DIR / filename),
        "qty": 1,
        "dims_mm": dims_mm,
        "name": name,
    }


def to_local_path(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("/outputs/"):
        return str(ROOT / url.lstrip("/").replace("/", os.sep))
    if url.startswith("outputs/"):
        return str(ROOT / url.replace("/", os.sep))
    if os.path.exists(url):
        return str(Path(url).resolve())
    return url


def main_entry() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    variant_1 = [
        item(
            "local-coffee-table",
            "coffee_table",
            "item1_coffee.webp",
            "Gubi Epic Coffee Table",
            {"width_mm": 1100, "depth_mm": 1100, "height_mm": 350},
        ),
        item(
            "local-lounge-chair",
            "lounge_chair",
            "item2_chair.webp",
            "New Works Bukowski Lounge Chair",
            {"width_mm": 760, "depth_mm": 820, "height_mm": 760},
        ),
        item(
            "local-speaker",
            "speaker",
            "item3_speaker.webp",
            "Bang Olufsen Beolab 18",
            {"width_mm": 200, "depth_mm": 200, "height_mm": 1320},
        ),
    ]
    variant_2 = [
        variant_1[0],
        item(
            "local-side-table",
            "side_table",
            "item4_side.png",
            "Eastern Edition Black Granite Side Table",
            {"width_mm": 450, "depth_mm": 450, "height_mm": 520},
        ),
        item(
            "local-floor-lamp",
            "floor_lamp",
            "item6_lamp.png",
            "Verpan Fun 1STM Floor Stand",
            {"width_mm": 450, "depth_mm": 450, "height_mm": 1600},
        ),
    ]
    variant_3 = [
        variant_1[1],
        item(
            "local-wall-decor",
            "wall_decor",
            "item5_wall.webp",
            "Valentin Loellmann Mirror Wall Piece",
            {"width_mm": 900, "height_mm": 1200},
        ),
        variant_2[2],
    ]

    req = CartSimpleBatchRequest(
        image_url=str(ASSET_DIR / "room.jpg"),
        room="living room",
        style="warm modern Korean apartment living room",
        placement="Keep the room camera and architecture stable across all variants.",
        simple_generation_mode=True,
        variants=[
            {"variant": "1", "items": variant_1},
            {"variant": "2", "items": variant_2},
            {"variant": "3", "items": variant_3},
        ],
    )

    job_payload, response_variants = main.build_external_cart_batch_job(
        req,
        cart_max_items=main.CART_MAX_ITEMS,
        apply_cart_limits=apply_cart_limits,
        build_cart_summary=build_cart_summary,
        materialize_input=main._materialize_input,
        normalize_item_image=lambda local_path, unique_id, index: main._normalize_item_image(
            local_path,
            unique_id,
            index,
            max_size=1024,
        ),
        resolve_image_url=main.resolve_image_url,
        build_s3_prefix=main._build_s3_prefix,
        build_item_target_key=main._build_item_target_key,
    )

    started_at = time.time()
    result = job_entrypoints.job_render_cart_simple_batch(job_payload)
    elapsed_sec = round(time.time() - started_at, 2)

    result_rows = result.get("results") if isinstance(result, dict) else []
    image_rows = []
    for row in result_rows or []:
        render = row.get("render") or {}
        image_rows.append(
            {
                "variant_index": row.get("variant_index"),
                "result_url": render.get("result_url"),
                "local_path": to_local_path(render.get("result_url")),
                "empty_room_url": render.get("empty_room_url"),
                "empty_room_local_path": to_local_path(render.get("empty_room_url")),
                "error": row.get("error") or render.get("error"),
            }
        )

    summary = {
        "elapsed_sec": elapsed_sec,
        "error": result.get("error") if isinstance(result, dict) else None,
        "empty_room_url": result.get("empty_room_url") if isinstance(result, dict) else None,
        "empty_room_local_path": to_local_path(result.get("empty_room_url") if isinstance(result, dict) else None),
        "variant_count": len(response_variants),
        "result_count": len(image_rows),
        "images": image_rows,
    }
    summary_path = OUT_DIR / f"live_summary_{int(time.time())}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary_path": str(summary_path), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main_entry()
