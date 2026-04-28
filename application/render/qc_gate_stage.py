from __future__ import annotations


_HARD_RULES = {
    "primary_width_vs_room_width",
    "rug_vs_anchor_footprint",
    "tiny_item_vs_anchor_height",
    "mirror_reflection_drift",
    "primary_anchor_unmatched",
    "no_matched_items",
    "strict_scale_contract_not_ready",
    "scale_guide_leak_detected",
    "deadline_budget_exhausted",
    "incomplete_items_missing_required_dimensions",
    "validator_exception",
}
_CONFIDENCE_AWARE_GEOMETRY_RULES = {
    "primary_width_vs_room_width",
    "rug_vs_anchor_footprint",
    "tiny_item_vs_anchor_height",
}


def _is_confidence_aware_geometry_rule(rule: str) -> bool:
    return (
        rule in _CONFIDENCE_AWARE_GEOMETRY_RULES
        or rule.startswith("scale_plan_anchor_")
        or rule.startswith("scale_plan_room_")
    )


def _room_geometry_rule(rule: str) -> bool:
    return rule == "primary_width_vs_room_width" or rule.startswith("scale_plan_room_")


def _anchor_geometry_rule(rule: str) -> bool:
    return (
        rule in {"rug_vs_anchor_footprint", "tiny_item_vs_anchor_height"}
        or rule.startswith("scale_plan_anchor_")
    )


def _should_soften_confidence_aware_rule(
    rule: str,
    *,
    strict_internal: bool,
    geometry_source: str,
    geometry_confidence: str,
    strict_scale_mode: str,
) -> bool:
    if strict_internal or not _is_confidence_aware_geometry_rule(rule):
        return False

    estimated_geometry = str(geometry_source or "").strip().lower() == "estimated"
    confidence = str(geometry_confidence or "").strip().lower()
    range_based_mode = str(strict_scale_mode or "").strip().lower() == "range_based_geometry_mode"
    if not estimated_geometry or not range_based_mode:
        return False

    if confidence in {"none", "", "low"}:
        return True
    if confidence == "medium":
        return _room_geometry_rule(rule) or _anchor_geometry_rule(rule)
    if confidence == "high":
        return _room_geometry_rule(rule)
    return False


def _split_rules(
    failed_rules: list[str] | None,
    *,
    strict_internal: bool,
    geometry_source: str = "unknown",
    geometry_confidence: str = "none",
    strict_scale_mode: str = "advisory_geometry_mode",
) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    for raw in failed_rules or []:
        rule = str(raw or "").strip()
        if not rule:
            continue
        if _should_soften_confidence_aware_rule(
            rule,
            strict_internal=strict_internal,
            geometry_source=geometry_source,
            geometry_confidence=geometry_confidence,
            strict_scale_mode=strict_scale_mode,
        ):
            soft.append(rule)
            continue
        if (
            rule in _HARD_RULES
            or _anchor_geometry_rule(rule)
            or rule.startswith("reference_")
            or (strict_internal and rule.startswith("scale_plan_room_"))
        ):
            hard.append(rule)
        else:
            soft.append(rule)
    return hard, soft


def annotate_variant_reviews(
    variant_diagnostics: list[dict] | None,
    *,
    strict_internal: bool,
    geometry_source: str = "unknown",
    geometry_confidence: str = "none",
    strict_scale_mode: str = "advisory_geometry_mode",
) -> list[dict]:
    annotated: list[dict] = []
    for row in variant_diagnostics or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        hard_rules, soft_rules = _split_rules(
            item.get("scalecheck_failed_rules") or [],
            strict_internal=strict_internal,
            geometry_source=geometry_source,
            geometry_confidence=geometry_confidence,
            strict_scale_mode=strict_scale_mode,
        )
        issue_texts = [str(value or "").strip().lower() for value in (item.get("scalecheck_issues") or []) if str(value or "").strip()]
        if strict_internal and any(text.startswith("validator exception") for text in issue_texts):
            hard_rules = [*hard_rules, "validator_exception"] if "validator_exception" not in hard_rules else hard_rules
        matched = int(item.get("matched_source_count") or 0)
        unmatched = int(item.get("unmatched_source_count") or 0)
        weighted = float(item.get("weighted_issue_score") or 0.0)
        hard_qc_pass = bool(item.get("review_pass")) and not hard_rules and unmatched == 0 and not bool(item.get("scale_check_failed"))
        soft_qc_pass = matched > 0 and not hard_rules and unmatched == 0 and not (strict_internal and bool(item.get("scale_check_failed")))
        qc_issue_score = round(
            weighted
            + (len(hard_rules) * 5.0)
            + (len(soft_rules) * 1.5)
            + (unmatched * 2.5),
            4,
        )
        if strict_internal and hard_rules:
            qc_reason = "strict_hard_fail"
        elif hard_qc_pass:
            qc_reason = "hard_qc_pass"
        elif soft_qc_pass:
            qc_reason = "soft_qc_pass"
        else:
            qc_reason = "weighted_fallback"
        estimated_geometry = str(geometry_source or "").strip().lower() == "estimated"
        high_confidence_geometry = str(geometry_confidence or "").strip().lower() == "high"
        item["confidence_hard_block"] = bool(
            not strict_internal
            and estimated_geometry
            and high_confidence_geometry
            and any(_anchor_geometry_rule(rule) for rule in hard_rules)
        )
        item["hard_qc_pass"] = hard_qc_pass
        item["soft_qc_pass"] = soft_qc_pass
        item["hard_failed_rules"] = hard_rules
        item["soft_failed_rules"] = soft_rules
        item["qc_issue_score"] = qc_issue_score
        item["qc_reason"] = qc_reason
        annotated.append(item)
    return annotated


def sort_variant_paths(variant_diagnostics: list[dict] | None) -> list[str]:
    rows = [row for row in (variant_diagnostics or []) if isinstance(row, dict) and row.get("path")]
    rows.sort(
        key=lambda row: (
            0 if row.get("hard_qc_pass") else 1,
            0 if row.get("soft_qc_pass") else 1,
            float(row.get("qc_issue_score") or 0.0),
            int(row.get("variant_index") or 0),
        )
    )
    return [str(row.get("path")) for row in rows]


def select_rankable_paths(variant_diagnostics: list[dict] | None, *, strict_internal: bool) -> list[str]:
    rows = [row for row in (variant_diagnostics or []) if isinstance(row, dict) and row.get("path")]
    rows = [row for row in rows if not row.get("confidence_hard_block")]
    hard = [str(row.get("path")) for row in rows if row.get("hard_qc_pass")]
    if hard:
        return hard
    soft = [str(row.get("path")) for row in rows if row.get("soft_qc_pass")]
    if soft:
        return soft
    if strict_internal:
        return []
    fallback = sort_variant_paths(rows)
    return fallback[: max(1, min(2, len(fallback)))]
