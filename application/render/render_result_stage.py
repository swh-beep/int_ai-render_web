def extract_render_result_url(render_result: dict) -> str | None:
    result_url = render_result.get("result_url")
    if result_url:
        return result_url
    urls = render_result.get("result_urls") or []
    return urls[0] if urls else None


def build_detail_payload(render_result: dict, *, audience: str) -> dict:
    return {
        "image_url": render_result.get("result_url") or extract_render_result_url(render_result),
        "moodboard_url": render_result.get("moodboard_url"),
        "furniture_data": render_result.get("furniture_data"),
        "audience": audience,
    }
