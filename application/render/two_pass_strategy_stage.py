from __future__ import annotations

from typing import Any

from application.render.postprocess_support import normalize_label_for_match, resolve_item_family


_PRIMARY_ANCHOR_FAMILIES = {
    "sofa": 100,
    "lounge_sofa": 98,
    "bed": 96,
    "storage": 90,
    "table": 82,
    "desk": 80,
}
_SECONDARY_ANCHOR_FAMILIES = {
    "lounge_chair": 65,
    "chair": 60,
    "stool": 45,
}
_EXCLUDED_ANCHOR_FAMILIES = {"rug", "mirror", "decor", "floor_lamp", "table_lamp"}
_SURFACE_PLACED_FAMILIES = {"table_lamp", "decor"}


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _resolve_family(item: dict) -> str:
    return resolve_item_family(item)


def _resolve_dims(item: dict) -> dict[str, int]:
    dims = (
        item.get("dims_mm")
        or item.get("requested_dims_mm")
        or ((item.get("product_identity") or {}).get("dims_mm") if isinstance(item.get("product_identity"), dict) else None)
        or {}
    )
    return {
        "width_mm": _coerce_positive_int(dims.get("width_mm")) or 0,
        "depth_mm": _coerce_positive_int(dims.get("depth_mm")) or 0,
        "height_mm": _coerce_positive_int(dims.get("height_mm")) or 0,
        "radius_mm": _coerce_positive_int(dims.get("radius_mm")) or 0,
    }


def _dims_complete(item: dict) -> bool:
    dims = _resolve_dims(item)
    return dims["width_mm"] > 0 and dims["depth_mm"] > 0 and dims["height_mm"] > 0


def _structural_archetype(item: dict) -> str:
    strategy = (item.get("archetype_strategy") or {}) if isinstance(item, dict) else {}
    return str(strategy.get("structural_archetype") or "").strip().lower()


def _placement_family(item: dict) -> str:
    family = _resolve_family(item)
    placement_contract = (item.get("placement_contract") or {}) if isinstance(item, dict) else {}
    if placement_contract.get("placement_family"):
        placement_family = str(placement_contract.get("placement_family") or "").strip().lower()
        if not (family == "storage" and placement_family in {"surface_placed", "small_free_object"}):
            return placement_family
    envelope = (item.get("layout_envelope") or {}) if isinstance(item, dict) else {}
    if envelope.get("placement_family"):
        placement_family = str(envelope.get("placement_family") or "").strip().lower()
        if not (family == "storage" and placement_family in {"surface_placed", "small_free_object"}):
            return placement_family

    if family == "mirror":
        return "wall_attached"
    if family == "rug":
        return "rug"
    if family in _SURFACE_PLACED_FAMILIES:
        return "surface_placed"
    if family in {"floor_lamp", "table_lamp", "stool"}:
        return "small_free_object"
    return "floor_placed"


def _room_width_ratio(item: dict) -> float:
    for source in (
        item.get("layout_envelope"),
        item.get("placement_contract", {}).get("room_ratio_targets") if isinstance(item.get("placement_contract"), dict) else None,
    ):
        if isinstance(source, dict):
            value = source.get("room_width_ratio")
            try:
                parsed = float(value)
            except Exception:
                parsed = 0.0
            if parsed > 0:
                return parsed
    return 0.0


def _footprint(item: dict) -> int:
    dims = _resolve_dims(item)
    return dims["width_mm"] * dims["depth_mm"]


def _max_dim(item: dict) -> int:
    dims = _resolve_dims(item)
    return max(dims["width_mm"], dims["depth_mm"], dims["height_mm"], dims["radius_mm"])


def _is_tiny_absolute_object(item: dict) -> bool:
    archetype = _structural_archetype(item)
    if archetype == "tiny_absolute_scale_object":
        return True
    return _max_dim(item) > 0 and _max_dim(item) <= 350


def is_anchor_eligible(item: dict) -> bool:
    if not isinstance(item, dict) or not _dims_complete(item):
        return False

    family = _resolve_family(item)
    placement_family = _placement_family(item)
    if family in _EXCLUDED_ANCHOR_FAMILIES:
        return False
    if placement_family in {"wall_attached", "surface_placed", "small_free_object"}:
        return False
    if _is_tiny_absolute_object(item):
        return False

    dims = _resolve_dims(item)
    width_mm = dims["width_mm"]
    depth_mm = dims["depth_mm"]
    footprint = width_mm * depth_mm
    room_width_ratio = _room_width_ratio(item)

    if family in {"sofa", "lounge_sofa"}:
        return width_mm >= 1400 or room_width_ratio >= 0.28
    if family == "bed":
        return width_mm >= 1200 or room_width_ratio >= 0.24
    if family == "storage":
        return width_mm >= 1000 or room_width_ratio >= 0.22
    if family in {"table", "desk"}:
        return (width_mm >= 850 and depth_mm >= 500) or room_width_ratio >= 0.18
    if family in {"chair", "lounge_chair"}:
        return footprint >= 700 * 700 or room_width_ratio >= 0.16

    return footprint >= 700 * 700 and placement_family == "floor_placed"


