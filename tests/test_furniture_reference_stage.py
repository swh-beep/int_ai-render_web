from pathlib import Path

from PIL import Image, ImageDraw

from application.details import furniture_reference_stage as stage
from application.details.furniture_reference_stage import (
    build_furniture_only_reference_atlas,
    count_usable_furniture_reference_boxes,
)


def _room(path: Path, *, tint=(220, 218, 210), size=(640, 360)):
    img = Image.new("RGB", size, tint)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, size[0] - 1, size[1] - 1), outline=(190, 188, 180), width=3)
    draw.line((0, size[1] * 0.62, size[0], size[1] * 0.62), fill=(200, 197, 190), width=2)
    img.save(path)
    return img


def test_build_furniture_atlas_preserves_central_furniture_and_drops_edge_noise(tmp_path):
    empty_path = tmp_path / "empty.jpg"
    furnished_path = tmp_path / "furnished.jpg"
    output_path = tmp_path / "atlas.jpg"
    empty = _room(empty_path)
    furnished = empty.copy()
    draw = ImageDraw.Draw(furnished)
    draw.rectangle((0, 42, 96, 170), fill=(120, 160, 200))  # edge-connected window/noise
    draw.rectangle((210, 178, 470, 270), fill=(64, 83, 122))  # central sofa
    draw.ellipse((170, 248, 520, 330), fill=(150, 136, 110))  # rug
    furnished.save(furnished_path)

    result = build_furniture_only_reference_atlas(str(furnished_path), str(empty_path), str(output_path))

    assert result == str(output_path)
    with Image.open(output_path) as atlas:
        assert atlas.size[0] <= 1536
        assert atlas.size[1] <= 1536
        colors = atlas.convert("RGB").getcolors(maxcolors=1000000)
        assert colors is not None
        assert any(color[1][0] < 90 and color[1][1] < 110 and color[1][2] < 150 for color in colors)
        assert not any(color[1] == (120, 160, 200) for color in colors)


def test_build_furniture_atlas_breaks_full_room_composition(tmp_path):
    empty_path = tmp_path / "empty.jpg"
    furnished_path = tmp_path / "furnished.jpg"
    output_path = tmp_path / "atlas.jpg"
    empty = _room(empty_path)
    furnished = empty.copy()
    draw = ImageDraw.Draw(furnished)
    draw.rectangle((230, 185, 425, 260), fill=(72, 82, 130))
    furnished.save(furnished_path)

    assert build_furniture_only_reference_atlas(str(furnished_path), str(empty_path), str(output_path))
    with Image.open(output_path) as atlas:
        assert atlas.size != empty.size
        assert atlas.getpixel((4, 4)) != empty.getpixel((4, 4))
        assert all(abs(actual - expected) <= 2 for actual, expected in zip(atlas.getpixel((4, 4)), (238, 236, 232)))


def test_build_furniture_atlas_no_diff_returns_none(tmp_path):
    empty_path = tmp_path / "empty.jpg"
    furnished_path = tmp_path / "furnished.jpg"
    output_path = tmp_path / "atlas.jpg"
    empty = _room(empty_path)
    empty.save(furnished_path)

    assert build_furniture_only_reference_atlas(str(furnished_path), str(empty_path), str(output_path)) is None
    assert not output_path.exists()


def test_build_furniture_atlas_caps_large_input_size(tmp_path):
    empty_path = tmp_path / "empty.jpg"
    furnished_path = tmp_path / "furnished.jpg"
    output_path = tmp_path / "atlas.jpg"
    empty = _room(empty_path, size=(3200, 1800))
    furnished = empty.copy()
    draw = ImageDraw.Draw(furnished)
    draw.rectangle((1200, 850, 2100, 1300), fill=(70, 90, 130))
    furnished.save(furnished_path)

    assert build_furniture_only_reference_atlas(str(furnished_path), str(empty_path), str(output_path))
    with Image.open(output_path) as atlas:
        assert max(atlas.size) <= 1536


def test_build_furniture_atlas_preserves_sofa_connected_to_bottom_shadow(tmp_path):
    empty_path = tmp_path / "empty.jpg"
    furnished_path = tmp_path / "furnished.jpg"
    output_path = tmp_path / "atlas.jpg"
    empty = _room(empty_path)
    furnished = empty.copy()
    draw = ImageDraw.Draw(furnished)
    draw.rectangle((210, 185, 470, 260), fill=(68, 82, 126))
    draw.rectangle((180, 260, 520, 359), fill=(72, 65, 58))  # shadow/rug bridge to bottom edge
    furnished.save(furnished_path)

    assert build_furniture_only_reference_atlas(str(furnished_path), str(empty_path), str(output_path))
    with Image.open(output_path) as atlas:
        colors = atlas.convert("RGB").getcolors(maxcolors=1000000)
        assert colors is not None
        assert any(color[1][0] < 90 and color[1][1] < 105 and color[1][2] < 145 for color in colors)


