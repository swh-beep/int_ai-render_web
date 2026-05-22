from types import SimpleNamespace

from application.details.detail_analysis_stage import prepare_detail_generation_items
from application.render.render_bootstrap_stage import _build_summary
from application.render.render_room_workflow import _build_simple_generation_specs, run_render_room_workflow
from application.render.render_workflow_contracts import (
    RenderWorkflowAnalysisServices,
    RenderWorkflowDependencies,
    RenderWorkflowGenerationServices,
    RenderWorkflowPostprocessServices,
    RenderWorkflowRequest,
    RenderWorkflowRuntime,
    RenderWorkflowStorageServices,
)
from infrastructure.ai.gemini_prompts import build_empty_room_prompt


class _SummaryRef:
    def __init__(self):
        self.summary = None

    def set(self, summary):
        self.summary = summary
        return "summary-token"

    def get(self):
        return self.summary


def _logger():
    return SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
    )


def test_empty_room_prompt_removes_shelves_even_if_wall_attached():
    prompt = build_empty_room_prompt()

    assert "BOOKCASE/SHELVING RULE" in prompt
    assert "bookshelf" in prompt.lower()
    assert "even if it appears attached to the wall" in prompt
    assert "If unsure whether it is built-in furniture or architecture, remove it." in prompt


def test_empty_room_prompt_requests_architectural_grid_alignment():
    prompt = build_empty_room_prompt()

    assert "ARCHITECTURAL GRID ALIGNMENT" in prompt
    assert "VERTICAL LINE LOCK" in prompt
    assert "perfectly vertical and parallel to the image y-axis" in prompt
    assert "Correct camera roll, pitch, keystone distortion" in prompt
    assert "horizontal receding lines may converge" in prompt
    assert "perspective/grid rectification takes priority" in prompt


def test_simple_generation_specs_keep_only_prompt_minimum_and_two_pass_metadata():
    specs = {
        "items": [
            {
                "target_key": "chair-1",
                "label": "Chair",
                "category": "chair",
                "qty": 1,
                "dims_mm": {"width_mm": 500, "depth_mm": 520, "height_mm": 780},
                "crop_path": "outputs/chair.png",
                "description": "Soft boucle lounge chair",
                "identity_profile": {"silhouette": "long text"},
                "product_identity": {"family": "chair"},
                "archetype_strategy": {"avoid": ["sofa"]},
                "reference_features": {"material_cues": ["boucle"]},
                "layout_envelope": {"room_width_ratio": 0.2},
                "placement_contract": {"zone": "left"},
            }
        ],
        "primary_scale": {"target_key": "chair-1"},
        "size_hierarchy_scale": ["Chair"],
        "two_pass_strategy": {"pass1_primary_keys": ["chair-1"]},
    }

    simple = _build_simple_generation_specs(specs)

    item = simple["items"][0]
    assert item["target_key"] == "chair-1"
    assert item["crop_path"] == "outputs/chair.png"
    assert item["dims_mm"] == {"width_mm": 500, "depth_mm": 520, "height_mm": 780}
    assert "description" not in item
    assert "identity_profile" not in item
    assert "product_identity" not in item
    assert "archetype_strategy" not in item
    assert "reference_features" not in item
    assert "layout_envelope" not in item
    assert "placement_contract" not in item
    assert simple["two_pass_strategy"] == {"pass1_primary_keys": ["chair-1"]}
    assert simple["size_hierarchy_scale"] == ["Chair"]
    assert simple["primary"]["target_key"] == "chair-1"


