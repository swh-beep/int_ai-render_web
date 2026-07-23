from application.details.regenerate_detail_workflow import run_regenerate_single_detail_job


def test_run_regenerate_single_detail_job_restores_cached_crop_url_before_generation(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"room")
    restored_crop = tmp_path / "restored.png"
    restored_crop.write_bytes(b"crop")
    generated = tmp_path / "detail.png"
    generated.write_bytes(b"detail")
    seen_items = []
    furniture_data = [
        {"label": "Chair", "target_key": "cart_1", "crop_path": "outputs/stale.png", "crop_url": "https://cdn.example/crop.png", "box_2d": [10, 10, 100, 100]},
    ]
    original = [dict(item) for item in furniture_data]

    def materialize(ref, label):
        if ref == "https://cdn.example/source.png":
            return str(source)
        if ref == "https://cdn.example/crop.png":
            return str(restored_crop)
        return None

    def construct_dynamic_styles(items):
        return [{"name": "Detail: Chair", "target_key": "cart_1", "target_label": "Chair", "ratio": "1:1"}]

    def generate_detail_view(local_path, style, unique_id, index, analyzed_items, prefer_crop_extract=False):
        seen_items.extend([dict(item) for item in analyzed_items])
        return {"path": str(generated), "style_name": style["name"]}

    result = run_regenerate_single_detail_job(
        {
            "original_image_url": "https://cdn.example/source.png",
            "style_index": 1,
            "target_key": "cart_1",
            "furniture_data": furniture_data,
            "audience": "external",
        },
        normalize_audience=lambda audience: audience or "external",
        build_s3_prefix=lambda audience, route, kind: f"{audience}/{route}/{kind}",
        materialize_input=materialize,
        resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/out/{path}",
        detect_furniture_boxes=lambda path: [],
        canonical_category=lambda label: "chair",
        build_item_target_key=lambda *args, **kwargs: "detail_1",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: items,
        construct_dynamic_styles=construct_dynamic_styles,
        normalize_label_for_match=lambda label: str(label or "").lower(),
        generate_detail_view=generate_detail_view,
        volume_ranking_snapshot=lambda items: [],
    )

    assert result["message"] == "Success"
    assert seen_items[0]["crop_path"] == str(restored_crop)
    assert seen_items[0]["crop_url"] == "https://cdn.example/crop.png"
    assert result["furniture_data"][0]["crop_path"] == str(restored_crop)
    assert furniture_data == original


def test_run_regenerate_single_detail_job_reuses_valid_local_crop_without_downloading(tmp_path):
    source = tmp_path / "source.png"
    source.write_bytes(b"room")
    local_crop = tmp_path / "local.png"
    local_crop.write_bytes(b"crop")
    generated = tmp_path / "detail.png"
    generated.write_bytes(b"detail")
    materialize_calls = []
    seen_items = []

    def materialize(ref, label):
        materialize_calls.append((ref, label))
        if ref == "https://cdn.example/source.png":
            return str(source)
        return None

    result = run_regenerate_single_detail_job(
        {
            "original_image_url": "https://cdn.example/source.png",
            "style_index": 1,
            "target_key": "cart_1",
            "furniture_data": [
                {
                    "label": "Chair",
                    "target_key": "cart_1",
                    "crop_path": str(local_crop),
                    "crop_url": "https://cdn.example/crop.png",
                    "box_2d": [10, 10, 100, 100],
                },
            ],
            "audience": "external",
        },
        normalize_audience=lambda audience: audience or "external",
        build_s3_prefix=lambda audience, route, kind: f"{audience}/{route}/{kind}",
        materialize_input=materialize,
        resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/out/{path}",
        detect_furniture_boxes=lambda path: [],
        canonical_category=lambda label: "chair",
        build_item_target_key=lambda *args, **kwargs: "detail_1",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: items,
        construct_dynamic_styles=lambda items: [
            {
                "name": "Detail: Chair",
                "target_key": "cart_1",
                "target_label": "Chair",
                "ratio": "1:1",
            },
        ],
        normalize_label_for_match=lambda label: str(label or "").lower(),
        generate_detail_view=lambda local_path, style, unique_id, index, analyzed_items, prefer_crop_extract=False: (
            seen_items.extend([dict(item) for item in analyzed_items])
            or {"path": str(generated), "style_name": style["name"]}
        ),
        volume_ranking_snapshot=lambda items: [],
    )

    assert result["message"] == "Success"
    assert seen_items[0]["crop_path"] == str(local_crop)
    assert materialize_calls == [("https://cdn.example/source.png", "detail_src")]
