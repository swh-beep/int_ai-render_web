from __future__ import annotations

from typing import Any

from application.render.postprocess_support import category_match_family
from application.render.render_contracts import ProductIdentity


_SUPPORT_GEOMETRY_HINTS = (
    "cantilever",
    "dual-post",
    "dual post",
    "sled",
    "pedestal",
    "plinth",
    "spindle",
    "frame",
    "chrome frame",
    "tube frame",
    "block base",
    "arched base",
    "four-leg",
    "four leg",
    "three-leg",
    "three leg",
    "column base",
    "support",
)

_OPENING_OR_GAP_HINTS = (
    "gap",
    "gapped",
    "open-back",
    "open back",
    "open side",
    "cutout",
    "void",
    "missing center",
    "split back",
    "segmented",
    "channel",
)

_PATTERN_HINTS = (
    "striped",
    "stripe",
    "ribbed",
    "patterned",
    "pattern",
    "concentric",
    "border",
    "woven",
    "tufted",
    "quilted",
    "checker",
)

_REFLECTION_HINTS = (
    "mirror",
    "reflective",
    "reflection",
    "black frame",
    "rounded frame",
    "wall mirror",
    "full reflection",
)

_CRITICAL_FAMILIES = {
    "sofa",
    "lounge_sofa",
    "lounge_seating",
    "chair",
    "lounge_chair",
    "mirror",
    "rug",
    "table",
    "desk",
    "floor_lamp",
    "table_lamp",
    "ceiling_light",
    "wall_light",
    "storage",
}


def _coerce_dims(value: dict | None) -> dict[str, int | None]:
    value = value if isinstance(value, dict) else {}
    result: dict[str, int | None] = {}
    for key in ("width_mm", "depth_mm", "height_mm", "radius_mm"):
        try:
            parsed = int(value.get(key) or 0)
        except Exception:
            parsed = 0
        result[key] = parsed if parsed > 0 else None
    return result


def _merge_unique(*groups: list[str], limit: int = 8) -> list[str]:
    merged: list[str] = []
    for group in groups:
        if not isinstance(group, list):
            continue
        for raw in group:
            text = str(raw or "").strip()
            if not text or text in merged:
                continue
            merged.append(text)
            if len(merged) >= limit:
                return merged
    return merged


def _extract_from_text(text: str, hints: tuple[str, ...], *, limit: int = 6) -> list[str]:
    normalized = str(text or "").lower()
    hits: list[str] = []
    for hint in hints:
        if hint in normalized and hint not in hits:
            hits.append(hint)
        if len(hits) >= limit:
            break
    return hits


def _family_for_item(item: dict) -> str:
    profile = (item.get("identity_profile") or {}) if isinstance(item, dict) else {}
    for candidate in (
        profile.get("family"),
        item.get("category"),
        item.get("category_canonical"),
        item.get("label"),
    ):
        family = category_match_family(candidate)
        if family:
            return str(family)
    return str(item.get("category_canonical") or item.get("category") or item.get("label") or "").strip().lower()


def _product_identity_summary(identity: ProductIdentity) -> str:
    bits: list[str] = []
    if identity.family:
        bits.append(f"family={identity.family}")
    if identity.topology_cues:
        bits.append("topology=" + ", ".join(identity.topology_cues[:3]))
    if identity.support_geometry:
        bits.append("support=" + ", ".join(identity.support_geometry[:2]))
    if identity.opening_or_gap_features:
        bits.append("openings=" + ", ".join(identity.opening_or_gap_features[:2]))
    if identity.pattern_cues:
        bits.append("pattern=" + ", ".join(identity.pattern_cues[:2]))
    if identity.reflection_constraints:
        bits.append("reflection=" + ", ".join(identity.reflection_constraints[:2]))
    return "; ".join(bits)


