import unittest
from pathlib import Path

from api_models import DetailRequest, RegenerateDetailRequest
from application.details.detail_analysis_stage import load_analyzed_items
from application.details.detail_workflow import run_generate_details_job
from application.render.render_result_stage import build_detail_payload
from application.render.render_workflow import run_render_with_details_job
from render_route_services import (
    build_detail_generation_job_payload,
    build_regenerate_detail_job_payload,
)


class DetailChainContractsTests(unittest.TestCase):
    def test_build_detail_payload_preserves_itemized_context(self):
        render_result = {
            "result_urls": ["https://cdn.example/rendered/main-1.png"],
            "moodboard_url": None,
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
        }

        payload = build_detail_payload(render_result, audience="internal")

        self.assertEqual(payload["image_url"], "https://cdn.example/rendered/main-1.png")
        self.assertIsNone(payload["moodboard_url"])
        self.assertEqual(payload["furniture_data"], [{"label": "Accent Chair", "target_key": "detail_001"}])
        self.assertEqual(payload["audience"], "internal")

    def test_detail_generation_job_payload_keeps_furniture_data_without_moodboard(self):
        req = DetailRequest(
            image_url="https://cdn.example/rendered/main-1.png",
            furniture_data=[{"label": "Accent Chair", "target_key": "detail_001"}],
            audience="internal",
        )

        payload = build_detail_generation_job_payload(req)

        self.assertEqual(payload["image_url"], "https://cdn.example/rendered/main-1.png")
        self.assertIsNone(payload["moodboard_url"])
        self.assertEqual(payload["furniture_data"], [{"label": "Accent Chair", "target_key": "detail_001"}])
        self.assertEqual(payload["audience"], "internal")

    def test_regenerate_detail_job_payload_keeps_target_metadata_and_furniture_data(self):
        req = RegenerateDetailRequest(
            original_image_url="https://cdn.example/rendered/main-1.png",
            style_index=2,
            target_key="detail_001",
            target_label="Accent Chair",
            style_index_mode="overall",
            furniture_data=[{"label": "Accent Chair", "target_key": "detail_001"}],
            audience="internal",
        )

        payload = build_regenerate_detail_job_payload(req)

        self.assertEqual(payload["original_image_url"], "https://cdn.example/rendered/main-1.png")
        self.assertEqual(payload["style_index"], 2)
        self.assertEqual(payload["target_key"], "detail_001")
        self.assertEqual(payload["target_label"], "Accent Chair")
        self.assertEqual(payload["style_index_mode"], "overall")
        self.assertIsNone(payload["moodboard_url"])
        self.assertEqual(payload["furniture_data"], [{"label": "Accent Chair", "target_key": "detail_001"}])
        self.assertEqual(payload["audience"], "internal")

    def test_load_analyzed_items_prefers_cached_furniture_data(self):
        furniture_data = [{"label": "Accent Chair", "target_key": "detail_001"}]
        detect_calls = []
        materialize_calls = []
        analyze_calls = []

        analyzed = load_analyzed_items(
            furniture_data=furniture_data,
            moodboard_url=None,
            local_path="outputs/rendered-main.png",
            materialize_input=lambda url, prefix: materialize_calls.append((url, prefix)),
            detect_furniture_boxes=lambda path: detect_calls.append(path),
            canonical_category=lambda label: label,
            build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
            max_concurrency_analysis=2,
            analyze_cropped_item=lambda path, item: analyze_calls.append((path, item)),
            attach_volume_ranks=lambda items: items,
        )

        self.assertEqual(analyzed, furniture_data)
        self.assertEqual(detect_calls, [])
        self.assertEqual(materialize_calls, [])
        self.assertEqual(analyze_calls, [])


if __name__ == "__main__":
    unittest.main()


def test_run_render_with_details_job_passes_shared_deadline_budget_to_details():
    captured = {}
    persisted = []

    def fake_time_now():
        if not captured:
            captured["time_calls"] = 1
            return 100.0
        return 140.0

    def fake_detail_job_runner(detail_payload):
        captured["detail_payload"] = dict(detail_payload)
        return {"details": [{"url": "https://cdn.example/detail-1.png"}], "message": "ok"}

    result = run_render_with_details_job(
        {
            "render": {"audience": "external"},
            "extra": {"resolved": {"room": "livingroom", "style": "natural", "variant": "2"}},
        },
        normalize_audience=lambda audience: audience or "external",
        render_job_runner=lambda render_payload, persist_result=False: {
            "result_url": "https://cdn.example/render.png",
            "result_urls": ["https://cdn.example/render.png"],
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
        },
        detail_job_runner=fake_detail_job_runner,
        persist_job_result=lambda payload, audience=None: persisted.append((payload, audience)),
        total_timeout_limit_sec=600.0,
        time_now=fake_time_now,
    )

    detail_payload = captured["detail_payload"]
    assert detail_payload["absolute_deadline_ts"] == 700.0
    assert detail_payload["detail_budget_sec"] == 560.0
    assert detail_payload["minimum_detail_budget_sec"] == 5.0
    assert result["details"]["details"][0]["url"] == "https://cdn.example/detail-1.png"
    assert result["resolved"]["room"] == "livingroom"
    assert persisted[-1][1] == "external"


