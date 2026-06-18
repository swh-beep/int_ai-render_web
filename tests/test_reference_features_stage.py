import json
from types import SimpleNamespace

from PIL import Image

from application.render import item_analysis_stage
from application.render.reference_features_stage import (
    extract_reference_features,
    should_extract_reference_features,
)


def _write_png(path, size=(96, 96)):
    Image.new("RGB", size, color=(255, 255, 255)).save(path, format="PNG")


def test_should_extract_reference_features_marks_critical_archetypes():
    should_extract, reason = should_extract_reference_features(
        label="Black Wall Mirror",
        category="mirror",
        category_canonical="mirror",
        dims_mm={"width_mm": 400, "depth_mm": 10, "height_mm": 800},
    )

    assert should_extract is True
    assert reason == "reflective_wall_object"


def test_should_extract_reference_features_marks_decor_for_identity_extraction():
    should_extract, reason = should_extract_reference_features(
        label="Decor Vase",
        category="decor",
        category_canonical="decor",
        dims_mm={"width_mm": 400, "depth_mm": 400, "height_mm": 400},
    )

    assert should_extract is True
    assert reason == "decor_reference_identity_object"


def test_should_extract_reference_features_marks_general_items_for_identity_extraction():
    should_extract, reason = should_extract_reference_features(
        label="Floor Speaker",
        category="speaker",
        category_canonical="speaker",
        dims_mm={"width_mm": 320, "depth_mm": 340, "height_mm": 980},
    )

    assert should_extract is True
    assert reason == "general_reference_identity_object"


def test_extract_reference_features_can_force_fallback_without_model_call(tmp_path):
    crop_path = tmp_path / "crop.png"
    _write_png(crop_path)
    call_count = {"count": 0}

    def _call_model(*args, **kwargs):
        call_count["count"] += 1
        raise AssertionError("model call should not happen")

    result = extract_reference_features(
        crop_path=str(crop_path),
        label="Decor Vase",
        category="decor",
        description="Decorative ceramic vase with smooth surface.",
        dims_mm={"width_mm": 400, "depth_mm": 400, "height_mm": 400},
        call_gemini_with_failover=_call_model,
        analysis_model_name="model",
        safe_json_from_model_text=lambda text: {},
        log_brief=True,
        allow_model_call=False,
    )

    assert call_count["count"] == 0
    assert result["reflective_surface"] is False


def test_extract_reference_features_retries_same_prompt_until_specific_features(tmp_path):
    crop_path = tmp_path / "crop.png"
    _write_png(crop_path)
    prompts = []
    responses = iter(
        [
            {
                "silhouette_cues": ["lamp"],
                "material_cues": ["metal"],
                "distinctive_parts": [],
                "preserve_rules": [],
                "reflective_surface": False,
            },
            {
                "silhouette_cues": ["flat mushroom shade", "thin offset stem"],
                "material_cues": ["brushed metal", "opal diffuser"],
                "distinctive_parts": ["single disc shade", "small round base"],
                "preserve_rules": ["preserve the off-center stem", "keep the low disc shade"],
                "reflective_surface": False,
            },
        ]
    )

    def _call_model(_model, content, *_args, **_kwargs):
        prompts.append(content[0])
        return SimpleNamespace(text=json.dumps(next(responses)))

    result = extract_reference_features(
        crop_path=str(crop_path),
        label="Table Lamp",
        category="table_lamp",
        description="Small table lamp.",
        dims_mm={"width_mm": 260, "depth_mm": 260, "height_mm": 420},
        call_gemini_with_failover=_call_model,
        analysis_model_name="model",
        safe_json_from_model_text=lambda text: json.loads(text),
        log_brief=True,
        allow_model_call=True,
        extraction_reason="light_fixture_identity_object",
    )

    assert len(prompts) == 2
    assert prompts[0] == prompts[1]
    assert result["distinctive_parts"] == ["single disc shade", "small round base"]
    assert result["analysis_attempts"] == 2
    assert result["analysis_retry_count"] == 1
    assert result["analysis_quality"] == "model_sufficient"


def test_extract_reference_features_falls_back_after_three_weak_attempts(tmp_path):
    crop_path = tmp_path / "crop.png"
    _write_png(crop_path)
    call_count = {"count": 0}

    def _call_model(*_args, **_kwargs):
        call_count["count"] += 1
        return SimpleNamespace(
            text=json.dumps(
                {
                    "silhouette_cues": ["chair"],
                    "material_cues": [],
                    "distinctive_parts": [],
                    "preserve_rules": [],
                    "reflective_surface": False,
                }
            )
        )

    result = extract_reference_features(
        crop_path=str(crop_path),
        label="Dining Chair",
        category="chair",
        description="A chair.",
        dims_mm={"width_mm": 460, "depth_mm": 520, "height_mm": 790},
        call_gemini_with_failover=_call_model,
        analysis_model_name="model",
        safe_json_from_model_text=lambda text: json.loads(text),
        log_brief=True,
        allow_model_call=True,
        extraction_reason="topology_sensitive_seating",
    )

    assert call_count["count"] == 3
    assert result["analysis_attempts"] == 3
    assert result["analysis_quality"] == "fallback_after_weak_model"


