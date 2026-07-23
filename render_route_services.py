from __future__ import annotations

import os
import shutil
import time
import uuid
from typing import Callable

from fastapi import UploadFile

from application.render.direct_item_image_prep import prepare_direct_item_image
from application.render.curtain_material_stage import is_curtain_item, prepare_material_swatch_image
from application.render.item_analysis_profile import DETAILED_ITEM_ANALYSIS_PROFILE
from application.tracker_metadata import attach_tracker_metadata, extract_tracker_metadata
from api_models import (
    CartRenderRequest,
    CartSimpleBatchRequest,
    DetailRequest,
    ExternalRenderVideoRequest,
    FinalizeRequest,
    InternalRenderRequest,
    PresetRenderRequest,
    RegenerateDetailRequest,
    UpscaleRequest,
)
from preset_helpers import resolve_preset_request


_CATEGORY_METADATA_FIELDS = (
    "category_path",
    "category_source",
    "main_category",
    "sub_category",
    "mainCategory",
    "subCategory",
    "product_type",
)


def _category_metadata(payload: dict) -> dict:
    return {field: payload.get(field) for field in _CATEGORY_METADATA_FIELDS if payload.get(field) not in (None, "")}


def _text_field(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "categoryName", "category_name", "title", "value"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
    return ""


def _category_path_leaf(value) -> str:
    text = _text_field(value)
    if not text:
        return ""
    parts = [text]
    for separator in (">", "/", "|"):
        if separator in text:
            parts = [part.strip() for part in text.split(separator)]
            break
    for part in reversed(parts):
        if part:
            return part
    return text


def _readable_category_label(value) -> str:
    text = _text_field(value)
    if not text:
        return ""
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def _category_label(payload: dict) -> str:
    payload = payload if isinstance(payload, dict) else {}
    for key in ("subCategory", "sub_category"):
        label = _readable_category_label(payload.get(key))
        if label:
            return label
    label = _readable_category_label(_category_path_leaf(payload.get("category_path")))
    if label:
        return label
    for key in ("mainCategory", "main_category", "category_canonical", "category"):
        label = _readable_category_label(payload.get(key))
        if label:
            return label
    return ""


def _product_name(payload: dict) -> str:
    return _text_field((payload if isinstance(payload, dict) else {}).get("name"))


def _safe_upload_name(upload: UploadFile | None, fallback: str) -> str:
    if upload is None:
        return fallback
    filename = upload.filename or fallback
    safe = "".join([c for c in filename if c.isalnum() or c in "._-"])
    return safe or fallback


def _persist_upload_to_outputs(upload: UploadFile, *, prefix: str, fallback_name: str, unique_id: str, timestamp: int) -> str:
    path = os.path.join("outputs", f"{prefix}_{timestamp}_{unique_id}_{_safe_upload_name(upload, fallback_name)}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as buffer:
        shutil.copyfileobj(upload.file, buffer)
    return path


def persist_internal_room_upload(file: UploadFile) -> str:
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time())
    return _persist_upload_to_outputs(file, prefix="raw", fallback_name="input.png", unique_id=unique_id, timestamp=timestamp)


def persist_internal_item_uploads(item_images: list[UploadFile]) -> list[str]:
    return prepare_internal_item_upload_paths(persist_internal_item_source_uploads(item_images))


def persist_internal_item_source_uploads(item_images: list[UploadFile]) -> list[str]:
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time())
    saved_paths: list[str] = []
    for idx, upload in enumerate(item_images, start=1):
        raw_path = _persist_upload_to_outputs(
            upload,
            prefix=f"cart_item_src_{idx}",
            fallback_name=f"cart_item_{idx}.png",
            unique_id=unique_id,
            timestamp=timestamp,
        )
        saved_paths.append(raw_path)
    return saved_paths


