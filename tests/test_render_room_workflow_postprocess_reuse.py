from application.render.render_room_workflow import (
    _apply_selected_review_boxes_to_analyzed_items,
    _can_skip_postprocess_remap,
    _compact_variant_diagnostics,
    _resolve_postprocess_ranking_inputs,
    _select_final_generated_results,
    _should_launch_budgeted_fallback_variant,
    _variant_quality_sort_key,
)


def test_apply_selected_review_boxes_reuses_matched_bbox_for_target_key():
    analyzed = [
        {
            "target_key": "sofa-1",
            "label": "Sofa",
            "box_2d": [100, 200, 300, 600],
            "box_source": "source_reference",
        },
        {
            "target_key": "lamp-2",
            "label": "Lamp",
            "box_2d": [10, 20, 30, 60],
            "box_source": "source_reference",
        },
    ]
    selected_review = {
        "scalecheck_diagnostics": {
            "matched_items": {
                "sofa-1": {
                    "bbox_norm": [0.2, 0.1, 0.8, 0.5],
                    "label": "Sofa",
                    "match_confidence": 0.93,
                }
            }
        }
    }

    updated = _apply_selected_review_boxes_to_analyzed_items(analyzed, selected_review)

    assert updated[0]["box_2d"] == [100, 200, 500, 800]
    assert updated[0]["source_box_2d"] == [100, 200, 300, 600]
    assert updated[0]["box_source"] == "selected_variant_review"
    assert updated[0]["box_label_detected"] == "Sofa"
    assert updated[0]["box_match_score"] == 0.93
    assert updated[1]["box_2d"] == [10, 20, 30, 60]


def test_apply_selected_review_boxes_ignores_missing_or_invalid_matches():
    analyzed = [{"target_key": "mirror-1", "label": "Mirror", "box_2d": [5, 6, 7, 8]}]
    selected_review = {
        "scalecheck_diagnostics": {
            "matched_items": {
                "mirror-1": {
                    "bbox_norm": [0.5, 0.5, 0.5, 0.9],
                    "label": "Mirror",
                }
            }
        }
    }

    updated = _apply_selected_review_boxes_to_analyzed_items(analyzed, selected_review)

    assert updated[0]["box_2d"] == [5, 6, 7, 8]
    assert updated[0].get("box_source") is None


def test_compact_variant_diagnostics_requires_matched_items_for_review_pass():
    rows = _compact_variant_diagnostics(
        [
            {
                "path": "candidate.png",
                "scale_check_failed": False,
                "scalecheck_failed_rules": [],
                "scalecheck_issues": [],
                "scalecheck_diagnostics": {
                    "failed_rules": [],
                    "matched_items": {},
                    "unmatched_items": [],
                    "issue_records": [],
                },
            }
        ]
    )

    assert rows[0]["review_pass"] is False
    assert rows[0]["matched_source_count"] == 0


def test_compact_variant_diagnostics_does_not_infer_review_pass_without_diagnostics():
    rows = _compact_variant_diagnostics(
        [
            {
                "path": "candidate-no-diagnostics.png",
                "scale_check_failed": False,
                "scalecheck_failed_rules": [],
                "scalecheck_issues": [],
            }
        ]
    )

    assert rows[0]["review_pass"] is False
    assert rows[0]["review_score"] == 0


def test_compact_variant_diagnostics_exposes_identity_qc_from_reference_review():
    rows = _compact_variant_diagnostics(
        [
            {
                "path": "candidate-identity-drift.png",
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["reference_integration_drift"],
                "scalecheck_issues": ["reference_integration_drift:chair-1"],
                "scalecheck_diagnostics": {
                    "failed_rules": ["reference_integration_drift"],
                    "matched_items": {"chair-1": {"bbox_norm": [0.1, 0.2, 0.4, 0.7]}},
                    "unmatched_items": [],
                    "issue_records": [
                        {"rule_kind": "reference_integration_drift", "weighted_score": 1.0},
                        {"rule_kind": "scale_fit_violation", "weighted_score": 0.5},
                    ],
                },
            }
        ]
    )

    assert rows[0]["identity_fail_count"] == 1
    assert rows[0]["identity_issue_count"] == 1
    assert rows[0]["fidelity_fail_count"] == 1
    assert rows[0]["identity_failed_rules"] == ["reference_integration_drift"]
    assert rows[0]["identity_qc_summary"]["pass"] is False


