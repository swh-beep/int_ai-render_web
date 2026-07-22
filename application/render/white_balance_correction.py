from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops, ImageCms, ImageStat


logger = logging.getLogger(__name__)

_DEADBAND_A = 0.8
_DEADBAND_B = 1.4
_MILD_STRENGTH = 0.75
_SEVERE_STRENGTH = 1.0
_MAX_CORRECTION_A = 18.0
_MAX_CORRECTION_B = 24.0
_SEVERE_CAST_THRESHOLD = 8.0
_TOP_SAMPLE_FRACTION = 0.30
_ANALYSIS_MAX_SIZE = (1024, 576)
_MILD_MIN_NEUTRAL_WEIGHT = 0.22
_SEVERE_MIN_NEUTRAL_WEIGHT = 0.70
_TRANSFORM_STATE = threading.local()


@dataclass(frozen=True)
class WhiteBalanceCorrectionResult:
    path: str
    corrected: bool
    original_path: str
    output_path: str | None = None
    reason: str = ""
    passes: int = 0
    raw_delta_a: float = 0.0
    raw_delta_b: float = 0.0
    final_delta_a: float | None = None
    final_delta_b: float | None = None
    elapsed_ms: int = 0
    diagnostics: dict = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        return self.corrected


@dataclass(frozen=True)
class _NeutralMeasurement:
    a: float
    b: float
    pixel_count: int


