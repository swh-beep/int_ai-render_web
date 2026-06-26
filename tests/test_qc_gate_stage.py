from application.render.qc_gate_stage import annotate_variant_reviews, select_rankable_paths, sort_variant_paths


def test_qc_gate_prefers_hard_pass_and_sorts_by_qc_issue_score():
    annotated = annotate_variant_reviews(
        [
            {
                "variant_index": 0,
                "path": "v0.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["primary_width_vs_room_width"],
                "weighted_issue_score": 8.0,
                "unmatched_source_count": 1,
            },
            {
                "variant_index": 1,
                "path": "v1.png",
                "review_pass": True,
                "scale_check_failed": False,
                "scalecheck_failed_rules": [],
                "weighted_issue_score": 0.4,
                "unmatched_source_count": 0,
            },
            {
                "variant_index": 2,
                "path": "v2.png",
                "review_pass": False,
                "scale_check_failed": False,
                "scalecheck_failed_rules": ["low_confidence_match"],
                "weighted_issue_score": 1.2,
                "unmatched_source_count": 0,
            },
        ],
        strict_internal=True,
    )

    by_path = {row["path"]: row for row in annotated}
    assert by_path["v1.png"]["hard_qc_pass"] is True
    assert by_path["v0.png"]["qc_reason"] == "strict_hard_fail"
    assert sort_variant_paths(annotated)[0] == "v1.png"
    assert select_rankable_paths(annotated, strict_internal=True) == ["v1.png"]


def test_qc_gate_allows_soft_candidates_only_for_non_strict():
    annotated = annotate_variant_reviews(
        [
            {
                "variant_index": 0,
                "path": "v0.png",
                "review_pass": False,
                "scale_check_failed": False,
                "scalecheck_failed_rules": ["low_confidence_match"],
                "weighted_issue_score": 1.0,
                "matched_source_count": 1,
                "unmatched_source_count": 0,
            },
            {
                "variant_index": 1,
                "path": "v1.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["reference_shape_drift"],
                "weighted_issue_score": 0.5,
                "unmatched_source_count": 0,
            },
        ],
        strict_internal=False,
    )

    assert select_rankable_paths(annotated, strict_internal=False) == ["v0.png"]


def test_qc_gate_fallback_sort_prefers_identity_clean_candidate_over_lower_scale_score():
    annotated = annotate_variant_reviews(
        [
            {
                "variant_index": 0,
                "path": "identity-drift.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["reference_shape_drift"],
                "identity_fail_count": 1,
                "identity_issue_count": 1,
                "weighted_issue_score": 1.0,
                "matched_source_count": 4,
                "unmatched_source_count": 0,
            },
            {
                "variant_index": 1,
                "path": "scale-drift.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["scale_plan_room_width_ratio"],
                "identity_fail_count": 0,
                "identity_issue_count": 0,
                "weighted_issue_score": 20.0,
                "matched_source_count": 4,
                "unmatched_source_count": 0,
            },
        ],
        strict_internal=False,
    )

    by_path = {row["path"]: row for row in annotated}
    assert by_path["identity-drift.png"]["qc_reason"] == "identity_qc_fail"
    assert sort_variant_paths(annotated)[0] == "scale-drift.png"
    assert select_rankable_paths(annotated, strict_internal=False)[0] == "scale-drift.png"


def test_qc_gate_strict_mode_does_not_fallback_to_failed_candidates():
    annotated = annotate_variant_reviews(
        [
            {
                "variant_index": 0,
                "path": "v0.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["primary_width_vs_room_width"],
                "weighted_issue_score": 0.2,
                "unmatched_source_count": 0,
            },
            {
                "variant_index": 1,
                "path": "v1.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["scale_plan_room_width_ratio"],
                "weighted_issue_score": 0.1,
                "unmatched_source_count": 0,
            },
        ],
        strict_internal=True,
    )

    assert select_rankable_paths(annotated, strict_internal=True) == []