def test_analyze_cropped_item_uses_fallback_reference_features_for_noncritical_item(monkeypatch, tmp_path):
    image_path = tmp_path / "item.png"
    _write_png(image_path)
    allow_flags = []

    def _fake_extract_reference_features(**kwargs):
        allow_flags.append(kwargs.get("allow_model_call"))
        return {}

    monkeypatch.setattr(item_analysis_stage, "extract_reference_features", _fake_extract_reference_features)

    result = item_analysis_stage.analyze_cropped_item(
        str(image_path),
        {
            "label": "Decor Vase",
            "box_2d": [0, 0, 1000, 1000],
            "category": "decor",
            "category_canonical": "decor",
            "target_key": "decor_vase",
            "source_index": 1,
        },
        call_gemini_with_failover=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model call should not happen")),
        analysis_model_name="model",
        safe_extract_json=lambda text: json.loads(text),
        normalize_dims_dict=lambda dims: dims,
        log_brief=True,
        unique_id="test",
        item_index=1,
        save_crop=False,
        enable_text_read=False,
        provided_dims_mm={"width_mm": 400, "depth_mm": 400, "height_mm": 400},
    )

    assert allow_flags == [False]
    assert result["reference_features"]["extraction_mode"] == "deterministic"


def test_analyze_cropped_item_uses_deterministic_reference_features_for_critical_item(monkeypatch, tmp_path):
    image_path = tmp_path / "item.png"
    _write_png(image_path)
    allow_flags = []

    def _fake_extract_reference_features(**kwargs):
        allow_flags.append(kwargs.get("allow_model_call"))
        return {"preserve_rules": ["wall-mounted reflective surface"]}

    monkeypatch.setattr(item_analysis_stage, "extract_reference_features", _fake_extract_reference_features)

    result = item_analysis_stage.analyze_cropped_item(
        str(image_path),
        {
            "label": "Black Wall Mirror",
            "box_2d": [0, 0, 1000, 1000],
            "category": "mirror",
            "category_canonical": "mirror",
            "target_key": "mirror_1",
            "source_index": 1,
        },
        call_gemini_with_failover=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model call should not happen")),
        analysis_model_name="model",
        safe_extract_json=lambda text: json.loads(text),
        normalize_dims_dict=lambda dims: dims,
        log_brief=True,
        unique_id="test",
        item_index=1,
        save_crop=False,
        enable_text_read=False,
        provided_dims_mm={"width_mm": 400, "depth_mm": 10, "height_mm": 800},
    )

    assert allow_flags == [False]
    assert result["reference_features"]["extraction_mode"] == "deterministic"
    assert "wall-mounted reflective surface" in result["description"]


def test_analyze_cropped_item_can_model_extract_reference_features_without_text_read(monkeypatch, tmp_path):
    image_path = tmp_path / "item.png"
    _write_png(image_path)
    calls = []

    def _fake_extract_reference_features(**kwargs):
        calls.append(kwargs)
        return {
            "silhouette_cues": ["rectangular shade"],
            "material_cues": ["wood", "fabric"],
            "distinctive_parts": ["block base"],
            "preserve_rules": ["copy exact shade and base geometry"],
        }

    monkeypatch.setattr(item_analysis_stage, "extract_reference_features", _fake_extract_reference_features)

    result = item_analysis_stage.analyze_cropped_item(
        str(image_path),
        {
            "label": "Table Lamp",
            "box_2d": [0, 0, 1000, 1000],
            "category": "table_lamp",
            "category_canonical": "table_lamp",
            "target_key": "lamp_1",
            "source_index": 1,
        },
        call_gemini_with_failover=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("text OCR model should not happen")),
        analysis_model_name="model",
        safe_extract_json=lambda text: json.loads(text),
        normalize_dims_dict=lambda dims: dims,
        log_brief=True,
        unique_id="test",
        item_index=1,
        save_crop=False,
        enable_text_read=False,
        allow_reference_feature_model=True,
        provided_dims_mm={"width_mm": 330, "depth_mm": 330, "height_mm": 480},
    )

    assert calls
    assert calls[0]["allow_model_call"] is True
    assert calls[0]["extraction_reason"] == "light_fixture_identity_object"
    assert result["reference_features"]["extraction_mode"] == "model"
    assert "rectangular shade" in result["description"]
    assert "block base" in result["description"]