def test_run_render_with_details_job_skips_details_when_budget_is_exhausted():
    persisted = []
    time_calls = {"count": 0}

    def fake_time_now():
        time_calls["count"] += 1
        if time_calls["count"] == 1:
            return 100.0
        return 699.5

    result = run_render_with_details_job(
        {
            "render": {"audience": "external"},
            "extra": {"cart_kept": [{"id": "chair-1"}], "cart_dropped": []},
        },
        normalize_audience=lambda audience: audience or "external",
        render_job_runner=lambda render_payload, persist_result=False: {
            "result_url": "https://cdn.example/render.png",
            "result_urls": ["https://cdn.example/render.png"],
        },
        detail_job_runner=lambda detail_payload: (_ for _ in ()).throw(AssertionError("detail job should not run")),
        persist_job_result=lambda payload, audience=None: persisted.append((payload, audience)),
        total_timeout_limit_sec=600.0,
        time_now=fake_time_now,
    )

    assert result["details"]["details"] == []
    assert result["details"]["furniture_boxes"] == []
    assert "deadline budget exhaustion" in result["details"]["message"].lower()
    assert result["cart_kept"] == [{"id": "chair-1"}]


def test_run_generate_details_job_budgeted_mode_limits_styles_and_uses_style_timeouts(monkeypatch, tmp_path):
    image_path = tmp_path / "detail-src.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    monkeypatch.setattr("application.details.detail_workflow.time.time", lambda: 100.0)
    recorded_timeouts = []

    result = run_generate_details_job(
        {
            "image_url": str(image_path),
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
            "audience": "external",
            "absolute_deadline_ts": 130.0,
            "minimum_detail_budget_sec": 5.0,
        },
        normalize_audience=lambda audience: audience or "external",
        build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
        persist_job_result=lambda payload, audience=None: None,
        materialize_input=lambda url, prefix: url,
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
        log_section=lambda message: None,
        detect_furniture_boxes=lambda path: [],
        canonical_category=lambda label: label or "",
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: items,
        construct_dynamic_styles=lambda analyzed_items: [
            {"name": "Detail: Chair"},
            {"name": "Detail: Lamp"},
            {"name": "Detail: Table"},
            {"name": "Detail: Mirror"},
        ],
        generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None: (
            recorded_timeouts.append(float(style_config["timeout_sec"])) or {
                "path": original_image_path,
                "style_name": style_config.get("name"),
            }
        ),
        normalize_label_for_match=lambda label: label.strip().lower(),
        volume_ranking_snapshot=lambda items: [{"target_key": row.get("target_key")} for row in items if isinstance(row, dict)],
    )

    assert len(result["details"]) == 1
    assert recorded_timeouts == [29.0]
    assert result["details"][0]["style_name"] == "Detail: Chair"


def test_run_generate_details_job_budgeted_mode_returns_empty_shape_when_budget_is_too_low(monkeypatch, tmp_path):
    image_path = tmp_path / "detail-src.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    monkeypatch.setattr("application.details.detail_workflow.time.time", lambda: 100.0)

    result = run_generate_details_job(
        {
            "image_url": str(image_path),
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
            "audience": "external",
            "absolute_deadline_ts": 103.0,
            "minimum_detail_budget_sec": 5.0,
        },
        normalize_audience=lambda audience: audience or "external",
        build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
        persist_job_result=lambda payload, audience=None: None,
        materialize_input=lambda url, prefix: url,
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
        log_section=lambda message: None,
        detect_furniture_boxes=lambda path: (_ for _ in ()).throw(AssertionError("analysis should not run when budget is already exhausted")),
        canonical_category=lambda label: label or "",
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: items,
        construct_dynamic_styles=lambda analyzed_items: [{"name": "Detail: Chair"}],
        generate_detail_view=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("detail generation should not run when budget is already exhausted")),
        normalize_label_for_match=lambda label: label.strip().lower(),
        volume_ranking_snapshot=lambda items: [{"target_key": row.get("target_key")} for row in items if isinstance(row, dict)],
    )

    assert result["details"] == []
    assert result["furniture_boxes"] == []
    assert "deadline budget exhaustion" in result["message"].lower()
