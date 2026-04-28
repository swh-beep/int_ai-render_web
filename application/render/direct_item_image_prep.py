from __future__ import annotations

import os
import statistics
from collections import deque
from typing import NamedTuple

from PIL import Image, ImageOps


class _BackgroundDetection(NamedTuple):
    confident: bool
    bg_color: tuple[int, int, int]
    threshold: int


def prepare_direct_item_image(local_path: str, *, output_path: str, max_size: int = 1024) -> str | None:
    if not local_path or not os.path.exists(local_path):
        return None

    try:
        with Image.open(local_path) as opened:
            image = ImageOps.exif_transpose(opened)
            prepared = _prepare_direct_item_image(image, max_size=max_size)
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            prepared.save(output_path, format="PNG")
            prepared.close()
        return output_path
    except Exception:
        return None


def _prepare_direct_item_image(image: Image.Image, *, max_size: int) -> Image.Image:
    working = image.copy()

    alpha_bbox = _meaningful_alpha_bbox(working)
    if alpha_bbox is not None:
        rgba = working.convert("RGBA")
        cropped = _crop_with_padding(rgba, alpha_bbox)
        return _resize_for_output(cropped, max_size=max_size)

    rgb = working.convert("RGB")
    detection = _detect_simple_border_background(rgb)
    if detection.confident:
        cutout = _cutout_simple_background(rgb, detection.bg_color, detection.threshold)
        object_bbox = _meaningful_alpha_bbox(cutout)
        if object_bbox is not None:
            cropped = _crop_with_padding(cutout, object_bbox)
            return _resize_for_output(cropped, max_size=max_size)
        cutout.close()

    return _resize_for_output(rgb, max_size=max_size)


def _resize_for_output(image: Image.Image, *, max_size: int) -> Image.Image:
    result = image.copy()
    if max(result.size) > max_size:
        result.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return result


def _meaningful_alpha_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    if "A" not in image.getbands():
        return None

    alpha = image.getchannel("A")
    min_alpha, max_alpha = alpha.getextrema()
    if min_alpha >= 250 or max_alpha <= 0:
        return None

    bbox = alpha.point(lambda value: 255 if value > 8 else 0, mode="L").getbbox()
    if bbox is None:
        return None

    return bbox


def _crop_with_padding(image: Image.Image, bbox: tuple[int, int, int, int], *, padding_ratio: float = 0.12) -> Image.Image:
    left, top, right, bottom = bbox
    width, height = image.size
    box_width = max(1, right - left)
    box_height = max(1, bottom - top)
    pad_x = max(4, int(box_width * padding_ratio))
    pad_y = max(4, int(box_height * padding_ratio))
    padded_box = (
        max(0, left - pad_x),
        max(0, top - pad_y),
        min(width, right + pad_x),
        min(height, bottom + pad_y),
    )
    return image.crop(padded_box)


def _detect_simple_border_background(image: Image.Image) -> _BackgroundDetection:
    probe = image.copy()
    probe.thumbnail((160, 160), Image.Resampling.BILINEAR)

    width, height = probe.size
    if width < 16 or height < 16:
        return _BackgroundDetection(False, (0, 0, 0), 0)

    border_pixels = _collect_border_pixels(probe)
    if len(border_pixels) < (width + height):
        return _BackgroundDetection(False, (0, 0, 0), 0)

    bg_color = tuple(int(statistics.median(channel)) for channel in zip(*border_pixels))
    threshold = 42

    border_match_ratio = sum(1 for pixel in border_pixels if _color_distance(pixel, bg_color) <= threshold) / len(border_pixels)
    corners = [
        probe.getpixel((0, 0)),
        probe.getpixel((width - 1, 0)),
        probe.getpixel((0, height - 1)),
        probe.getpixel((width - 1, height - 1)),
    ]
    corner_match_ratio = sum(1 for pixel in corners if _color_distance(pixel, bg_color) <= threshold) / len(corners)

    if border_match_ratio < 0.9 or corner_match_ratio < 1.0:
        return _BackgroundDetection(False, bg_color, threshold)

    background_mask = _flood_fill_background(probe, bg_color, threshold)
    total_pixels = width * height
    background_pixels = sum(background_mask)
    object_pixels = total_pixels - background_pixels
    if background_pixels < int(total_pixels * 0.35):
        return _BackgroundDetection(False, bg_color, threshold)
    if object_pixels < int(total_pixels * 0.02) or object_pixels > int(total_pixels * 0.7):
        return _BackgroundDetection(False, bg_color, threshold)

    bbox = _mask_bbox(background_mask, width, height, target_value=0)
    if bbox is None:
        return _BackgroundDetection(False, bg_color, threshold)

    left, top, right, bottom = bbox
    bbox_width = right - left
    bbox_height = bottom - top
    if bbox_width >= int(width * 0.94) or bbox_height >= int(height * 0.94):
        return _BackgroundDetection(False, bg_color, threshold)

    border_object_touch = _object_border_touch_ratio(background_mask, width, height)
    if border_object_touch > 0.08:
        return _BackgroundDetection(False, bg_color, threshold)

    return _BackgroundDetection(True, bg_color, threshold)


