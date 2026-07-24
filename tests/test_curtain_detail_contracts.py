from pathlib import Path

import pytest
from PIL import Image

from application.details.detail_generation_stage import generate_detail_view
from application.details.detail_style_stage import construct_dynamic_styles
from application.details.detail_workflow import (
    _should_prefer_crop_extract_for_detail,
    select_external_detail_styles,
)


def _curtain_marker(material_path: str = "https://cdn.example/swatch.png") -> dict:
    return {
        "label": "Narcis Curtain",
        "category": "curtain",
        "category_canonical": "curtain",
        "target_key": "cart_curtain_001",
        "detail_role": "curtain_material",
        "detail_mode": "curtain_material_generation",
        "material_reference_path": material_path,
        "blackout_percent": 90,
    }


def test_construct_dynamic_styles_adds_marker_only_curtain_detail_ahead_of_localized_items():
    styles = construct_dynamic_styles(
        [
            {
                "label": "Sofa",
                "category": "sofa",
                "category_canonical": "sofa",
                "target_key": "cart_sofa_001",
                "box_2d": [400, 200, 800, 700],
                "box_source": "product_reference_localization",
                "crop_path": "sofa.png",
                "volume_rank": 1,
            },
            _curtain_marker(),
        ]
    )

    assert styles[0]["target_category_canonical"] == "curtain"
    assert styles[0]["priority_detail"] is True
    assert styles[0]["detail_mode"] == "curtain_material_generation"
    assert styles[0]["blackout_percent"] == 90
    assert "without darkening the room" in styles[0]["prompt"]


def test_construct_dynamic_styles_still_excludes_unmarked_default_curtain():
    styles = construct_dynamic_styles(
        [
            {
                "label": "Curtain",
                "category": "curtain",
                "category_canonical": "curtain",
                "box_2d": [100, 100, 900, 300],
            }
        ]
    )

    assert styles == []


def test_external_detail_selection_keeps_cap_and_prioritizes_curtain_generation():
    curtain = construct_dynamic_styles([_curtain_marker()])[0]
    ordinary = [
        {
            "name": f"Detail: Product {index}",
            "target_key": f"cart_product_{index}",
            "detail_mode": "product_identity_lock",
        }
        for index in range(1, 7)
    ]

    selected = select_external_detail_styles([*ordinary, curtain], limit=6)

    assert len(selected) == 6
    assert selected[0]["detail_mode"] == "curtain_material_generation"
    assert _should_prefer_crop_extract_for_detail(selected[0], audience="external") is False
    assert _should_prefer_crop_extract_for_detail(selected[0], audience="internal") is False


@pytest.mark.parametrize(
    ("model_name", "expected_mode"),
    [
        ("gemini-3.1-flash-image", "model_regeneration"),
        ("gpt-image-1", "gpt_image_detail"),
    ],
)
def test_curtain_detail_generation_supplies_material_swatch_to_each_image_provider(tmp_path, model_name, expected_mode):
    source = tmp_path / "room.png"
    swatch = tmp_path / "swatch.png"
    Image.new("RGB", (800, 1000), color=(245, 245, 245)).save(source)
    Image.new("RGB", (800, 800), color=(180, 150, 145)).save(swatch)
    style = construct_dynamic_styles([_curtain_marker(str(swatch))])[0]
    captured = {}

    def fake_generate(name, content, request_options, safety_settings, **kwargs):
        captured["labels"] = [value for value in content if isinstance(value, str)]
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source),
        style,
        "curtain-test",
        1,
        furniture_data=[_curtain_marker(str(swatch))],
        prefer_crop_extract=False,
        materialize_input=lambda value, prefix: value,
        normalize_label_for_match=lambda value: str(value or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=fake_generate,
        model_name=model_name,
    )

    output_path = Path(result["path"])
    try:
        assert result["generation_mode"] == expected_mode
        assert any("CURTAIN MATERIAL SWATCH" in label for label in captured["labels"])
    finally:
        output_path.unlink(missing_ok=True)


def test_gpt_curtain_detail_prompt_applies_material_and_brightness_contract_without_generic_conflict(tmp_path):
    source = tmp_path / "room.png"
    swatch = tmp_path / "swatch.png"
    Image.new("RGB", (800, 1000), color=(245, 245, 245)).save(source)
    Image.new("RGB", (800, 800), color=(180, 150, 145)).save(swatch)
    style = construct_dynamic_styles([_curtain_marker(str(swatch))])[0]
    captured = {}

    def fake_generate(name, content, request_options, safety_settings, **kwargs):
        captured["prompt"] = content[0]
        captured["content"] = list(content)
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source.read_bytes()})()})()],
            },
        )()

    result = generate_detail_view(
        str(source),
        style,
        "curtain-gpt-prompt",
        1,
        furniture_data=[_curtain_marker(str(swatch))],
        prefer_crop_extract=False,
        materialize_input=lambda value, prefix: value,
        normalize_label_for_match=lambda value: str(value or "").strip().lower(),
        allow_harassment_only_safety_settings=lambda: {},
        call_gemini_with_failover=fake_generate,
        model_name="gpt-image-1",
    )

    output_path = Path(result["path"])
    try:
        prompt = captured["prompt"]
        assert "90% blackout" in prompt
        assert "Do not darken the room" in prompt
        assert "lighting brightness" in prompt
        assert "supplied CURTAIN MATERIAL SWATCH" in prompt
        assert "material, color, weave, threads, and surface texture" in prompt
        assert "Change only the curtain surface appearance" in prompt
        assert "color, material unchanged" not in prompt
        assert "materials, lighting direction" not in prompt
        assert any(isinstance(value, str) and "CURTAIN MATERIAL SWATCH" in value for value in captured["content"])
    finally:
        output_path.unlink(missing_ok=True)
