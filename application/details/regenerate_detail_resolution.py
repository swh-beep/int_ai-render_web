from typing import Callable


def resolve_regeneration_style(
    *,
    dynamic_styles: list,
    raw_style_index: int,
    req_target_key: str,
    req_target_label: str,
    style_index_mode: str,
    normalize_label_for_match: Callable[[str], str],
) -> tuple[dict | None, str | None, int | None]:
    detail_style_refs = [
        (index, style)
        for index, style in enumerate(dynamic_styles)
        if str((style or {}).get("name") or "").startswith("Detail:")
    ]
    detail_styles = [style for _, style in detail_style_refs]

    def _to_zero_based(value: int) -> int:
        return value - 1 if value >= 1 else value

    def _resolve_detail_by_index(raw_index: int) -> tuple[dict | None, str | None, int | None]:
        if raw_index < 0:
            raw_index = 0
        if 0 <= raw_index < len(dynamic_styles):
            candidate = dynamic_styles[raw_index]
            if str((candidate or {}).get("name") or "").startswith("Detail:"):
                return candidate, "style_index_detail_from_overall", raw_index + 1
        if detail_styles:
            detail_index = raw_index
            if detail_index >= len(detail_styles):
                detail_index = len(detail_styles) - 1
            if detail_index < 0:
                detail_index = 0
            overall_index, style = detail_style_refs[detail_index]
            return style, "style_index_detail", overall_index + 1
        return None, None, None

    style = None
    resolved_by = None
    resolved_style_index = None

    if req_target_key:
        for index, item_style in enumerate(dynamic_styles):
            if str((item_style or {}).get("target_key") or "").strip() == req_target_key:
                style = item_style
                resolved_by = "target_key"
                resolved_style_index = index + 1
                break

    if style is None and req_target_label:
        normalized_target = normalize_label_for_match(req_target_label)
        for index, item_style in enumerate(dynamic_styles):
            name = str((item_style or {}).get("name") or "")
            if not name.startswith("Detail:"):
                continue
            style_label = str((item_style or {}).get("target_label") or "").strip()
            if not style_label:
                style_label = name.split("Detail:", 1)[1].strip()
            normalized_style_label = normalize_label_for_match(style_label)
            if normalized_target and normalized_style_label and normalized_target == normalized_style_label:
                style = item_style
                resolved_by = "target_label"
                resolved_style_index = index + 1
                break

        if style is None:
            for index, item_style in enumerate(dynamic_styles):
                name = str((item_style or {}).get("name") or "")
                if not name.startswith("Detail:"):
                    continue
                style_label = str((item_style or {}).get("target_label") or "").strip()
                if not style_label:
                    style_label = name.split("Detail:", 1)[1].strip()
                normalized_style_label = normalize_label_for_match(style_label)
                if normalized_target and normalized_style_label and (
                    normalized_target in normalized_style_label or normalized_style_label in normalized_target
                ):
                    style = item_style
                    resolved_by = "target_label_partial"
                    resolved_style_index = index + 1
                    break

    if style is not None:
        return style, resolved_by, resolved_style_index

    index = _to_zero_based(raw_style_index)
    if style_index_mode == "overall":
        if index < 0:
            index = 0
        elif index >= len(dynamic_styles):
            index = len(dynamic_styles) - 1
        return dynamic_styles[index], "style_index_overall", index + 1

    if style_index_mode == "detail":
        style, resolved_by, resolved_style_index = _resolve_detail_by_index(index)
        if style is not None:
            return style, resolved_by, resolved_style_index
        if index < 0:
            index = 0
        elif index >= len(dynamic_styles):
            index = len(dynamic_styles) - 1
        return dynamic_styles[index], "style_index_overall_fallback", index + 1

    style, resolved_by, resolved_style_index = _resolve_detail_by_index(index)
    if style is not None:
        return style, resolved_by if resolved_by != "style_index_detail" else "style_index_detail_auto", resolved_style_index

    if index < 0:
        index = 0
    elif index >= len(dynamic_styles):
        index = len(dynamic_styles) - 1
    return dynamic_styles[index], "style_index_overall_auto", index + 1


def attach_regenerated_target_metadata(
    output: dict,
    *,
    style: dict,
    analyzed_items: list,
    normalize_label_for_match: Callable[[str], str],
) -> dict:
    style_name = str(output.get("style_name") or style.get("name") or "")
    if not style_name.startswith("Detail:"):
        return output

    style_target_key = str(style.get("target_key") or "").strip()
    style_target_label = str(style.get("target_label") or "").strip()
    analyzed_key_map = {
        str(item.get("target_key") or "").strip(): item
        for item in (analyzed_items or [])
        if isinstance(item, dict) and str(item.get("target_key") or "").strip()
    }

    target_label = style_target_label or style_name.split("Detail:", 1)[1].strip()
    if target_label:
        output["target_label"] = target_label
    if style_target_key:
        output["target_key"] = style_target_key

    hit = analyzed_key_map.get(style_target_key) if style_target_key else None
    if not isinstance(hit, dict) and target_label:
        normalized_target = normalize_label_for_match(target_label)
        for item in analyzed_items:
            if not isinstance(item, dict):
                continue
            if normalize_label_for_match(item.get("label") or "") == normalized_target:
                hit = item
                break

    if isinstance(hit, dict):
        hit_key = str(hit.get("target_key") or "").strip()
        if hit_key and not output.get("target_key"):
            output["target_key"] = hit_key
        output["target_box_2d"] = hit.get("box_2d")
        output["target_source_box_2d"] = hit.get("source_box_2d")
        output["target_box_source"] = hit.get("box_source")
        output["target_volume_rank"] = hit.get("volume_rank")
        output["target_volume_proxy"] = hit.get("volume_proxy")

    output["resolved_target_key"] = output.get("target_key")
    output["resolved_target_label"] = output.get("target_label")
    return output
