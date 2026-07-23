from pathlib import Path

from PIL import Image

import application.render.white_balance_correction as white_balance_correction
from application.render.white_balance_correction import (
    _highlight_weight_lut,
    _hybrid40_final_blend_lut,
    _measure_top_neutral_ab,
    _shift_lab_ab,
    apply_reference_relative_white_balance,
)


def _save_image(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (24, 20)) -> None:
    image = Image.new("RGB", size, color=color)
    try:
        image.save(path, "PNG")
    finally:
        image.close()


def _measured_ab(path: Path) -> tuple[float, float]:
    with Image.open(path) as image:
        return _measure_top_neutral_ab(image.convert("RGB"))


def test_neutral_image_is_noop(tmp_path):
    image_path = tmp_path / "neutral.png"
    _save_image(image_path, (190, 190, 190))

    result = apply_reference_relative_white_balance(str(image_path))

    assert result.path == str(image_path)
    assert result.corrected is False
    assert result.reason == "within_deadband"
    assert result.applied is False
    assert result.elapsed_ms >= 0


def test_custom_stage_name_is_preserved_in_diagnostics(tmp_path):
    image_path = tmp_path / "neutral.png"
    _save_image(image_path, (190, 190, 190))

    result = apply_reference_relative_white_balance(
        str(image_path),
        stage_name="image_studio_edit",
    )

    assert result.corrected is False
    assert result.diagnostics["stage"] == "image_studio_edit"


def test_reference_is_diagnostic_only_and_matching_warm_output_corrects_absolute_neutral(tmp_path):
    reference_path = tmp_path / "reference.png"
    image_path = tmp_path / "matching-warm-output.png"
    _save_image(reference_path, (225, 210, 145))
    _save_image(image_path, (225, 210, 145))
    _, before_b = _measured_ab(image_path)

    result = apply_reference_relative_white_balance(
        str(image_path),
        reference_path=str(reference_path),
    )
    _, after_b = _measured_ab(Path(result.path))

    assert result.corrected is True
    assert result.reason == "corrected"
    assert abs(after_b) < abs(before_b)
    assert result.diagnostics["reference_provided"] is True
    assert result.diagnostics["target_mode"] == "absolute_neutral"
    assert result.diagnostics["target_a"] == 0.0
    assert result.diagnostics["target_b"] == 0.0


def test_per_pass_highlight_weight_lut_keeps_original_behavior():
    lut = _highlight_weight_lut()

    assert lut[0] == 255
    assert lut[round(95.0 * 255.0 / 100.0)] > round(0.40 * 255)
    assert lut[255] == round(0.30 * 255)


def test_hybrid_40_final_blend_lut_boundaries():
    lut = _hybrid40_final_blend_lut()

    l60 = round(60.0 * 255.0 / 100.0)
    l95 = round(95.0 * 255.0 / 100.0)

    assert lut[0] == 255
    assert lut[l60] == 255
    assert lut[l95] == round(0.40 * 255)
    assert lut[255] == round(0.40 * 255)
    assert round(0.40 * 255) < lut[round((l60 + l95) / 2)] < 255


def test_correction_reduces_magenta_cast(tmp_path):
    image_path = tmp_path / "magenta.png"
    _save_image(image_path, (220, 170, 220))
    before_a, _ = _measured_ab(image_path)

    result = apply_reference_relative_white_balance(str(image_path))
    after_a, _ = _measured_ab(Path(result.path))

    assert result.corrected is True
    assert abs(after_a) < abs(before_a)


def test_correction_reduces_yellow_cast(tmp_path):
    image_path = tmp_path / "yellow.png"
    _save_image(image_path, (225, 210, 145))
    _, before_b = _measured_ab(image_path)

    result = apply_reference_relative_white_balance(str(image_path))
    _, after_b = _measured_ab(Path(result.path))

    assert result.corrected is True
    assert abs(after_b) < abs(before_b)


