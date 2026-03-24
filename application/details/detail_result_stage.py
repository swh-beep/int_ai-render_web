import os
from typing import Callable


def _build_furniture_boxes(analyzed_items: list) -> tuple[list, dict[str, dict], dict[str, dict]]:
    analyzed_map = {}
    analyzed_key_map = {}
    furniture_boxes = []

    for item in analyzed_items or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        normalized = str(item.get("_normalized_label") or "").strip()
        target_key = str(item.get("target_key") or "").strip()
        if target_key and target_key not in analyzed_key_map:
            analyzed_key_map[target_key] = item
        if normalized and normalized not in analyzed_map:
            analyzed_map[normalized] = item
        furniture_boxes.append(
            {
                "label": label or None,
                "target_key": target_key or None,
                "source_index": item.get("source_index"),
                "category": item.get("category"),
                "category_canonical": item.get("category_canonical"),
                "box_2d": item.get("box_2d"),
                "source_box_2d": item.get("source_box_2d"),
                "box_source": item.get("box_source"),
                "crop_path": item.get("crop_path"),
                "volume_rank": item.get("volume_rank"),
                "volume_proxy": item.get("volume_proxy"),
                "volume_rank_basis": item.get("volume_rank_basis"),
                "category_score": item.get("category_score"),
            }
        )

    return furniture_boxes, analyzed_map, analyzed_key_map


def _build_used_cutout_references(
    analyzed_items: list,
    *,
    materialize_input: Callable[[str | None, str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    prefix_detail_user: str,
) -> list:
    used_cutout_references = []
    temp_cutout_meta_paths = []
    try:
        for item in analyzed_items or []:
            if not isinstance(item, dict):
                continue
            crop_path = item.get("crop_path")
            if not crop_path:
                continue
            item_meta = {
                "label": item.get("label"),
                "target_key": item.get("target_key"),
                "source_index": item.get("source_index"),
                "category": item.get("category"),
                "category_canonical": item.get("category_canonical"),
                "crop_path": crop_path,
                "box_2d": item.get("box_2d"),
                "source_box_2d": item.get("source_box_2d"),
                "box_source": item.get("box_source"),
                "volume_rank": item.get("volume_rank"),
                "volume_proxy": item.get("volume_proxy"),
                "volume_rank_basis": item.get("volume_rank_basis"),
            }
            try:
                local_cp = materialize_input(crop_path, f"detail_meta_cutout_{len(used_cutout_references) + 1}") if isinstance(crop_path, str) else None
                if local_cp and os.path.exists(local_cp):
                    if local_cp != crop_path:
                        temp_cutout_meta_paths.append(local_cp)
                    crop_url = resolve_image_url(local_cp, prefix_detail_user)
                    if crop_url:
                        item_meta["crop_url"] = crop_url
            except Exception:
                pass
            used_cutout_references.append(item_meta)
            if len(used_cutout_references) >= 12:
                break
    finally:
        for temp_path in temp_cutout_meta_paths:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass

    return used_cutout_references


def _build_detail_entries(
    generated_paths: list,
    *,
    analyzed_map: dict[str, dict],
    analyzed_key_map: dict[str, dict],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    prefix_detail_rendered: str,
    normalize_label_for_match: Callable[[str], str],
) -> list:
    details = []

    for item in generated_paths:
        path = item.get("path")
        if not path:
            continue
        url = resolve_image_url(path, prefix_detail_rendered)
        if not url:
            continue

        detail_obj = {
            "index": item["index"] + 1,
            "url": url,
            "style_name": item.get("style_name"),
            "cutout_ref_count": int(item.get("cutout_ref_count") or 0),
        }
        labels = item.get("cutout_ref_labels") or []
        if labels:
            detail_obj["cutout_ref_labels"] = labels

        style_name = str(item.get("style_name") or "")
        style_target_key = str(item.get("style_target_key") or "").strip()
        style_target_label = str(item.get("style_target_label") or "").strip()
        if style_name.startswith("Detail:"):
            target_label = style_target_label or style_name.split("Detail:", 1)[1].strip()
            if target_label:
                detail_obj["target_label"] = target_label
            if style_target_key:
                detail_obj["target_key"] = style_target_key

            hit = analyzed_key_map.get(style_target_key) if style_target_key else None
            if not isinstance(hit, dict) and target_label:
                hit = analyzed_map.get(normalize_label_for_match(target_label))

            if isinstance(hit, dict):
                hit_key = str(hit.get("target_key") or "").strip()
                if hit_key and not detail_obj.get("target_key"):
                    detail_obj["target_key"] = hit_key
                detail_obj["target_box_2d"] = hit.get("box_2d")
                detail_obj["target_source_box_2d"] = hit.get("source_box_2d")
                detail_obj["target_box_source"] = hit.get("box_source")
                detail_obj["target_volume_rank"] = hit.get("volume_rank")
                detail_obj["target_volume_proxy"] = hit.get("volume_proxy")

        details.append(detail_obj)

    return details


def build_detail_generation_output(
    *,
    analyzed_items: list,
    generated_paths: list,
    materialize_input: Callable[[str | None, str], str | None],
    resolve_image_url: Callable[[str | None, str | None], str | None],
    prefix_detail_user: str,
    prefix_detail_rendered: str,
    normalize_label_for_match: Callable[[str], str],
    volume_ranking_snapshot: Callable[[list], list],
) -> dict:
    normalized_items = []
    for item in analyzed_items or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        row["_normalized_label"] = normalize_label_for_match(str(item.get("label") or ""))
        normalized_items.append(row)

    furniture_boxes, analyzed_map, analyzed_key_map = _build_furniture_boxes(normalized_items)
    used_cutout_references = _build_used_cutout_references(
        normalized_items,
        materialize_input=materialize_input,
        resolve_image_url=resolve_image_url,
        prefix_detail_user=prefix_detail_user,
    )
    details = _build_detail_entries(
        generated_paths,
        analyzed_map=analyzed_map,
        analyzed_key_map=analyzed_key_map,
        resolve_image_url=resolve_image_url,
        prefix_detail_rendered=prefix_detail_rendered,
        normalize_label_for_match=normalize_label_for_match,
    )

    return {
        "details": details,
        "furniture_boxes": furniture_boxes,
        "used_cutout_references": used_cutout_references,
        "volume_ranking": volume_ranking_snapshot(normalized_items),
        "message": "Detail views generated successfully",
    }
