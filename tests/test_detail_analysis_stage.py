from application.details.detail_analysis_stage import prepare_detail_generation_items


def test_prepare_detail_generation_items_restores_crop_url_to_local_crop_path(tmp_path):
    staged = tmp_path / "room.png"
    staged.write_bytes(b"room")
    restored_crop = tmp_path / "restored.png"
    restored_crop.write_bytes(b"crop")
    furniture_data = [
        {"label": "Chair", "target_key": "cart_1", "crop_path": "outputs/stale.png", "crop_url": "https://cdn.example/crop.png", "box_2d": [10, 10, 100, 100]},
    ]
    original = [dict(item) for item in furniture_data]
    localized_refs = []

    def materialize(ref, label):
        assert ref == "https://cdn.example/crop.png"
        return str(restored_crop)

    def detect_item_bbox_norm(staged_path, crop_path, label, **kwargs):
        localized_refs.append(crop_path)
        return [0.2, 0.1, 0.4, 0.3]

    prepared = prepare_detail_generation_items(
        furniture_data=furniture_data,
        moodboard_url=None,
        local_path=str(staged),
        materialize_input=materialize,
        detect_furniture_boxes=lambda path: [],
        canonical_category=lambda label: "chair",
        build_item_target_key=lambda *args, **kwargs: "detail_1",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: items,
        normalize_label_for_match=lambda label: label.lower(),
        detect_item_bbox_norm=detect_item_bbox_norm,
    )

    assert prepared[0]["crop_path"] == str(restored_crop)
    assert prepared[0]["crop_url"] == "https://cdn.example/crop.png"
    assert prepared[0]["box_source"] == "product_reference_localization"
    assert localized_refs == [str(restored_crop)]
    assert furniture_data == original


def test_prepare_detail_generation_items_keeps_metadata_when_crop_url_materialization_fails(tmp_path):
    staged = tmp_path / "room.png"
    staged.write_bytes(b"room")
    furniture_data = [
        {"label": "Chair", "target_key": "cart_1", "crop_path": "outputs/stale.png", "crop_url": "https://cdn.example/missing.png", "box_2d": [10, 10, 100, 100]},
    ]

    prepared = prepare_detail_generation_items(
        furniture_data=furniture_data,
        moodboard_url=None,
        local_path=str(staged),
        materialize_input=lambda ref, label: None,
        detect_furniture_boxes=lambda path: [],
        canonical_category=lambda label: "chair",
        build_item_target_key=lambda *args, **kwargs: "detail_1",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: items,
        normalize_label_for_match=lambda label: label.lower(),
        detect_item_bbox_norm=lambda *args, **kwargs: None,
    )

    assert prepared[0]["crop_path"] == "outputs/stale.png"
    assert prepared[0]["crop_url"] == "https://cdn.example/missing.png"
    assert prepared[0]["detail_localization_status"] == "unverified"


def test_prepare_detail_generation_items_reuses_valid_local_crop_without_downloading(tmp_path):
    staged = tmp_path / "room.png"
    staged.write_bytes(b"room")
    local_crop = tmp_path / "local.png"
    local_crop.write_bytes(b"crop")
    materialize_calls = []

    prepared = prepare_detail_generation_items(
        furniture_data=[
            {
                "label": "Chair",
                "target_key": "cart_1",
                "crop_path": str(local_crop),
                "crop_url": "https://cdn.example/crop.png",
                "box_2d": [10, 10, 100, 100],
            },
        ],
        moodboard_url=None,
        local_path=str(staged),
        materialize_input=lambda ref, label: materialize_calls.append((ref, label)),
        detect_furniture_boxes=lambda path: [],
        canonical_category=lambda label: "chair",
        build_item_target_key=lambda *args, **kwargs: "detail_1",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: items,
        normalize_label_for_match=lambda label: label.lower(),
        detect_item_bbox_norm=lambda staged_path, crop_path, label, **kwargs: [0.2, 0.1, 0.4, 0.3],
    )

    assert prepared[0]["crop_path"] == str(local_crop)
    assert prepared[0]["crop_url"] == "https://cdn.example/crop.png"
    assert materialize_calls == []