def prepare_internal_item_upload_paths(raw_paths: list[str], item_specs: list[dict] | None = None) -> list[str]:
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time())
    saved_paths: list[str] = []
    for idx, raw_path in enumerate(raw_paths, start=1):
        item_spec = item_specs[idx - 1] if item_specs and idx - 1 < len(item_specs) else None
        basename = os.path.basename(str(raw_path)) or f"cart_item_{idx}.png"
        safe_name = "".join([c for c in basename if c.isalnum() or c in "._-"]) or f"cart_item_{idx}.png"
        final_path = os.path.join("outputs", f"cart_item_{timestamp}_{unique_id}_{safe_name}")
        if is_curtain_item(item_spec):
            final_path = os.path.join("outputs", f"curtain_material_{timestamp}_{unique_id}_{idx}.png")
            prepared_path = prepare_material_swatch_image(raw_path, output_path=final_path, max_size=2048)
        else:
            prepared_path = prepare_direct_item_image(raw_path, output_path=final_path, max_size=1024)
        if prepared_path:
            try:
                os.remove(raw_path)
            except Exception:
                pass
            saved_paths.append(prepared_path)
            continue

        try:
            os.replace(raw_path, final_path)
            saved_paths.append(final_path)
        except Exception:
            saved_paths.append(raw_path)
    return saved_paths


def persist_internal_render_uploads(file: UploadFile, moodboard: UploadFile | None) -> tuple[str, str | None]:
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time())
    raw_path = _persist_upload_to_outputs(
        file,
        prefix="raw",
        fallback_name="input.png",
        unique_id=unique_id,
        timestamp=timestamp,
    )
    mood_path = None
    if moodboard is not None:
        mood_path = _persist_upload_to_outputs(
            moodboard,
            prefix="mb",
            fallback_name="moodboard.png",
            unique_id=unique_id,
            timestamp=timestamp,
        )

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
        "item_analysis_profile": DETAILED_ITEM_ANALYSIS_PROFILE,
        "simple_generation_mode": True,
    }


def build_internal_itemized_async_render_job_payload(
    *,
    raw_path: str,
    item_specs: list[dict],
    item_paths: list[str],
    room: str,
    style: str,
    variant: str,
    dimensions: str,
    placement: str,
    resolve_image_url: Callable[[str | None, str | None], str | None],
    build_s3_prefix: Callable[[str, str, str | None], str],
    build_item_target_key: Callable[..., str],
    publish_inputs: bool = True,
) -> dict:
    audience = "internal"
    validated_specs: list[dict] = []
    for payload_index, spec in enumerate(item_specs, start=1):
        upload_index = spec.get("upload_index")
        if isinstance(upload_index, bool) or not isinstance(upload_index, int):
            raise ValueError(f"Item {payload_index} has invalid upload_index")
        if upload_index < 0 or upload_index >= len(item_paths):
            raise ValueError(f"Item {payload_index} has invalid upload_index")

        label = _category_label(spec)
        if not label:
            name = _product_name(spec)
            if name:
                label = name
            else:
                raise ValueError(f"Item {payload_index} has invalid label")

        qty = spec.get("qty")
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            raise ValueError(f"Item {payload_index} has invalid qty")

        validated_specs.append(
            {
                "payload_index": payload_index,
                "upload_index": upload_index,
                "label": label,
                "qty": qty,
                "dims_mm": spec.get("dims_mm"),
                "category": spec.get("category"),
                "product_name": _product_name(spec) or None,
                "client_id": spec.get("client_id"),
            }
        )

    file_ref = (
        resolve_image_url(raw_path, build_s3_prefix(audience, "mainrendered", "user-photos"))
        if publish_inputs
        else None
    )

    moodboard_items = []
    item_prefix = build_s3_prefix(audience, "customize", "item-images") if publish_inputs else None
    for spec in validated_specs:
        item_path = item_paths[spec["upload_index"]]
        if not item_path:
            raise ValueError(f"Item {spec['payload_index']} has invalid upload_index")

        item_ref = resolve_image_url(item_path, item_prefix) if publish_inputs else None

        moodboard_items.append(
            {
                "label": spec["label"],
                "path": item_ref or item_path,
                "dims_mm": spec["dims_mm"],
                "qty": spec["qty"],
                "category": spec["category"],
                "product_name": spec.get("product_name"),
                "item_id": spec["client_id"],
                "payload_index": spec["payload_index"],
                "target_key": build_item_target_key(
                    "internal",
                    spec["payload_index"],
                    label=spec["label"],
                    category=spec["category"],
                    item_id=spec["client_id"],
                ),
            }
        )

    return {
        "file_path": file_ref or raw_path,
        "moodboard_items": moodboard_items,
        "room": room,
        "style": style,
        "variant": variant,
        "dimensions": dimensions,
        "placement": placement,
        "audience": audience,
        "item_analysis_profile": DETAILED_ITEM_ANALYSIS_PROFILE,
        "simple_generation_mode": True,
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
        "item_analysis_profile": DETAILED_ITEM_ANALYSIS_PROFILE,
        "simple_generation_mode": True if req.simple_generation_mode is None else bool(req.simple_generation_mode),
    }
    return {"render": payload}


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
        "item_analysis_profile": DETAILED_ITEM_ANALYSIS_PROFILE,
        "simple_generation_mode": True if req.simple_generation_mode is None else bool(req.simple_generation_mode),
    }
    job_payload = {
        "require_details": True,
        "render": payload,
        "extra": {
            "preset_id": req.preset_id,
            "resolved": resolved_surface,
            "video_enabled": False,
            "video_disabled_reason": "Video generation is disabled for preset renders",
            "detail_target_count": 6,
            "detail_target_policy": "preset_fixed_six_unique_targets",
        },
    }
    job_payload = attach_tracker_metadata(
        job_payload,
        extract_tracker_metadata(req, default_job_kind="preset"),
    )
    return job_payload, resolved_surface