def test_variant_quality_sort_key_prioritizes_identity_clean_over_lower_scale_score():
    diagnostics_by_path = {
        "identity-drift.png": {
            "path": "identity-drift.png",
            "variant_index": 0,
            "review_pass": False,
            "identity_fail_count": 1,
            "identity_issue_count": 1,
            "unmatched_source_count": 0,
            "weighted_issue_score": 1.0,
            "geometry_fail_count": 0,
        },
        "scale-drift.png": {
            "path": "scale-drift.png",
            "variant_index": 1,
            "review_pass": False,
            "identity_fail_count": 0,
            "identity_issue_count": 0,
            "unmatched_source_count": 0,
            "weighted_issue_score": 20.0,
            "geometry_fail_count": 8,
        },
    }

    ordered = sorted(diagnostics_by_path, key=lambda path: _variant_quality_sort_key(path, diagnostics_by_path))

    assert ordered == ["scale-drift.png", "identity-drift.png"]


def test_variant_quality_sort_key_prefers_more_matched_items_before_scale_weight():
    diagnostics_by_path = {
        "many-items-scale-drift.png": {
            "path": "many-items-scale-drift.png",
            "variant_index": 0,
            "review_pass": False,
            "identity_fail_count": 0,
            "identity_issue_count": 0,
            "matched_source_count": 9,
            "unmatched_source_count": 0,
            "weighted_issue_score": 40.0,
            "geometry_fail_count": 10,
            "scale_check_failed": True,
            "identity_qc_summary": {"pass": True},
        },
        "few-items-scale-cleaner.png": {
            "path": "few-items-scale-cleaner.png",
            "variant_index": 1,
            "review_pass": False,
            "identity_fail_count": 0,
            "identity_issue_count": 0,
            "matched_source_count": 4,
            "unmatched_source_count": 0,
            "weighted_issue_score": 5.0,
            "geometry_fail_count": 1,
            "scale_check_failed": True,
            "identity_qc_summary": {"pass": True},
        },
    }

    ordered = sorted(diagnostics_by_path, key=lambda path: _variant_quality_sort_key(path, diagnostics_by_path))

    assert ordered == ["many-items-scale-drift.png", "few-items-scale-cleaner.png"]


def test_variant_quality_sort_key_demotes_reference_fidelity_failure_even_with_lower_scale_weight():
    diagnostics_by_path = {
        "reference-drift.png": {
            "path": "reference-drift.png",
            "variant_index": 0,
            "review_pass": False,
            "identity_fail_count": 1,
            "identity_issue_count": 1,
            "identity_failed_rules": ["reference_shape_drift"],
            "matched_source_count": 9,
            "unmatched_source_count": 0,
            "weighted_issue_score": 3.0,
            "geometry_fail_count": 1,
            "scale_check_failed": True,
            "identity_qc_summary": {"pass": False},
        },
        "scale-drift-only.png": {
            "path": "scale-drift-only.png",
            "variant_index": 1,
            "review_pass": False,
            "identity_fail_count": 0,
            "identity_issue_count": 0,
            "identity_failed_rules": [],
            "matched_source_count": 7,
            "unmatched_source_count": 0,
            "weighted_issue_score": 30.0,
            "geometry_fail_count": 10,
            "scale_check_failed": True,
            "identity_qc_summary": {"pass": True},
        },
    }

    ordered = sorted(diagnostics_by_path, key=lambda path: _variant_quality_sort_key(path, diagnostics_by_path))

    assert ordered == ["scale-drift-only.png", "reference-drift.png"]


def test_can_skip_postprocess_remap_for_strict_global_matches():
    assert (
        _can_skip_postprocess_remap(
            strict_scale_requested=True,
            variant_diagnostics=[
                {
                    "matched_source_count": 3,
                }
            ],
        )
        is True
    )
    assert (
        _can_skip_postprocess_remap(
            strict_scale_requested=True,
            variant_diagnostics=[
                {
                    "matched_source_count": 3,
                }
            ],
        )
        is True
    )


