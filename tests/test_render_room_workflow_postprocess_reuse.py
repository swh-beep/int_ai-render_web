from application.render.render_room_workflow import (
    _apply_selected_review_boxes_to_analyzed_items,
    _can_skip_postprocess_remap,
    _compact_variant_diagnostics,
    _resolve_postprocess_ranking_inputs,
    _select_final_generated_results,
    _should_launch_budgeted_fallback_variant,
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


def test_can_skip_postprocess_remap_only_for_strict_nonrepair_global_matches():
    assert (
        _can_skip_postprocess_remap(
            strict_scale_requested=True,
            variant_diagnostics=[
                {
                    "matched_source_count": 3,
                    "repair_applied": False,
                    "repair_attempt_count": 0,
                    "repair_target_keys": [],
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
                    "repair_applied": True,
                    "repair_attempt_count": 1,
                    "repair_target_keys": ["lamp-1"],
                }
            ],
        )
        is False
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
                "repair_applied": False,
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
                "repair_applied": True,
                "repair_attempt_count": 1,
            },
        ],
        strict_scale_requested=True,
    )

    assert generated_results == ["candidate-a.png"]
    assert selected_index == 0
    assert selected_reason == "strict_delivery_best_effort"
    assert selected_review["path"] == "candidate-a.png"
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
