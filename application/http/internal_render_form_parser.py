from __future__ import annotations

import json
from typing import Any

from fastapi import UploadFile


def _require_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null")
    stripped = value.strip()
    return stripped or None


def _require_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _row_prefix(row_index: int) -> str:
    return f"Item {row_index}"


def _require_row_non_empty_str(value: Any, *, row_index: int, field_name: str, missing_message: str) -> str:
    try:
        return _require_non_empty_str(value, field_name)
    except ValueError:
        raise ValueError(f"{_row_prefix(row_index)} {missing_message}") from None


def _require_row_positive_int(value: Any, *, row_index: int, field_name: str, invalid_message: str) -> int:
    try:
        return _require_positive_int(value, field_name)
    except ValueError:
        raise ValueError(f"{_row_prefix(row_index)} {invalid_message}") from None


def _parse_dims_mm(dims_mm: Any, *, row_index: int) -> dict[str, int]:
    if not isinstance(dims_mm, dict):
        raise ValueError(f"{_row_prefix(row_index)} is missing required dims: width_mm, depth_mm, height_mm")

    missing = [key for key in ("width_mm", "depth_mm", "height_mm") if key not in dims_mm]
    if missing:
        raise ValueError(f"{_row_prefix(row_index)} is missing required dims: {', '.join(missing)}")

    return {
        "width_mm": _require_row_positive_int(
            dims_mm.get("width_mm"),
            row_index=row_index,
            field_name="dims_mm.width_mm",
            invalid_message="has invalid dims_mm.width_mm",
        ),
        "depth_mm": _require_row_positive_int(
            dims_mm.get("depth_mm"),
            row_index=row_index,
            field_name="dims_mm.depth_mm",
            invalid_message="has invalid dims_mm.depth_mm",
        ),
        "height_mm": _require_row_positive_int(
            dims_mm.get("height_mm"),
            row_index=row_index,
            field_name="dims_mm.height_mm",
            invalid_message="has invalid dims_mm.height_mm",
        ),
    }


def parse_internal_render_items_form(items_json: str, item_images: list[UploadFile]) -> list[dict[str, Any]]:
    if items_json is None:
        raw_items = []
    elif isinstance(items_json, str) and items_json.strip() == "":
        raw_items = []
    elif not isinstance(items_json, str):
        raise ValueError("items_json must be valid JSON")
    else:
        try:
            raw_items = json.loads(items_json)
        except json.JSONDecodeError as exc:
            raise ValueError("items_json must be valid JSON") from exc

    if not isinstance(raw_items, list):
        raise ValueError("items_json must be a JSON array")
    if not raw_items:
        raise ValueError("items_json must contain at least one item")
    if len(item_images) != len(raw_items):
        raise ValueError("item_images count must match items_json count")

    parsed_items: list[dict[str, Any]] = []
    for upload_index, raw_item in enumerate(raw_items):
        row_index = upload_index + 1
        if not isinstance(raw_item, dict):
            raise ValueError(f"Item {row_index} must be an object")

        client_id_value = raw_item.get("client_id")
        if client_id_value is None or (not isinstance(client_id_value, str) and not client_id_value):
            client_id = f"item-{row_index}"
        elif isinstance(client_id_value, str):
            stripped_client_id = client_id_value.strip()
            client_id = stripped_client_id if stripped_client_id else f"item-{row_index}"
        else:
            client_id = str(client_id_value)
        category = _require_row_non_empty_str(
            raw_item.get("category"),
            row_index=row_index,
            field_name="category",
            missing_message="is missing category",
        )
        qty = _require_row_positive_int(
            raw_item.get("qty"),
            row_index=row_index,
            field_name="qty",
            invalid_message="has invalid qty",
        )

        dims_mm = _parse_dims_mm(raw_item.get("dims_mm"), row_index=row_index)

        parsed_items.append(
            {
                "client_id": client_id,
                "name": _require_optional_str(raw_item.get("name"), "name"),
                "category": category,
                "qty": qty,
                "dims_mm": dims_mm,
                "upload_index": upload_index,
            }
        )

    return parsed_items


def parse_internal_render_form_items(items_json: str, item_images: list[UploadFile]) -> list[dict[str, Any]]:
    return parse_internal_render_items_form(items_json, item_images)
