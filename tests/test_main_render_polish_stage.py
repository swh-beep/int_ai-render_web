from pathlib import Path

from PIL import Image

from application.render.main_render_polish_stage import polish_main_render


def test_polish_main_render_uses_compositing_realism_prompt_and_auto_quality_options(tmp_path):
    source_path = tmp_path / "main.png"
    Image.new("RGB", (1600, 900), color=(245, 245, 245)).save(source_path, format="PNG")
    source_bytes = source_path.read_bytes()
    Path("outputs").mkdir(exist_ok=True)
    captured = {}

    def _call_repair(model_name, content, request_options, safety_settings, system_instruction=None, **kwargs):
        captured["model_name"] = model_name
        captured["content"] = list(content)
        captured["prompt"] = content[0]
        captured["request_options"] = dict(request_options)
        captured["kwargs"] = dict(kwargs)
        return type(
            "Resp",
            (),
            {
                "candidates": [object()],
                "parts": [type("Part", (), {"inline_data": type("Inline", (), {"data": source_bytes})()})()],
            },
        )()

    result = polish_main_render(
        str(source_path),
        unique_id="unitcase",
        allow_all_safety_settings=lambda: {},
        call_repair_with_failover=_call_repair,
        repair_model_name="gpt-image-2",
        match_aspect_to_target=lambda raw_path, target_path: raw_path,
        logger=type("Logger", (), {"warning": lambda *a, **k: None})(),
    )

    output_path = Path(result)
    try:
        assert output_path.exists()
        assert captured["model_name"] == "gpt-image-2"
        assert captured["request_options"]["aspect_ratio"] == "16:9"
        assert captured["request_options"]["max_attempts"] == 1
        assert "thinking_level" not in captured["request_options"]
        assert "quality" not in captured["request_options"]
        assert "final high-end interior photograph and compositing realism pass" in captured["prompt"]
        assert "Remove any Photoshop-composite look." in captured["prompt"]
        assert "natural contact shadows" in captured["prompt"]
        assert "soft ambient occlusion" in captured["prompt"]
        assert "cutout halos" in captured["prompt"]
        assert "hard pasted edges" in captured["prompt"]
        assert "Match sharpness, grain/noise" in captured["prompt"]
        assert "Match the overall tonal grade" in captured["prompt"]
        assert "black levels, midtone warmth, saturation, contrast curve, and color cast" in captured["prompt"]
        assert "same color-grading family as the room" in captured["prompt"]
        assert "Prevent objects from floating" in captured["prompt"]
        assert "Do not move, replace, resize, repaint, restyle" in captured["prompt"]
        assert "Only adjust exposure, white balance, contrast, shadows, highlights, and subtle lens realism." not in captured["prompt"]
        assert "Enhance the light, shadows, contrast, white balance, material texture" not in captured["prompt"]
        assert len(captured["content"]) == 2
        assert captured["kwargs"]["log_tag"] == "Stage2.MainPolish"
    finally:
        if output_path.exists():
            output_path.unlink()


def test_polish_main_render_retries_empty_responses_three_times_by_default(tmp_path):
    source_path = tmp_path / "main.png"
    Image.new("RGB", (1600, 900), color=(245, 245, 245)).save(source_path, format="PNG")
    attempts = []
    warnings = []

    def _call_repair(model_name, content, request_options, safety_settings, system_instruction=None, **kwargs):
        attempts.append(dict(request_options))
        return type("Resp", (), {"candidates": [object()], "parts": []})()

    result = polish_main_render(
        str(source_path),
        unique_id="unitcase-retry",
        allow_all_safety_settings=lambda: {},
        call_repair_with_failover=_call_repair,
        repair_model_name="gpt-image-2",
        match_aspect_to_target=lambda raw_path, target_path: raw_path,
        logger=type("Logger", (), {"warning": lambda self, message: warnings.append(message)})(),
    )

    assert result is None
    assert len(attempts) == 3
    assert all(options["max_attempts"] == 1 for options in attempts)
    assert warnings
