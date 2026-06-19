from pathlib import Path

from PIL import Image

from application.render.furnished_generation_stage import (
    _build_grouped_small_item_sheet_reference,
    _select_grouped_small_item_sheet_items,
    _split_cutout_reference_items_for_generation,
)


def _item(key, *, main_category, dims_sum, crop_path="crop.png"):
    width = max(1, dims_sum // 3)
    depth = max(1, dims_sum // 3)
    height = max(1, dims_sum - width - depth)
    return {
        "target_key": key,
        "label": key,
        "mainCategory": main_category,
        "category": "decor" if main_category == "소품" else "table_lamp",
        "dims_mm": {
            "width_mm": width,
            "depth_mm": depth,
            "height_mm": height,
        },
        "crop_path": crop_path,
    }


def test_grouped_small_item_sheet_prefers_props_before_smaller_non_props():
    items = [
        _item("sofa", main_category="소파", dims_sum=9000),
        _item("small_lamp_1", main_category="조명", dims_sum=300),
        _item("small_lamp_2", main_category="조명", dims_sum=350),
        _item("prop_large_plant", main_category="소품", dims_sum=2000),
        _item("prop_large_frame_1", main_category="소품", dims_sum=1500),
        _item("prop_large_frame_2", main_category="소품", dims_sum=1800),
    ]

    selected = _select_grouped_small_item_sheet_items(items, sheet_count=4)

    assert [item["target_key"] for item in selected] == [
        "prop_large_frame_1",
        "prop_large_frame_2",
        "prop_large_plant",
        "small_lamp_1",
    ]


def test_grouped_small_item_sheet_reference_labels_each_item(tmp_path: Path):
    paths = []
    for index, color in enumerate(["red", "blue"], start=1):
        path = tmp_path / f"item_{index}.png"
        Image.new("RGBA", (80, 60), color).save(path)
        paths.append(path)

    items = [
        _item("prop_vase", main_category="소품", dims_sum=450, crop_path=str(paths[0])),
        _item("prop_frame", main_category="소품", dims_sum=900, crop_path=str(paths[1])),
    ]

    header, sheet = _build_grouped_small_item_sheet_reference(items)

    assert sheet.size == (2048, 2048)
    assert "S1 = prop_vase" in header
    assert "S2 = prop_frame" in header
    assert "Use S1-S2 as individual purchasable items" in header
    assert "W=150mm D=150mm H=150mm" in header


def test_twenty_cutouts_fit_gemini_image_prompt_limit():
    items = []
    for index in range(20):
        main_category = "소품" if index >= 12 else "가구"
        items.append(
            _item(
                f"item_{index:02d}",
                main_category=main_category,
                dims_sum=100 + index,
            )
            | {"priority": index}
        )

    direct_items, sheet_items = _split_cutout_reference_items_for_generation(
        items,
        direct_sort_key=lambda item: item["priority"],
    )

    assert len(direct_items) == 12
    assert len(sheet_items) == 8
    assert 1 + len(direct_items) + 1 == 14
