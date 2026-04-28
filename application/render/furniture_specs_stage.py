from typing import Callable

from application.render.two_pass_strategy_stage import (
    apply_two_pass_strategy,
    compute_strategy_priority,
    is_anchor_eligible,
    select_anchor_candidate,
)


def volume_proxy(dims: dict) -> int:
    try:
        width_mm = int(dims.get("width_mm") or 0)
        depth_mm = int(dims.get("depth_mm") or 0)
        height_mm = int(dims.get("height_mm") or 0)
        if width_mm and depth_mm and height_mm:
            return width_mm * depth_mm * height_mm
        if width_mm and depth_mm:
            return width_mm * depth_mm
        if width_mm:
            return width_mm
    except Exception:
        pass
    return 0


def item_box_area_proxy(box_2d) -> int:
    try:
        if not isinstance(box_2d, list) or len(box_2d) != 4:
            return 0
        ymin, xmin, ymax, xmax = box_2d
        height = max(0.0, float(ymax) - float(ymin))
        width = max(0.0, float(xmax) - float(xmin))
        return int(round(width * height))
    except Exception:
        return 0


def _extract_item_dims_for_ranking(
    item: dict,
    *,
    normalize_dims_dict: Callable[[dict], dict],
    dims_has_positive_values: Callable[[dict], bool],
    parse_object_dimensions_mm: Callable[[str], dict],
) -> dict:
    req_dims = normalize_dims_dict(item.get("requested_dims_mm") or item.get("dims_mm") or {}) if isinstance(item, dict) else {}
    if dims_has_positive_values(req_dims):
        return {
            "width_mm": req_dims.get("width_mm"),
            "depth_mm": req_dims.get("depth_mm"),
            "height_mm": req_dims.get("height_mm"),
            "radius_mm": req_dims.get("radius_mm"),
        }

    parsed = parse_object_dimensions_mm((item or {}).get("description", "") if isinstance(item, dict) else "")
    return {
        "width_mm": parsed.get("width_mm"),
        "depth_mm": parsed.get("depth_mm"),
        "height_mm": parsed.get("height_mm"),
        "radius_mm": parsed.get("radius_mm"),
    }


def _category_priority_score(item: dict, *, canonical_category: Callable[[str | None], str]) -> int:
    try:
        cat = (item or {}).get("category_canonical") or canonical_category((item or {}).get("category") or (item or {}).get("label"))
    except Exception:
        cat = ""

    table = {
        "sofa": 100,
        "bed": 90,
        "table": 80,
        "storage": 60,
        "light": 50,
        "chair": 40,
        "rug": 25,
        "tv": 20,
        "mirror": 15,
        "plant": 10,
        "decor": 5,
    }
    return int(table.get(cat or "", 0))


def attach_volume_ranks(
    analyzed_items: list,
    *,
    normalize_dims_dict: Callable[[dict], dict],
    dims_has_positive_values: Callable[[dict], bool],
    parse_object_dimensions_mm: Callable[[str], dict],
    canonical_category: Callable[[str | None], str],
) -> list:
    if not isinstance(analyzed_items, list):
        return analyzed_items

    has_explicit_category = any(
        isinstance(it, dict) and str((it or {}).get("category") or "").strip()
        for it in analyzed_items
    )

    pairs = []
    for idx, src in enumerate(analyzed_items):
        item = dict(src or {})
        dims = _extract_item_dims_for_ranking(
            item,
            normalize_dims_dict=normalize_dims_dict,
            dims_has_positive_values=dims_has_positive_values,
            parse_object_dimensions_mm=parse_object_dimensions_mm,
        )
        vp = volume_proxy(dims)
        basis = "dims_mm"

        if vp <= 0:
            area = item_box_area_proxy(item.get("box_2d"))
            if area > 0:
                vp = area
                basis = "box_area_2d"
            else:
                basis = "unknown"

        item["volume_proxy"] = int(vp or 0)
        item["volume_rank_basis"] = basis

        if not item.get("category_canonical"):
            item["category_canonical"] = canonical_category(item.get("category") or item.get("label"))
        item["category_score"] = _category_priority_score(item, canonical_category=canonical_category)

        if not item.get("dims_mm") and any([
            dims.get("width_mm"),
            dims.get("depth_mm"),
            dims.get("height_mm"),
            dims.get("radius_mm"),
        ]):
            item["dims_mm"] = {
                "width_mm": dims.get("width_mm"),
                "depth_mm": dims.get("depth_mm"),
                "height_mm": dims.get("height_mm"),
                "radius_mm": dims.get("radius_mm"),
            }

        pairs.append((idx, item))

    if has_explicit_category:
        ranked = sorted(
            pairs,
            key=lambda x: (
                -(int((x[1] or {}).get("category_score") or 0)),
                -(int((x[1] or {}).get("volume_proxy") or 0)),
                x[0],
            ),
        )
    else:
        ranked = sorted(
            pairs,
            key=lambda x: (-(int((x[1] or {}).get("volume_proxy") or 0)), x[0]),
        )

    for rank, (_, item) in enumerate(ranked, start=1):
        item["volume_rank"] = rank

    return [item for _, item in sorted(pairs, key=lambda x: x[0])]


