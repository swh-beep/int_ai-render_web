from pathlib import Path

from PIL import Image

from application.render.curtain_material_stage import (
    CURTAIN_BLACKOUT_PERCENT,
    apply_curtain_material_edit,
    is_curtain_item,
    prepare_material_swatch_image,
    split_curtain_items,
)
from application.render.render_workflow import run_render_job, run_render_with_details_job


class _Upload:
    def __init__(self, path: str):
        self.path = path

    def close(self):
        return None


def test_curtain_category_aliases_are_classified():
    assert is_curtain_item({"category": "curtain"})
    assert is_curtain_item({"category": "Drapes"})
    assert is_curtain_item({"category_path": "패브릭 > 커튼·블라인드"})
    assert not is_curtain_item({"category": "rug"})


def test_split_curtain_items_uses_first_and_leaves_no_curtain_in_base_payload():
    payload = {
        "room": "livingroom",
        "moodboard_items": [
            {"category": "sofa", "path": "sofa.png"},
            {"category": "curtain", "path": "first.png", "item_id": "curtain-1"},
            {"category": "커튼", "path": "second.png", "item_id": "curtain-2"},
            {"category": "chair", "path": "chair.png"},
        ],
    }

    base_payload, selected = split_curtain_items(payload)

    assert selected["item_id"] == "curtain-1"
    assert [item["category"] for item in base_payload["moodboard_items"]] == ["sofa", "chair"]
    assert payload["moodboard_items"][1]["path"] == "first.png"


def test_split_curtain_items_is_noop_for_no_curtain_payload():
    payload = {"room": "livingroom", "moodboard_items": [{"category": "sofa", "path": "sofa.png"}]}

    base_payload, selected = split_curtain_items(payload)

    assert selected is None
    assert base_payload == payload


def test_run_render_job_no_curtain_keeps_existing_render_path_and_never_calls_editor(tmp_path):
    room = tmp_path / "room.png"
    sofa = tmp_path / "sofa.png"
    Image.new("RGB", (20, 20), color="white").save(room)
    Image.new("RGB", (20, 20), color="blue").save(sofa)
    captured = {}

    def render_room(**kwargs):
        captured["moodboard_items"] = kwargs["moodboard_items"]
        return {"result_url": "existing-main", "result_urls": ["existing-main"]}

    result = run_render_job(
        {
            "file_path": str(room),
            "audience": "internal",
            "moodboard_items": [{"category": "sofa", "path": str(sofa)}],
        },
        materialize_input=lambda value, prefix: value,
        normalize_audience=lambda value: value or "internal",
        local_upload_factory=_Upload,
        render_room=render_room,
        json_from_response=lambda value: value,
        persist_job_result=lambda value, audience=None: None,
        curtain_material_editor=lambda *args: (_ for _ in ()).throw(AssertionError("editor must not run")),
    )

    assert result == {"result_url": "existing-main", "result_urls": ["existing-main"]}
    assert captured["moodboard_items"][0]["category"] == "sofa"


def test_prepare_material_swatch_preserves_full_frame_without_cutout(tmp_path):
    source = tmp_path / "swatch.jpg"
    output = tmp_path / "prepared.png"
    image = Image.new("RGB", (80, 60), color=(210, 196, 180))
    image.putpixel((0, 0), (205, 190, 175))
    image.putpixel((79, 59), (215, 200, 185))
    image.save(source, format="JPEG", quality=100)

    result = prepare_material_swatch_image(str(source), output_path=str(output))

    assert result == str(output)
    with Image.open(output) as prepared:
        assert prepared.size == (80, 60)
        assert prepared.mode == "RGB"