def test_run_render_room_workflow_uses_unified_best_of_three_main_mode(monkeypatch):
    summary_ref = _SummaryRef()
    captured = {}

    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_bootstrap_stage",
        lambda **kwargs: SimpleNamespace(
            unique_id="job-simple",
            start_time=0.0,
            summary=_build_summary(),
            summary_token=kwargs["summary_ref"].set(_build_summary()),
        ),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_audience_stage",
        lambda **kwargs: SimpleNamespace(
            audience="internal",
            enable_scale_check=True,
            prefix_main_user="main/user",
            prefix_main_empty="main/empty",
            prefix_main_rendered="main/rendered",
            prefix_customize="customize",
        ),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_input_stage",
        lambda **kwargs: SimpleNamespace(timestamp="ts-simple", std_path="outputs/std.png"),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_empty_stage",
        lambda **kwargs: SimpleNamespace(step1_img="outputs/empty.png", step1_raw="raw-empty"),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_scale_stage",
        lambda **kwargs: SimpleNamespace(
            room_dims_parsed={"width_mm": 4000, "depth_mm": 3500, "height_mm": 2400},
            room_dims_valid=True,
            enable_scale_guidance=True,
            room_planes={"floor": "plane"},
            wall_span_norm=(0.0, 1.0),
            windows_present=False,
            room_analysis_text="room analysis",
            furniture_specs_text="verbose specs",
            furniture_specs_json={"items": []},
            primary_item=None,
            scale_guide_path="outputs/scale-guide.png",
            size_hierarchy=["Chair"],
            full_analyzed_data=[],
        ),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.prepare_render_references",
        lambda **kwargs: SimpleNamespace(mb_url="moodboard.png", ref_paths=["outputs/ref.png"], item_refs=[]),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_analysis_stage",
        lambda **kwargs: SimpleNamespace(
            windows_present=False,
            room_analysis_text="room analysis",
            room_planes={"floor": "plane"},
            wall_span_norm=(0.0, 1.0),
            furniture_specs_text="verbose specs",
            furniture_specs_json={
                "items": [
                    {
                        "target_key": "chair-1",
                        "label": "Chair",
                        "category": "chair",
                        "qty": 1,
                        "dims_mm": {"width_mm": 500, "depth_mm": 520, "height_mm": 780},
                        "crop_path": "outputs/chair.png",
                        "description": "Soft boucle lounge chair",
                        "identity_profile": {"silhouette": "long text"},
                        "product_identity": {"family": "chair"},
                        "archetype_strategy": {"avoid": ["sofa"]},
                        "reference_features": {"material_cues": ["boucle"]},
                        "layout_envelope": {"room_width_ratio": 0.2},
                        "placement_contract": {"zone": "left"},
                    }
                ],
                "primary_scale": {"target_key": "chair-1"},
                "size_hierarchy_scale": ["Chair"],
                "two_pass_strategy": {"pass1_primary_keys": ["chair-1"]},
            },
            full_analyzed_data=[
                {
                    "target_key": "chair-1",
                    "label": "Chair",
                    "category": "chair",
                    "dims_mm": {"width_mm": 500, "depth_mm": 520, "height_mm": 780},
                    "crop_path": "outputs/chair.png",
                    "description": "Soft boucle lounge chair",
                    "identity_profile": {"silhouette": "long text"},
                    "product_identity": {"family": "chair"},
                    "archetype_strategy": {"avoid": ["sofa"]},
                    "reference_features": {"material_cues": ["boucle"]},
                    "layout_envelope": {"room_width_ratio": 0.2},
                    "placement_contract": {"zone": "left"},
                }
            ],
            primary_item={"target_key": "chair-1", "label": "Chair"},
            scale_guide_path="outputs/scale-guide.png",
            size_hierarchy=["Chair"],
        ),
    )

    def fake_variant_stage(**kwargs):
        captured["variant_kwargs"] = dict(kwargs)
        return [
            {"path": "outputs/simple-a.png", "scalecheck_fail_count": 0, "scalecheck_retry_count": 0},
            {"path": "outputs/simple-b.png", "scalecheck_fail_count": 0, "scalecheck_retry_count": 0},
            {"path": "outputs/simple-c.png", "scalecheck_fail_count": 0, "scalecheck_retry_count": 0},
        ]

    monkeypatch.setattr("application.render.render_room_workflow.run_render_variant_stage", fake_variant_stage)

    def fake_postprocess(**kwargs):
        captured["postprocess_kwargs"] = dict(kwargs)
        return SimpleNamespace(
            generated_results=["outputs/simple-b.png", "outputs/simple-a.png", "outputs/simple-c.png"],
            full_analyzed_data=list(kwargs["full_analyzed_data"]),
            volume_ranking=[{"target_key": "chair-1", "volume_rank": 1}],
            rerank_applied=True,
        )

    monkeypatch.setattr("application.render.render_room_workflow.run_render_postprocess_stage", fake_postprocess)

    def fake_polish(path, **kwargs):
        captured.setdefault("polish_calls", []).append({"path": path, **kwargs})
        return path.replace(".png", "_polished.png")

    request = RenderWorkflowRequest(
        file=object(),
        room="room",
        style="style",
        variant="variant",
        dimensions="4000x3500x2400",
        placement="center",
        audience="internal",
        moodboard_items=[],
        simple_generation_mode=False,
    )
    deps = RenderWorkflowDependencies(
        runtime=RenderWorkflowRuntime(
            style_map={"style": "Style"},
            generate_unique_id=lambda: "job-simple",
            time_now=lambda: 0.0,
            log_section=lambda *args, **kwargs: None,
            summary_ref=summary_ref,
            reset_summary_token=lambda *args, **kwargs: None,
            logger=_logger(),
            log_brief=False,
            log_summary=False,
            use_s3_moodboard=False,
            max_concurrency_analysis=1,
            cart_max_analysis_workers=1,
            total_timeout_limit_sec=600.0,
        ),
        storage=RenderWorkflowStorageServices(
            normalize_audience=lambda aud: aud or "internal",
            build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
            standardize_image=lambda *args, **kwargs: "outputs/std.png",
            materialize_input=lambda *args, **kwargs: "outputs/std.png",
            resolve_image_url=lambda path, **kwargs: f"url://{path}",
            find_s3_moodboard_key=lambda *args, **kwargs: None,
            s3_public_url=lambda path: f"url://{path}",
        ),
        analysis=RenderWorkflowAnalysisServices(
            parse_room_dimensions_mm=lambda dimensions: {"width_mm": 4000, "depth_mm": 3500, "height_mm": 2400},
            room_dims_valid_fn=lambda dims: True,
            build_item_target_key=lambda *args, **kwargs: "target",
            canonical_category=lambda value: value or "unknown",
            detect_furniture_boxes=lambda *args, **kwargs: [],
            analyze_room_structure=lambda *args, **kwargs: {},
            analyze_cropped_item=lambda *args, **kwargs: {},
            normalize_dims_dict=lambda dims: dims,
            parse_object_dimensions_mm=lambda value: {},
            build_furniture_specs_json=lambda *args, **kwargs: {"items": []},
            create_scale_guide_overlay_with_model=lambda *args, **kwargs: None,
            match_aspect_to_target=lambda *args, **kwargs: None,
            estimate_room_dims_contract=lambda **kwargs: {"dims_mm_center": {"width_mm": 4000, "depth_mm": 3500, "height_mm": 2400}, "source": "explicit", "confidence": "high"},
            build_product_identity_bundle=lambda items, *args, **kwargs: (list(items or []), []),
            build_archetype_strategies=lambda items, *args, **kwargs: (list(items or []), []),
            build_scene_contract=lambda **kwargs: {"critical_item_keys": ["chair-1"], "geometry_source": "explicit", "geometry_confidence": "high"},
            build_placement_plan=lambda **kwargs: ({"placement_zones": {"chair-1": "left"}}, list(kwargs.get("analyzed_items") or [])),
            build_geometry_contract=lambda **kwargs: {"anchor_item_key": "chair-1", "geometry_source": "explicit", "geometry_confidence": "high"},
        ),
        generation=RenderWorkflowGenerationServices(
            generate_empty_room=lambda *args, **kwargs: ("outputs/empty.png", None),
            generate_furnished_room=lambda *args, **kwargs: "outputs/simple-result.png",
            polish_main_image=fake_polish,
        ),
        postprocess=RenderWorkflowPostprocessServices(
            rank_best_variant=lambda *args, **kwargs: None,
            refresh_item_boxes_from_main_render=lambda path, items: items,
            attach_volume_ranks=lambda items: items,
            volume_ranking_snapshot=lambda items: [{"target_key": "chair-1", "volume_rank": 1}],
        ),
    )

    result = run_render_room_workflow(request, deps)

    variant_kwargs = captured["variant_kwargs"]
    simple_item = variant_kwargs["furniture_specs_json"]["items"][0]
    assert variant_kwargs["max_variants"] == 3
    assert variant_kwargs["max_workers"] == 3
    assert variant_kwargs["max_generation_attempts"] == 1
    assert variant_kwargs["enable_scale_check"] is False
    assert variant_kwargs["furniture_specs_text"]
    assert variant_kwargs["scale_guide_path"] is None
    assert variant_kwargs["primary_item"]["target_key"] == "chair-1"
    assert variant_kwargs["size_hierarchy"] == ["Chair"]
    assert variant_kwargs["scale_plan"] is None
    assert variant_kwargs["geometry_contract"] is None
    assert variant_kwargs["scene_contract"]["critical_item_keys"] == ["chair-1"]
    assert variant_kwargs["placement_plan"] is None
    assert simple_item["crop_path"] == "outputs/chair.png"
    assert "identity_profile" not in simple_item
    assert "product_identity" not in simple_item
    assert "archetype_strategy" not in simple_item
    assert "reference_features" not in simple_item
    assert "layout_envelope" not in simple_item
    assert "placement_contract" not in simple_item
    assert captured["postprocess_kwargs"]["generated_results"] == [
        "outputs/simple-a.png",
        "outputs/simple-b.png",
        "outputs/simple-c.png",
    ]
    assert captured["postprocess_kwargs"]["allow_failed_rerank"] is True
    assert [call["path"] for call in captured["polish_calls"]] == [
        "outputs/simple-b.png",
        "outputs/simple-a.png",
        "outputs/simple-c.png",
    ]
    assert [call["unique_id"] for call in captured["polish_calls"]] == ["job-simple", "job-simple", "job-simple"]
    assert result["result_url"] == "url://outputs/simple-b_polished.png"
    assert result["result_urls"] == [
        "url://outputs/simple-b_polished.png",
        "url://outputs/simple-a_polished.png",
        "url://outputs/simple-c_polished.png",
    ]
    assert result["candidate_result_urls"] == [
        "url://outputs/simple-b.png",
        "url://outputs/simple-a.png",
        "url://outputs/simple-c.png",
    ]
    assert result["scale_plan"]
    assert result["geometry_contract"]["anchor_item_key"] == "chair-1"
    assert result["scene_contract"]["critical_item_keys"] == ["chair-1"]
    assert result["placement_plan"]["placement_zones"] == {"chair-1": "left"}