def _fallback_anchor_candidate(item: dict) -> bool:
    return _fallback_anchor_tier(item) > 0


def _fallback_anchor_tier(item: dict) -> int:
    if not isinstance(item, dict) or not _dims_complete(item):
        return 0
    family = _resolve_family(item)
    placement_family = _placement_family(item)
    if family in {"rug", "mirror", "decor"}:
        return 0
    if placement_family in {"wall_attached", "surface_placed", "small_free_object"}:
        return 0
    if _is_tiny_absolute_object(item):
        return 0

    archetype = _structural_archetype(item)
    if placement_family == "floor_placed" and archetype != "support_geometry_object":
        return 3
    if archetype == "support_geometry_object":
        return 2
    return 1


def _anchor_family_score(item: dict) -> int:
    family = _resolve_family(item)
    if family in _PRIMARY_ANCHOR_FAMILIES:
        return _PRIMARY_ANCHOR_FAMILIES[family]
    if family in _SECONDARY_ANCHOR_FAMILIES:
        return _SECONDARY_ANCHOR_FAMILIES[family]
    if _fallback_anchor_candidate(item):
        return 25
    return 0


def select_preferred_anchor_item(items: list[dict] | None, primary_item: dict | None = None) -> dict | None:
    rows = [row for row in (items or []) if isinstance(row, dict)]
    if not rows:
        return None

    primary_key = str((primary_item or {}).get("target_key") or "").strip()
    if primary_key:
        for row in rows:
            if str(row.get("target_key") or "").strip() == primary_key and is_anchor_eligible(row):
                return row

    preferred = [row for row in rows if is_anchor_eligible(row)]
    if not preferred:
        preferred = [row for row in rows if _fallback_anchor_candidate(row)]
    if not preferred:
        return None

    preferred.sort(
        key=lambda row: (
            _anchor_family_score(row),
            _fallback_anchor_tier(row),
            _footprint(row),
            _resolve_dims(row)["width_mm"],
            -(_coerce_positive_int(row.get("source_index")) or 0),
        ),
        reverse=True,
    )
    return preferred[0]


def _pass_role_for_item(item: dict, *, anchor_key: str | None) -> str:
    family = _resolve_family(item)
    placement_family = _placement_family(item)
    archetype = _structural_archetype(item)
    target_key = str(item.get("target_key") or item.get("label") or "").strip()

    if target_key and anchor_key and target_key == anchor_key:
        return "pass1_anchor"
    if family == "rug":
        return "pass1_footprint"
    if placement_family == "wall_attached":
        return "pass2_wall"
    if placement_family == "surface_placed":
        return "pass2_support_sensitive"
    if placement_family == "small_free_object" or archetype == "tiny_absolute_scale_object":
        return "pass2_small"
    if archetype == "support_geometry_object" and not is_anchor_eligible(item):
        return "pass2_support_sensitive"
    if is_anchor_eligible(item):
        return "pass1_footprint"
    if placement_family == "floor_placed":
        return "pass2_floor_secondary"
    return "pass2_decor"


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        fragments: list[str] = []
        for nested in value.values():
            fragments.extend(_flatten_text(nested))
        return fragments
    if isinstance(value, (list, tuple, set)):
        fragments: list[str] = []
        for nested in value:
            fragments.extend(_flatten_text(nested))
        return fragments
    text = str(value).strip()
    return [text] if text else []


def _reference_feature_signal_count(item: dict) -> int:
    features = item.get("reference_features") if isinstance(item, dict) else None
    if not isinstance(features, dict):
        return 0
    count = 0
    for key in ("silhouette_cues", "distinctive_parts", "preserve_rules", "material_cues", "color_cues"):
        value = features.get(key)
        if isinstance(value, (list, tuple, set)):
            count += len([row for row in value if str(row or "").strip()])
        elif str(value or "").strip():
            count += 1
    return count


def _is_product_backed_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    target_key = normalize_label_for_match(item.get("target_key") or "")
    item_id = normalize_label_for_match(item.get("item_id") or "")
    crop_path = str(item.get("crop_path") or "").strip()
    return bool(target_key.startswith("cart_product") or item_id.startswith("product_") or crop_path)


def _identity_rich_reason(item: dict) -> str:
    family = _resolve_family(item)
    signal_count = _reference_feature_signal_count(item)
    text = normalize_label_for_match(" ".join(_flatten_text(item.get("reference_features"))))
    has_shape_language = any(
        token in text
        for token in (
            "asymmetric",
            "off center",
            "off-center",
            "fluted",
            "bowl",
            "diffuser",
            "loop",
            "stem",
            "grid",
            "shelf",
            "shelving",
            "silhouette",
            "distinctive",
        )
    )
    if signal_count >= 2:
        return "reference_feature_contract"
    if _is_product_backed_item(item) and family in {"table", "table_lamp", "floor_lamp", "storage"} and has_shape_language:
        return "product_shape_contract"
    return ""