def test_pack_components_uses_component_mask_not_global_overlapping_pixels():
    furnished = Image.new("RGB", (160, 90), (238, 236, 232))
    draw = ImageDraw.Draw(furnished)
    draw.rectangle((45, 35, 70, 55), fill=(200, 20, 20))
    draw.rectangle((88, 35, 113, 55), fill=(20, 50, 210))
    global_mask = Image.new("L", furnished.size, 0)
    mask_draw = ImageDraw.Draw(global_mask)
    mask_draw.rectangle((45, 35, 70, 55), fill=255)
    mask_draw.rectangle((88, 35, 113, 55), fill=255)
    red_pixels = tuple((x, y) for y in range(35, 56) for x in range(45, 71))
    blue_pixels = tuple((x, y) for y in range(35, 56) for x in range(88, 114))
    components = [
        stage._Component((45, 35, 71, 56), red_pixels, len(red_pixels)),
        stage._Component((88, 35, 114, 56), blue_pixels, len(blue_pixels)),
    ]

    atlas = stage._pack_components(furnished, global_mask, components)

    assert atlas is not None
    colors = atlas.convert("RGB").getcolors(maxcolors=1000000)
    assert colors is not None
    redish = sum(count for count, rgb in colors if rgb[0] > 170 and rgb[1] < 80 and rgb[2] < 80)
    blueish = sum(count for count, rgb in colors if rgb[0] < 80 and rgb[1] < 90 and rgb[2] > 170)
    assert redish > 0
    assert blueish > 0
    assert redish < len(red_pixels) * 1.6
    assert blueish < len(blue_pixels) * 1.6


def test_detected_box_atlas_preserves_light_furniture_missed_by_difference(tmp_path):
    empty_path = tmp_path / "empty.png"
    furnished_path = tmp_path / "furnished.png"
    diff_output_path = tmp_path / "diff-atlas.jpg"
    box_output_path = tmp_path / "box-atlas.jpg"
    empty = _room(empty_path, tint=(244, 242, 236))
    furnished = empty.copy()
    draw = ImageDraw.Draw(furnished)
    # This nearly white sofa is deliberately below the difference-mask floor.
    draw.rounded_rectangle(
        (190, 175, 500, 285),
        radius=24,
        fill=(250, 248, 242),
    )
    furnished.save(furnished_path)

    assert (
        build_furniture_only_reference_atlas(
            str(furnished_path),
            str(empty_path),
            str(diff_output_path),
        )
        is None
    )
    result = build_furniture_only_reference_atlas(
        str(furnished_path),
        str(empty_path),
        str(box_output_path),
        item_boxes=[
            {
                "label": "Ivory Sofa",
                "box_2d": [470, 270, 830, 800],
            }
        ],
    )

    assert result == str(box_output_path)
    with Image.open(box_output_path) as atlas:
        assert atlas.size != furnished.size
        pixels = list(atlas.convert("RGB").getdata())
        assert any(
            red >= 247 and green >= 245 and blue >= 238
            for red, green, blue in pixels
        )


def test_full_frame_placeholder_is_ignored_and_uses_difference_fallback(tmp_path):
    empty_path = tmp_path / "empty.png"
    furnished_path = tmp_path / "furnished.png"
    output_path = tmp_path / "atlas.jpg"
    empty = _room(empty_path)
    furnished = empty.copy()
    ImageDraw.Draw(furnished).rectangle(
        (210, 178, 470, 270),
        fill=(64, 83, 122),
    )
    furnished.save(furnished_path)
    placeholders = [{"label": "Source Reference", "box_2d": [0, 0, 1000, 1000]}]

    assert count_usable_furniture_reference_boxes(placeholders) == 0
    assert build_furniture_only_reference_atlas(
        str(furnished_path),
        str(empty_path),
        str(output_path),
        item_boxes=placeholders,
    )
    with Image.open(output_path) as atlas:
        colors = atlas.convert("RGB").getcolors(maxcolors=1000000)
        assert colors is not None
        assert any(
            red < 90 and green < 110 and blue < 150
            for _count, (red, green, blue) in colors
        )


def test_reference_box_selection_deduplicates_and_caps_at_sixteen():
    boxes = []
    for index in range(20):
        row = index // 5
        col = index % 5
        ymin = 40 + row * 180
        xmin = 40 + col * 180
        boxes.append(
            {
                "label": f"Object {index}",
                "box_2d": [ymin, xmin, ymin + 90, xmin + 90],
            }
        )
    boxes.extend(
        [
            {"label": "Duplicate", "box_2d": [42, 42, 132, 132]},
            {"label": "Full frame", "box_2d": [0, 0, 1000, 1000]},
            {"label": "Invalid", "box_2d": [100, 100]},
            {"label": "NaN", "box_2d": [0, float("nan"), 100, 100]},
            {"label": "Infinity", "box_2d": [0, 0, float("inf"), 100]},
        ]
    )

    assert count_usable_furniture_reference_boxes(boxes) == 16