def test_apply_curtain_material_edit_calls_editor_once_and_replaces_selected_main(tmp_path):
    base = tmp_path / "base.png"
    swatch = tmp_path / "swatch.png"
    edited = tmp_path / "edited.png"
    for path, color in ((base, "white"), (swatch, "gray"), (edited, "pink")):
        Image.new("RGB", (20, 20), color=color).save(path)
    calls = []

    def process(photo_paths, instructions, mode, unique_id, index):
        calls.append((list(photo_paths), instructions, mode, unique_id, index))
        return str(edited)

    result = apply_curtain_material_edit(
        {
            "result_url": "base-url",
            "result_urls": ["base-url", "unused-base-variant"],
            "furniture_data": [{"label": "Sofa"}],
            "artifact_manifest": {"root_prefix": "jobs/123", "selected_result_urls": ["base-url"]},
        },
        {"category": "curtain", "path": "swatch-url", "target_key": "cart_curtain_001", "item_id": "11"},
        audience="external",
        materialize_input=lambda value, prefix: str(base) if value == "base-url" else str(swatch),
        process_image_edit_logic=process,
        resolve_image_url=lambda path, **kwargs: "edited-url",
        build_s3_prefix=lambda *parts: "/".join(parts),
    )

    assert len(calls) == 1
    assert calls[0][0] == [str(base), str(swatch)]
    assert calls[0][2] == "edit"
    assert calls[0][4] == 1
    assert "90%" in calls[0][1]
    assert "밝게 유지" in calls[0][1]
    assert result["result_url"] == "edited-url"
    assert result["result_urls"] == ["edited-url"]
    assert result["artifact_manifest"]["selected_result_urls"] == ["edited-url"]
    assert result["curtain_material"] == {"status": "applied", "blackout_percent": CURTAIN_BLACKOUT_PERCENT}
    marker = result["furniture_data"][-1]
    assert marker["detail_role"] == "curtain_material"
    assert marker["material_reference_path"] == "swatch-url"


def test_apply_curtain_material_edit_failure_returns_white_base_and_marker(tmp_path):
    base = tmp_path / "base.png"
    swatch = tmp_path / "swatch.png"
    Image.new("RGB", (20, 20), color="white").save(base)
    Image.new("RGB", (20, 20), color="gray").save(swatch)

    result = apply_curtain_material_edit(
        {"result_url": "white-base", "result_urls": ["white-base"], "furniture_data": []},
        {"category": "curtain", "path": "swatch-url"},
        audience="internal",
        materialize_input=lambda value, prefix: str(base) if value == "white-base" else str(swatch),
        process_image_edit_logic=lambda *args: (_ for _ in ()).throw(RuntimeError("forced editor failure")),
        resolve_image_url=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not publish")),
        build_s3_prefix=lambda *parts: "/".join(parts),
    )

    assert result["result_url"] == "white-base"
    assert result["result_urls"] == ["white-base"]
    assert "error" not in result
    assert result["curtain_material"]["status"] == "fallback_white"
    assert result["furniture_data"][-1]["detail_role"] == "curtain_material"


def test_white_fallback_render_still_flows_into_required_detail_generation(tmp_path):
    base = tmp_path / "base.png"
    swatch = tmp_path / "swatch.png"
    Image.new("RGB", (20, 20), color="white").save(base)
    Image.new("RGB", (20, 20), color="gray").save(swatch)
    fallback_render = apply_curtain_material_edit(
        {"result_url": "white-base", "result_urls": ["white-base"], "furniture_data": []},
        {"category": "curtain", "path": "swatch-url"},
        audience="external",
        materialize_input=lambda value, prefix: str(base) if value == "white-base" else str(swatch),
        process_image_edit_logic=lambda *args: None,
        resolve_image_url=lambda *args, **kwargs: None,
        build_s3_prefix=lambda *parts: "/".join(parts),
    )
    captured = {}

    def detail_runner(payload):
        captured["payload"] = payload
        return {"details": [{"url": "detail-url"}]}

    result = run_render_with_details_job(
        {"require_details": True, "render": {"audience": "external"}},
        normalize_audience=lambda value: value or "external",
        render_job_runner=lambda payload, persist_result=False: fallback_render,
        detail_job_runner=detail_runner,
        persist_job_result=lambda value, audience=None: None,
    )

    assert result["render"]["result_url"] == "white-base"
    assert result["details"]["details"] == [{"url": "detail-url"}]
    assert captured["payload"]["furniture_data"][-1]["detail_role"] == "curtain_material"
