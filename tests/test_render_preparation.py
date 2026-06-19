from application.render.render_preparation import collect_local_moodboard_items


def test_collect_local_moodboard_items_preserves_external_cart_category_metadata(tmp_path):
    local_item = tmp_path / "mood_item.png"
    local_item.write_bytes(b"image")

    items = [
        {
            "label": "몬타나 프리 333000",
            "path": "https://example.com/montana.png",
            "qty": 1,
            "category": "decor",
            "category_path": "수납·선반장 > 일반수납장",
            "mainCategory": "수납·선반장",
            "subCategory": "일반수납장",
            "product_type": "수납장",
            "target_key": "cart_39555_몬타나-프리-333000_001",
        }
    ]

    local_items, cleanup_paths, cleanup_sources = collect_local_moodboard_items(
        items,
        materialize_input=lambda source, prefix: str(local_item),
    )

    assert cleanup_paths == [str(local_item)]
    assert cleanup_sources == []
    assert len(local_items) == 1
    prepared = local_items[0]
    assert prepared["category"] == "decor"
    assert prepared["category_path"] == "수납·선반장 > 일반수납장"
    assert prepared["mainCategory"] == "수납·선반장"
    assert prepared["subCategory"] == "일반수납장"
    assert prepared["product_type"] == "수납장"