def _identity_confidence(identity: ProductIdentity, *, crop_path: str | None) -> float:
    score = 0.25
    if crop_path:
        score += 0.2
    score += min(0.2, len(identity.topology_cues) * 0.05)
    score += min(0.15, len(identity.support_geometry) * 0.05)
    score += min(0.15, len(identity.opening_or_gap_features) * 0.075)
    score += min(0.1, len(identity.pattern_cues) * 0.05)
    score += min(0.1, len(identity.reflection_constraints) * 0.05)
    score += min(0.15, len(identity.preserve_rules) * 0.03)
    return round(min(1.0, score), 3)


def build_product_identity_bundle(analyzed_items: list[dict] | None) -> tuple[list[dict], list[dict]]:
    enriched_items: list[dict] = []
    product_identities: list[dict] = []

    for row in analyzed_items or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        profile = dict(item.get("identity_profile") or {})
        ref = dict(item.get("reference_features") or {})
        family = _family_for_item(item)
        description = str(item.get("description") or "")
        label = str(item.get("label") or "")
        text_blob = " ".join([label, str(item.get("category") or ""), description]).strip()

        topology_cues = _merge_unique(
            list(ref.get("silhouette_cues") or []),
            list(profile.get("shape_cues") or []),
        )
        support_geometry = _merge_unique(
            _extract_from_text(text_blob, _SUPPORT_GEOMETRY_HINTS),
            list(ref.get("distinctive_parts") or []),
            [cue for cue in (profile.get("distinctive_parts") or []) if any(h in str(cue).lower() for h in _SUPPORT_GEOMETRY_HINTS)],
        )
        opening_or_gap_features = _merge_unique(
            _extract_from_text(text_blob, _OPENING_OR_GAP_HINTS),
            [cue for cue in (ref.get("distinctive_parts") or []) if any(h in str(cue).lower() for h in _OPENING_OR_GAP_HINTS)],
            [cue for cue in (profile.get("preserve_rules") or []) if any(h in str(cue).lower() for h in _OPENING_OR_GAP_HINTS)],
        )
        pattern_cues = _merge_unique(
            _extract_from_text(text_blob, _PATTERN_HINTS),
            [cue for cue in (ref.get("distinctive_parts") or []) if any(h in str(cue).lower() for h in _PATTERN_HINTS)],
        )
        reflection_constraints = _merge_unique(
            _extract_from_text(text_blob, _REFLECTION_HINTS),
            ["reflective_surface"] if bool(ref.get("reflective_surface") or profile.get("reflective_surface")) else [],
        )
        preserve_rules = _merge_unique(
            ["exact_reference_image"] if item.get("crop_path") else [],
            list(ref.get("preserve_rules") or []),
            list(profile.get("preserve_rules") or []),
            support_geometry,
            opening_or_gap_features,
            pattern_cues,
            reflection_constraints,
        )

        identity = ProductIdentity(
            target_key=str(item.get("target_key") or ""),
            family=family,
            dims_mm=_coerce_dims(item.get("requested_dims_mm") or item.get("dims_mm") or {}),
            topology_cues=topology_cues,
            support_geometry=support_geometry,
            opening_or_gap_features=opening_or_gap_features,
            pattern_cues=pattern_cues,
            reflection_constraints=reflection_constraints,
            preserve_rules=preserve_rules,
        )

        identity_dict = identity.as_dict()
        confidence = _identity_confidence(identity, crop_path=item.get("crop_path"))
        strictness = "critical" if family in _CRITICAL_FAMILIES else "standard"

        profile["family"] = family or profile.get("family")
        profile["topology_cues"] = list(identity.topology_cues)
        profile["support_geometry"] = list(identity.support_geometry)
        profile["opening_or_gap_features"] = list(identity.opening_or_gap_features)
        profile["pattern_cues"] = list(identity.pattern_cues)
        profile["reflection_constraints"] = list(identity.reflection_constraints)
        profile["preserve_rules"] = list(identity.preserve_rules)
        profile["product_identity_summary"] = _product_identity_summary(identity)

        item["identity_profile"] = profile
        item["product_identity"] = identity_dict
        item["identity_confidence"] = confidence
        item["identity_strictness"] = strictness

        enriched_items.append(item)
        product_identities.append(identity_dict)

    return enriched_items, product_identities