def _requires_identity_validation(item: dict, pass_role: str) -> bool:
    if not str(pass_role or "").startswith("pass2_"):
        return False
    return bool(_identity_rich_reason(item))


def _strategy_priority(pass_role: str) -> int:
    order = {
        "pass1_anchor": 100,
        "pass1_footprint": 85,
        "pass2_wall": 70,
        "pass2_support_sensitive": 60,
        "pass2_small": 55,
        "pass2_floor_secondary": 45,
        "pass2_decor": 20,
    }
    return int(order.get(pass_role, 10))


def compute_pass_role(item: dict, *, anchor_key: str | None = None) -> str:
    return _pass_role_for_item(item, anchor_key=anchor_key)


def compute_strategy_priority(item: dict) -> int:
    target_key = str((item or {}).get("target_key") or (item or {}).get("label") or "").strip()
    pass_role = str(((item or {}).get("two_pass_strategy") or {}).get("pass_role") or "").strip()
    if not pass_role:
        pass_role = _pass_role_for_item(item, anchor_key=target_key if is_anchor_eligible(item) else None)
    base = _strategy_priority(pass_role)
    return int(
        base
        + (_anchor_family_score(item) if is_anchor_eligible(item) else 0)
        + min(25, int(round(_room_width_ratio(item) * 40)))
    )


def select_anchor_candidate(items: list[dict] | None, primary_item: dict | None = None) -> dict | None:
    return select_preferred_anchor_item(items, primary_item=primary_item)


def build_two_pass_strategy(
    analyzed_items: list[dict] | None,
    *,
    primary_item: dict | None = None,
) -> tuple[list[dict], list[dict], dict | None]:
    rows = [dict(row) for row in (analyzed_items or []) if isinstance(row, dict)]
    anchor = select_preferred_anchor_item(rows, primary_item=primary_item)
    anchor_key = str((anchor or {}).get("target_key") or "").strip() or None

    enriched: list[dict] = []
    summaries: list[dict] = []
    rebound_anchor: dict | None = None

    for row in rows:
        item = dict(row)
        target_key = str(item.get("target_key") or item.get("label") or "").strip()
        pass_role = _pass_role_for_item(item, anchor_key=anchor_key)
        requires_identity_validation = _requires_identity_validation(item, pass_role)
        strategy = {
            "target_key": target_key,
            "family": _resolve_family(item),
            "anchor_eligible": bool(is_anchor_eligible(item)),
            "fallback_anchor_candidate": bool(_fallback_anchor_candidate(item)),
            "anchor_family_score": _anchor_family_score(item),
            "pass_role": pass_role,
            "strategy_priority": _strategy_priority(pass_role),
        }
        if requires_identity_validation:
            strategy["requires_identity_validation"] = True
            strategy["identity_validation_reason"] = _identity_rich_reason(item)
        item["two_pass_strategy"] = strategy
        item["anchor_eligible"] = strategy["anchor_eligible"]
        item["pass_role"] = strategy["pass_role"]
        item["strategy_priority"] = strategy["strategy_priority"]
        if requires_identity_validation:
            item["requires_identity_validation"] = True
            item["identity_validation_reason"] = strategy.get("identity_validation_reason")
        identity = dict(item.get("identity_profile") or {})
        if identity:
            identity["two_pass_strategy"] = strategy
            item["identity_profile"] = identity
        enriched.append(item)
        summaries.append(strategy)
        if anchor_key and target_key == anchor_key:
            rebound_anchor = item

    return enriched, summaries, rebound_anchor or anchor


def apply_two_pass_strategy(
    analyzed_items: list[dict] | None,
    primary_item: dict | None = None,
) -> tuple[list[dict], dict]:
    enriched, summaries, rebound_anchor = build_two_pass_strategy(analyzed_items, primary_item=primary_item)
    summary = {
        "recommended_anchor_key": str((rebound_anchor or {}).get("target_key") or "").strip() or None,
        "anchor_eligible_keys": [row.get("target_key") for row in summaries if row.get("anchor_eligible") and row.get("target_key")],
        "pass1_primary_keys": [row.get("target_key") for row in summaries if row.get("pass_role") == "pass1_anchor" and row.get("target_key")],
        "pass1_support_keys": [
            row.get("target_key")
            for row in summaries
            if row.get("pass_role") == "pass1_footprint" and row.get("target_key")
        ],
        "pass2_detail_keys": [
            row.get("target_key")
            for row in summaries
            if str(row.get("pass_role") or "").startswith("pass2_") and row.get("target_key")
        ],
        "identity_validation_required_keys": [
            row.get("target_key")
            for row in summaries
            if row.get("requires_identity_validation") and row.get("target_key")
        ],
    }
    return enriched, summary
