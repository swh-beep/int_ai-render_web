from types import SimpleNamespace

from PIL import Image

from application.render.item_analysis_stage import detect_furniture_boxes


def test_detect_furniture_boxes_uses_minimum_timeout_and_three_attempts(tmp_path):
    image_path = tmp_path / "room.png"
    Image.new("RGB", (32, 32), color=(240, 240, 240)).save(image_path, format="PNG")
    captured = {}

    def _call_gemini(model_name, content, request_options, safety_settings, **kwargs):
        captured["model_name"] = model_name
        captured["request_options"] = dict(request_options)
        captured["kwargs"] = dict(kwargs)
        return SimpleNamespace(text='[{"label":"Sofa","box_2d":[100,100,600,800]}]')

    result = detect_furniture_boxes(
        str(image_path),
        log_brief=True,
        call_gemini_with_failover=_call_gemini,
        default_model_name="gemini-detect-default",
        timeout_sec=12,
        max_attempts=1,
    )

    assert result[0]["label"] == "Sofa"
    assert captured["model_name"] == "gemini-detect-default"
    assert captured["request_options"]["timeout"] == 60
    assert captured["request_options"]["max_attempts"] == 3
    assert captured["kwargs"]["log_tag"] == "Analysis.DetectFurniture"


def test_detect_furniture_boxes_returns_empty_on_detection_failure_instead_of_fake_items(tmp_path):
    image_path = tmp_path / "room.png"
    Image.new("RGB", (32, 32), color=(240, 240, 240)).save(image_path, format="PNG")

    result = detect_furniture_boxes(
        str(image_path),
        log_brief=True,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        default_model_name="gemini-detect-default",
    )

    assert result == []