def test_select_final_generated_results_keeps_soft_qc_subset_when_strict_has_no_hard_qc():
    generated_results, selected_index, selected_reason, selected_review, blocked = _select_final_generated_results(
        ["soft-pass.png", "fallback.png"],
        [
            {
                "path": "soft-pass.png",
                "variant_index": 0,
                "hard_qc_pass": False,
                "soft_qc_pass": True,
                "review_pass": False,
                "weighted_issue_score": 0.0,
            },
            {
                "path": "fallback.png",
                "variant_index": 1,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "weighted_issue_score": 2.5,
            },
        ],
        strict_scale_requested=True,
    )

    assert generated_results == ["soft-pass.png"]
    assert selected_index == 0
    assert selected_reason == "soft_qc_pass_ranked"
    assert selected_review["path"] == "soft-pass.png"
    assert blocked is False


def test_select_final_generated_results_prefers_hard_qc_subset_for_strict_mode():
    generated_results, selected_index, selected_reason, selected_review, blocked = _select_final_generated_results(
        ["soft-pass.png", "hard-pass.png"],
        [
            {
                "path": "soft-pass.png",
                "variant_index": 0,
                "hard_qc_pass": False,
                "soft_qc_pass": True,
                "review_pass": False,
                "weighted_issue_score": 0.0,
            },
            {
                "path": "hard-pass.png",
                "variant_index": 1,
                "hard_qc_pass": True,
                "soft_qc_pass": True,
                "review_pass": True,
                "weighted_issue_score": 0.0,
            },
        ],
        strict_scale_requested=True,
    )

    assert generated_results == ["hard-pass.png"]
    assert selected_index == 1
    assert selected_reason == "hard_qc_pass_ranked"
    assert selected_review["path"] == "hard-pass.png"
    assert blocked is False


def test_select_final_generated_results_allows_validation_unavailable_best_effort_for_strict_mode():
    generated_results, selected_index, selected_reason, selected_review, blocked = _select_final_generated_results(
        ["candidate-a.png", "candidate-b.png"],
        [
            {
                "path": "candidate-a.png",
                "variant_index": 0,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["no_matched_items"],
                "hard_failed_rules": ["no_matched_items"],
                "matched_source_count": 0,
                "unmatched_source_count": 7,
                "fidelity_fail_count": 0,
                "placement_fail_count": 0,
                "geometry_fail_count": 0,
                "weighted_issue_score": 14.0,
            },
            {
                "path": "candidate-b.png",
                "variant_index": 1,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["no_matched_items"],
                "hard_failed_rules": ["no_matched_items"],
                "matched_source_count": 0,
                "unmatched_source_count": 7,
                "fidelity_fail_count": 0,
                "placement_fail_count": 0,
                "geometry_fail_count": 0,
                "weighted_issue_score": 18.0,
            },
        ],
        strict_scale_requested=True,
    )

    assert generated_results == ["candidate-a.png", "candidate-b.png"]
    assert selected_index == 0
    assert selected_reason == "strict_validation_unavailable_best_effort"
    assert selected_review["path"] == "candidate-a.png"
    assert blocked is False


def test_select_final_generated_results_allows_repaired_delivery_best_effort_for_strict_mode():
    generated_results, selected_index, selected_reason, selected_review, blocked = _select_final_generated_results(
        ["candidate-b.png", "candidate-a.png"],
        [
            {
                "path": "candidate-b.png",
                "variant_index": 1,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["primary_width_vs_room_width"],
                "hard_failed_rules": ["primary_width_vs_room_width"],
                "matched_source_count": 3,
                "unmatched_source_count": 4,
                "fidelity_fail_count": 0,
                "placement_fail_count": 0,
                "geometry_fail_count": 14,
                "weighted_issue_score": 81.0,
            },
            {
                "path": "candidate-a.png",
                "variant_index": 0,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["primary_width_vs_room_width", "unmatched_source_items"],
                "hard_failed_rules": ["primary_width_vs_room_width", "unmatched_source_items"],
                "matched_source_count": 5,
                "unmatched_source_count": 2,
                "fidelity_fail_count": 0,
                "placement_fail_count": 0,
                "geometry_fail_count": 17,
                "weighted_issue_score": 73.184,
            },
        ],
        strict_scale_requested=True,
    )

    assert generated_results == ["candidate-a.png"]
    assert selected_index == 0
    assert selected_reason == "strict_delivery_best_effort"
    assert selected_review["path"] == "candidate-a.png"
    assert blocked is False