def apply_reference_relative_white_balance(
    image_path: str,
    *,
    reference_path: str | None = None,
    empty_room: bool = False,
    enabled: bool | None = None,
    logger_override=None,
) -> WhiteBalanceCorrectionResult:
    """Correct global color cast toward the request's own source-room white balance.

    Empty rooms are corrected only for a strong cast. Furnished main candidates use
    one mild pass, or up to two stronger measured passes for a severe cast. The
    source file is never modified and any processing error falls back to it.
    """

    started_at = time.perf_counter()
    active_logger = logger_override or logger
    original_path = str(image_path)
    stage = "empty" if empty_room else "main"
    if enabled is None:
        enabled = os.getenv("RENDER_WHITE_BALANCE_CORRECTION_ENABLED", "1").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
    if not enabled:
        return _no_change_result(original_path, "disabled", started_at)

    output_path: Path | None = None
    try:
        with Image.open(image_path) as source:
            source_size = source.size
            source_measurement = _measure_top_neutral(source)
        if reference_path:
            with Image.open(reference_path) as reference:
                reference_measurement = _measure_top_neutral(reference)
        else:
            reference_measurement = _NeutralMeasurement(0.0, 0.0, 0)

        raw_delta_a = source_measurement.a - reference_measurement.a
        raw_delta_b = source_measurement.b - reference_measurement.b
        raw_max_axis = max(abs(raw_delta_a), abs(raw_delta_b))
        base_diagnostics = {
            "stage": stage,
            "source_size": source_size,
            "source_neutral_pixels": source_measurement.pixel_count,
            "reference_neutral_pixels": reference_measurement.pixel_count,
            "reference_a": round(reference_measurement.a, 3),
            "reference_b": round(reference_measurement.b, 3),
        }

        if empty_room and raw_max_axis < _SEVERE_CAST_THRESHOLD:
            result = _no_change_result(
                original_path,
                "empty_room_cast_below_threshold",
                started_at,
                raw_delta_a=raw_delta_a,
                raw_delta_b=raw_delta_b,
                diagnostics=base_diagnostics,
            )
            _log_result(active_logger, result, stage)
            return result
        if not empty_room and _within_deadband(raw_delta_a, raw_delta_b):
            result = _no_change_result(
                original_path,
                "within_deadband",
                started_at,
                raw_delta_a=raw_delta_a,
                raw_delta_b=raw_delta_b,
                diagnostics=base_diagnostics,
            )
            _log_result(active_logger, result, stage)
            return result

        severe = raw_max_axis >= _SEVERE_CAST_THRESHOLD
        pass_limit = 2 if severe else 1
        strength = _SEVERE_STRENGTH if severe else _MILD_STRENGTH
        min_neutral_weight = _SEVERE_MIN_NEUTRAL_WEIGHT if severe else _MILD_MIN_NEUTRAL_WEIGHT
        with Image.open(image_path) as source:
            current_rgb = source.convert("RGB")

        passes = 0
        try:
            for _pass_index in range(pass_limit):
                current_measurement = _measure_top_neutral(current_rgb)
                delta_a = current_measurement.a - reference_measurement.a
                delta_b = current_measurement.b - reference_measurement.b
                offset_a = _effective_offset(delta_a, _DEADBAND_A, _MAX_CORRECTION_A) * strength
                offset_b = _effective_offset(delta_b, _DEADBAND_B, _MAX_CORRECTION_B) * strength
                if abs(offset_a) < 0.5 and abs(offset_b) < 0.5:
                    break
                corrected_rgb = _shift_lab_ab(
                    current_rgb,
                    offset_a=offset_a,
                    offset_b=offset_b,
                    min_neutral_weight=min_neutral_weight,
                )
                current_rgb.close()
                current_rgb = corrected_rgb
                passes += 1

            if passes == 0:
                result = _no_change_result(
                    original_path,
                    "within_output_quantization",
                    started_at,
                    raw_delta_a=raw_delta_a,
                    raw_delta_b=raw_delta_b,
                    diagnostics=base_diagnostics,
                )
                _log_result(active_logger, result, stage)
                return result

            output_path = _corrected_sibling_path(Path(image_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            current_rgb.save(output_path, format="JPEG", quality=97, subsampling=0)
        finally:
            current_rgb.close()

        with Image.open(output_path) as corrected_image:
            if corrected_image.size != source_size:
                raise ValueError(f"corrected image size changed: {corrected_image.size} != {source_size}")
            final_measurement = _measure_top_neutral(corrected_image)
        final_delta_a = final_measurement.a - reference_measurement.a
        final_delta_b = final_measurement.b - reference_measurement.b
        before_error = math.hypot(raw_delta_a, raw_delta_b)
        after_error = math.hypot(final_delta_a, final_delta_b)
        if after_error > before_error + 0.25:
            output_path.unlink(missing_ok=True)
            result = _no_change_result(
                original_path,
                "correction_not_improved",
                started_at,
                raw_delta_a=raw_delta_a,
                raw_delta_b=raw_delta_b,
                diagnostics={**base_diagnostics, "candidate_error_after": round(after_error, 3)},
            )
            _log_result(active_logger, result, stage)
            return result

        result = WhiteBalanceCorrectionResult(
            path=str(output_path),
            corrected=True,
            original_path=original_path,
            output_path=str(output_path),
            reason="corrected",
            passes=passes,
            raw_delta_a=raw_delta_a,
            raw_delta_b=raw_delta_b,
            final_delta_a=final_delta_a,
            final_delta_b=final_delta_b,
            elapsed_ms=_elapsed_ms(started_at),
            diagnostics={
                **base_diagnostics,
                "severe": severe,
                "minimum_neutral_weight": min_neutral_weight,
                "before_error": round(before_error, 3),
                "after_error": round(after_error, 3),
            },
        )
        _log_result(active_logger, result, stage)
        return result
    except Exception as exc:
        if output_path is not None:
            try:
                output_path.unlink(missing_ok=True)
            except Exception:
                pass
        result = WhiteBalanceCorrectionResult(
            path=original_path,
            corrected=False,
            original_path=original_path,
            reason="error",
            elapsed_ms=_elapsed_ms(started_at),
            diagnostics={"stage": stage, "error": str(exc)},
        )
        try:
            active_logger.warning(
                "[WhiteBalance] stage=%s action=fallback path=%s elapsed_ms=%d error=%s",
                stage,
                Path(original_path).name,
                result.elapsed_ms,
                exc,
            )
        except Exception:
            pass
        return result


def _measure_top_neutral_ab(image: Image.Image) -> tuple[float, float]:
    measurement = _measure_top_neutral(image)
    return measurement.a, measurement.b


def _measure_top_neutral(image: Image.Image) -> _NeutralMeasurement:
    sample = image.convert("RGB")
    sample.thumbnail(_ANALYSIS_MAX_SIZE, Image.Resampling.LANCZOS)
    sample_height = max(1, int(round(sample.height * _TOP_SAMPLE_FRACTION)))
    top = sample.crop((0, 0, sample.width, sample_height))
    sample.close()

    lab = _rgb_to_lab(top)
    hsv = top.convert("HSV")
    top.close()
    l_channel, a_channel, b_channel = lab.split()
    lab.close()
    saturation = hsv.getchannel("S")
    hsv.close()
    try:
        mask = _neutral_mask(saturation, l_channel, saturation_limit=0.30, min_l=24.0, max_l=98.5)
        required_pixels = min(250, max(12, int(mask.width * mask.height * 0.01)))
        pixel_count = _mask_pixel_count(mask)
        if pixel_count < required_pixels:
            mask.close()
            mask = _neutral_mask(saturation, l_channel, saturation_limit=0.45, min_l=18.0, max_l=99.5)
            pixel_count = _mask_pixel_count(mask)
        if pixel_count < max(6, required_pixels // 2):
            mask.close()
            mask = l_channel.point(_range_mask_lut(12.0, 99.8))
            pixel_count = _mask_pixel_count(mask)
        if pixel_count <= 0:
            mask.close()
            mask = Image.new("L", l_channel.size, 255)
            pixel_count = mask.width * mask.height
        a_value = float(ImageStat.Stat(a_channel, mask=mask).median[0]) - 128.0
        b_value = float(ImageStat.Stat(b_channel, mask=mask).median[0]) - 128.0
        mask.close()
        return _NeutralMeasurement(a=a_value, b=b_value, pixel_count=pixel_count)
    finally:
        saturation.close()
        l_channel.close()
        a_channel.close()
        b_channel.close()


def _neutral_mask(
    saturation: Image.Image,
    lightness: Image.Image,
    *,
    saturation_limit: float,
    min_l: float,
    max_l: float,
) -> Image.Image:
    saturation_mask = saturation.point(
        [255 if value < round(255 * saturation_limit) else 0 for value in range(256)]
    )
    lightness_mask = lightness.point(_range_mask_lut(min_l, max_l))
    try:
        return ImageChops.multiply(saturation_mask, lightness_mask)
    finally:
        saturation_mask.close()
        lightness_mask.close()


def _range_mask_lut(min_l: float, max_l: float) -> list[int]:
    return [
        255 if min_l < (value * 100.0 / 255.0) < max_l else 0
        for value in range(256)
    ]


def _mask_pixel_count(mask: Image.Image) -> int:
    histogram = mask.histogram()
    return int(sum(histogram[1:]))


def _shift_lab_ab(
    image: Image.Image,
    *,
    offset_a: float,
    offset_b: float,
    min_neutral_weight: float,
) -> Image.Image:
    original = image.convert("RGB")
    hsv = original.convert("HSV")
    saturation = hsv.getchannel("S")
    hsv.close()
    lab = _rgb_to_lab(original)
    l_channel, a_channel, b_channel = lab.split()
    lab.close()

    shifted_a = a_channel.point(_shift_lut(-offset_a))
    shifted_b = b_channel.point(_shift_lut(-offset_b))
    shifted_lab = Image.merge("LAB", (l_channel, shifted_a, shifted_b))
    shifted_rgb = _lab_to_rgb(shifted_lab)
    shifted_lab.close()
    shifted_a.close()
    shifted_b.close()

    neutral_weight = saturation.point(_neutral_weight_lut(min_neutral_weight))
    shadow_weight = l_channel.point(_shadow_weight_lut())
    highlight_weight = l_channel.point(_highlight_weight_lut())
    saturation.close()
    l_channel.close()
    a_channel.close()
    b_channel.close()
    neutral_shadow = ImageChops.multiply(neutral_weight, shadow_weight)
    weight = ImageChops.multiply(neutral_shadow, highlight_weight)
    neutral_weight.close()
    shadow_weight.close()
    highlight_weight.close()
    neutral_shadow.close()
    try:
        return Image.composite(shifted_rgb, original, weight)
    finally:
        shifted_rgb.close()
        original.close()
        weight.close()


def _neutral_weight_lut(minimum_weight: float) -> list[int]:
    values = []
    for value in range(256):
        saturation = value / 255.0
        weight = max(minimum_weight, min(1.0, 1.0 - saturation / 0.65))
        values.append(round(weight * 255))
    return values


def _shadow_weight_lut() -> list[int]:
    values = []
    for value in range(256):
        lightness = value * 100.0 / 255.0
        weight = max(0.0, min(1.0, (lightness - 4.0) / 20.0))
        values.append(round(weight * 255))
    return values


def _highlight_weight_lut() -> list[int]:
    values = []
    for value in range(256):
        lightness = value * 100.0 / 255.0
        weight = max(0.30, min(1.0, (101.0 - lightness) / 5.0))
        values.append(round(weight * 255))
    return values


def _shift_lut(offset: float) -> list[int]:
    return [_clamp_u8(value + offset) for value in range(256)]


def _rgb_to_lab(image: Image.Image) -> Image.Image:
    rgb_to_lab, _lab_to_rgb_transform = _color_transforms()
    rgb = image.convert("RGB")
    try:
        return ImageCms.applyTransform(rgb, rgb_to_lab)
    finally:
        rgb.close()


def _lab_to_rgb(image: Image.Image) -> Image.Image:
    _rgb_to_lab_transform, lab_to_rgb = _color_transforms()
    return ImageCms.applyTransform(image, lab_to_rgb)


def _color_transforms():
    transforms = getattr(_TRANSFORM_STATE, "transforms", None)
    if transforms is None:
        srgb_profile = ImageCms.createProfile("sRGB")
        lab_profile = ImageCms.createProfile("LAB")
        transforms = (
            ImageCms.buildTransformFromOpenProfiles(srgb_profile, lab_profile, "RGB", "LAB"),
            ImageCms.buildTransformFromOpenProfiles(lab_profile, srgb_profile, "LAB", "RGB"),
        )
        _TRANSFORM_STATE.transforms = transforms
    return transforms


def _effective_offset(value: float, deadband: float, limit: float) -> float:
    magnitude = abs(float(value))
    if magnitude <= deadband:
        return 0.0
    return math.copysign(min(magnitude - deadband, limit), value)


def _within_deadband(delta_a: float, delta_b: float) -> bool:
    return abs(delta_a) <= _DEADBAND_A and abs(delta_b) <= _DEADBAND_B


def _corrected_sibling_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.wb-{uuid4().hex[:12]}.jpg")


def _no_change_result(
    original_path: str,
    reason: str,
    started_at: float,
    *,
    raw_delta_a: float = 0.0,
    raw_delta_b: float = 0.0,
    diagnostics: dict | None = None,
) -> WhiteBalanceCorrectionResult:
    return WhiteBalanceCorrectionResult(
        path=original_path,
        corrected=False,
        original_path=original_path,
        reason=reason,
        raw_delta_a=raw_delta_a,
        raw_delta_b=raw_delta_b,
        elapsed_ms=_elapsed_ms(started_at),
        diagnostics=dict(diagnostics or {}),
    )


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _log_result(active_logger, result: WhiteBalanceCorrectionResult, stage: str) -> None:
    try:
        active_logger.info(
            "[WhiteBalance] stage=%s action=%s path=%s delta_a=%.2f delta_b=%.2f "
            "final_a=%s final_b=%s passes=%d elapsed_ms=%d",
            stage,
            "correct" if result.corrected else "skip",
            Path(result.original_path).name,
            result.raw_delta_a,
            result.raw_delta_b,
            f"{result.final_delta_a:.2f}" if result.final_delta_a is not None else "n/a",
            f"{result.final_delta_b:.2f}" if result.final_delta_b is not None else "n/a",
            result.passes,
            result.elapsed_ms,
        )
    except Exception:
        pass


def _clamp_u8(value: float) -> int:
    return int(max(0, min(255, round(value))))
