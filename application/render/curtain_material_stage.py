from __future__ import annotations

import os
import uuid
from typing import Callable

from PIL import Image, ImageOps

from application.render.artifact_paths import artifact_subprefix
from application.render.postprocess_support import category_match_family


CURTAIN_BLACKOUT_PERCENT = 90
CURTAIN_DETAIL_ROLE = "curtain_material"
CURTAIN_DETAIL_MODE = "curtain_material_generation"


def is_curtain_item(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    for field in (
        "category_canonical",
        "category",
        "sub_category",
        "subCategory",
        "category_path",
        "label",
    ):
        if category_match_family(item.get(field)) == "curtain":
            return True
    return False


def split_curtain_items(payload: dict) -> tuple[dict, dict | None]:
    render_payload = dict(payload or {})
    ordinary_items: list[dict] = []
    first_curtain: dict | None = None
    for item in list(render_payload.get("moodboard_items") or []):
        if isinstance(item, dict) and is_curtain_item(item):
            if first_curtain is None:
                first_curtain = dict(item)
            continue
        ordinary_items.append(item)
    if first_curtain is not None:
        render_payload["moodboard_items"] = ordinary_items
    return render_payload, first_curtain


def prepare_material_swatch_image(local_path: str, *, output_path: str, max_size: int = 2048) -> str | None:
    if not local_path or not os.path.exists(local_path):
        return None
    try:
        with Image.open(local_path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            if max(image.size) > max_size:
                image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            image.save(output_path, format="PNG")
        return output_path
    except Exception:
        return None


def build_curtain_edit_instructions() -> str:
    return (
        "레퍼런스 이미지는 평면 커튼 원단의 재질 디테일 이미지야. "
        "공간 속 기존 커튼의 색상과 재질만 이 레퍼런스로 변경해줘. "
        f"암막률은 정확히 {CURTAIN_BLACKOUT_PERCENT}%로 표현해줘. "
        "암막률은 원단의 불투명도만 뜻하고 공간의 노출, 조명 밝기, 화이트밸런스는 현재처럼 밝게 유지해줘. "
        "카메라, 공간 구조, 창문, 가구, 소품, 커튼의 위치와 주름 형태는 그대로 유지해줘. "
        "다른 요소는 변경하지 마."
    )


def build_curtain_detail_marker(item: dict) -> dict:
    material_reference_path = item.get("path") or item.get("url")
    label = item.get("product_name") or item.get("name") or item.get("label") or "Curtain"
    return {
        "label": label,
        "category": item.get("category") or "curtain",
        "category_canonical": "curtain",
        "item_id": item.get("item_id") or item.get("id"),
        "target_key": item.get("target_key") or "curtain_material_001",
        "source_index": item.get("payload_index") or item.get("source_index"),
        "detail_role": CURTAIN_DETAIL_ROLE,
        "detail_mode": CURTAIN_DETAIL_MODE,
        "material_reference_path": material_reference_path,
        "blackout_percent": CURTAIN_BLACKOUT_PERCENT,
    }


def _with_curtain_marker(render_result: dict, curtain_item: dict) -> dict:
    result = dict(render_result or {})
    furniture_data = [dict(item) for item in result.get("furniture_data") or [] if isinstance(item, dict)]
    furniture_data.append(build_curtain_detail_marker(curtain_item))
    result["furniture_data"] = furniture_data
    return result


def _curtain_result_prefix(
    render_result: dict,
    *,
    audience: str,
    build_s3_prefix: Callable[..., str],
) -> str:
    manifest = render_result.get("artifact_manifest") if isinstance(render_result, dict) else None
    root_prefix = str((manifest or {}).get("root_prefix") or "").strip() if isinstance(manifest, dict) else ""
    if root_prefix:
        return artifact_subprefix(root_prefix, "curtain-material")
    return build_s3_prefix(audience, "mainrendered", "curtain-material")


def apply_curtain_material_edit(
    render_result: dict,
    curtain_item: dict,
    *,
    audience: str,
    materialize_input: Callable[[str | None, str], str | None],
    process_image_edit_logic: Callable[..., str | None],
    resolve_image_url: Callable[..., str | None],
    build_s3_prefix: Callable[..., str],
) -> dict:
    result = _with_curtain_marker(render_result, curtain_item)
    base_url = result.get("result_url") or next(iter(result.get("result_urls") or []), None)
    material_reference = curtain_item.get("path") or curtain_item.get("url")
    if not base_url or not material_reference:
        result["curtain_material"] = {
            "status": "fallback_white",
            "blackout_percent": CURTAIN_BLACKOUT_PERCENT,
            "reason": "missing_base_or_material_reference",
        }
        return result

    try:
        local_base = materialize_input(base_url, "curtain_base")
        local_material = materialize_input(material_reference, "curtain_material")
        if not local_base or not local_material:
            raise RuntimeError("curtain inputs could not be materialized")
        edited_path = process_image_edit_logic(
            [local_base, local_material],
            build_curtain_edit_instructions(),
            "edit",
            uuid.uuid4().hex[:8],
            1,
        )
        if not edited_path:
            raise RuntimeError("curtain material edit returned no image")
        edited_url = resolve_image_url(
            edited_path,
            s3_prefix_override=_curtain_result_prefix(
                result,
                audience=audience,
                build_s3_prefix=build_s3_prefix,
            ),
        )
        if not edited_url:
            raise RuntimeError("curtain material edit could not be published")

        result["result_url"] = edited_url
        result["result_urls"] = [edited_url]
        manifest = result.get("artifact_manifest")
        if isinstance(manifest, dict):
            updated_manifest = dict(manifest)
            updated_manifest["selected_result_urls"] = [edited_url]
            updated_manifest["curtain_material_result_url"] = edited_url
            updated_manifest["selected_result_filename"] = os.path.basename(str(edited_path)) or None
            result["artifact_manifest"] = updated_manifest
        result["curtain_material"] = {
            "status": "applied",
            "blackout_percent": CURTAIN_BLACKOUT_PERCENT,
        }
        return result
    except Exception as exc:
        result["curtain_material"] = {
            "status": "fallback_white",
            "blackout_percent": CURTAIN_BLACKOUT_PERCENT,
            "reason": str(exc),
        }
        return result
