from application.render.render_response_stage import build_render_response_payload
from application.render.render_room_workflow import _hydrate_item_dims, _sync_furniture_specs_contracts


def test_build_render_response_payload_exposes_room_and_scene_contracts_for_internal_debug():
    payload = build_render_response_payload(
        std_path="outputs/original.png",
        step1_img="outputs/empty.png",
        scale_guide_path=None,
        generated_results=["outputs/result.png"],
        selected_result_index=0,
        selected_result_reason="review_pass_ranked",
        selected_variant_review={"review_pass": True},
        variant_diagnostics=[],
        scale_plan={"strict_scale_requested": True},
        room_dims_contract={"source": "explicit", "confidence": "high"},
        geometry_contract={"strict_scale_ready": True, "anchor_item_key": "sofa-1"},
        scene_contract={"geometry_source": "explicit", "critical_item_keys": ["sofa-1"]},
        placement_plan={"anchor_item_key": "sofa-1", "placement_zones": {"sofa-1": {"zone": "back_wall_anchor_band"}}},
        include_replay_debug=True,
        moodboard_url=None,
        furniture_data=[],
        volume_ranking=[],
        prefix_main_user="internal/mainrendered/user/",
        prefix_main_empty="internal/mainrendered/empty/",
        prefix_main_rendered="internal/mainrendered/rendered/",
        resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{path}",
    )

    assert payload["room_dims_contract"]["source"] == "explicit"
    assert payload["geometry_contract"]["strict_scale_ready"] is True
    assert payload["scene_contract"]["geometry_source"] == "explicit"
    assert payload["scene_contract"]["critical_item_keys"] == ["sofa-1"]
    assert payload["placement_plan"]["anchor_item_key"] == "sofa-1"


def test_hydrate_item_dims_promotes_requested_dims_and_identity_dims():
    hydrated = _hydrate_item_dims(
        {
            "target_key": "sofa-1",
            "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1100},
            "product_identity": {"dims_mm": {"height_mm": 800}},
        }
    )

    assert hydrated["dims_mm"] == {
        "width_mm": 2400,
        "depth_mm": 1100,
        "height_mm": 800,
        "radius_mm": None,
    }


def test_sync_furniture_specs_contracts_rehydrates_primary_from_analyzed_items():
    furniture_specs_json = {
        "items": [{"target_key": "sofa-1"}],
        "primary": {"target_key": "sofa-1"},
        "primary_scale": {"target_key": "sofa-1"},
    }
    analyzed_items = [
        {
            "target_key": "sofa-1",
            "requested_dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800},
            "product_identity": {"dims_mm": {"width_mm": 2400, "depth_mm": 1100, "height_mm": 800}},
            "identity_profile": {"family": "sofa"},
            "archetype_strategy": {"render_strategy": "topology_sensitive_seating", "strictness": "critical"},
            "layout_envelope": {"room_width_ratio": 0.6},
            "placement_contract": {"zone": "back_wall"},
        }
    ]

    synced = _sync_furniture_specs_contracts(furniture_specs_json, analyzed_items, placement_plan=None)

    assert synced["primary"]["dims_mm"] == {
        "width_mm": 2400,
        "depth_mm": 1100,
        "height_mm": 800,
        "radius_mm": None,
    }
    assert synced["primary_scale"]["dims_mm"] == {
        "width_mm": 2400,
        "depth_mm": 1100,
        "height_mm": 800,
        "radius_mm": None,
    }
    assert synced["items"][0]["identity_profile"]["family"] == "sofa"
    assert synced["items"][0]["archetype_strategy"]["render_strategy"] == "topology_sensitive_seating"
    assert synced["primary"]["archetype_strategy"]["strictness"] == "critical"


def test_build_render_response_payload_blocks_final_result_but_still_returns_best_effort_candidate():
    payload = build_render_response_payload(
        std_path="outputs/original.png",
        step1_img="outputs/empty.png",
        scale_guide_path=None,
        generated_results=[],
        candidate_results=["outputs/result_a.png", "outputs/result_b.png"],
        selected_result_index=None,
        selected_result_reason="strict_hard_qc_blocked",
        selected_variant_review=None,
        variant_diagnostics=[],
        final_result_blocked=True,
        scale_plan={"strict_scale_requested": True},
        room_dims_contract={"source": "explicit", "confidence": "high"},
        geometry_contract={"strict_scale_ready": True, "anchor_item_key": "sofa-1"},
        scene_contract={"geometry_source": "explicit", "critical_item_keys": ["sofa-1"]},
        placement_plan={"anchor_item_key": "sofa-1"},
        include_replay_debug=True,
        moodboard_url=None,
        furniture_data=[],
        volume_ranking=[],
        prefix_main_user="internal/mainrendered/user/",
        prefix_main_empty="internal/mainrendered/empty/",
        prefix_main_rendered="internal/mainrendered/rendered/",
        resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{path}",
    )

    assert payload["result_url"] == "https://cdn.example/outputs/result_a.png"
    assert payload["result_urls"] == [
        "https://cdn.example/outputs/result_a.png",
        "https://cdn.example/outputs/result_b.png",
    ]
    assert payload["final_result_blocked"] is True
    assert payload["candidate_result_urls"] == [
        "https://cdn.example/outputs/result_a.png",
        "https://cdn.example/outputs/result_b.png",
    ]
    assert payload["selected_result_filename"] == "result_a.png"
    assert payload["message"] == "QC blocked final selection"


def test_build_render_response_payload_blocks_final_result_but_falls_back_to_empty_room_when_no_candidates():
    payload = build_render_response_payload(
        std_path="outputs/original.png",
        step1_img="outputs/empty.png",
        scale_guide_path=None,
        generated_results=[],
        candidate_results=[],
        selected_result_index=None,
        selected_result_reason="strict_hard_qc_blocked",
        selected_variant_review=None,
        variant_diagnostics=[],
        final_result_blocked=True,
        scale_plan={"strict_scale_requested": True},
        room_dims_contract={"source": "estimated", "confidence": "medium"},
        geometry_contract={"strict_scale_ready": False, "anchor_item_key": "sofa-1"},
        scene_contract={"geometry_source": "estimated", "critical_item_keys": ["sofa-1"]},
        placement_plan={"anchor_item_key": "sofa-1"},
        include_replay_debug=True,
        moodboard_url=None,
        furniture_data=[],
        volume_ranking=[],
        prefix_main_user="internal/mainrendered/user/",
        prefix_main_empty="internal/mainrendered/empty/",
        prefix_main_rendered="internal/mainrendered/rendered/",
        resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{path}",
    )

    assert payload["result_url"] == "https://cdn.example/outputs/empty.png"
    assert payload["result_urls"] == ["https://cdn.example/outputs/empty.png"]
    assert payload["final_result_blocked"] is True
    assert payload["candidate_result_urls"] == []
    assert payload["selected_result_filename"] == "empty.png"
    assert payload["message"] == "QC blocked final selection"
