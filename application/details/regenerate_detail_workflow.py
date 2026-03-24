import os
import uuid
from typing import Callable, Optional

from application.details.regenerate_detail_resolution import (
    attach_regenerated_target_metadata,
    resolve_regeneration_style,
)


def run_regenerate_single_detail_job(
    payload: dict,
    *,
    normalize_audience: Callable[[Optional[str]], str],
    build_s3_prefix: Callable[[str, str, str | None], str],
    materialize_input: Callable[[str | None, str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    attach_volume_ranks: Callable[[list], list],
    construct_dynamic_styles: Callable[[list], list],
    normalize_label_for_match: Callable[[str], str],
    generate_detail_view: Callable[[str, dict, str, int, list | None], dict | str | None],
    volume_ranking_snapshot: Callable[[list], list],
) -> dict:
    try:
        original_image_url = payload.get("original_image_url")
        raw_style_index = int(payload.get("style_index") or 1)
        req_target_key = str(payload.get("target_key") or "").strip()
        req_target_label = str(payload.get("target_label") or "").strip()
        style_index_mode = str(payload.get("style_index_mode") or "auto").strip().lower()
        if style_index_mode not in {"auto", "detail", "overall"}:
            style_index_mode = "auto"

        furniture_data = payload.get("furniture_data")
        audience = payload.get("audience")

        aud = normalize_audience(audience)
        prefix_detail_user = build_s3_prefix(aud, "detailrendered", "user-photos")
        prefix_detail_rendered = build_s3_prefix(aud, "detailrendered", "rendered")

        local_path = materialize_input(original_image_url, "detail_src")
        if not local_path or not os.path.exists(local_path):
            return {"error": "Original image not found"}
        resolve_image_url(local_path, s3_prefix_override=prefix_detail_user)

        if furniture_data and len(furniture_data) > 0:
            print(">> [Single Retry] Using cached furniture data!", flush=True)
            analyzed_items = furniture_data
        else:
            analyzed_items = [{"label": "Main Furniture", "description": "High quality furniture matching the room style."}]

        try:
            analyzed_items = attach_volume_ranks(analyzed_items)
        except Exception:
            pass

        dynamic_styles = construct_dynamic_styles(analyzed_items)
        if not dynamic_styles:
            return {"error": "No styles available"}

        style, resolved_by, resolved_style_index = resolve_regeneration_style(
            dynamic_styles=dynamic_styles,
            raw_style_index=raw_style_index,
            req_target_key=req_target_key,
            req_target_label=req_target_label,
            style_index_mode=style_index_mode,
            normalize_label_for_match=normalize_label_for_match,
        )
        if style is None:
            return {"error": "No matching style for regeneration"}

        unique_id = uuid.uuid4().hex[:6]
        result = generate_detail_view(local_path, style, unique_id, int(resolved_style_index or 1), analyzed_items)
        if not result:
            return {"error": "Generation failed"}

        path = result.get("path") if isinstance(result, dict) else result
        url = resolve_image_url(path, s3_prefix_override=prefix_detail_rendered) if path else None
        if not url:
            return {"error": "Generation failed"}

        output = {
            "url": url,
            "message": "Success",
            "volume_ranking": volume_ranking_snapshot(analyzed_items),
            "resolved_by": resolved_by,
            "resolved_style_index": int(resolved_style_index or 1),
            "requested_style_index": raw_style_index,
            "requested_target_key": req_target_key or None,
            "requested_target_label": req_target_label or None,
        }
        if isinstance(result, dict):
            output["style_name"] = result.get("style_name") or style.get("name")
            output["cutout_ref_count"] = int(result.get("cutout_ref_count") or 0)
            labels = list(result.get("cutout_ref_labels") or [])
            if labels:
                output["cutout_ref_labels"] = labels

        return attach_regenerated_target_metadata(
            output,
            style=style,
            analyzed_items=analyzed_items,
            normalize_label_for_match=normalize_label_for_match,
        )
    except Exception as exc:
        return {"error": str(exc)}
