import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from application.render.item_analysis_profile import (
    DETAILED_ITEM_ANALYSIS_PROFILE,
    normalize_item_analysis_profile,
)


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
class RenderInputs:
    file_path: str
    moodboard_path: str | None
    moodboard_items: list[dict]
    room: str
    style: str
    variant: str
    dimensions: str
    placement: str
    audience: str
    item_analysis_profile: str = DETAILED_ITEM_ANALYSIS_PROFILE
    precomputed_empty_room_path: str | None = None
    precomputed_empty_room_raw_path: str | None = None


@dataclass
class RenderResources:
    file_obj: Any
    mood_obj: Any | None
    local_items: list[dict]
    cleanup_paths: list[str]
    cleanup_sources: list[str]


def prepare_render_inputs(
    payload: dict,
    *,
    materialize_input: Callable[[str | None, str], str | None],
    normalize_audience: Callable[[Optional[str]], str],
) -> RenderInputs | dict:
    file_path = materialize_input(payload.get("file_path"), "input")
    if not file_path or not os.path.exists(file_path):
        return {"error": "Input file not found"}

    return RenderInputs(
        file_path=file_path,
        moodboard_path=payload.get("moodboard_path"),
        moodboard_items=payload.get("moodboard_items") or [],
        room=payload.get("room", ""),
        style=payload.get("style", ""),
        variant=payload.get("variant", ""),
        dimensions=payload.get("dimensions", ""),
        placement=payload.get("placement", ""),
        audience=normalize_audience(payload.get("audience")),
        item_analysis_profile=normalize_item_analysis_profile(payload.get("item_analysis_profile")),
        precomputed_empty_room_path=payload.get("precomputed_empty_room_path"),
        precomputed_empty_room_raw_path=payload.get("precomputed_empty_room_raw_path"),
    )


def collect_local_moodboard_items(
    moodboard_items: list[dict],
    *,
    materialize_input: Callable[[str | None, str], str | None],
) -> tuple[list[dict], list[str], list[str]]:
    local_items: list[dict] = []
    cleanup_paths: list[str] = []
    cleanup_sources: list[str] = []

    for item in moodboard_items:
        try:
            path_or_url = item.get("path") or item.get("url")
            label = item.get("label") or item.get("name") or item.get("category") or "Item"
            local_path = materialize_input(path_or_url, "mood")
            if local_path and os.path.exists(local_path):
                try:
                    qty_val = int(item.get("qty") or 1)
                except Exception:
                    qty_val = 1
                if qty_val < 1:
                    qty_val = 1
                local_items.append(
                    {
                        "label": label,
                        "path": local_path,
                        "dims_mm": item.get("dims_mm"),
                        "options": item.get("options"),
                        "qty": qty_val,
                        "category": item.get("category"),
                        "product_name": item.get("product_name"),
                        "item_id": item.get("item_id"),
                        "payload_index": item.get("payload_index"),
                        "target_key": item.get("target_key"),
                        **{
                            field: item.get(field)
                            for field in _CATEGORY_METADATA_FIELDS
                            if item.get(field) not in (None, "")
                        },
                    }
                )
                if os.path.basename(local_path).startswith("mood_"):
                    cleanup_paths.append(local_path)
            if isinstance(path_or_url, str) and path_or_url.startswith("/outputs/"):
                src_local = path_or_url.lstrip("/")
                if os.path.basename(src_local).startswith("cart_item_") and os.path.exists(src_local):
                    cleanup_sources.append(src_local)
        except Exception:
            continue

    return local_items, cleanup_paths, cleanup_sources


def prepare_render_resources(
    inputs: RenderInputs,
    *,
    materialize_input: Callable[[str | None, str], str | None],
    local_upload_factory: Callable[[str], Any],
) -> RenderResources:
    file_obj = local_upload_factory(inputs.file_path)
    mood_local = materialize_input(inputs.moodboard_path, "mood") if inputs.moodboard_path else None
    mood_obj = local_upload_factory(mood_local) if mood_local and os.path.exists(mood_local) else None
    local_items, cleanup_paths, cleanup_sources = collect_local_moodboard_items(
        inputs.moodboard_items,
        materialize_input=materialize_input,
    )
    return RenderResources(
        file_obj=file_obj,
        mood_obj=mood_obj,
        local_items=local_items,
        cleanup_paths=cleanup_paths,
        cleanup_sources=cleanup_sources,
    )


def cleanup_render_resources(resources: RenderResources) -> None:
    resources.file_obj.close()
    if resources.mood_obj:
        resources.mood_obj.close()
    for path in resources.cleanup_paths:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    for path in resources.cleanup_sources:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