def _collect_border_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    width, height = image.size
    pixels = []
    for x in range(width):
        pixels.append(image.getpixel((x, 0)))
        pixels.append(image.getpixel((x, height - 1)))
    for y in range(1, height - 1):
        pixels.append(image.getpixel((0, y)))
        pixels.append(image.getpixel((width - 1, y)))
    return pixels


def _flood_fill_background(image: Image.Image, bg_color: tuple[int, int, int], threshold: int) -> list[int]:
    width, height = image.size
    pixels = image.load()
    mask = [0] * (width * height)
    queue: deque[tuple[int, int]] = deque()

    def _mark_if_background(x: int, y: int) -> None:
        idx = y * width + x
        if mask[idx]:
            return
        if _color_distance(pixels[x, y], bg_color) > threshold:
            return
        mask[idx] = 1
        queue.append((x, y))

    for x in range(width):
        _mark_if_background(x, 0)
        _mark_if_background(x, height - 1)
    for y in range(height):
        _mark_if_background(0, y)
        _mark_if_background(width - 1, y)

    while queue:
        x, y = queue.popleft()
        for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if next_x < 0 or next_y < 0 or next_x >= width or next_y >= height:
                continue
            _mark_if_background(next_x, next_y)

    return mask


def _cutout_simple_background(image: Image.Image, bg_color: tuple[int, int, int], threshold: int) -> Image.Image:
    rgba = image.convert("RGBA")
    mask = _flood_fill_background(image, bg_color, threshold)
    alpha = bytearray(255 if value == 0 else 0 for value in mask)
    alpha_image = Image.frombytes("L", image.size, bytes(alpha))
    rgba.putalpha(alpha_image)
    return rgba


def _mask_bbox(mask: list[int], width: int, height: int, *, target_value: int) -> tuple[int, int, int, int] | None:
    left = width
    top = height
    right = -1
    bottom = -1

    for y in range(height):
        row_offset = y * width
        for x in range(width):
            if mask[row_offset + x] != target_value:
                continue
            if x < left:
                left = x
            if y < top:
                top = y
            if x > right:
                right = x
            if y > bottom:
                bottom = y

    if right < left or bottom < top:
        return None

    return left, top, right + 1, bottom + 1


def _object_border_touch_ratio(mask: list[int], width: int, height: int) -> float:
    border_count = 0
    object_count = 0

    for x in range(width):
        border_count += 2
        if mask[x] == 0:
            object_count += 1
        if mask[(height - 1) * width + x] == 0:
            object_count += 1

    for y in range(1, height - 1):
        border_count += 2
        if mask[y * width] == 0:
            object_count += 1
        if mask[y * width + (width - 1)] == 0:
            object_count += 1

    return object_count / max(1, border_count)


def _color_distance(pixel: tuple[int, int, int], bg_color: tuple[int, int, int]) -> int:
    return sum(abs(int(pixel[index]) - int(bg_color[index])) for index in range(3))
