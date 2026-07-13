import os
import re
import shutil
from dataclasses import dataclass
from typing import Any, Callable


_CATEGORY_METADATA_FIELDS = (
    "category_path",
    "category_source",
    "main_category",
    "sub_category",
    "mainCategory",
    "subCategory",
    "product_type",
)


@dataclass
class RenderReferenceSelection:
    ref_path: str | None
    mb_url: str | None
    ref_paths: list[str]
    item_refs: list[dict[str, Any]]


def prepare_render_references(
    *,
    moodboard_items: list[dict] | None,
    style: str,
    room: str,
    variant: str,
    moodboard: Any | None,
    timestamp: int,
    unique_id: str,
    prefix_customize: str,
    use_s3_moodboard: bool,
    materialize_input: Callable[[str | None, str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    build_item_target_key: Callable[..., str],
    canonical_category: Callable[[str | None], str],
    find_s3_moodboard_key: Callable[[str, str, str], str | None],
    s3_public_url: Callable[[str], str],
) -> RenderReferenceSelection:
    ref_path = None
    mb_url = None
    ref_paths: list[str] = []
    item_refs: list[dict[str, Any]] = []

    if moodboard_items:
        for item in moodboard_items:
            try:
                label = str(item.get("label") or item.get("category") or item.get("category_canonical") or item.get("name") or "Item")
                src = item.get("path") or item.get("url")
                local_path = materialize_input(src, "mb") if src else None
                if local_path and os.path.exists(local_path):
                    try:
                        qty_val = int(item.get("qty") or 1)
                    except Exception:
                        qty_val = 1
                    if qty_val < 1:
                        qty_val = 1
                    payload_index = int(item.get("payload_index") or (len(item_refs) + 1))
                    category = item.get("category")
                    item_id = item.get("item_id")
                    target_key = item.get("target_key") or build_item_target_key(
                        "cart",
                        payload_index,
                        label=label,
                        category=category,
                        item_id=item_id,
                    )
                    item_refs.append(
                        {
                            "label": label,
                            "path": local_path,
                            "dims_mm": item.get("dims_mm"),
                            "options": item.get("options"),
                            "qty": qty_val,
                            "category": category,
                            "product_name": item.get("product_name"),
                            "item_id": item_id,
                            "payload_index": payload_index,
                            "target_key": target_key,
                            **{
                                field: item.get(field)
                                for field in _CATEGORY_METADATA_FIELDS
                                if item.get(field) not in (None, "")
                            },
                        }
                    )
                    ref_paths.append(local_path)
            except Exception:
                continue

    if not ref_paths and style != "Customize":
        safe_room = room.lower().replace(" ", "")
        safe_style = style.lower().replace(" ", "-").replace("_", "-")
        assets_dir = None

        if not use_s3_moodboard:
            target_path = os.path.join("assets", safe_room, safe_style)
            if os.path.exists(target_path):
                assets_dir = target_path
            else:
                root_assets = "assets"
                if os.path.exists(root_assets):
                    found_room = next((directory for directory in os.listdir(root_assets) if directory.lower() == safe_room), None)
                    if found_room:
                        room_path = os.path.join(root_assets, found_room)
                        found_style = next((directory for directory in os.listdir(room_path) if directory.lower() == safe_style), None)
                        if found_style:
                            assets_dir = os.path.join(room_path, found_style)

            if assets_dir and os.path.exists(assets_dir):
                files = sorted(os.listdir(assets_dir))
                found = False
                pattern = rf"(?:^|[^0-9]){re.escape(variant)}(?:[^0-9]|$)"
                valid_exts = (".png", ".jpg", ".jpeg", ".webp")

                for filename in files:
                    if filename.lower().endswith(valid_exts) and re.search(pattern, filename, re.IGNORECASE):
                        ref_path = os.path.join(assets_dir, filename)
                        mb_url = f"/assets/{os.path.basename(os.path.dirname(assets_dir))}/{os.path.basename(assets_dir)}/{filename}"
                        found = True
                        break

                if not found:
                    valid_files = [filename for filename in files if filename.lower().endswith(valid_exts)]
                    if valid_files:
                        filename = valid_files[0]
                        ref_path = os.path.join(assets_dir, filename)
                        mb_url = f"/assets/{os.path.basename(os.path.dirname(assets_dir))}/{os.path.basename(assets_dir)}/{filename}"

        if use_s3_moodboard or not ref_path:
            s3_key = find_s3_moodboard_key(safe_room, safe_style, variant)
            if s3_key:
                mb_url = s3_public_url(s3_key)
                ref_path = materialize_input(mb_url, "mb")

    if not ref_paths and style == "Customize" and moodboard:
        moodboard_name = "".join([char for char in moodboard.filename if char.isalnum() or char in "._-"])
        moodboard_path = os.path.join("outputs", f"mb_{timestamp}_{unique_id}_{moodboard_name}")
        with open(moodboard_path, "wb") as buffer:
            shutil.copyfileobj(moodboard.file, buffer)
        ref_path = moodboard_path
        mb_url = resolve_image_url(moodboard_path, s3_prefix_override=prefix_customize)

    if not ref_paths and ref_path:
        ref_paths = [ref_path]

    return RenderReferenceSelection(
        ref_path=ref_path,
        mb_url=mb_url,
        ref_paths=ref_paths,
        item_refs=item_refs,
    )