def volume_ranking_snapshot(analyzed_items: list) -> list:
    rows = []
    for it in analyzed_items or []:
        if not isinstance(it, dict):
            continue
        rows.append(
            {
                "rank": int(it.get("volume_rank") or 0),
                "label": it.get("label"),
                "target_key": it.get("target_key"),
                "category": it.get("category"),
                "category_canonical": it.get("category_canonical"),
                "category_score": int(it.get("category_score") or 0),
                "volume_proxy": int(it.get("volume_proxy") or 0),
                "volume_rank_basis": it.get("volume_rank_basis"),
                "qty": int(it.get("qty") or 1),
                "box_source": it.get("box_source"),
            }
        )
    rows.sort(key=lambda x: (x.get("rank") or 10**9, -(x.get("volume_proxy") or 0), str(x.get("label") or "")))
    return rows


def build_furniture_specs_json(
    analyzed_items: list,
    *,
    normalize_dims_dict: Callable[[dict], dict],
    dims_has_positive_values: Callable[[dict], bool],
    parse_object_dimensions_mm: Callable[[str], dict],
    is_rug_like: Callable[[str], bool],
    canonical_category: Callable[[str | None], str],
) -> dict:
    items = []
    priority_keywords = {
        "sofa": 100,
        "couch": 100,
        "sectional": 100,
        "bed": 90,
        "table": 80,
        "desk": 80,
        "dining": 80,
        "console": 60,
        "shelf": 60,
        "cabinet": 60,
        "storage": 60,
        "lamp": 50,
        "light": 50,
        "chair": 40,
        "armchair": 40,
        "tv": 10,
        "plant": 5,
    }

    strategy_items, two_pass_summary = apply_two_pass_strategy(analyzed_items or [])
    for idx, it in enumerate(strategy_items or []):
        label = it.get("label", "") or ""
        desc = it.get("description", "") or ""
        box = it.get("box_2d")
        req_dims = normalize_dims_dict(it.get("requested_dims_mm") or it.get("dims_mm") or {})
        req_dims_authoritative = dims_has_positive_values(req_dims)

        if req_dims_authoritative:
            dims = {
                "width_mm": req_dims.get("width_mm"),
                "depth_mm": req_dims.get("depth_mm"),
                "height_mm": req_dims.get("height_mm"),
                "radius_mm": req_dims.get("radius_mm"),
                "raw": {"source": "requested_dims_mm"},
            }
        else:
            dims = parse_object_dimensions_mm(desc)

        is_rug = is_rug_like(label)

        width_mm = dims.get("width_mm") or 0
        depth_mm = dims.get("depth_mm") or 0
        height_mm = dims.get("height_mm") or 1000
        vp = (width_mm * depth_mm * height_mm) if (width_mm or depth_mm) else 0
        if is_rug:
            vp = 0

        cat_score = 0
        canonical = canonical_category(it.get("category") or it.get("category_canonical") or label)
        cat_score_map = {
            "sofa": 100,
            "bed": 90,
            "table": 80,
            "storage": 60,
            "light": 50,
            "chair": 40,
            "rug": 25,
            "tv": 20,
            "mirror": 15,
            "plant": 10,
            "decor": 5,
        }
        if canonical:
            cat_score = int(cat_score_map.get(canonical, 0))

        norm_label = label.lower()
        for key, score in priority_keywords.items():
            if key in norm_label:
                cat_score = max(cat_score, score)

        items.append(
            {
                "index": idx,
                "label": label,
                "target_key": it.get("target_key"),
                "source_index": it.get("source_index"),
                "category": it.get("category"),
                "category_canonical": canonical,
                "is_rug": is_rug,
                "category_score": cat_score,
                "qty": int(it.get("qty") or 1),
                "options": it.get("options"),
                "requested_dims_mm": req_dims if req_dims_authoritative else None,
                "dims_mm": {
                    "width_mm": dims.get("width_mm"),
                    "depth_mm": dims.get("depth_mm"),
                    "height_mm": dims.get("height_mm"),
                    "radius_mm": dims.get("radius_mm"),
                },
                "volume_proxy": vp,
                "box_2d": box,
                "description": desc,
                "crop_path": (it.get("crop_path") if isinstance(it, dict) else None),
                "identity_profile": ((it.get("identity_profile") if isinstance(it, dict) else None) or None),
                "layout_envelope": (
                    (it.get("layout_envelope") if isinstance(it, dict) else None)
                    or ((it.get("identity_profile") or {}).get("layout_envelope") if isinstance(it, dict) else None)
                ),
                "two_pass_strategy": ((it.get("two_pass_strategy") if isinstance(it, dict) else None) or None),
                "anchor_eligible": bool((it.get("anchor_eligible") if isinstance(it, dict) else False)),
                "pass_role": (it.get("pass_role") if isinstance(it, dict) else None),
                "strategy_priority": int((it.get("strategy_priority") if isinstance(it, dict) else 0) or 0),
            }
        )

    primary = None
    candidates = [x for x in items if not x["is_rug"]]
    if candidates:
        candidates_sorted = sorted(candidates, key=lambda x: (x["category_score"], x["volume_proxy"], -x["index"]), reverse=True)
        primary = candidates_sorted[0]

    def _scale_sort_key(row: dict):
        dm = row.get("dims_mm") or {}
        try:
            width_mm = int(dm.get("width_mm") or 0)
        except Exception:
            width_mm = 0
        try:
            depth_mm = int(dm.get("depth_mm") or 0)
        except Exception:
            depth_mm = 0
        try:
            height_mm = int(dm.get("height_mm") or 0)
        except Exception:
            height_mm = 0
        try:
            radius_mm = int(dm.get("radius_mm") or 0)
        except Exception:
            radius_mm = 0

        has_dims = 1 if (width_mm > 0 or depth_mm > 0 or height_mm > 0 or radius_mm > 0) else 0
        try:
            vol = int(row.get("volume_proxy") or 0)
        except Exception:
            vol = 0
        try:
            idx = int(row.get("index") or 0)
        except Exception:
            idx = 0
        return (has_dims, vol, width_mm, depth_mm, height_mm, radius_mm, -idx)

    primary_scale = select_anchor_candidate(items, primary_item=primary)
    if not primary_scale:
        scale_candidates = [
            x
            for x in candidates
            if dims_has_positive_values(x.get("dims_mm") or {})
            and bool(((x.get("two_pass_strategy") or {}).get("fallback_anchor_candidate")))
        ]
        if scale_candidates:
            scale_sorted = sorted(scale_candidates, key=_scale_sort_key, reverse=True)
            primary_scale = scale_sorted[0]
        else:
            primary_scale = primary

    max_width_mm = 0
    for x in candidates:
        width_mm = x.get("dims_mm", {}).get("width_mm") or 0
        try:
            max_width_mm = max(max_width_mm, int(width_mm))
        except Exception:
            pass

    try:
        if primary_scale and max_width_mm and not (primary_scale.get("dims_mm", {}) or {}).get("width_mm"):
            primary_scale.setdefault("dims_mm", {})["width_mm"] = int(max_width_mm)
    except Exception:
        pass

    try:
        if primary and max_width_mm and not (primary.get("dims_mm", {}) or {}).get("width_mm"):
            primary.setdefault("dims_mm", {})["width_mm"] = int(max_width_mm)
    except Exception:
        pass

    hierarchy = [x.get("label", "") for x in (analyzed_items or []) if not is_rug_like(x.get("label", ""))]
    scale_hierarchy = [
        x.get("label", "")
        for x in sorted(
            candidates,
            key=lambda row: (
                int(is_anchor_eligible(row)),
                int(row.get("strategy_priority") or compute_strategy_priority(row)),
                *_scale_sort_key(row),
            ),
            reverse=True,
        )
        if x.get("label")
    ]
    if not scale_hierarchy:
        scale_hierarchy = list(hierarchy)

    return {
        "items": items,
        "primary": primary,
        "primary_scale": primary_scale,
        "max_width_mm": max_width_mm,
        "size_hierarchy": hierarchy,
        "size_hierarchy_scale": scale_hierarchy,
        "two_pass_strategy": two_pass_summary,
    }