def test_select_final_generated_results_uses_least_bad_candidate_when_strict_qc_blocks_all():
    generated_results, selected_index, selected_reason, selected_review, blocked = _select_final_generated_results(
        ["candidate-a.png", "candidate-b.png", "candidate-c.png"],
        [
            {
                "path": "candidate-a.png",
                "variant_index": 0,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": [
                    "primary_width_vs_room_width",
                    "unmatched_source_items",
                    "extra_instance_detected:sofa",
                ],
                "hard_failed_rules": [
                    "primary_width_vs_room_width",
                    "unmatched_source_items",
                    "extra_instance_detected:sofa",
                ],
                "matched_source_count": 5,
                "unmatched_source_count": 5,
                "fidelity_fail_count": 0,
                "placement_fail_count": 0,
                "geometry_fail_count": 0,
                "scalecheck_fail_count": 3,
                "weighted_issue_score": 55.0,
            },
            {
                "path": "candidate-b.png",
                "variant_index": 1,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": [
                    "scale_plan_room_height_ratio",
                    "unmatched_source_items",
                ],
                "hard_failed_rules": [
                    "scale_plan_room_height_ratio",
                    "unmatched_source_items",
                ],
                "matched_source_count": 6,
                "unmatched_source_count": 3,
                "fidelity_fail_count": 0,
                "placement_fail_count": 0,
                "geometry_fail_count": 0,
                "scalecheck_fail_count": 2,
                "weighted_issue_score": 31.0,
            },
            {
                "path": "candidate-c.png",
                "variant_index": 2,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "scale_check_failed": True,
                "scalecheck_failed_rules": ["unmatched_source_items"],
                "hard_failed_rules": ["unmatched_source_items"],
                "matched_source_count": 4,
                "unmatched_source_count": 6,
                "fidelity_fail_count": 1,
                "placement_fail_count": 2,
                "geometry_fail_count": 1,
                "scalecheck_fail_count": 1,
                "weighted_issue_score": 72.0,
            },
        ],
        strict_scale_requested=True,
    )

    assert generated_results == ["candidate-b.png"]
    assert selected_index == 1
    assert selected_reason == "all_failed_weighted_fallback"
    assert selected_review["path"] == "candidate-b.png"
    assert blocked is False


def test_resolve_postprocess_ranking_inputs_disables_failed_rerank_for_strict_failed_candidates():
    rankable_results, allow_failed_rerank = _resolve_postprocess_ranking_inputs(
        ["variant-c.png", "variant-a.png", "variant-b.png"],
        [
            {
                "path": "variant-a.png",
                "variant_index": 0,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "weighted_issue_score": 2.0,
                "scale_check_failed": True,
                "unmatched_source_count": 1,
            },
            {
                "path": "variant-b.png",
                "variant_index": 1,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "weighted_issue_score": 0.5,
                "scale_check_failed": True,
                "unmatched_source_count": 0,
            },
            {
                "path": "variant-c.png",
                "variant_index": 2,
                "hard_qc_pass": False,
                "soft_qc_pass": False,
                "review_pass": False,
                "weighted_issue_score": 4.0,
                "scale_check_failed": True,
                "unmatched_source_count": 2,
            },
        ],
        strict_scale_requested=True,
    )

    assert rankable_results == []
    assert allow_failed_rerank is False


def test_should_launch_budgeted_fallback_variant_requires_budget_and_no_rankable_candidates():
    assert (
        _should_launch_budgeted_fallback_variant(
            [{"path": "variant-1.png", "review_pass": False, "soft_qc_pass": False, "hard_qc_pass": False}],
            strict_scale_requested=True,
            remaining_budget_sec=359.9,
            rankable_selector=lambda rows, strict_internal=False: [],
        )
        is False
    )
    assert (
        _should_launch_budgeted_fallback_variant(
            [{"path": "variant-1.png", "review_pass": True, "soft_qc_pass": False, "hard_qc_pass": False}],
            strict_scale_requested=True,
            remaining_budget_sec=400.0,
            rankable_selector=lambda rows, strict_internal=False: ["variant-1.png"],
        )
        is False
    )
    assert (
        _should_launch_budgeted_fallback_variant(
            [{"path": "variant-1.png", "review_pass": False, "soft_qc_pass": False, "hard_qc_pass": False}],
            strict_scale_requested=True,
            remaining_budget_sec=400.0,
            rankable_selector=lambda rows, strict_internal=False: [],
        )
        is True
    )