def test_run_render_room_workflow_uses_diagnostics_fallback_when_rerank_misses(monkeypatch):
    summary_ref = _SummaryRef()
    captured = {}

    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_bootstrap_stage",
        lambda **kwargs: SimpleNamespace(
            unique_id="job-simple-external",
            start_time=0.0,
            summary=_build_summary(),
            summary_token=kwargs["summary_ref"].set(_build_summary()),
        ),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_audience_stage",
        lambda **kwargs: SimpleNamespace(
            audience="external",
            enable_scale_check=False,
            prefix_main_user="main/user",
            prefix_main_empty="main/empty",
            prefix_main_rendered="main/rendered",
            prefix_customize="customize",
        ),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_input_stage",
        lambda **kwargs: SimpleNamespace(timestamp="ts-simple", std_path="outputs/std.png"),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_empty_stage",
        lambda **kwargs: SimpleNamespace(step1_img="outputs/empty.png", step1_raw="raw-empty"),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_scale_stage",
        lambda **kwargs: SimpleNamespace(
            room_dims_parsed={"width_mm": 4000, "depth_mm": 3500, "height_mm": 2400},
            room_dims_valid=True,
            enable_scale_guidance=False,
            room_planes={"floor": "plane"},
            wall_span_norm=(0.0, 1.0),
            windows_present=False,
            room_analysis_text="room analysis",
            furniture_specs_text="verbose specs",
            furniture_specs_json={"items": []},
            primary_item=None,
            scale_guide_path=None,
            size_hierarchy=["Chair"],
            full_analyzed_data=[],
        ),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.prepare_render_references",
        lambda **kwargs: SimpleNamespace(mb_url="moodboard.png", ref_paths=["outputs/ref.png"], item_refs=[]),
    )
    monkeypatch.setattr(
        "application.render.render_room_workflow.run_render_analysis_stage",
        lambda **kwargs: SimpleNamespace(
            windows_present=False,
            room_analysis_text="room analysis",
            room_planes={"floor": "plane"},
            wall_span_norm=(0.0, 1.0),
            furniture_specs_text="verbose specs",
            furniture_specs_json={"items": []},
            full_analyzed_data=[],
            primary_item=None,
            scale_guide_path=None,
            size_hierarchy=["Chair"],
        ),
    )

    def fake_variant_stage(**kwargs):
        return [
            {"path": "outputs/simple-a.png", "weighted_issue_score": 4.0, "review_pass": False, "variant_index": 0},
            {"path": "outputs/simple-b.png", "weighted_issue_score": 1.0, "review_pass": False, "variant_index": 1},
            {"path": "outputs/simple-c.png", "weighted_issue_score": 2.0, "review_pass": False, "variant_index": 2},
        ]

    monkeypatch.setattr("application.render.render_room_workflow.run_render_variant_stage", fake_variant_stage)

    def fake_postprocess(**kwargs):
        captured["postprocess_kwargs"] = dict(kwargs)
        return SimpleNamespace(
            generated_results=["outputs/simple-a.png", "outputs/simple-b.png", "outputs/simple-c.png"],
            full_analyzed_data=list(kwargs["full_analyzed_data"]),
            volume_ranking=[],
            rerank_applied=False,
        )

    monkeypatch.setattr("application.render.render_room_workflow.run_render_postprocess_stage", fake_postprocess)

    def fake_polish(path, **kwargs):
        captured["polish"] = {"path": path, **kwargs}
        return "outputs/simple-b_polished.png"

    request = RenderWorkflowRequest(
        file=object(),
        room="room",
        style="style",
        variant="variant",
        dimensions="4000x3500x2400",
        placement="center",
        audience="external",
        moodboard_items=[],
        simple_generation_mode=False,
    )
    deps = RenderWorkflowDependencies(
        runtime=RenderWorkflowRuntime(
            style_map={"style": "Style"},
            generate_unique_id=lambda: "job-simple-external",
            time_now=lambda: 0.0,
            log_section=lambda *args, **kwargs: None,
            summary_ref=summary_ref,
            reset_summary_token=lambda *args, **kwargs: None,
            logger=_logger(),
            log_brief=False,
            log_summary=False,
            use_s3_moodboard=False,
            max_concurrency_analysis=1,
            cart_max_analysis_workers=1,
            total_timeout_limit_sec=600.0,
        ),
        storage=RenderWorkflowStorageServices(
            normalize_audience=lambda aud: aud or "external",
            build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
            standardize_image=lambda *args, **kwargs: "outputs/std.png",
            materialize_input=lambda *args, **kwargs: "outputs/std.png",
            resolve_image_url=lambda path, **kwargs: f"url://{path}",
            find_s3_moodboard_key=lambda *args, **kwargs: None,
            s3_public_url=lambda path: f"url://{path}",
        ),
        analysis=RenderWorkflowAnalysisServices(
            parse_room_dimensions_mm=lambda dimensions: {"width_mm": 4000, "depth_mm": 3500, "height_mm": 2400},
            room_dims_valid_fn=lambda dims: True,
            build_item_target_key=lambda *args, **kwargs: "target",
            canonical_category=lambda value: value or "unknown",
            detect_furniture_boxes=lambda *args, **kwargs: [],
            analyze_room_structure=lambda *args, **kwargs: {},
            analyze_cropped_item=lambda *args, **kwargs: {},
            normalize_dims_dict=lambda dims: dims,
            parse_object_dimensions_mm=lambda value: {},
            build_furniture_specs_json=lambda *args, **kwargs: {"items": []},
            create_scale_guide_overlay_with_model=lambda *args, **kwargs: None,
            match_aspect_to_target=lambda *args, **kwargs: None,
        ),
        generation=RenderWorkflowGenerationServices(
            generate_empty_room=lambda *args, **kwargs: ("outputs/empty.png", None),
            generate_furnished_room=lambda *args, **kwargs: "outputs/simple-result.png",
            polish_main_image=fake_polish,
        ),
        postprocess=RenderWorkflowPostprocessServices(
            rank_best_variant=lambda *args, **kwargs: None,
            refresh_item_boxes_from_main_render=lambda path, items: items,
            attach_volume_ranks=lambda items: items,
            volume_ranking_snapshot=lambda items: [],
        ),
    )

    result = run_render_room_workflow(request, deps)

    assert captured["postprocess_kwargs"]["generated_results"] == [
        "outputs/simple-a.png",
        "outputs/simple-b.png",
        "outputs/simple-c.png",
    ]
    assert captured["polish"]["path"] == "outputs/simple-b.png"
    assert result["result_url"] == "url://outputs/simple-b_polished.png"
    assert result["result_urls"] == ["url://outputs/simple-b_polished.png"]


