from application.render.batch_detection_support import (
    build_detection_rows_from_matches,
    build_matched_items_from_rows,
    detect_rows_from_render,
    match_items_to_detected_rows,
)
from application.render.postprocess_support import (
    _SENSITIVE_REMAP_FAMILIES,
    canonical_category,
    category_match_family,
    remap_match_score,
)


def test_detect_rows_from_render_normalizes_detected_boxes():
    rows = detect_rows_from_render(
        "render.png",
        detect_furniture_boxes=lambda *args, **kwargs: [
            {"label": "Mirror", "box_2d": [100, 200, 900, 800]},
            {"label": "Invalid", "box_2d": [100, 100]},
        ],
        model_name="model",
        timeout_sec=30,
        retry=0,
        canonical_category=canonical_category,
        category_match_family=category_match_family,
    )

    assert len(rows) == 1
    assert rows[0]["category_canonical"] == "mirror"
    assert rows[0]["family"] == "mirror"
    assert rows[0]["bbox_norm"] == (0.2, 0.1, 0.8, 0.9)


def test_match_items_to_detected_rows_and_build_detection_rows_preserve_source_identity():
    analyzed_items = [
        {
            "label": "Walnut Sideboard",
            "category": "storage",
            "category_canonical": "storage",
            "target_key": "storage_001",
            "source_index": 7,
            "box_2d": [1, 1, 2, 2],
        }
    ]
    detected_rows = [
        {
            "label": "Cabinet",
            "category": "storage",
            "category_canonical": "storage",
            "family": "storage",
            "box_2d": [100, 100, 320, 860],
            "bbox_norm": (0.1, 0.1, 0.86, 0.32),
        }
    ]

    matches = match_items_to_detected_rows(
        analyzed_items,
        detected_rows,
        remap_match_score=remap_match_score,
        category_match_family=category_match_family,
        canonical_category=canonical_category,
        sensitive_remap_families=_SENSITIVE_REMAP_FAMILIES,
    )
    refreshed = build_matched_items_from_rows(analyzed_items, matches)
    detection_rows = build_detection_rows_from_matches(matches)

    assert refreshed[0]["box_source"] == "main_render"
    assert refreshed[0]["box_label_detected"] == "Cabinet"
    assert detection_rows[0]["target_key"] == "storage_001"
    assert detection_rows[0]["source_index"] == 7
    assert detection_rows[0]["bbox_norm"] == (0.1, 0.1, 0.86, 0.32)


def test_match_items_to_detected_rows_does_not_accept_sensitive_family_unique_with_weak_score():
    analyzed_items = [
        {
            "label": "Standing Mirror",
            "category": "mirror",
            "category_canonical": "mirror",
            "target_key": "mirror_001",
            "source_index": 1,
            "box_2d": [1, 1, 2, 2],
        }
    ]
    detected_rows = [
        {
            "label": "Poster",
            "category": "decor",
            "category_canonical": "decor",
            "family": "mirror",
            "box_2d": [100, 100, 400, 500],
            "bbox_norm": (0.1, 0.1, 0.5, 0.4),
        }
    ]

    matches = match_items_to_detected_rows(
        analyzed_items,
        detected_rows,
        remap_match_score=remap_match_score,
        category_match_family=category_match_family,
        canonical_category=canonical_category,
        sensitive_remap_families=_SENSITIVE_REMAP_FAMILIES,
    )

    assert matches[0]["picked_row"] is None
    assert matches[0]["match_strategy"] == "unmatched"