def build_external_cart_job(
    req: CartRenderRequest,
    *,
    default_job_kind: str = "cart",
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

    item_refs = []
    for idx, it in enumerate(kept):
        img_url = it.get("image_url") or it.get("image")
        if not img_url:
            continue
        label = _category_label(it) or str(it.get("id") or "").strip() or _product_name(it) or "Item"
        product_name = _product_name(it)
        try:
            qty_val = int(it.get("qty") or 1)
        except Exception:
            qty_val = 1
        if qty_val < 1:
            qty_val = 1
        item_refs.append(
            {
                "label": label,
                "path": img_url,
                "dims_mm": it.get("dims_mm"),
                "options": it.get("options"),
                "qty": qty_val,
                "category": it.get("category"),
                "product_name": product_name or None,
                "item_id": it.get("id"),
                "payload_index": idx + 1,
                "worker_preprocess": "external_cart_item_v1",
                "target_key": build_item_target_key(
                    "cart",
                    idx + 1,
                    label=label,
                    category=it.get("category"),
                    item_id=it.get("id"),
                ),
                **_category_metadata(it),
            }
        )

    if not item_refs:
        raise ValueError("No valid item images after processing")

    placement_parts = []
    if req.style:
        placement_parts.append(f"STYLE: {req.style}")
    if req.placement:
        placement_parts.append(req.placement)
    ordinary_cart_items = [item for item in kept if not is_curtain_item(item)]
    if ordinary_cart_items:
        placement_parts.append(build_cart_summary(ordinary_cart_items))
    placement = "\n".join([p for p in placement_parts if p])

    payload = {
        "file_path": req.image_url,
        "moodboard_items": item_refs,
        "room": req.room or "",
        "style": "Customize",
        "variant": str(req.variant or "1"),
        "dimensions": req.dimensions or "",
        "placement": placement,
        "audience": "external",
        "item_analysis_profile": DETAILED_ITEM_ANALYSIS_PROFILE,
        "simple_generation_mode": True if req.simple_generation_mode is None else bool(req.simple_generation_mode),
    }
    job_payload = {
        "require_details": True,
        "render": payload,
        "extra": {"cart_kept": kept, "cart_dropped": dropped},
    }
    job_payload = attach_tracker_metadata(
        job_payload,
        extract_tracker_metadata(req, default_job_kind=default_job_kind),
    )
    return job_payload, kept, dropped


def _batch_variant_value(batch_req: CartSimpleBatchRequest, variant: object, field_name: str, default: str = ""):
    variant_value = getattr(variant, field_name, None)
    if variant_value not in (None, ""):
        return variant_value
    batch_value = getattr(batch_req, field_name, None)
    if batch_value not in (None, ""):
        return batch_value
    return default


def build_external_cart_batch_job(
    req: CartSimpleBatchRequest,
    *,
    cart_max_items: int,
    apply_cart_limits: Callable[[list[dict], int], tuple[list[dict], list[dict]]],
    build_cart_summary: Callable[[list[dict]], str],
    materialize_input: Callable[[str, str], str | None],
    normalize_item_image: Callable[[str, str, int], str | None],
    resolve_image_url: Callable[[str, str | None], str | None],
    build_s3_prefix: Callable[[str, str], str],
    build_item_target_key: Callable[..., str],
) -> tuple[dict, list[dict]]:
    if not req.variants:
        raise ValueError("variants are required")

    job_variants: list[dict] = []
    response_variants: list[dict] = []
    for index, variant in enumerate(req.variants, start=1):
        if not getattr(variant, "items", None):
            raise ValueError(f"Variant {index} items are required")
        variant_req = CartRenderRequest(
            image_url=req.image_url,
            items=variant.items,
            room=_batch_variant_value(req, variant, "room", ""),
            style=_batch_variant_value(req, variant, "style", None),
            variant=_batch_variant_value(req, variant, "variant", str(index)),
            dimensions=_batch_variant_value(req, variant, "dimensions", ""),
            placement=_batch_variant_value(req, variant, "placement", ""),
            simple_generation_mode=(
                variant.simple_generation_mode
                if variant.simple_generation_mode is not None
                else req.simple_generation_mode
            ),
            service_source=req.service_source,
            client_service=req.client_service,
            environment=req.environment,
            journey_id=req.journey_id,
            request_id=req.request_id,
            result_id=req.result_id,
            parent_job_id=req.parent_job_id,
            job_kind=req.job_kind,
        )
        job_payload, kept, dropped = build_external_cart_job(
            variant_req,
            cart_max_items=cart_max_items,
            apply_cart_limits=apply_cart_limits,
            build_cart_summary=build_cart_summary,
            materialize_input=materialize_input,
            normalize_item_image=normalize_item_image,
            resolve_image_url=resolve_image_url,
            build_s3_prefix=build_s3_prefix,
            build_item_target_key=build_item_target_key,
        )
        job_variants.append(
            {
                "variant_index": index,
                "render": job_payload["render"],
                "extra": job_payload.get("extra") or {},
            }
        )
        response_variants.append(
            {
                "variant_index": index,
                "cart_kept": kept,
                "cart_dropped": dropped,
            }
        )

    return (
        attach_tracker_metadata(
            {
            "audience": "external",
            "image_url": req.image_url,
            "variants": job_variants,
            },
            extract_tracker_metadata(req, default_job_kind="cart_simple_batch"),
        ),
        response_variants,
    )


def build_external_render_video_job(req: ExternalRenderVideoRequest) -> dict:
    render_job_id = (req.render_job_id or "").strip()
    if not render_job_id:
        raise ValueError("render_job_id is required")

    requested_clip_count = int(req.clip_count or 7)
    if requested_clip_count < 4 or requested_clip_count > 7:
        raise ValueError("clip_count must be between 4 and 7")
    clip_count = 7

    cfg_scale = float(req.cfg_scale or 0.5)
    if cfg_scale <= 0:
        raise ValueError("cfg_scale must be greater than 0")

    return {
        "render_job_id": render_job_id,
        "clip_count": clip_count,
        "cfg_scale": cfg_scale,
        "audience": "external",
    }


def build_detail_generation_job_payload(req: DetailRequest) -> dict:
    return {
        "image_url": req.image_url,
        "empty_room_url": req.empty_room_url,
        "moodboard_url": req.moodboard_url,
        "furniture_data": req.furniture_data,
        "room_dims_contract": req.room_dims_contract,
        "geometry_contract": req.geometry_contract,
        "scene_contract": req.scene_contract,
        "placement_plan": req.placement_plan,
        "audience": req.audience,
        "simple_generation_mode": True if req.simple_generation_mode is None else bool(req.simple_generation_mode),
        "require_details": bool(req.require_details),
    }


def build_regenerate_detail_job_payload(req: RegenerateDetailRequest) -> dict:
    return {
        "original_image_url": req.original_image_url,
        "empty_room_url": req.empty_room_url,
        "style_index": req.style_index,
        "target_key": req.target_key,
        "target_label": req.target_label,
        "target_box_2d": req.target_box_2d,
        "target_source_box_2d": req.target_source_box_2d,
        "style_index_mode": req.style_index_mode,
        "furniture_data": req.furniture_data,
        "moodboard_url": req.moodboard_url,
        "room_dims_contract": req.room_dims_contract,
        "geometry_contract": req.geometry_contract,
        "scene_contract": req.scene_contract,
        "placement_plan": req.placement_plan,
        "audience": req.audience,
    }


def build_upscale_job_payload(req: UpscaleRequest) -> dict:
    return {"image_url": req.image_url}


def build_finalize_download_job_payload(req: FinalizeRequest) -> dict:
    return {"image_url": req.image_url}


def build_empty_room_job_payload(req: FinalizeRequest) -> dict:
    return {"image_url": req.image_url, "audience": "internal"}