def test_prepare_detail_generation_items_simple_mode_refreshes_current_boxes_but_preserves_cached_identity(tmp_path):
    source_path = tmp_path / "main.png"
    source_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    cached_items = [
        {
            "target_key": "chair-1",
            "label": "Chair",
            "box_2d": [100, 100, 700, 700],
            "box_source": "cached_main_render",
            "crop_path": "outputs/chair.png",
            "identity_profile": {"silhouette": "rolled-arm"},
            "reference_features": {"material_cues": ["boucle"]},
            "placement_contract": {"zone": "left"},
        }
    ]
    detect_calls = []
    analyze_calls = []

    result = prepare_detail_generation_items(
        furniture_data=cached_items,
        moodboard_url=None,
        local_path=str(source_path),
        materialize_input=lambda *args, **kwargs: None,
        detect_furniture_boxes=lambda path: detect_calls.append(path) or [{"label": "Chair", "box_2d": [220, 260, 820, 900]}],
        canonical_category=lambda value: value or "unknown",
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index}_{label}",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: analyze_calls.append((path, dict(item))) or {
            **item,
            "crop_path": "outputs/fresh-chair.png",
            "description": "fresh chair analysis",
            "category_canonical": "chair",
        },
        attach_volume_ranks=lambda items: [dict(item, volume_rank=index + 1) for index, item in enumerate(items)],
        normalize_label_for_match=lambda value: str(value or "").strip().lower(),
        simple_generation_mode=True,
    )

    assert detect_calls == [str(source_path)]
    assert len(analyze_calls) == 0
    assert result[0]["target_key"] == "detail_1_Chair"
    assert result[0]["box_2d"] == [220, 260, 820, 900]
    assert result[0]["box_source"] == "detail_current_image_analysis"
    assert "source_box_2d" not in result[0]
    assert "crop_path" not in result[0]
    assert "identity_profile" not in result[0]
    assert "reference_features" not in result[0]
    assert "placement_contract" not in result[0]
    assert result[0]["volume_rank"] == 1