def test_correction_reduces_blue_cast(tmp_path):
    image_path = tmp_path / "blue.png"
    _save_image(image_path, (160, 185, 235))
    _, before_b = _measured_ab(image_path)

    result = apply_reference_relative_white_balance(str(image_path))
    _, after_b = _measured_ab(Path(result.path))

    assert result.corrected is True
    assert abs(after_b) < abs(before_b)


def test_spatial_weighting_protects_saturated_product_color():
    image = Image.new("RGB", (80, 40), (210, 185, 210))
    for x in range(40, 80):
        for y in range(40):
            image.putpixel((x, y), (225, 25, 35))

    corrected = _shift_lab_ab(
        image,
        offset_a=10.0,
        offset_b=0.0,
        min_neutral_weight=0.22,
    )
    try:
        neutral_before = image.getpixel((20, 20))
        neutral_after = corrected.getpixel((20, 20))
        product_before = image.getpixel((60, 20))
        product_after = corrected.getpixel((60, 20))
        neutral_change = sum(abs(a - b) for a, b in zip(neutral_before, neutral_after))
        product_change = sum(abs(a - b) for a, b in zip(product_before, product_after))
        assert neutral_change > product_change
    finally:
        image.close()
        corrected.close()


def test_empty_room_uses_same_cast_threshold_as_main(tmp_path):
    image_path = tmp_path / "mild.png"
    _save_image(image_path, (194, 190, 184))

    main_result = apply_reference_relative_white_balance(str(image_path))
    empty_result = apply_reference_relative_white_balance(str(image_path), empty_room=True)

    assert empty_result.corrected is main_result.corrected is True
    assert empty_result.reason == main_result.reason == "corrected"
    assert empty_result.passes == main_result.passes


def test_empty_room_corrects_strong_cast(tmp_path):
    image_path = tmp_path / "strong.png"
    _save_image(image_path, (230, 205, 145))

    result = apply_reference_relative_white_balance(str(image_path), empty_room=True)

    assert result.corrected is True
    assert Path(result.path).exists()


def test_dimensions_and_original_file_are_preserved(tmp_path):
    image_path = tmp_path / "source.png"
    _save_image(image_path, (225, 210, 145), size=(31, 17))
    original_bytes = image_path.read_bytes()
    _, before_b = _measured_ab(image_path)

    result = apply_reference_relative_white_balance(str(image_path))
    _, after_b = _measured_ab(Path(result.path))

    assert result.corrected is True
    assert abs(after_b) < abs(before_b)
    assert image_path.read_bytes() == original_bytes
    with Image.open(result.path) as corrected:
        assert corrected.size == (31, 17)


def test_severe_cast_gets_two_measured_passes_and_one_final_hybrid40_blend(tmp_path, monkeypatch):
    image_path = tmp_path / "severe.png"
    _save_image(image_path, (245, 190, 95))
    original_blend = white_balance_correction._apply_hybrid40_final_blend
    blend_calls = 0

    def counting_blend(original, candidate):
        nonlocal blend_calls
        blend_calls += 1
        return original_blend(original, candidate)

    monkeypatch.setattr(white_balance_correction, "_apply_hybrid40_final_blend", counting_blend)

    result = apply_reference_relative_white_balance(str(image_path))

    assert result.corrected is True
    assert result.passes == 2
    assert blend_calls == 1


def test_failure_falls_back_to_original_path(tmp_path):
    missing_path = tmp_path / "missing.png"

    result = apply_reference_relative_white_balance(str(missing_path))

    assert result.path == str(missing_path)
    assert result.corrected is False
    assert result.reason == "error"
    assert "error" in result.diagnostics


def test_corrected_sibling_path_is_concurrency_safe(tmp_path):
    image_path = tmp_path / "source.png"
    _save_image(image_path, (225, 210, 145))

    first = apply_reference_relative_white_balance(str(image_path))
    second = apply_reference_relative_white_balance(str(image_path))

    assert first.corrected is True
    assert second.corrected is True
    assert first.path != second.path
    assert Path(first.path).parent == tmp_path
    assert Path(second.path).parent == tmp_path
