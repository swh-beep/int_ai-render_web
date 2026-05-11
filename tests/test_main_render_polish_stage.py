from pathlib import Path

from PIL import Image

from application.render.main_render_polish_stage import polish_main_render


def test_polish_main_render_uses_short_edit_prompt_and_auto_quality_options(tmp_path):
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
        assert "real interior magazine photograph" in captured["prompt"]
        assert "light, shadows, contrast, white balance, material texture" in captured["prompt"]
        assert "Reduce any artificial composite look" in captured["prompt"]
        assert "room structure, camera framing" in captured["prompt"]
        assert len(captured["content"]) == 2
        assert captured["kwargs"]["log_tag"] == "Stage2.MainPolish"
    finally:
        if output_path.exists():
            output_path.unlink()