def test_qc_gate_treats_room_ratio_scale_plan_rules_as_soft_for_non_strict_mode():
    annotated = annotate_variant_reviews(
        [
            {
                "variant_index": 0,
                "path": "v0.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["scale_plan_room_width_ratio"],
                "weighted_issue_score": 1.0,
                "unmatched_source_count": 0,
            }
        ],
        strict_internal=False,
    )

    by_path = {row["path"]: row for row in annotated}
    assert by_path["v0.png"]["hard_failed_rules"] == []
    assert by_path["v0.png"]["soft_failed_rules"] == ["scale_plan_room_width_ratio"]


def test_qc_gate_treats_room_ratio_rules_as_soft_for_external_estimated_geometry():
    annotated = annotate_variant_reviews(
        [
            {
                "variant_index": 0,
                "path": "v0.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["primary_width_vs_room_width", "rug_vs_anchor_footprint"],
                "weighted_issue_score": 1.0,
                "unmatched_source_count": 0,
            }
        ],
        strict_internal=False,
        geometry_source="estimated",
        geometry_confidence="medium",
        strict_scale_mode="range_based_geometry_mode",
    )

    by_path = {row["path"]: row for row in annotated}
    assert by_path["v0.png"]["hard_failed_rules"] == []
    assert by_path["v0.png"]["soft_failed_rules"] == ["primary_width_vs_room_width", "rug_vs_anchor_footprint"]


def test_qc_gate_keeps_anchor_ratio_rules_hard_for_high_confidence_estimated_geometry():
    annotated = annotate_variant_reviews(
        [
            {
                "variant_index": 0,
                "path": "v0.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": [
                    "primary_width_vs_room_width",
                    "rug_vs_anchor_footprint",
                    "scale_plan_anchor_width_ratio",
                ],
                "weighted_issue_score": 1.0,
                "unmatched_source_count": 0,
            }
        ],
        strict_internal=False,
        geometry_source="estimated",
        geometry_confidence="high",
        strict_scale_mode="range_based_geometry_mode",
    )

    by_path = {row["path"]: row for row in annotated}
    assert by_path["v0.png"]["hard_failed_rules"] == ["rug_vs_anchor_footprint", "scale_plan_anchor_width_ratio"]
    assert by_path["v0.png"]["soft_failed_rules"] == ["primary_width_vs_room_width"]
    assert by_path["v0.png"]["confidence_hard_block"] is True
    assert select_rankable_paths(annotated, strict_internal=False) == []


def test_qc_gate_treats_all_confidence_aware_rules_as_soft_for_low_confidence_estimated_geometry():
    annotated = annotate_variant_reviews(
        [
            {
                "variant_index": 0,
                "path": "v0.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": [
                    "primary_width_vs_room_width",
                    "rug_vs_anchor_footprint",
                    "scale_plan_anchor_width_ratio",
                    "scale_plan_room_width_ratio",
                ],
                "weighted_issue_score": 1.0,
                "unmatched_source_count": 0,
            }
        ],
        strict_internal=False,
        geometry_source="estimated",
        geometry_confidence="low",
        strict_scale_mode="range_based_geometry_mode",
    )

    by_path = {row["path"]: row for row in annotated}
    assert by_path["v0.png"]["hard_failed_rules"] == []
    assert by_path["v0.png"]["soft_failed_rules"] == [
        "primary_width_vs_room_width",
        "rug_vs_anchor_footprint",
        "scale_plan_anchor_width_ratio",
        "scale_plan_room_width_ratio",
    ]


def test_qc_gate_keeps_room_ratio_rules_hard_for_internal_strict_geometry():
    annotated = annotate_variant_reviews(
        [
            {
                "variant_index": 0,
                "path": "v0.png",
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["primary_width_vs_room_width"],
                "weighted_issue_score": 1.0,
                "unmatched_source_count": 0,
            }
        ],
        strict_internal=True,
        geometry_source="explicit",
        geometry_confidence="high",
        strict_scale_mode="strict_geometry_mode",
    )

    by_path = {row["path"]: row for row in annotated}
    assert by_path["v0.png"]["hard_failed_rules"] == ["primary_width_vs_room_width"]