def test_analyze_cropped_item_merges_options_reference_features_when_model_fallback_is_weak(monkeypatch, tmp_path):
    image_path = tmp_path / "layer-lamp.png"
    _write_png(image_path)

    def _fake_extract_reference_features(**kwargs):
        return {
            "silhouette_cues": ["slim"],
            "material_cues": [],
            "distinctive_parts": [],
            "preserve_rules": ["surface scale"],
            "analysis_quality": "fallback_after_weak_model",
        }

    monkeypatch.setattr(item_analysis_stage, "extract_reference_features", _fake_extract_reference_features)

    result = item_analysis_stage.analyze_cropped_item(
        str(image_path),
        {
            "label": "Layer Table Lamp",
            "box_2d": [0, 0, 1000, 1000],
            "category": "table_lamp",
            "category_canonical": "table_lamp",
            "target_key": "cart_layer_lamp_012",
            "source_index": 12,
            "options": {
                "reference_features": {
                    "silhouette_cues": ["Stacked layered shade profile", "slim cylindrical base"],
                    "distinctive_parts": ["Layered shade", "compact upright stem"],
                    "preserve_rules": ["preserve stacked horizontal shade rings"],
                }
            },
        },
        call_gemini_with_failover=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("OCR model should not happen")),
        analysis_model_name="model",
        safe_extract_json=lambda text: json.loads(text),
        normalize_dims_dict=lambda dims: dims,
        log_brief=True,
        unique_id="test",
        item_index=1,
        save_crop=False,
        enable_text_read=False,
        allow_reference_feature_model=True,
        provided_dims_mm={"width_mm": 300, "depth_mm": 300, "height_mm": 500},
    )

    features = result["reference_features"]
    assert "Stacked layered shade profile" in features["silhouette_cues"]
    assert "Layered shade" in features["distinctive_parts"]
    assert "surface scale" in features["preserve_rules"]
    assert features["options_reference_features_applied"] is True
    assert "Stacked layered shade profile" in result["description"]
    assert "Layered shade" in result["description"]


def test_analyze_cropped_item_uses_ocr_dims_to_enable_reference_features(monkeypatch, tmp_path):
    image_path = tmp_path / "item.png"
    _write_png(image_path)
    allow_flags = []

    def _fake_extract_reference_features(**kwargs):
        allow_flags.append(kwargs.get("allow_model_call"))
        return {}

    monkeypatch.setattr(item_analysis_stage, "extract_reference_features", _fake_extract_reference_features)

    result = item_analysis_stage.analyze_cropped_item(
        str(image_path),
        {
            "label": "Main Furniture",
            "box_2d": [0, 0, 1000, 1000],
            "category": None,
            "category_canonical": None,
            "target_key": "main_furniture",
            "source_index": 1,
        },
        call_gemini_with_failover=lambda *args, **kwargs: SimpleNamespace(
            text=json.dumps(
                {
                    "description": "Low-profile upholstered main furniture.",
                    "dimensions_mm": {"width": 2400, "depth": 1100, "height": 800, "radius": None},
                    "raw_text_found": "2400*1100*800",
                }
            )
        ),
        analysis_model_name="model",
        safe_extract_json=lambda text: json.loads(text),
        normalize_dims_dict=lambda dims: {k: v for k, v in dims.items() if v},
        log_brief=True,
        unique_id="test",
        item_index=1,
        save_crop=False,
        enable_text_read=True,
        provided_dims_mm=None,
    )

    assert allow_flags == [True]
    assert "2400mm" in result["description"]
    assert result["reference_features"]["extraction_mode"] == "model"


def test_analyze_cropped_item_rewrites_generic_description_into_dimension_aware_identity(monkeypatch, tmp_path):
    image_path = tmp_path / "item.png"
    _write_png(image_path)

    def _fake_extract_reference_features(**kwargs):
        return {
            "material_cues": ["metal", "linen"],
            "silhouette_cues": ["curved", "slim"],
            "distinctive_parts": ["wrapped arm rail"],
            "preserve_rules": ["keep the exposed frame visible"],
        }

    monkeypatch.setattr(item_analysis_stage, "extract_reference_features", _fake_extract_reference_features)

    result = item_analysis_stage.analyze_cropped_item(
        str(image_path),
        {
            "label": "Accent Chair",
            "box_2d": [0, 0, 1000, 1000],
            "category": "chair",
            "category_canonical": "chair",
            "target_key": "accent_chair",
            "source_index": 1,
        },
        call_gemini_with_failover=lambda *args, **kwargs: SimpleNamespace(
            text=json.dumps({"description": "A high quality Accent Chair."})
        ),
        analysis_model_name="model",
        safe_extract_json=lambda text: json.loads(text),
        normalize_dims_dict=lambda dims: {k: v for k, v in dims.items() if v},
        log_brief=True,
        unique_id="test",
        item_index=1,
        save_crop=False,
        enable_text_read=False,
        provided_dims_mm={"width_mm": 680, "depth_mm": 760, "height_mm": 820},
    )

    assert "W=680mm" in result["description"]
    assert "curved" in result["description"]
    assert "wrapped arm rail" in result["description"]
    assert "human-scale furniture piece" in result["description"]
