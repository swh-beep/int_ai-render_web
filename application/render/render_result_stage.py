def extract_render_result_url(render_result: dict) -> str | None:
    result_url = render_result.get("result_url")
    if result_url:
        return result_url
    urls = render_result.get("result_urls") or []
    return urls[0] if urls else None


def build_detail_payload(render_result: dict, *, audience: str) -> dict:
    payload = {
        "image_url": render_result.get("result_url") or extract_render_result_url(render_result),
        "empty_room_url": render_result.get("empty_room_url"),
        "moodboard_url": render_result.get("moodboard_url"),
        "furniture_data": render_result.get("furniture_data"),
        "room_dims_contract": render_result.get("room_dims_contract"),
        "geometry_contract": render_result.get("geometry_contract"),
        "scene_contract": render_result.get("scene_contract"),
        "placement_plan": render_result.get("placement_plan"),
        "audience": audience,
    }
    artifact_manifest = render_result.get("artifact_manifest") if isinstance(render_result, dict) else None
    if isinstance(artifact_manifest, dict) and artifact_manifest.get("root_prefix"):
        payload["artifact_root_prefix"] = artifact_manifest.get("root_prefix")
    return payload
