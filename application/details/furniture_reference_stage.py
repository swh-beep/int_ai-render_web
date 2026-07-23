from __future__ import annotations

import math
import os
from collections import deque
from statistics import median
from dataclasses import dataclass
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageStat


_WORK_MAX_DIMENSION = 1536
_MIN_COMPONENT_AREA_FRACTION = 0.0015
_MAX_COMPONENT_AREA_FRACTION = 0.55
_EDGE_MARGIN_FRACTION = 0.018
_LINE_ASPECT_LIMIT = 12.0
_NEUTRAL_BG = (238, 236, 232)


@dataclass(frozen=True)
class _Component:
    bounds: tuple[int, int, int, int]
    pixels: tuple[tuple[int, int], ...]
    area: int


def build_furniture_only_reference_atlas(
    furnished_main_path: str | None,
    empty_room_path: str | None,
    output_path: str | None,
    *,
    item_boxes: Iterable[dict] | None = None,
    max_work_dimension: int = _WORK_MAX_DIMENSION,
) -> str | None:
    """Build a camera-neutral furniture atlas from a furnished room image.

    Valid detected object boxes are preferred because exact source-pixel crops preserve
    light-colored furniture that can disappear in an image-difference mask. If no usable
    boxes are available, aligned furnished/empty-room difference components are used as
    a fallback. In both cases the result is a shuffled contact sheet rather than a full
    room, so downstream generation does not receive the source camera as an authority.
    """

    if not furnished_main_path or not empty_room_path or not output_path:
        return None
    if not os.path.exists(furnished_main_path) or not os.path.exists(empty_room_path):
        return None

    try:
        furnished_full = _load_rgb(furnished_main_path)
        # Crop before downscaling the complete 4K render so small objects retain
        # their original source detail inside each atlas tile.
        box_tiles = _build_box_reference_tiles(furnished_full, item_boxes)
        if box_tiles:
            atlas = _pack_tiles(box_tiles)
            if atlas is None:
                return None
            parent = os.path.dirname(os.path.abspath(output_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            atlas.save(output_path, format="JPEG", quality=92, optimize=True)
            return output_path

        furnished = _cap_rgb_image(furnished_full, max_work_dimension)
        empty = _load_rgb_capped(empty_room_path, max_work_dimension)
        if furnished.size != empty.size:
            empty = empty.resize(furnished.size, Image.Resampling.LANCZOS)

        aligned_empty = _align_empty_to_furnished(empty, furnished)
        mask = _difference_mask(furnished, aligned_empty)
        components = _extract_non_architecture_components(mask, furnished.size)
        if not components:
            return None

        atlas = _pack_components(furnished, mask, components)
        if atlas is None:
            return None

        parent = os.path.dirname(os.path.abspath(output_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        atlas.save(output_path, format="JPEG", quality=92, optimize=True)
        return output_path
    except Exception:
        return None


def _load_rgb_capped(path: str, max_dimension: int) -> Image.Image:
    return _cap_rgb_image(_load_rgb(path), max_dimension)


def _load_rgb(path: str) -> Image.Image:
    with Image.open(path) as opened:
        return ImageOps.exif_transpose(opened).convert("RGB")


def _cap_rgb_image(img: Image.Image, max_dimension: int) -> Image.Image:
    cap = max(1, int(max_dimension or _WORK_MAX_DIMENSION))
    if max(img.size) > cap:
        img = img.copy()
        img.thumbnail((cap, cap), Image.Resampling.LANCZOS)
    return img


def _align_empty_to_furnished(empty: Image.Image, furnished: Image.Image) -> Image.Image:
    empty_small = empty.resize((96, max(1, round(96 * empty.height / empty.width))), Image.Resampling.BILINEAR)
    furnished_small = furnished.resize(empty_small.size, Image.Resampling.BILINEAR)
    offsets: list[int] = []
    for channel in range(3):
        empty_values = list(empty_small.getchannel(channel).getdata())
        furnished_values = list(furnished_small.getchannel(channel).getdata())
        offsets.append(int(round(median(furnished_values) - median(empty_values))))

    channels = []
    for channel, offset in zip(empty.split(), offsets, strict=True):
        channels.append(channel.point(lambda value, delta=offset: max(0, min(255, int(value) + delta))))
    return Image.merge("RGB", channels)


def _difference_mask(furnished: Image.Image, aligned_empty: Image.Image) -> Image.Image:
    diff = ImageChops.difference(furnished, aligned_empty).convert("L")
    blurred = diff.filter(ImageFilter.GaussianBlur(radius=1.2))
    stat = ImageStat.Stat(blurred)
    mean = float(stat.mean[0])
    stddev = float(stat.stddev[0])
    threshold = _percentile(blurred.getdata(), 86)
    threshold = max(26, min(82, int(round(max(threshold, mean + stddev * 0.85)))))
    mask = blurred.point(lambda value: 255 if value >= threshold else 0)
    mask = mask.filter(ImageFilter.MaxFilter(7))
    mask = mask.filter(ImageFilter.MinFilter(5))
    return mask


def _percentile(values: Iterable[int], pct: int) -> int:
    ordered = sorted(int(value) for value in values)
    if not ordered:
        return 0
    index = int(round((max(0, min(100, pct)) / 100.0) * (len(ordered) - 1)))
    return ordered[index]


def _extract_non_architecture_components(mask: Image.Image, size: tuple[int, int]) -> list[_Component]:
    width, height = size
    working_mask = _remove_border_band(mask, size)
    data = working_mask.load()
    visited: set[tuple[int, int]] = set()
    min_area = max(24, int(width * height * _MIN_COMPONENT_AREA_FRACTION))
    max_area = int(width * height * _MAX_COMPONENT_AREA_FRACTION)
    edge_margin_x = max(2, int(width * _EDGE_MARGIN_FRACTION))
    edge_margin_y = max(2, int(height * _EDGE_MARGIN_FRACTION))
    components: list[_Component] = []

    for y in range(height):
        for x in range(width):
            if data[x, y] == 0 or (x, y) in visited:
                continue
            bounds, area, touches_edge, pixels = _flood_component(
                data,
                visited,
                x,
                y,
                width,
                height,
                edge_margin_x,
                edge_margin_y,
            )
            if area < min_area or area > max_area:
                continue
            left, top, right, bottom = bounds
            bw = right - left + 1
            bh = bottom - top + 1
            aspect = max(bw / max(1, bh), bh / max(1, bw))
            fill_ratio = area / max(1, bw * bh)
            if touches_edge and (aspect > 5.0 or fill_ratio < 0.18):
                continue
            if aspect > _LINE_ASPECT_LIMIT and fill_ratio < 0.28:
                continue
            components.append(_Component((left, top, right + 1, bottom + 1), tuple(pixels), area))

    components.sort(key=lambda row: row.area, reverse=True)
    return components[:12]


def _remove_border_band(mask: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    cleaned = mask.copy()
    draw = ImageDraw.Draw(cleaned)
    band_x = max(2, int(width * _EDGE_MARGIN_FRACTION))
    band_y = max(2, int(height * _EDGE_MARGIN_FRACTION))
    draw.rectangle((0, 0, width - 1, band_y), fill=0)
    draw.rectangle((0, height - band_y - 1, width - 1, height - 1), fill=0)
    draw.rectangle((0, 0, band_x, height - 1), fill=0)
    draw.rectangle((width - band_x - 1, 0, width - 1, height - 1), fill=0)
    return cleaned


def _flood_component(
    data,
    visited: set[tuple[int, int]],
    start_x: int,
    start_y: int,
    width: int,
    height: int,
    edge_margin_x: int,
    edge_margin_y: int,
) -> tuple[tuple[int, int, int, int], int, bool, list[tuple[int, int]]]:
    queue = deque([(start_x, start_y)])
    visited.add((start_x, start_y))
    left = right = start_x
    top = bottom = start_y
    area = 0
    touches_edge = False
    pixels: list[tuple[int, int]] = []

    while queue:
        x, y = queue.popleft()
        area += 1
        pixels.append((x, y))
        left = min(left, x)
        right = max(right, x)
        top = min(top, y)
        bottom = max(bottom, y)
        if x <= edge_margin_x or y <= edge_margin_y or x >= width - edge_margin_x - 1 or y >= height - edge_margin_y - 1:
            touches_edge = True
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            if data[nx, ny] == 0 or (nx, ny) in visited:
                continue
            visited.add((nx, ny))
            queue.append((nx, ny))

    return (left, top, right, bottom), area, touches_edge, pixels


def _pack_components(
    furnished: Image.Image,
    mask: Image.Image,
    components: list[_Component],
) -> Image.Image | None:
    tiles: list[Image.Image] = []
    for component in components:
        left, top, right, bottom = _expand_bounds(component.bounds, furnished.size, padding_fraction=0.10)
        crop = furnished.crop((left, top, right, bottom))
        component_mask = Image.new("L", furnished.size, 0)
        component_mask_data = component_mask.load()
        for x, y in component.pixels:
            component_mask_data[x, y] = 255
        crop_mask = component_mask.crop((left, top, right, bottom)).filter(ImageFilter.GaussianBlur(radius=1.0))
        tile = Image.new("RGB", crop.size, _NEUTRAL_BG)
        tile.paste(crop, (0, 0), crop_mask)
        tile.thumbnail((480, 360), Image.Resampling.LANCZOS)
        if tile.width < 20 or tile.height < 20:
            continue
        tiles.append(tile)

    return _pack_tiles(tiles)


def _build_box_reference_tiles(
    furnished: Image.Image,
    item_boxes: Iterable[dict] | None,
) -> list[Image.Image]:
    candidates = _select_reference_boxes(item_boxes)
    width, height = furnished.size
    tiles: list[Image.Image] = []
    for _area, (ymin, xmin, ymax, xmax) in candidates[:16]:
        left = int(round(xmin * width))
        top = int(round(ymin * height))
        right = int(round(xmax * width))
        bottom = int(round(ymax * height))
        box_width = max(1, right - left)
        box_height = max(1, bottom - top)
        pad_x = max(4, int(round(box_width * 0.08)))
        pad_y = max(4, int(round(box_height * 0.08)))
        left = max(0, left - pad_x)
        top = max(0, top - pad_y)
        right = min(width, right + pad_x)
        bottom = min(height, bottom + pad_y)
        if right - left < 20 or bottom - top < 20:
            continue
        tile = furnished.crop((left, top, right, bottom))
        tile.thumbnail((480, 360), Image.Resampling.LANCZOS)
        if tile.width >= 20 and tile.height >= 20:
            tiles.append(tile)
    return tiles


def count_usable_furniture_reference_boxes(
    item_boxes: Iterable[dict] | None,
) -> int:
    """Return the exact number of object tiles the atlas builder would accept."""

    return len(_select_reference_boxes(item_boxes))


def _select_reference_boxes(
    item_boxes: Iterable[dict] | None,
) -> list[tuple[float, tuple[float, float, float, float]]]:
    candidates: list[tuple[float, tuple[float, float, float, float]]] = []
    for item in item_boxes or []:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_detection_box(item.get("box_2d"))
        if normalized is None:
            continue
        ymin, xmin, ymax, xmax = normalized
        area = (ymax - ymin) * (xmax - xmin)
        # Full-frame source-reference placeholders are not object detections and
        # would recreate the very camera authority this atlas is meant to remove.
        if area < 0.0002 or area > 0.72:
            continue
        if any(
            _normalized_box_iou(normalized, existing) >= 0.72
            for _, existing in candidates
        ):
            continue
        candidates.append((area, normalized))

    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates[:16]


def _normalize_detection_box(raw_box) -> tuple[float, float, float, float] | None:
    if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
        return None
    try:
        values = [float(value) for value in raw_box]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    scale = 1000.0 if max(abs(value) for value in values) > 1.0 else 1.0
    ymin, xmin, ymax, xmax = [value / scale for value in values]
    ymin = max(0.0, min(1.0, ymin))
    xmin = max(0.0, min(1.0, xmin))
    ymax = max(0.0, min(1.0, ymax))
    xmax = max(0.0, min(1.0, xmax))
    if ymax <= ymin or xmax <= xmin:
        return None
    return ymin, xmin, ymax, xmax


def _normalized_box_iou(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    first_ymin, first_xmin, first_ymax, first_xmax = first
    second_ymin, second_xmin, second_ymax, second_xmax = second
    inter_height = max(0.0, min(first_ymax, second_ymax) - max(first_ymin, second_ymin))
    inter_width = max(0.0, min(first_xmax, second_xmax) - max(first_xmin, second_xmin))
    intersection = inter_height * inter_width
    if intersection <= 0.0:
        return 0.0
    first_area = (first_ymax - first_ymin) * (first_xmax - first_xmin)
    second_area = (second_ymax - second_ymin) * (second_xmax - second_xmin)
    union = first_area + second_area - intersection
    return intersection / union if union > 0.0 else 0.0


def _pack_tiles(tiles: list[Image.Image]) -> Image.Image | None:
    if not tiles:
        return None

    cols = 3 if len(tiles) > 2 else len(tiles)
    tile_w = max(tile.width for tile in tiles)
    tile_h = max(tile.height for tile in tiles)
    gap = 24
    rows = (len(tiles) + cols - 1) // cols
    atlas = Image.new("RGB", (cols * tile_w + (cols + 1) * gap, rows * tile_h + (rows + 1) * gap), _NEUTRAL_BG)
    draw = ImageDraw.Draw(atlas)
    for index, tile in enumerate(tiles):
        col = index % cols
        row = index // cols
        x = gap + col * (tile_w + gap) + (tile_w - tile.width) // 2
        y = gap + row * (tile_h + gap) + (tile_h - tile.height) // 2
        draw.rectangle((x - 2, y - 2, x + tile.width + 1, y + tile.height + 1), outline=(212, 210, 205), width=1)
        atlas.paste(tile, (x, y))
    atlas.thumbnail((_WORK_MAX_DIMENSION, _WORK_MAX_DIMENSION), Image.Resampling.LANCZOS)
    return atlas


def _expand_bounds(
    bounds: tuple[int, int, int, int],
    size: tuple[int, int],
    *,
    padding_fraction: float,
) -> tuple[int, int, int, int]:
    width, height = size
    left, top, right, bottom = bounds
    pad_x = max(4, int((right - left) * padding_fraction))
    pad_y = max(4, int((bottom - top) * padding_fraction))
    return (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    )
