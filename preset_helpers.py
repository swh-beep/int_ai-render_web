import json
import os


def load_preset_map(preset_map_path: str, cached_map: dict | None) -> dict:
    if cached_map is not None:
        return cached_map
    if not preset_map_path or not os.path.exists(preset_map_path):
        return {}
    try:
        with open(preset_map_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_preset_request(data: dict, preset_map: dict) -> dict:
    preset_room = None
    preset_style = None
    preset_variant = None
    preset_dims = ""
    preset_placement = ""

    preset_id = data.get("preset_id")
    if preset_id:
        preset = preset_map.get(preset_id)
        if not preset:
            raise ValueError("Unknown preset_id")
        preset_room = preset.get("room") or preset.get("room_type") or preset.get("room_name")
        preset_style = preset.get("style")
        preset_variant = preset.get("variant") or preset.get("variant_id") or preset.get("variant_index")
        preset_dims = preset.get("dimensions") or ""
        preset_placement = preset.get("placement") or ""

    room = preset_room or data.get("room")
    style = preset_style or data.get("style")
    variant = str(preset_variant or data.get("variant") or "1")
    if not room or not style:
        raise ValueError("room/style required or preset_id invalid")

    placement_parts = []
    if preset_placement:
        placement_parts.append(str(preset_placement))
    if data.get("placement"):
        placement_parts.append(data.get("placement"))

    return {
        "room": room,
        "style": style,
        "variant": variant,
        "dimensions": data.get("dimensions") or preset_dims or "",
        "placement": "\n".join([p for p in placement_parts if p]),
    }
