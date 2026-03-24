from __future__ import annotations

import os
import shutil
import time
import uuid
from typing import Callable

from fastapi import UploadFile

from api_models import (
    CartRenderRequest,
    DetailRequest,
    FinalizeRequest,
    InternalRenderRequest,
    PresetRenderRequest,
    RegenerateDetailRequest,
    UpscaleRequest,
)
from preset_helpers import resolve_preset_request


def build_internal_render_job_payload(req: InternalRenderRequest) -> dict:
    payload = {
        "file_path": req.image_url,
        "moodboard_path": req.moodboard_url,
        "room": req.room,
        "style": req.style,
        "variant": req.variant,
        "dimensions": req.dimensions or "",
        "placement": req.placement or "",
        "audience": "internal",
    }
    return {"render": payload}


def _safe_upload_name(upload: UploadFile | None, fallback: str) -> str:
    if upload is None:
        return fallback
    filename = upload.filename or fallback
    safe = "".join([c for c in filename if c.isalnum() or c in "._-"])
    return safe or fallback


def persist_internal_render_uploads(file: UploadFile, moodboard: UploadFile | None) -> tuple[str, str | None]:
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time())

    raw_path = os.path.join("outputs", f"raw_{timestamp}_{unique_id}_{_safe_upload_name(file, 'input.png')}")
    with open(raw_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    mood_path = None
    if moodboard is not None:
        mood_path = os.path.join("outputs", f"mb_{timestamp}_{unique_id}_{_safe_upload_name(moodboard, 'moodboard.png')}")
        with open(mood_path, "wb") as buffer:
            shutil.copyfileobj(moodboard.file, buffer)

    return raw_path, mood_path


def persist_internal_media_uploads(
    input_photos: list[UploadFile],
    *,
    prefix: str,
    mode: str | None = None,
    mask: UploadFile | None = None,
) -> tuple[str, list[str], str | None]:
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time())

    saved_photo_paths = []
    for idx, photo in enumerate(input_photos):
        name = _safe_upload_name(photo, f"{prefix}_{idx}.png")
        if mode:
            path = os.path.join("outputs", f"{prefix}_{mode}_{timestamp}_{unique_id}_{idx}_{name}")
        else:
            path = os.path.join("outputs", f"{prefix}_{timestamp}_{unique_id}_{idx}_{name}")
        with open(path, "wb") as buffer:
            shutil.copyfileobj(photo.file, buffer)
        saved_photo_paths.append(path)

    mask_path = None
    if mask is not None and mask.filename:
        mask_name = _safe_upload_name(mask, "mask.png")
        mode_token = mode or "default"
        mask_path = os.path.join("outputs", f"mask_{mode_token}_{timestamp}_{unique_id}_{mask_name}")
        with open(mask_path, "wb") as buffer:
            shutil.copyfileobj(mask.file, buffer)

    return unique_id, saved_photo_paths, mask_path


def build_internal_async_render_job_payload(
    *,
    raw_path: str,
    mood_path: str | None,
    room: str,
    style: str,
    variant: str,
    dimensions: str,
    placement: str,
    resolve_image_url: Callable[[str | None, str | None], str | None],
    build_s3_prefix: Callable[[str, str, str | None], str],
) -> dict:
    audience = "internal"
    file_ref = resolve_image_url(raw_path, build_s3_prefix(audience, "mainrendered", "user-photos"))
    mood_ref = resolve_image_url(mood_path, build_s3_prefix(audience, "customize", None)) if mood_path else None
    return {
        "file_path": file_ref or raw_path,
        "moodboard_path": mood_ref or mood_path,
        "room": room,
        "style": style,
        "variant": variant,
        "dimensions": dimensions,
        "placement": placement,
        "audience": audience,
    }


def build_image_edit_job_payload(
    *,
    saved_photo_paths: list[str],
    instructions: str,
    mode: str,
    unique_id: str,
    mask_path: str | None,
    resolve_image_url: Callable[[str | None, str | None], str | None],
    build_s3_prefix: Callable[[str, str, str | None], str],
) -> dict:
    audience = "internal"
    category = "editrendered" if mode == "edit" else "decorrendered"
    prefix_user = build_s3_prefix(audience, category, "user-photos")
    photo_refs = [resolve_image_url(path, prefix_user) or path for path in saved_photo_paths]
    mask_ref = resolve_image_url(mask_path, prefix_user) if mask_path else None
    return {
        "photo_paths": photo_refs,
        "instructions": instructions,
        "mode": mode,
        "unique_id": unique_id,
        "mask_path": mask_ref or mask_path,
        "audience": audience,
    }


def build_frontal_view_job_payload(
    *,
    saved_photo_paths: list[str],
    unique_id: str,
    resolve_image_url: Callable[[str | None, str | None], str | None],
    build_s3_prefix: Callable[[str, str, str | None], str],
) -> dict:
    audience = "internal"
    prefix_user = build_s3_prefix(audience, "realphotorendered", "user-photos")
    photo_refs = [resolve_image_url(path, prefix_user) or path for path in saved_photo_paths]
    return {"photo_paths": photo_refs, "unique_id": unique_id, "audience": audience}


