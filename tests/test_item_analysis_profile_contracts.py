from application.render.render_analysis_stage import _analyze_items
from application.render.render_workflow import run_render_job
from infrastructure.ai.service_scope import ai_service_scope, current_ai_service_scope


def test_direct_item_analysis_uses_detailed_profile_without_enabling_ocr():
    captured = []

    def _analyze(_path, item_data, **kwargs):
        captured.append((item_data, kwargs))
        return {
            "description": "Light beige leather lounge chair with a low folded silhouette.",
            "crop_path": "outputs/perla.png",
            "reference_features": {
                "color_cues": ["light beige"],
                "analysis_profile": kwargs["analysis_profile"],
            },
        }

    rows = _analyze_items(
        item_metas=[
            {
                "label": "라운지체어",
                "product_name": "DS-266_SELECT PERLA",
                "source_path": "outputs/perla-source.png",
                "box_2d": [0, 0, 1000, 1000],
                "dims_mm": {"width_mm": 1000, "depth_mm": 800, "height_mm": 700},
                "qty": 1,
                "category": "lounge_chair",
                "category_canonical": "lounge_chair",
                "item_id": "23963",
                "source_index": 1,
                "target_key": "cart_23963_001",
            }
        ],
        item_refs=[{"item_id": "23963"}],
        unique_id="profile-test",
        analyze_cropped_item=_analyze,
        normalize_dims_dict=lambda dims: dict(dims or {}),
        canonical_category=lambda value: value or "unknown",
        build_item_target_key=lambda *args, **kwargs: "fallback-key",
        room_dims_parsed={"width_mm": 5000, "depth_mm": 4000, "height_mm": 2600},
        max_concurrency_analysis=2,
        cart_max_analysis_workers=2,
        item_analysis_profile="detailed",
    )

    assert len(captured) == 1
    item_data, kwargs = captured[0]
    assert item_data["product_name"] == "DS-266_SELECT PERLA"
    assert kwargs["analysis_profile"] == "detailed"
    assert kwargs["enable_text_read"] is False
    assert kwargs["allow_reference_feature_model"] is True
    assert rows[0]["item_analysis_profile"] == "detailed"
    assert rows[0]["product_name"] == "DS-266_SELECT PERLA"


def test_direct_item_analysis_preserves_ai_service_scope_in_worker_threads():
    observed_scopes = []

    def _analyze(_path, item_data, **_kwargs):
        observed_scopes.append(current_ai_service_scope())
        return {"description": item_data["label"]}

    with ai_service_scope("kr_ai_designer"):
        _analyze_items(
            item_metas=[
                {
                    "label": "라운지체어",
                    "source_path": "outputs/perla-source.png",
                    "box_2d": [0, 0, 1000, 1000],
                    "category": "lounge_chair",
                }
            ],
            item_refs=[{"item_id": "23963"}],
            unique_id="scope-test",
            analyze_cropped_item=_analyze,
            normalize_dims_dict=lambda dims: dict(dims or {}),
            canonical_category=lambda value: value or "unknown",
            build_item_target_key=lambda *args, **kwargs: "fallback-key",
            room_dims_parsed={},
            max_concurrency_analysis=2,
            cart_max_analysis_workers=2,
        )

    assert observed_scopes == ["kr_ai_designer"]


def test_render_job_forwards_analysis_profile_to_shared_render_workflow(tmp_path):
    room_path = tmp_path / "room.png"
    room_path.write_bytes(b"room")
    item_path = tmp_path / "item.png"
    item_path.write_bytes(b"item")
    captured = {}

    class _Upload:
        def __init__(self, path):
            self.path = path

        def close(self):
            return None

    def _materialize(value, _prefix):
        if value == "room-ref":
            return str(room_path)
        if value == "item-ref":
            return str(item_path)
        return value

    def _render_room(**kwargs):
        captured.update(kwargs)
        return {"result_url": "https://cdn.example/result.png"}

    result = run_render_job(
        {
            "file_path": "room-ref",
            "moodboard_items": [{"path": "item-ref", "label": "chair"}],
            "room": "livingroom",
            "style": "Customize",
            "variant": "1",
            "audience": "external",
            "item_analysis_profile": "detailed",
        },
        materialize_input=_materialize,
        normalize_audience=lambda value: value or "external",
        local_upload_factory=_Upload,
        render_room=_render_room,
        json_from_response=lambda value: value,
        persist_job_result=lambda *_args, **_kwargs: None,
    )

    assert result["result_url"] == "https://cdn.example/result.png"
    assert captured["item_analysis_profile"] == "detailed"
    assert captured["moodboard_items"][0]["path"] == str(item_path)
