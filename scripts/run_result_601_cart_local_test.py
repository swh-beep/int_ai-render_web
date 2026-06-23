from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

load_dotenv(ROOT / ".env")

from api_models import CartRenderRequest  # noqa: E402
from application import job_entrypoints  # noqa: E402
from request_helpers import apply_cart_limits, build_cart_summary  # noqa: E402
import main  # noqa: E402


CASE_PATH = ROOT / "result-601-api-response-fresh.json"
OUT_DIR = ROOT / "outputs" / "result_601_cart_local_test"
ASSET_DIR = OUT_DIR / "assets"


PRODUCT_CATEGORY = {
    39522: "floor_lamp",
    39521: "rug",
    39080: "decor",
    39076: "decor",
    39067: "decor",
    38668: "lamp",
    38543: "sofa",
    37582: "sofa_table",
    37426: "chair",
}

PRODUCT_DIMS = {
    39522: {"width_mm": 280, "depth_mm": 280, "height_mm": 1380},
    39521: {"width_mm": 3000, "depth_mm": 4000, "height_mm": 30},
    39080: {"width_mm": 400, "depth_mm": 400, "height_mm": 950},
    39076: {"width_mm": 250, "depth_mm": 250, "height_mm": 430},
    39067: {"width_mm": 900, "depth_mm": 50, "height_mm": 1200},
    38668: {"width_mm": 550, "depth_mm": 440, "height_mm": 480},
    38543: {"width_mm": 4850, "depth_mm": 2850, "height_mm": 530},
    37582: {"width_mm": 1400, "depth_mm": 1400, "height_mm": 330},
    37426: {"width_mm": 370, "depth_mm": 460, "height_mm": 740},
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-").lower() or "item"


def _asset_path(asset_id: str) -> str | None:
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        path = ASSET_DIR / f"{asset_id}{ext}"
        if path.exists():
            return str(path)
    return None


def _item(row: dict) -> dict:
    product_id = int(row["productId"])
    return {
        "id": f"product_{product_id}",
        "category": PRODUCT_CATEGORY.get(product_id, "decor"),
        "image_url": _asset_path(str(product_id)) or row["imageUrl"],
        "qty": 1,
        "dims_mm": PRODUCT_DIMS.get(product_id),
        "name": row.get("aiLabel") or row.get("productName") or f"product {product_id}",
        "options": {
            "product_id": product_id,
            "ai_description": row.get("aiDescription") or "",
        },
        "product_type": ((row.get("productType") or {}).get("name") or ""),
    }


def _build_request(data: dict) -> CartRenderRequest:
    image_url = _asset_path("room") or (data.get("inputPhotos") or [{}])[0].get("url") or data.get("beforeImage")
    if not image_url:
        raise RuntimeError("601 input image URL is missing")
    items = [_item(row) for row in data.get("furnitureItems") or []]
    if not items:
        raise RuntimeError("601 furnitureItems are missing")
    return CartRenderRequest(
        image_url=image_url,
        room=(data.get("roomType") or {}).get("aiServiceValue") or "livingroom",
        style="Mid-Century",
        variant=str(data.get("variant") or "1"),
        placement="Use the selected cart products only. Preserve the room structure and camera.",
        simple_generation_mode=True,
        items=items,
    )


def main_entry() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    local_resolve_image_url = lambda local_path, s3_prefix_override=None: local_path
    main.resolve_image_url = local_resolve_image_url
    try:
        job_entrypoints._services().resolve_image_url = local_resolve_image_url
    except Exception:
        pass

    data = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    req = _build_request(data)
    job_payload, kept, dropped = main.build_external_cart_job(
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
        # Local-only test harness: keep normalized cart item files on disk instead
        # of publishing them to S3. The desktop sandbox blocks outbound S3 calls.
        resolve_image_url=local_resolve_image_url,
        build_s3_prefix=main._build_s3_prefix,
        build_item_target_key=main._build_item_target_key,
    )

    started_at = time.time()
    result = job_entrypoints.job_render_with_details(job_payload)
    elapsed_sec = round(time.time() - started_at, 2)
    render = result.get("render") or {}
    detail_payload = result.get("details") or {}
    details = detail_payload.get("details") or []
    summary = {
        "elapsed_sec": elapsed_sec,
        "error": result.get("error") or render.get("error") or detail_payload.get("error"),
        "cart_kept_count": len(kept),
        "cart_dropped_count": len(dropped),
        "main_url": render.get("result_url"),
        "main_urls": render.get("result_urls") or [],
        "empty_room_url": render.get("empty_room_url"),
        "detail_count": len(details),
        "detail_urls": [row.get("url") for row in details if isinstance(row, dict) and row.get("url")],
        "detail_targets": [
            {
                "index": row.get("index"),
                "target_key": row.get("target_key"),
                "target_label": row.get("target_label"),
                "target_box_source": row.get("target_box_source"),
                "target_box_2d": row.get("target_box_2d"),
            }
            for row in details
            if isinstance(row, dict)
        ],
        "detail_box_sources": [
            {
                "target_key": row.get("target_key"),
                "label": row.get("label"),
                "box_source": row.get("box_source"),
                "box_2d": row.get("box_2d"),
                "detail_skip_reason": row.get("detail_skip_reason"),
            }
            for row in (detail_payload.get("furniture_data") or [])
            if isinstance(row, dict)
        ],
    }
    out_path = OUT_DIR / f"summary_{int(time.time())}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary_path": str(out_path), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main_entry()