def build_external_preset_job(req: PresetRenderRequest, preset_map: dict) -> tuple[dict, dict]:
    resolved = resolve_preset_request(
        {
            "preset_id": req.preset_id,
            "room": req.room,
            "style": req.style,
            "variant": req.variant,
            "dimensions": req.dimensions,
            "placement": req.placement,
        },
        preset_map,
    )

    resolved_surface = {
        "room": resolved["room"],
        "style": resolved["style"],
        "variant": resolved["variant"],
    }
    payload = {
        "file_path": req.image_url,
        "moodboard_path": None,
        "room": resolved_surface["room"],
        "style": resolved_surface["style"],
        "variant": resolved_surface["variant"],
        "dimensions": resolved["dimensions"],
        "placement": resolved["placement"],
        "audience": "external",
    }
    job_payload = {
        "render": payload,
        "extra": {
            "preset_id": req.preset_id,
            "resolved": resolved_surface,
        },
    }
    return job_payload, resolved_surface


def build_external_cart_job(
    req: CartRenderRequest,
    *,
    cart_max_items: int,
    apply_cart_limits: Callable[[list[dict], int], tuple[list[dict], list[dict]]],
    build_cart_summary: Callable[[list[dict]], str],
    materialize_input: Callable[[str, str], str | None],
    normalize_item_image: Callable[[str, str, int], str | None],
    resolve_image_url: Callable[[str, str | None], str | None],
    build_s3_prefix: Callable[[str, str], str],
    build_item_target_key: Callable[..., str],
) -> tuple[dict, list[dict], list[dict]]:
    items = [it.model_dump() for it in req.items]
    kept, dropped = apply_cart_limits(items, cart_max_items)
    if not kept:
        raise ValueError("No items after applying limits")

    unique_id = uuid.uuid4().hex[:8]
    item_refs = []
    for idx, it in enumerate(kept):
        img_url = it.get("image_url") or it.get("image")
        if not img_url:
            continue
        local_src = materialize_input(img_url, f"cart_item_{idx}")
        norm_path = normalize_item_image(local_src, unique_id, idx + 1) if local_src else None
        if not norm_path:
            continue
        ref_url = resolve_image_url(norm_path, build_s3_prefix("external", "customize"))
        if ref_url and isinstance(ref_url, str) and ref_url.startswith("http"):
            try:
                if os.path.exists(norm_path):
                    os.remove(norm_path)
            except Exception:
                pass
        try:
            if local_src and os.path.exists(local_src):
                abs_src = os.path.abspath(local_src)
                abs_out = os.path.abspath("outputs") + os.sep
                if abs_src.startswith(abs_out) and os.path.basename(local_src).startswith("cart_item_"):
                    os.remove(local_src)
        except Exception:
            pass
        label = it.get("name") or it.get("category") or it.get("id") or "Item"
        try:
            qty_val = int(it.get("qty") or 1)
        except Exception:
            qty_val = 1
        if qty_val < 1:
            qty_val = 1
        item_refs.append(
            {
                "label": label,
                "path": ref_url or norm_path,
                "dims_mm": it.get("dims_mm"),
                "options": it.get("options"),
                "qty": qty_val,
                "category": it.get("category"),
                "item_id": it.get("id"),
                "payload_index": idx + 1,
                "target_key": build_item_target_key(
                    "cart",
                    idx + 1,
                    label=label,
                    category=it.get("category"),
                    item_id=it.get("id"),
                ),
            }
        )

    if not item_refs:
        raise ValueError("No valid item images after processing")

    placement_parts = []
    if req.style:
        placement_parts.append(f"STYLE: {req.style}")
    if req.placement:
        placement_parts.append(req.placement)
    placement_parts.append(build_cart_summary(kept))
    placement = "\n".join([p for p in placement_parts if p])

    payload = {
        "file_path": req.image_url,
        "moodboard_items": item_refs,
        "room": req.room or "livingroom",
        "style": "Customize",
        "variant": str(req.variant or "1"),
        "dimensions": req.dimensions or "",
        "placement": placement,
        "audience": "external",
    }
    job_payload = {
        "render": payload,
        "extra": {"cart_kept": kept, "cart_dropped": dropped},
    }
    return job_payload, kept, dropped


def build_detail_generation_job_payload(req: DetailRequest) -> dict:
    return {
        "image_url": req.image_url,
        "moodboard_url": req.moodboard_url,
        "furniture_data": req.furniture_data,
        "audience": req.audience,
    }


def build_regenerate_detail_job_payload(req: RegenerateDetailRequest) -> dict:
    return {
        "original_image_url": req.original_image_url,
        "style_index": req.style_index,
        "target_key": req.target_key,
        "target_label": req.target_label,
        "style_index_mode": req.style_index_mode,
        "furniture_data": req.furniture_data,
        "moodboard_url": req.moodboard_url,
        "audience": req.audience,
    }


def build_upscale_job_payload(req: UpscaleRequest) -> dict:
    return {"image_url": req.image_url}


def build_finalize_download_job_payload(req: FinalizeRequest) -> dict:
    return {"image_url": req.image_url}


def build_empty_room_job_payload(req: FinalizeRequest) -> dict:
    return {"image_url": req.image_url, "audience": "internal"}
