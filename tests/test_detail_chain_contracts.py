import unittest
from pathlib import Path

from api_models import DetailRequest, RegenerateDetailRequest
from application.details.detail_analysis_stage import load_analyzed_items, prepare_detail_generation_items
from application.details.detail_generation_stage import generate_detail_view as actual_generate_detail_view
from application.details.detail_style_stage import construct_dynamic_styles
from application.details.regenerate_detail_workflow import run_regenerate_single_detail_job
from application.details.detail_workflow import (
    _reconcile_internal_side_angle_results,
    run_generate_details_job,
    select_external_detail_styles,
)
from application.render.render_result_stage import build_detail_payload
from application.render.render_workflow import run_render_with_details_job
from infrastructure.ai.service_scope import ai_service_scope, current_ai_service_scope
from render_route_services import (
    build_detail_generation_job_payload,
    build_regenerate_detail_job_payload,
)


def test_reconcile_internal_side_angle_results_drops_crossed_camera_directions():
    generated_paths = [
        {
            "index": 1,
            "path": "requested-left-actual-right.jpg",
            "style_name": "Side Composition (Focus Left)",
            "style_ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "left",
            "requested_focus_side": "left",
            "camera_travel_side": "right",
            "camera_direction_matches": False,
            "angle_direction_fallback": True,
            "angle_qc": {
                "passed": True,
                "passed_for_requested_slot": False,
                "direction_only_mismatch": True,
            },
        },
        {
            "index": 2,
            "path": "requested-right-actual-left.jpg",
            "style_name": "Side Composition (Focus Right)",
            "style_ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "right",
            "requested_focus_side": "right",
            "camera_travel_side": "left",
            "camera_direction_matches": False,
            "angle_direction_fallback": True,
            "angle_qc": {
                "passed": True,
                "passed_for_requested_slot": False,
                "direction_only_mismatch": True,
            },
        },
    ]

    _reconcile_internal_side_angle_results(generated_paths)

    assert generated_paths == []


def test_reconcile_internal_side_angle_results_drops_wrong_slot_duplicate_of_exact_side(
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    wrong_path = output_dir / "requested-left-actual-right.jpg"
    exact_path = output_dir / "requested-right-actual-right.jpg"
    wrong_path.write_bytes(b"wrong")
    exact_path.write_bytes(b"exact")
    styles = [
        {"name": "High Angle Overview", "ratio": "16:9", "camera_mode": "overview_angle"},
        {
            "name": "Side Composition (Focus Left)",
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "left",
        },
        {
            "name": "Side Composition (Focus Right)",
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "right",
        },
    ]
    generated_paths = [
        {
            "index": 1,
            "path": str(wrong_path),
            "style_name": "Side Composition (Focus Left)",
            "style_ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "left",
            "requested_focus_side": "left",
            "camera_travel_side": "right",
            "camera_direction_matches": False,
            "angle_direction_fallback": True,
            "angle_qc": {
                "passed": True,
                "passed_for_requested_slot": False,
                "direction_only_mismatch": True,
                "metrics": {"camera_motion_score": 0.95, "model_confidence": 0.98},
            },
        },
        {
            "index": 2,
            "path": str(exact_path),
            "style_name": "Side Composition (Focus Right)",
            "style_ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "right",
            "requested_focus_side": "right",
            "camera_travel_side": "right",
            "camera_direction_matches": True,
            "angle_qc": {
                "passed": True,
                "passed_for_requested_slot": True,
                "direction_only_mismatch": False,
                "metrics": {"camera_motion_score": 0.70, "model_confidence": 0.80},
            },
        },
    ]

    _reconcile_internal_side_angle_results(generated_paths, styles)

    assert len(generated_paths) == 1
    assert generated_paths[0]["path"] == str(exact_path)
    assert generated_paths[0]["index"] == 2
    assert generated_paths[0]["focus_side"] == "right"
    assert generated_paths[0]["camera_travel_side"] == "right"
    assert generated_paths[0]["camera_direction_matches"] is True
    assert not wrong_path.exists()
    assert exact_path.exists()


def test_reconcile_internal_side_angle_results_drops_single_opposite_when_exact_slot_missing():
    styles = [
        {"name": "High Angle Overview", "ratio": "16:9", "camera_mode": "overview_angle"},
        {
            "name": "Side Composition (Focus Left)",
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "left",
        },
        {
            "name": "Side Composition (Focus Right)",
            "ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "right",
        },
    ]
    generated_paths = [
        {
            "index": 1,
            "path": "requested-left-actual-right.jpg",
            "style_name": "Side Composition (Focus Left)",
            "style_ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "left",
            "requested_focus_side": "left",
            "camera_travel_side": "right",
            "camera_direction_matches": False,
            "angle_direction_fallback": True,
            "angle_qc": {
                "passed": True,
                "passed_for_requested_slot": False,
                "direction_only_mismatch": True,
                "metrics": {"camera_motion_score": 0.88, "model_confidence": 0.92},
            },
        },
    ]

    _reconcile_internal_side_angle_results(generated_paths, styles)

    assert generated_paths == []


def test_reconcile_internal_side_angle_results_drops_unverified_exact_side():
    generated_paths = [
        {
            "index": 1,
            "path": "requested-left-actual-left-without-qc.jpg",
            "style_name": "Side Composition (Focus Left)",
            "style_ratio": "16:9",
            "camera_mode": "side_angle",
            "focus_side": "left",
            "requested_focus_side": "left",
            "camera_travel_side": "left",
            "camera_direction_matches": True,
        },
    ]

    _reconcile_internal_side_angle_results(generated_paths)

    assert generated_paths == []


class DetailChainContractsTests(unittest.TestCase):
    def test_select_external_detail_styles_prefers_product_backed_targets_at_six(self):
        styles = [
            {"name": "Detail: Sofa", "target_key": "cart_product-1_sofa", "detail_mode": "product_identity_lock"},
            {"name": "Detail: Coffee Table", "target_key": "cart_product-2_table", "detail_mode": "product_identity_lock"},
            {"name": "Detail: Lounge Chair", "target_key": "cart_product-3_chair", "detail_mode": "product_identity_lock"},
            {"name": "Detail: Floor Lamp", "target_key": "cart_product-4_lamp", "detail_mode": "product_identity_lock"},
            {"name": "Detail: Wall Clock", "target_key": "cart_product-5_clock", "detail_mode": "product_identity_lock"},
            {"name": "Detail: Side Table", "target_key": "cart_product-6_side-table", "detail_mode": "product_identity_lock"},
            {"name": "Detail: Generic Vase", "target_key": "detail_vase", "simple_scene_detail": True},
            {"name": "Detail: Generic Books", "target_key": "detail_books", "simple_scene_detail": True},
        ]

        selected = select_external_detail_styles(styles)

        self.assertEqual(
            [style["name"] for style in selected],
            [
                "Detail: Sofa",
                "Detail: Coffee Table",
                "Detail: Lounge Chair",
                "Detail: Floor Lamp",
                "Detail: Wall Clock",
                "Detail: Side Table",
            ],
        )

    def test_select_external_detail_styles_delays_overlapping_crop_targets(self):
        styles = [
            {
                "name": "Detail: Sofa",
                "target_key": "cart_product-38543_sofa_007",
                "detail_mode": "product_identity_lock",
                "target_box_2d": [548, 203, 778, 776],
                "target_category": "main_sofa",
            },
            {
                "name": "Detail: Rio Table",
                "target_key": "cart_product-37582_table_008",
                "detail_mode": "product_identity_lock",
                "target_box_2d": [648, 408, 817, 619],
                "target_category": "sofa_table",
            },
            {
                "name": "Detail: Tabletop Decor",
                "target_key": "cart_product-39080_decor_003",
                "detail_mode": "product_identity_lock",
                "target_box_2d": [556, 462, 692, 510],
                "target_category": "decor",
            },
            {
                "name": "Detail: Zig Chair",
                "target_key": "cart_product-37426_chair_009",
                "detail_mode": "product_identity_lock",
                "target_box_2d": [581, 698, 893, 822],
                "target_category": "dining_chair",
            },
            {
                "name": "Detail: Floor Lamp",
                "target_key": "cart_product-39522_floor-lamp_001",
                "detail_mode": "product_identity_lock",
                "target_box_2d": [370, 338, 545, 362],
                "target_category": "floor_lamp",
            },
            {
                "name": "Detail: Pendant",
                "target_key": "cart_product-38668_pendant_006",
                "detail_mode": "product_identity_lock",
                "target_box_2d": [76, 468, 320, 551],
                "target_category": "light",
            },
        ]

        selected = select_external_detail_styles(styles)

        self.assertEqual(len(selected), 6)
        self.assertEqual(
            [style["name"] for style in selected[:3]],
            ["Detail: Sofa", "Detail: Zig Chair", "Detail: Floor Lamp"],
        )
        self.assertEqual(
            [style["name"] for style in selected[3:]],
            ["Detail: Pendant", "Detail: Rio Table", "Detail: Tabletop Decor"],
        )

    def test_select_external_detail_styles_fills_six_unique_targets_before_duplicate_overlap(self):
        styles = [
            {
                "name": "Detail: Lounge Chair",
                "target_key": "detail_lounge-chair_001",
                "target_label": "Lounge Chair",
                "target_category": "lounge_chair",
                "target_box_2d": [520, 640, 860, 830],
            },
            {
                "name": "Detail: Armchair",
                "target_key": "detail_armchair_002",
                "target_label": "Armchair",
                "target_category": "lounge_chair",
                "target_box_2d": [528, 648, 858, 826],
            },
            {
                "name": "Detail: Sofa",
                "target_key": "detail_sofa_003",
                "target_label": "Sofa",
                "target_category": "main_sofa",
                "target_box_2d": [540, 120, 820, 480],
            },
            {
                "name": "Detail: Coffee Table",
                "target_key": "detail_table_004",
                "target_label": "Coffee Table",
                "target_category": "coffee_table",
                "target_box_2d": [660, 420, 820, 610],
            },
            {
                "name": "Detail: Floor Lamp",
                "target_key": "detail_lamp_005",
                "target_label": "Floor Lamp",
                "target_category": "floor_lamp",
                "target_box_2d": [250, 820, 760, 900],
            },
            {
                "name": "Detail: Side Table",
                "target_key": "detail_side-table_006",
                "target_label": "Side Table",
                "target_category": "side_table",
                "target_box_2d": [610, 500, 810, 680],
            },
            {
                "name": "Detail: Pendant",
                "target_key": "detail_pendant_007",
                "target_label": "Pendant",
                "target_category": "pendant",
                "target_box_2d": [80, 450, 300, 560],
            },
        ]

        selected = select_external_detail_styles(styles)

        self.assertEqual(len(selected), 6)
        self.assertNotIn("detail_armchair_002", [style.get("target_key") for style in selected])
        self.assertEqual(
            [style.get("target_key") for style in selected],
            [
                "detail_lounge-chair_001",
                "detail_sofa_003",
                "detail_table_004",
                "detail_lamp_005",
                "detail_side-table_006",
                "detail_pendant_007",
            ],
        )

    def test_select_external_detail_styles_keeps_order_when_under_limit(self):
        three_styles = [{"name": f"Detail: item-{idx}"} for idx in range(1, 4)]
        four_styles = [{"name": f"Detail: item-{idx}"} for idx in range(1, 5)]

        self.assertEqual(
            [style["name"] for style in select_external_detail_styles(three_styles)],
            ["Detail: item-1", "Detail: item-2", "Detail: item-3"],
        )
        self.assertEqual(
            [style["name"] for style in select_external_detail_styles(four_styles)],
            ["Detail: item-1", "Detail: item-2", "Detail: item-3", "Detail: item-4"],
        )

    def test_build_detail_payload_preserves_itemized_context(self):
        render_result = {
            "result_urls": ["https://cdn.example/rendered/main-1.png"],
            "empty_room_url": "https://cdn.example/rendered/empty-1.png",
            "moodboard_url": None,
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
            "room_dims_contract": {"dims_mm_center": {"width_mm": 5000}},
            "geometry_contract": {"geometry_source": "explicit_dimensions"},
            "scene_contract": {"critical_item_keys": ["detail_001"]},
            "placement_plan": {"anchor_item_key": "detail_001"},
        }

        payload = build_detail_payload(render_result, audience="internal")

        self.assertEqual(payload["image_url"], "https://cdn.example/rendered/main-1.png")
        self.assertIsNone(payload["moodboard_url"])
        self.assertEqual(payload["furniture_data"], [{"label": "Accent Chair", "target_key": "detail_001"}])
        self.assertEqual(payload["audience"], "internal")
        self.assertEqual(payload["empty_room_url"], "https://cdn.example/rendered/empty-1.png")
        self.assertEqual(payload["room_dims_contract"]["dims_mm_center"]["width_mm"], 5000)
        self.assertEqual(payload["geometry_contract"]["geometry_source"], "explicit_dimensions")
        self.assertEqual(payload["scene_contract"]["critical_item_keys"], ["detail_001"])
        self.assertEqual(payload["placement_plan"]["anchor_item_key"], "detail_001")

    def test_detail_generation_job_payload_keeps_furniture_data_without_moodboard(self):
        req = DetailRequest(
            image_url="https://cdn.example/rendered/main-1.png",
            empty_room_url="https://cdn.example/rendered/empty-1.png",
            furniture_data=[{"label": "Accent Chair", "target_key": "detail_001"}],
            room_dims_contract={"dims_mm_center": {"width_mm": 5000}},
            geometry_contract={"geometry_source": "explicit_dimensions"},
            scene_contract={"critical_item_keys": ["detail_001"]},
            placement_plan={"anchor_item_key": "detail_001"},
            audience="internal",
            require_details=True,
        )

        payload = build_detail_generation_job_payload(req)

        self.assertEqual(payload["image_url"], "https://cdn.example/rendered/main-1.png")
        self.assertEqual(payload["empty_room_url"], "https://cdn.example/rendered/empty-1.png")
        self.assertIsNone(payload["moodboard_url"])
        self.assertEqual(payload["furniture_data"], [{"label": "Accent Chair", "target_key": "detail_001"}])
        self.assertEqual(payload["room_dims_contract"]["dims_mm_center"]["width_mm"], 5000)
        self.assertEqual(payload["geometry_contract"]["geometry_source"], "explicit_dimensions")
        self.assertEqual(payload["scene_contract"]["critical_item_keys"], ["detail_001"])
        self.assertEqual(payload["placement_plan"]["anchor_item_key"], "detail_001")
        self.assertEqual(payload["audience"], "internal")
        self.assertIs(payload["require_details"], True)

    def test_regenerate_detail_job_payload_keeps_target_metadata_and_furniture_data(self):
        req = RegenerateDetailRequest(
            original_image_url="https://cdn.example/rendered/main-1.png",
            empty_room_url="https://cdn.example/rendered/empty-1.png",
            style_index=2,
            target_key="detail_001",
            target_label="Accent Chair",
            target_box_2d=[10, 20, 200, 220],
            target_source_box_2d=[12, 24, 198, 218],
            style_index_mode="overall",
            furniture_data=[{"label": "Accent Chair", "target_key": "detail_001"}],
            room_dims_contract={"dims_mm_center": {"width_mm": 5000}},
            geometry_contract={"geometry_source": "explicit_dimensions"},
            scene_contract={"critical_item_keys": ["detail_001"]},
            placement_plan={"anchor_item_key": "detail_001"},
            audience="internal",
        )

        payload = build_regenerate_detail_job_payload(req)

        self.assertEqual(payload["original_image_url"], "https://cdn.example/rendered/main-1.png")
        self.assertEqual(payload["empty_room_url"], "https://cdn.example/rendered/empty-1.png")
        self.assertEqual(payload["style_index"], 2)
        self.assertEqual(payload["target_key"], "detail_001")
        self.assertEqual(payload["target_label"], "Accent Chair")
        self.assertEqual(payload["target_box_2d"], [10, 20, 200, 220])
        self.assertEqual(payload["target_source_box_2d"], [12, 24, 198, 218])
        self.assertEqual(payload["style_index_mode"], "overall")
        self.assertIsNone(payload["moodboard_url"])
        self.assertEqual(payload["furniture_data"], [{"label": "Accent Chair", "target_key": "detail_001"}])
        self.assertEqual(payload["room_dims_contract"]["dims_mm_center"]["width_mm"], 5000)
        self.assertEqual(payload["geometry_contract"]["geometry_source"], "explicit_dimensions")
        self.assertEqual(payload["scene_contract"]["critical_item_keys"], ["detail_001"])
        self.assertEqual(payload["placement_plan"]["anchor_item_key"], "detail_001")
        self.assertEqual(payload["audience"], "internal")

    def test_run_regenerate_single_detail_job_rehydrates_requested_target_when_snapshot_missing(self):
        source_path = Path("outputs/test-regenerate-source.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        detect_calls = []
        generation_kwargs = []

        try:
            result = run_regenerate_single_detail_job(
                {
                    "original_image_url": str(source_path),
                    "style_index": 4,
                    "style_index_mode": "overall",
                    "target_key": "detail_001_floor-lamp",
                    "target_label": "Floor Lamp",
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{Path(path).name}" if path else None,
                detect_furniture_boxes=lambda path: detect_calls.append(path) or [{"label": "Floor Lamp", "box_2d": [10, 20, 300, 400]}],
                canonical_category=lambda label: str(label or "").lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: {
                    **item,
                    "description": "Tall articulated floor lamp with cream shade",
                    "crop_path": str(source_path),
                    "category_canonical": "floor_lamp",
                    "box_source": "detail_current_image_analysis",
                },
                attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
                construct_dynamic_styles=lambda items: [
                    {"name": "High Angle Overview"},
                    {"name": "Side Composition (Focus Left)"},
                    {"name": "Side Composition (Focus Right)"},
                    {
                        "name": "Detail: Floor Lamp",
                        "target_key": items[0].get("target_key"),
                        "target_label": items[0].get("label"),
                    },
                ],
                normalize_label_for_match=lambda text: str(text).strip().lower(),
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: (
                    generation_kwargs.append(kwargs)
                    or {
                        "path": original_image_path,
                        "style_name": style_config.get("name"),
                    }
                ),
                volume_ranking_snapshot=lambda items: [{"target_key": item.get("target_key")} for item in items if isinstance(item, dict)],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(len(detect_calls), 1)
        self.assertEqual(result["target_label"], "Floor Lamp")
        self.assertEqual(result["requested_target_key"], "detail_001_floor-lamp")
        self.assertEqual(result["resolved_by"], "requested_target_fallback->target_key")
        self.assertEqual(result["furniture_data"][0]["target_key"], "detail_001_floor-lamp")
        self.assertEqual(result["furniture_data"][0]["box_source"], "detail_current_image_analysis")
        self.assertEqual(generation_kwargs[0].get("prefer_crop_extract"), False)

    def test_run_regenerate_single_detail_job_preserves_requested_target_even_when_box_overlaps_other_detection(self):
        source_path = Path("outputs/test-regenerate-box-source.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        detections = [
            {"label": "Wardrobe", "box_2d": [100, 100, 300, 320]},
            {"label": "Floor Lamp", "box_2d": [350, 500, 700, 620]},
        ]

        try:
            result = run_regenerate_single_detail_job(
                {
                    "original_image_url": str(source_path),
                    "style_index": 4,
                    "style_index_mode": "auto",
                    "target_key": "legacy_cabinet_key",
                    "target_label": "Cabinet",
                    "target_box_2d": [102, 104, 298, 318],
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{Path(path).name}" if path else None,
                detect_furniture_boxes=lambda path: detections,
                canonical_category=lambda label: str(label or "").lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: {
                    **item,
                    "description": f"{item['label']} description",
                    "crop_path": str(source_path),
                    "category_canonical": str(item["label"]).lower().replace(" ", "_"),
                    "box_source": "detail_current_image_analysis",
                },
                attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
                construct_dynamic_styles=lambda items: [
                    {"name": "High Angle Overview"},
                    {"name": "Side Composition (Focus Left)"},
                    {"name": "Side Composition (Focus Right)"},
                    {
                        "name": f"Detail: {items[0].get('label')}",
                        "target_key": items[0].get("target_key"),
                        "target_label": items[0].get("label"),
                    },
                    {
                        "name": f"Detail: {items[1].get('label')}",
                        "target_key": items[1].get("target_key"),
                        "target_label": items[1].get("label"),
                    },
                ],
                normalize_label_for_match=lambda text: str(text).strip().lower(),
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: {
                    "path": original_image_path,
                    "style_name": style_config.get("name"),
                },
                volume_ranking_snapshot=lambda items: [{"target_key": item.get("target_key")} for item in items if isinstance(item, dict)],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(result["style_name"], "Detail: Cabinet")
        self.assertEqual(result["target_label"], "Cabinet")
        self.assertEqual(result["target_key"], "legacy_cabinet_key")
        self.assertEqual(result["resolved_by"], "requested_target_fallback->target_key")
        self.assertEqual(result["furniture_data"][0]["target_key"], "legacy_cabinet_key")
        self.assertEqual(result["furniture_data"][0]["box_source"], "detail_current_image_analysis")

    def test_run_regenerate_single_detail_job_preserves_requested_target_when_analysis_falls_back(self):
        source_path = Path("outputs/test-regenerate-generic-fallback.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        try:
            result = run_regenerate_single_detail_job(
                {
                    "original_image_url": str(source_path),
                    "style_index": 9,
                    "style_index_mode": "auto",
                    "target_key": "detail_pillow_022",
                    "target_label": "Pillow",
                    "target_box_2d": [420, 410, 560, 590],
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{Path(path).name}" if path else None,
                detect_furniture_boxes=lambda path: [],
                canonical_category=lambda label: str(label or "").lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: item,
                attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
                construct_dynamic_styles=lambda items: [
                    {"name": "High Angle Overview"},
                    {"name": "Side Composition (Focus Left)"},
                    {"name": "Side Composition (Focus Right)"},
                    {
                        "name": f"Detail: {items[0].get('label')}",
                        "target_key": items[0].get("target_key"),
                        "target_label": items[0].get("label"),
                    },
                ],
                normalize_label_for_match=lambda text: str(text).strip().lower(),
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: {
                    "path": original_image_path,
                    "style_name": style_config.get("name"),
                },
                volume_ranking_snapshot=lambda items: [{"target_key": item.get("target_key")} for item in items if isinstance(item, dict)],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(result["style_name"], "Detail: Pillow")
        self.assertEqual(result["target_key"], "detail_pillow_022")
        self.assertEqual(result["target_label"], "Pillow")
        self.assertEqual(result["resolved_by"], "requested_target_fallback->target_key")

    def test_run_regenerate_single_detail_job_preserves_requested_target_among_unmatched_analysis_items(self):
        source_path = Path("outputs/test-regenerate-unmatched-analysis.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        detections = [
            {"label": "Pendant Lamp", "box_2d": [20, 20, 140, 120]},
            {"label": "Dining Table", "box_2d": [300, 300, 700, 700]},
            {"label": "Sofa", "box_2d": [420, 410, 980, 900]},
        ]

        try:
            result = run_regenerate_single_detail_job(
                {
                    "original_image_url": str(source_path),
                    "style_index": 21,
                    "style_index_mode": "auto",
                    "target_key": "detail_pillow_022",
                    "target_label": "Pillow",
                    "target_box_2d": [540, 485, 670, 620],
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{Path(path).name}" if path else None,
                detect_furniture_boxes=lambda path: detections,
                canonical_category=lambda label: str(label or "").lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: {
                    **item,
                    "description": f"{item['label']} description",
                    "crop_path": str(source_path),
                    "category_canonical": str(item["label"]).lower().replace(" ", "_"),
                },
                attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
                construct_dynamic_styles=lambda items: [
                    {"name": "High Angle Overview"},
                    {"name": "Side Composition (Focus Left)"},
                    {"name": "Side Composition (Focus Right)"},
                    *[
                        {
                            "name": f"Detail: {item.get('label')}",
                            "target_key": item.get("target_key"),
                            "target_label": item.get("label"),
                        }
                        for item in items
                    ],
                ],
                normalize_label_for_match=lambda text: str(text).strip().lower(),
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: {
                    "path": original_image_path,
                    "style_name": style_config.get("name"),
                },
                volume_ranking_snapshot=lambda items: [{"target_key": item.get("target_key")} for item in items if isinstance(item, dict)],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(result["style_name"], "Detail: Pillow")
        self.assertEqual(result["target_key"], "detail_pillow_022")
        self.assertEqual(result["target_label"], "Pillow")
        self.assertEqual(result["resolved_by"], "requested_target_fallback->target_key")

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

    def test_prepare_detail_generation_items_preserves_cached_items_without_detection_only_targets(self):
        source_path = Path("outputs/test-detail-current-main.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        cached_items = [
            {
                "label": "Coffee Table",
                "target_key": "cached_table_001",
                "box_2d": [100, 120, 220, 320],
                "crop_path": "cached-table.png",
                "description": "Round fluted coffee table",
                "category": "table",
                "category_canonical": "table",
                "volume_rank": 2,
            }
        ]

        fresh_detections = [
            {"label": "Coffee Table", "box_2d": [420, 430, 640, 700]},
            {"label": "Armchair", "box_2d": [310, 720, 640, 860]},
        ]

        try:
            prepared = prepare_detail_generation_items(
                furniture_data=cached_items,
                moodboard_url=None,
                local_path=str(source_path),
                materialize_input=lambda url, prefix: url,
                detect_furniture_boxes=lambda path: fresh_detections,
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: {
                    **item,
                    "crop_path": f"fresh-{str(item['label']).lower().replace(' ', '-')}.png",
                    "description": f"fresh {item['label']}",
                    "category_canonical": str(item["label"]).strip().lower().replace(" ", "_"),
                },
                attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
            )

            self.assertEqual(len(prepared), 1)
            coffee_table = prepared[0]
            self.assertEqual(coffee_table["target_key"], "cached_table_001")
            self.assertEqual(coffee_table["crop_path"], "cached-table.png")
            self.assertEqual(coffee_table["description"], "Round fluted coffee table")
            self.assertEqual(coffee_table["box_2d"], [100, 120, 220, 320])
            self.assertNotIn("detail_002_armchair", [row.get("target_key") for row in prepared])
        finally:
            source_path.unlink(missing_ok=True)

    def test_prepare_detail_generation_items_uses_product_specific_localization_not_generic_detection_order(self):
        source_path = Path("outputs/test-detail-product-localization.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        cached_items = [
            {
                "label": "Gubi F300 Lounge Chair",
                "target_key": "cart_37694_gubi-f300_001",
                "box_2d": [650, 650, 910, 870],
                "box_source": "main_render",
                "box_label_detected": "accent chair",
                "box_match_score": 0.56,
                "box_match_strategy": "score_threshold",
                "crop_path": "f300.png",
                "category": "lounge_chair",
                "category_canonical": "lounge_chair",
                "volume_rank": 1,
            },
            {
                "label": "Cassina Soriana Armchair",
                "target_key": "cart_37692_soriana_002",
                "box_2d": [630, 110, 870, 290],
                "box_source": "main_render",
                "box_label_detected": "armchair",
                "box_match_score": 0.50,
                "box_match_strategy": "family_score_threshold",
                "crop_path": "soriana.png",
                "category": "lounge_chair",
                "category_canonical": "lounge_chair",
                "volume_rank": 2,
            },
        ]
        fresh_detections = [
            {"label": "armchair", "box_2d": [630, 110, 870, 290]},
            {"label": "accent chair", "box_2d": [650, 650, 910, 870]},
        ]
        localized = {
            "Gubi F300 Lounge Chair": (0.12, 0.14, 0.30, 0.42),
            "Cassina Soriana Armchair": (0.64, 0.66, 0.86, 0.91),
        }

        try:
            prepared = prepare_detail_generation_items(
                furniture_data=cached_items,
                moodboard_url=None,
                local_path=str(source_path),
                materialize_input=lambda url, prefix: url,
                detect_furniture_boxes=lambda path: fresh_detections,
                detect_item_bbox_norm=lambda staged_path, crop_path, label, item_context=None, timeout_sec=None: localized.get(label),
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: item,
                attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual([row["target_key"] for row in prepared], ["cart_37694_gubi-f300_001", "cart_37692_soriana_002"])
        self.assertEqual(prepared[0]["box_2d"], [140, 120, 420, 300])
        self.assertEqual(prepared[1]["box_2d"], [660, 640, 910, 860])
        self.assertEqual(prepared[0]["box_source"], "product_reference_localization")
        self.assertEqual(prepared[1]["box_source"], "product_reference_localization")
        self.assertEqual(prepared[0]["source_box_2d"], [650, 650, 910, 870])
        self.assertEqual(prepared[1]["source_box_2d"], [630, 110, 870, 290])
        self.assertNotIn("detail_001_armchair", [row.get("target_key") for row in prepared])

    def test_construct_dynamic_styles_skips_product_backed_items_without_verified_localization(self):
        styles = construct_dynamic_styles(
            [
                {
                    "label": "Gubi F300 Lounge Chair",
                    "target_key": "cart_37694_gubi-f300_001",
                    "box_2d": [650, 650, 910, 870],
                    "box_source": "main_render",
                    "box_label_detected": "accent chair",
                    "box_match_score": 0.56,
                    "box_match_strategy": "score_threshold",
                    "crop_path": "f300.png",
                    "category": "lounge_chair",
                    "category_canonical": "lounge_chair",
                    "detail_localization_status": "unverified",
                    "detail_skip_reason": "product_reference_localization_missing",
                },
                {
                    "label": "Cassina Soriana Armchair",
                    "target_key": "cart_37692_soriana_002",
                    "box_2d": [660, 640, 910, 860],
                    "box_source": "product_reference_localization",
                    "crop_path": "soriana.png",
                    "category": "lounge_chair",
                    "category_canonical": "lounge_chair",
                    "detail_localization_status": "product_reference_verified",
                },
            ]
        )

        self.assertEqual([style["target_key"] for style in styles], ["cart_37692_soriana_002"])

    def test_prepare_detail_generation_items_preserves_moodboard_analysis_when_no_cached_snapshot(self):
        source_path = Path("outputs/test-detail-main-image.png")
        moodboard_path = Path("outputs/test-detail-moodboard.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        moodboard_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        detect_calls = []

        try:
            prepared = prepare_detail_generation_items(
                furniture_data=None,
                moodboard_url="mock://moodboard",
                local_path=str(source_path),
                materialize_input=lambda url, prefix: str(moodboard_path) if url == "mock://moodboard" else url,
                detect_furniture_boxes=lambda path: detect_calls.append(path) or [{"label": "Chair", "box_2d": [100, 120, 220, 320]}],
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: {**item, "crop_path": "fresh-chair.png", "description": "chair", "category_canonical": "chair"},
                attach_volume_ranks=lambda items: items,
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
            )
        finally:
            source_path.unlink(missing_ok=True)
            moodboard_path.unlink(missing_ok=True)

        self.assertEqual(detect_calls, [str(moodboard_path)])
        self.assertEqual(prepared[0]["label"], "Chair")

    def test_run_generate_details_job_uses_cached_item_targets_without_detection_only_targets(self):
        source_path = Path("outputs/test-detail-generate-current.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        captured = {}

        def _construct_dynamic_styles(items):
            captured["style_targets"] = [row.get("target_key") for row in items]
            return [
                {
                    "name": f"Detail: {row.get('label')}",
                    "target_key": row.get("target_key"),
                    "target_label": row.get("label"),
                }
                for row in items
            ]

        try:
            result = run_generate_details_job(
                {
                    "image_url": str(source_path),
                    "furniture_data": [
                        {
                            "label": "Coffee Table",
                            "target_key": "cached_table_001",
                            "box_2d": [100, 120, 220, 320],
                            "crop_path": "cached-table.png",
                            "description": "Round fluted coffee table",
                            "category": "table",
                            "category_canonical": "table",
                        }
                    ],
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                persist_job_result=lambda payload, audience=None: None,
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
                log_section=lambda message: None,
                detect_furniture_boxes=lambda path: [
                    {"label": "Coffee Table", "box_2d": [420, 430, 640, 700]},
                    {"label": "Armchair", "box_2d": [310, 720, 640, 860]},
                ],
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: {
                    **item,
                    "crop_path": f"fresh-{str(item['label']).lower().replace(' ', '-')}.png",
                    "description": f"fresh {item['label']}",
                    "category_canonical": str(item["label"]).strip().lower().replace(" ", "_"),
                },
                attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
                construct_dynamic_styles=_construct_dynamic_styles,
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: {
                    "path": original_image_path,
                    "style_name": style_config.get("name"),
                },
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
                volume_ranking_snapshot=lambda items: [{"target_key": item.get("target_key")} for item in items if isinstance(item, dict)],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(captured["style_targets"], ["cached_table_001"])
        self.assertEqual(result["furniture_data"][0]["box_2d"], [420, 430, 640, 700])
        self.assertEqual(result["furniture_data"][0]["source_box_2d"], [100, 120, 220, 320])
        self.assertEqual(result["furniture_data"][0]["target_key"], "cached_table_001")
        self.assertEqual(result["furniture_data"][0]["crop_path"], "cached-table.png")
        self.assertEqual(len(result["furniture_data"]), 1)

    def test_external_generate_details_job_localizes_cart_products_without_fresh_detection_loss(self):
        source_path = Path("outputs/test-detail-cart-localize-products.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        detect_calls = []
        localized = {
            "Ma Jong Sofa": (0.18, 0.51, 0.76, 0.76),
            "Big Bird Rug": (0.20, 0.62, 0.82, 0.89),
            "Rio Table": (0.41, 0.62, 0.59, 0.77),
            "Floor Lamp": (0.34, 0.37, 0.36, 0.55),
            "Pendant Lamp": (0.47, 0.08, 0.55, 0.32),
            "Zig Zag Chair": (0.70, 0.58, 0.82, 0.89),
            "Plant Decor": (0.46, 0.55, 0.51, 0.69),
        }
        furniture_data = [
            {
                "label": "Floor Lamp",
                "target_key": "cart_product-39522_floor-lamp_001",
                "item_id": "product_39522",
                "category": "floor_lamp",
                "category_canonical": "floor_lamp",
                "box_2d": [0, 0, 1000, 1000],
                "crop_path": "floor-lamp.png",
                "volume_rank": 7,
            },
            {
                "label": "Big Bird Rug",
                "target_key": "cart_product-39521_rug_002",
                "item_id": "product_39521",
                "category": "rug",
                "category_canonical": "rug",
                "box_2d": [625, 203, 893, 824],
                "box_source": "selected_variant_review",
                "crop_path": "rug.png",
                "volume_rank": 3,
            },
            {
                "label": "Plant Decor",
                "target_key": "cart_product-39080_plant_003",
                "item_id": "product_39080",
                "category": "decor",
                "category_canonical": "decor",
                "box_2d": [0, 0, 1000, 1000],
                "crop_path": "plant.png",
                "volume_rank": 4,
            },
            {
                "label": "Pendant Lamp",
                "target_key": "cart_product-38668_pendant_006",
                "item_id": "product_38668",
                "category": "lamp",
                "category_canonical": "light",
                "box_2d": [0, 0, 1000, 1000],
                "crop_path": "pendant.png",
                "volume_rank": 6,
            },
            {
                "label": "Ma Jong Sofa",
                "target_key": "cart_product-38543_sofa_007",
                "item_id": "product_38543",
                "category": "sofa",
                "category_canonical": "main_sofa",
                "box_2d": [515, 184, 768, 763],
                "box_source": "selected_variant_review",
                "crop_path": "sofa.png",
                "volume_rank": 1,
            },
            {
                "label": "Rio Table",
                "target_key": "cart_product-37582_table_008",
                "item_id": "product_37582",
                "category": "sofa_table",
                "category_canonical": "sofa_table",
                "box_2d": [621, 410, 775, 592],
                "box_source": "selected_variant_review",
                "crop_path": "table.png",
                "volume_rank": 2,
            },
            {
                "label": "Zig Zag Chair",
                "target_key": "cart_product-37426_chair_009",
                "item_id": "product_37426",
                "category": "chair",
                "category_canonical": "dining_chair",
                "box_2d": [0, 0, 1000, 1000],
                "crop_path": "chair.png",
                "volume_rank": 5,
            },
        ]

        try:
            result = run_generate_details_job(
                {
                    "image_url": str(source_path),
                    "furniture_data": furniture_data,
                    "audience": "external",
                    "require_details": True,
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                persist_job_result=lambda payload, audience=None: None,
                materialize_input=lambda url, prefix: str(source_path) if prefix == "detail_src" else url,
                resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
                log_section=lambda message: None,
                detect_furniture_boxes=lambda path: detect_calls.append(path) or [
                    {"label": "Ma Jong Sofa", "box_2d": [519, 187, 764, 764]},
                    {"label": "Big Bird Rug", "box_2d": [629, 204, 893, 822]},
                    {"label": "Rio Table", "box_2d": [617, 409, 772, 592]},
                ],
                detect_item_bbox_norm=lambda staged_path, crop_path, label, item_context=None, timeout_sec=None: localized.get(label),
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: item,
                attach_volume_ranks=lambda items: sorted(
                    [{**item, "volume_rank": item.get("volume_rank") or index + 1} for index, item in enumerate(items)],
                    key=lambda item: int(item.get("volume_rank") or 10**9),
                ),
                construct_dynamic_styles=construct_dynamic_styles,
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: {
                    "path": original_image_path,
                    "style_name": style_config.get("name"),
                },
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
                volume_ranking_snapshot=lambda items: [{"target_key": item.get("target_key")} for item in items if isinstance(item, dict)],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(detect_calls, [])
        self.assertEqual(len(result["details"]), 6)
        self.assertEqual(
            {detail["target_key"] for detail in result["details"]},
            {
                "cart_product-38543_sofa_007",
                "cart_product-37582_table_008",
                "cart_product-39080_plant_003",
                "cart_product-37426_chair_009",
                "cart_product-38668_pendant_006",
                "cart_product-39522_floor-lamp_001",
            },
        )
        self.assertNotIn("cart_product-39521_rug_002", {detail["target_key"] for detail in result["details"]})
        self.assertTrue(
            all(
                row.get("box_source") == "product_reference_localization"
                for row in result["furniture_data"]
                if str(row.get("target_key") or "") != "cart_product-39521_rug_002"
            )
        )

    def test_run_regenerate_single_detail_job_uses_simple_generation_for_detail_styles(self):
        source_path = Path("outputs/test-regenerate-crop-mode.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        captured = {}

        try:
            result = run_regenerate_single_detail_job(
                {
                    "original_image_url": str(source_path),
                    "style_index": 4,
                    "style_index_mode": "auto",
                    "target_key": "chair_01",
                    "target_label": "Accent Chair",
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{Path(path).name}" if path else None,
                detect_furniture_boxes=lambda path: [],
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: item,
                attach_volume_ranks=lambda items: items,
                construct_dynamic_styles=lambda items: [
                    {"name": "High Angle Overview"},
                    {"name": "Side Composition (Focus Left)"},
                    {"name": "Side Composition (Focus Right)"},
                    {"name": "Detail: Accent Chair", "target_key": "chair_01", "target_label": "Accent Chair"},
                ],
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: captured.update(kwargs) or {
                    "path": original_image_path,
                    "style_name": style_config.get("name"),
                },
                volume_ranking_snapshot=lambda items: [],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(result["style_name"], "Detail: Accent Chair")
        self.assertFalse(captured["prefer_crop_extract"])

    def test_run_regenerate_single_detail_job_uses_cached_snapshot_without_current_image_analysis(self):
        source_path = Path("outputs/test-regenerate-cached-no-analysis.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        detect_calls = []
        analyze_calls = []

        try:
            result = run_regenerate_single_detail_job(
                {
                    "original_image_url": str(source_path),
                    "style_index": 4,
                    "style_index_mode": "auto",
                    "target_key": "chair_01",
                    "target_label": "Accent Chair",
                    "furniture_data": [
                        {
                            "label": "Accent Chair",
                            "target_key": "chair_01",
                            "box_2d": [120, 180, 820, 640],
                            "description": "Cached accent chair target",
                            "category_canonical": "chair",
                        }
                    ],
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{Path(path).name}" if path else None,
                detect_furniture_boxes=lambda path: detect_calls.append(path) or [{"label": "Wrong Fresh Sofa", "box_2d": [10, 20, 300, 400]}],
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: analyze_calls.append((path, item)) or item,
                attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
                construct_dynamic_styles=lambda items: [
                    {"name": "High Angle Overview"},
                    {"name": "Side Composition (Focus Left)"},
                    {"name": "Side Composition (Focus Right)"},
                    {
                        "name": "Detail: Accent Chair",
                        "target_key": items[0].get("target_key"),
                        "target_label": items[0].get("label"),
                    },
                ],
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: {
                    "path": original_image_path,
                    "style_name": style_config.get("name"),
                },
                volume_ranking_snapshot=lambda items: [{"target_key": item.get("target_key")} for item in items if isinstance(item, dict)],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(detect_calls, [])
        self.assertEqual(analyze_calls, [])
        self.assertEqual(result["style_name"], "Detail: Accent Chair")
        self.assertEqual(result["furniture_data"][0]["target_key"], "chair_01")

    def test_run_regenerate_single_angle_reuses_empty_room_and_scene_contracts(self):
        source_path = Path("outputs/test-regenerate-angle-source.png")
        empty_room_path = Path("outputs/test-regenerate-angle-empty.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        source_path.write_bytes(png_bytes)
        empty_room_path.write_bytes(png_bytes)
        captured_style = {}

        try:
            result = run_regenerate_single_detail_job(
                {
                    "original_image_url": str(source_path),
                    "empty_room_url": str(empty_room_path),
                    "style_index": 1,
                    "style_index_mode": "overall",
                    "furniture_data": [
                        {
                            "label": "Accent Chair",
                            "target_key": "chair_01",
                            "box_2d": [120, 180, 820, 640],
                        }
                    ],
                    "room_dims_contract": {"dims_mm_center": {"width_mm": 5000}},
                    "geometry_contract": {"geometry_source": "explicit_dimensions"},
                    "scene_contract": {"critical_item_keys": ["chair_01"]},
                    "placement_plan": {"anchor_item_key": "chair_01"},
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{Path(path).name}" if path else None,
                detect_furniture_boxes=lambda path: [],
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: item,
                attach_volume_ranks=lambda items: items,
                construct_dynamic_styles=lambda items: [{"name": "Detail: Accent Chair"}],
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: (
                    captured_style.update(style_config)
                    or {
                        "path": original_image_path,
                        "style_name": style_config.get("name"),
                        "generation_mode": "angle_generation_two_stage",
                        "camera_mode": style_config.get("camera_mode"),
                        "angle_direction_reconciled": True,
                        "angle_pipeline_trace": {"guide_reference_mode": "empty_room"},
                    }
                ),
                volume_ranking_snapshot=lambda items: [],
            )
        finally:
            source_path.unlink(missing_ok=True)
            empty_room_path.unlink(missing_ok=True)

        self.assertEqual(result["style_name"], "High Angle Overview")
        self.assertEqual(result["generation_mode"], "angle_generation_two_stage")
        self.assertIs(result["angle_direction_reconciled"], True)
        self.assertEqual(result["angle_pipeline_trace"]["guide_reference_mode"], "empty_room")
        self.assertIs(captured_style["internal_angle_generation"], True)
        self.assertEqual(captured_style["empty_room_path"], str(empty_room_path))
        self.assertEqual(captured_style["room_dims_contract"]["dims_mm_center"]["width_mm"], 5000)
        self.assertEqual(captured_style["geometry_contract"]["geometry_source"], "explicit_dimensions")
        self.assertEqual(captured_style["scene_contract"]["critical_item_keys"], ["chair_01"])
        self.assertEqual(captured_style["placement_plan"]["anchor_item_key"], "chair_01")

    def test_run_generate_details_job_blocks_source_reference_crop_targets(self):
        source_path = Path("outputs/test-detail-source-reference.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        try:
            result = run_generate_details_job(
                {
                    "image_url": str(source_path),
                    "furniture_data": [
                        {
                            "label": "Accent Chair",
                            "target_key": "chair_01",
                            "box_2d": [120, 180, 820, 640],
                            "box_source": "source_reference",
                            "crop_path": str(source_path),
                            "description": "Accent chair",
                            "category": "chair",
                            "category_canonical": "chair",
                        }
                    ],
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                persist_job_result=lambda payload, audience=None: None,
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
                log_section=lambda message: None,
                detect_furniture_boxes=lambda path: [],
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: item,
                attach_volume_ranks=lambda items: items,
                construct_dynamic_styles=lambda items: [
                    {
                        "name": "Detail: Accent Chair",
                        "target_key": "chair_01",
                        "target_label": "Accent Chair",
                    }
                ],
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: actual_generate_detail_view(
                    original_image_path,
                    style_config,
                    unique_id,
                    index,
                    furniture_data=furniture_data,
                    materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("source_reference crop block should not materialize cutouts")
                    ),
                    normalize_label_for_match=lambda text: str(text or "").strip().lower(),
                    allow_harassment_only_safety_settings=lambda: (_ for _ in ()).throw(
                        AssertionError("source_reference crop block should not request model safety settings")
                    ),
                    call_gemini_with_failover=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("source_reference crop block should not call the generation model")
                    ),
                    model_name="unused",
                    **kwargs,
                ),
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
                volume_ranking_snapshot=lambda items: [],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(result["error"], "Failed to generate images")

    def test_run_regenerate_single_detail_job_blocks_source_reference_crop_targets(self):
        source_path = Path("outputs/test-regenerate-source-reference.png")
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        try:
            result = run_regenerate_single_detail_job(
                {
                    "original_image_url": str(source_path),
                    "style_index": 4,
                    "style_index_mode": "auto",
                    "target_key": "chair_01",
                    "target_label": "Accent Chair",
                    "furniture_data": [
                        {
                            "label": "Accent Chair",
                            "target_key": "chair_01",
                            "box_2d": [120, 180, 820, 640],
                            "box_source": "source_reference",
                            "crop_path": str(source_path),
                            "description": "Accent chair",
                            "category": "chair",
                            "category_canonical": "chair",
                        }
                    ],
                    "audience": "internal",
                },
                normalize_audience=lambda audience: audience or "internal",
                build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
                materialize_input=lambda url, prefix: url,
                resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{Path(path).name}" if path else None,
                detect_furniture_boxes=lambda path: [],
                canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
                build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
                max_concurrency_analysis=1,
                analyze_cropped_item=lambda path, item: item,
                attach_volume_ranks=lambda items: items,
                construct_dynamic_styles=lambda items: [
                    {"name": "High Angle Overview"},
                    {"name": "Side Composition (Focus Left)"},
                    {"name": "Side Composition (Focus Right)"},
                    {
                        "name": "Detail: Accent Chair",
                        "target_key": "chair_01",
                        "target_label": "Accent Chair",
                    },
                ],
                normalize_label_for_match=lambda text: str(text or "").strip().lower(),
                generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: actual_generate_detail_view(
                    original_image_path,
                    style_config,
                    unique_id,
                    index,
                    furniture_data=furniture_data,
                    materialize_input=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("source_reference crop block should not materialize cutouts")
                    ),
                    normalize_label_for_match=lambda text: str(text or "").strip().lower(),
                    allow_harassment_only_safety_settings=lambda: (_ for _ in ()).throw(
                        AssertionError("source_reference crop block should not request model safety settings")
                    ),
                    call_gemini_with_failover=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        AssertionError("source_reference crop block should not call the generation model")
                    ),
                    model_name="unused",
                    **kwargs,
                ),
                volume_ranking_snapshot=lambda items: [],
            )
        finally:
            source_path.unlink(missing_ok=True)

        self.assertEqual(result["error"], "Generation failed")


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


def test_run_render_with_details_job_passes_preset_detail_target_policy_to_details():
    captured = {}

    def fake_detail_job_runner(detail_payload):
        captured["detail_payload"] = dict(detail_payload)
        return {"details": [{"url": "https://cdn.example/detail-1.png"}], "message": "ok"}

    run_render_with_details_job(
        {
            "require_details": True,
            "render": {"audience": "external"},
            "extra": {
                "resolved": {"room": "livingroom", "style": "natural", "variant": "2"},
                "detail_target_count": 6,
                "detail_target_policy": "preset_fixed_six_unique_targets",
            },
        },
        normalize_audience=lambda audience: audience or "external",
        render_job_runner=lambda render_payload, persist_result=False: {
            "result_url": "https://cdn.example/render.png",
            "result_urls": ["https://cdn.example/render.png"],
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
        },
        detail_job_runner=fake_detail_job_runner,
        persist_job_result=lambda payload, audience=None: None,
    )

    assert captured["detail_payload"]["detail_target_count"] == 6
    assert captured["detail_payload"]["detail_target_policy"] == "preset_fixed_six_unique_targets"


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


def test_run_render_with_details_job_required_details_ignores_shared_deadline_budget():
    captured = {}
    persisted = []

    def fake_detail_job_runner(detail_payload):
        captured["detail_payload"] = dict(detail_payload)
        return {"details": [{"url": "https://cdn.example/detail-1.png"}], "message": "ok"}

    result = run_render_with_details_job(
        {
            "require_details": True,
            "render": {"audience": "external"},
            "extra": {"cart_kept": [{"id": "chair-1"}], "cart_dropped": []},
        },
        normalize_audience=lambda audience: audience or "external",
        render_job_runner=lambda render_payload, persist_result=False: {
            "result_url": "https://cdn.example/render.png",
            "result_urls": ["https://cdn.example/render.png"],
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
        },
        detail_job_runner=fake_detail_job_runner,
        persist_job_result=lambda payload, audience=None: persisted.append((payload, audience)),
        total_timeout_limit_sec=1.0,
        time_now=lambda: 100.0,
    )

    detail_payload = captured["detail_payload"]
    assert "absolute_deadline_ts" not in detail_payload
    assert "detail_budget_sec" not in detail_payload
    assert detail_payload["require_details"] is True
    assert result["details"]["details"][0]["url"] == "https://cdn.example/detail-1.png"
    assert "error" not in result
    assert persisted[-1][1] == "external"


def test_run_render_with_details_job_required_details_marks_empty_details_as_error():
    persisted = []

    result = run_render_with_details_job(
        {
            "require_details": True,
            "render": {"audience": "external"},
            "extra": {"resolved": {"room": "livingroom", "style": "natural", "variant": "2"}},
        },
        normalize_audience=lambda audience: audience or "external",
        render_job_runner=lambda render_payload, persist_result=False: {
            "result_url": "https://cdn.example/render.png",
            "result_urls": ["https://cdn.example/render.png"],
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
        },
        detail_job_runner=lambda detail_payload: {"details": [], "message": "no usable detail shots"},
        persist_job_result=lambda payload, audience=None: persisted.append((payload, audience)),
        total_timeout_limit_sec=1.0,
        time_now=lambda: 100.0,
    )

    assert result["error"] == "Required detail generation failed: no usable detail shots"
    assert result["details"]["details"] == []
    assert persisted[-1][0]["error"] == result["error"]


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
        generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: (
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


def test_run_generate_details_job_budgeted_mode_caps_style_timeouts_at_180(monkeypatch, tmp_path):
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
            "absolute_deadline_ts": 500.0,
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
        construct_dynamic_styles=lambda analyzed_items: [{"name": "Detail: Chair"}],
        generate_detail_view=lambda original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs: (
            recorded_timeouts.append(float(style_config["timeout_sec"])) or {
                "path": original_image_path,
                "style_name": style_config.get("name"),
            }
        ),
        normalize_label_for_match=lambda label: label.strip().lower(),
        volume_ranking_snapshot=lambda items: [{"target_key": row.get("target_key")} for row in items if isinstance(row, dict)],
    )

    assert len(result["details"]) == 1
    assert recorded_timeouts == [180.0]


def test_run_generate_details_job_preserves_ai_service_scope_in_parallel_detail_threads(monkeypatch, tmp_path):
    image_path = tmp_path / "detail-src.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    observed_scopes = []

    def fake_generate_detail_view(original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs):
        observed_scopes.append(current_ai_service_scope())
        return {
            "path": original_image_path,
            "style_name": style_config.get("name"),
            "generation_mode": "angle_generation",
            "camera_mode": "side_angle",
            "focus_side": "left",
            "camera_travel_side": "left",
            "angle_qc_attempts": 1,
            "angle_qc": {"passed": True, "reject_reasons": []},
            "angle_pipeline_trace": {
                "version": 1,
                "direct_attempts": 1,
                "guide_attempts": [],
                "refurnish_attempts": [],
                "locked_plate_ignored": False,
            },
        }

    with ai_service_scope("internal_tool"):
        result = run_generate_details_job(
            {
                "image_url": str(image_path),
                "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
                "audience": "external",
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
                {"name": "Detail: Chair", "target_key": "cart_chair"},
                {"name": "Detail: Lamp", "target_key": "cart_lamp"},
            ],
            generate_detail_view=fake_generate_detail_view,
            normalize_label_for_match=lambda label: label.strip().lower(),
            volume_ranking_snapshot=lambda items: [
                {"target_key": row.get("target_key")} for row in items if isinstance(row, dict)
            ],
        )

    assert len(result["details"]) == 2
    assert observed_scopes == ["internal_tool", "internal_tool"]
    assert result["details"][0]["generation_mode"] == "angle_generation"
    assert result["details"][0]["camera_mode"] == "side_angle"
    assert result["details"][0]["focus_side"] == "left"
    assert result["details"][0]["camera_travel_side"] == "left"
    assert result["details"][0]["angle_qc_attempts"] == 1
    assert result["details"][0]["angle_qc"]["passed"] is True
    assert result["details"][0]["angle_pipeline_trace"]["direct_attempts"] == 1


def test_run_generate_details_job_preserves_ai_service_scope_in_budgeted_parallel_threads(monkeypatch, tmp_path):
    image_path = tmp_path / "detail-src.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    monkeypatch.setattr("application.details.detail_workflow.time.time", lambda: 100.0)

    observed_scopes = []

    def fake_generate_detail_view(original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs):
        observed_scopes.append(current_ai_service_scope())
        return {
            "path": original_image_path,
            "style_name": style_config.get("name"),
        }

    with ai_service_scope("internal_tool"):
        result = run_generate_details_job(
            {
                "image_url": str(image_path),
                "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
                "audience": "external",
                "absolute_deadline_ts": 500.0,
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
                {"name": "Detail: Chair", "target_key": "cart_chair"},
                {"name": "Detail: Lamp", "target_key": "cart_lamp"},
            ],
            generate_detail_view=fake_generate_detail_view,
            normalize_label_for_match=lambda label: label.strip().lower(),
            volume_ranking_snapshot=lambda items: [
                {"target_key": row.get("target_key")} for row in items if isinstance(row, dict)
            ],
        )

    assert len(result["details"]) == 2
    assert observed_scopes == ["internal_tool", "internal_tool"]


def test_internal_generate_details_job_returns_landscape_angle_metadata(tmp_path):
    image_path = tmp_path / "detail-src.png"
    empty_room_path = tmp_path / "detail-empty.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    empty_room_path.write_bytes(image_path.read_bytes())
    recorded_styles = {}
    recorded_crop_preferences = {}

    def fake_generate_detail_view(original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs):
        recorded_styles[int(index)] = dict(style_config)
        recorded_crop_preferences[int(index)] = bool(kwargs.get("prefer_crop_extract"))
        return {
            "path": original_image_path,
            "style_name": style_config.get("name"),
            "aspect_ratio": style_config.get("ratio"),
        }

    result = run_generate_details_job(
        {
            "image_url": str(image_path),
            "empty_room_url": str(empty_room_path),
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
            "room_dims_contract": {"dims_mm_center": {"width_mm": 5000}},
            "geometry_contract": {"geometry_source": "explicit_dimensions"},
            "scene_contract": {"critical_item_keys": ["detail_001"]},
            "placement_plan": {"anchor_item_key": "detail_001"},
            "audience": "internal",
        },
        normalize_audience=lambda audience: audience or "internal",
        build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
        persist_job_result=lambda payload, audience=None: None,
        materialize_input=lambda url, prefix: url,
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
        log_section=lambda message: None,
        detect_furniture_boxes=lambda path: [
            {
                "label": "Accent Chair",
                "box_2d": [100, 100, 700, 700],
            }
        ],
        canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: {
            **item,
            "description": "single accent chair",
            "crop_path": str(image_path),
        },
        attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
        construct_dynamic_styles=construct_dynamic_styles,
        generate_detail_view=fake_generate_detail_view,
        normalize_label_for_match=lambda label: str(label or "").strip().lower(),
        volume_ranking_snapshot=lambda items: [{"target_key": row.get("target_key")} for row in items if isinstance(row, dict)],
    )

    assert [row["style_name"] for row in result["details"]] == [
        "High Angle Overview",
        "Side Composition (Focus Left)",
        "Side Composition (Focus Right)",
        "Detail: Accent Chair",
    ]
    assert [row["aspect_ratio"] for row in result["details"]] == ["16:9", "16:9", "16:9", "4:5"]
    assert recorded_styles[1].get("camera_mode") == "overview_angle"
    assert recorded_styles[1].get("ratio") == "16:9"
    assert recorded_styles[2].get("camera_mode") == "side_angle"
    assert recorded_styles[2].get("focus_side") == "left"
    assert recorded_styles[3].get("camera_mode") == "side_angle"
    assert recorded_styles[3].get("focus_side") == "right"
    for index in (1, 2, 3):
        assert recorded_styles[index].get("internal_angle_generation") is True
        assert recorded_styles[index].get("empty_room_path") == str(empty_room_path)
        assert recorded_styles[index]["room_dims_contract"]["dims_mm_center"]["width_mm"] == 5000
        assert recorded_styles[index]["geometry_contract"]["geometry_source"] == "explicit_dimensions"
        assert recorded_styles[index]["scene_contract"]["critical_item_keys"] == ["detail_001"]
        assert recorded_styles[index]["placement_plan"]["anchor_item_key"] == "detail_001"
    assert recorded_styles[4].get("ratio") == "4:5"
    assert recorded_styles[4].get("simple_scene_detail") is True
    assert "internal_angle_generation" not in recorded_styles[4]
    assert "empty_room_path" not in recorded_styles[4]
    assert recorded_crop_preferences == {1: False, 2: False, 3: False, 4: False}


def test_internal_generate_details_job_localizes_uploaded_item_targets_without_fresh_detection_loss(tmp_path):
    image_path = tmp_path / "detail-src.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    sofa_crop_path = tmp_path / "sofa-crop.png"
    table_crop_path = tmp_path / "table-crop.png"
    lamp_crop_path = tmp_path / "lamp-crop.png"
    sofa_crop_path.write_bytes(image_path.read_bytes())
    table_crop_path.write_bytes(image_path.read_bytes())
    lamp_crop_path.write_bytes(image_path.read_bytes())
    detect_calls = []
    recorded_styles = {}
    recorded_crop_preferences = {}
    localized = {
        "Modular Sofa": (0.18, 0.15, 0.74, 0.54),
        "Side Table": (0.48, 0.56, 0.62, 0.72),
        "Floor Lamp": (0.74, 0.20, 0.88, 0.62),
    }

    def fake_generate_detail_view(original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs):
        recorded_styles[int(index)] = dict(style_config)
        recorded_crop_preferences[int(index)] = kwargs.get("prefer_crop_extract")
        return {
            "path": original_image_path,
            "style_name": style_config.get("name"),
            "aspect_ratio": style_config.get("ratio"),
        }

    result = run_generate_details_job(
        {
            "image_url": str(image_path),
            "furniture_data": [
                {
                    "label": "Modular Sofa",
                    "target_key": "internal_item-1_sofa_001",
                    "category": "sofa",
                    "category_canonical": "main_sofa",
                    "box_2d": [0, 0, 1000, 1000],
                    "crop_path": str(sofa_crop_path),
                    "volume_rank": 1,
                },
                {
                    "label": "Side Table",
                    "target_key": "internal_item-2_table_002",
                    "category": "table",
                    "category_canonical": "sofa_table",
                    "box_2d": [0, 0, 1000, 1000],
                    "crop_path": str(table_crop_path),
                    "volume_rank": 2,
                },
                {
                    "label": "Floor Lamp",
                    "target_key": "internal_item-3_floor-lamp_003",
                    "category": "floor_lamp",
                    "category_canonical": "floor_lamp",
                    "box_2d": [0, 0, 1000, 1000],
                    "crop_path": str(lamp_crop_path),
                    "volume_rank": 3,
                },
            ],
            "audience": "internal",
        },
        normalize_audience=lambda audience: audience or "internal",
        build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
        persist_job_result=lambda payload, audience=None: None,
        materialize_input=lambda url, prefix: url,
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
        log_section=lambda message: None,
        detect_furniture_boxes=lambda path: detect_calls.append(path) or [{"label": "Generic Chair", "box_2d": [100, 100, 200, 200]}],
        detect_item_bbox_norm=lambda staged_path, crop_path, label, item_context=None, timeout_sec=None: localized.get(label),
        canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: sorted(
            [{**item, "volume_rank": item.get("volume_rank") or index + 1} for index, item in enumerate(items)],
            key=lambda item: int(item.get("volume_rank") or 10**9),
        ),
        construct_dynamic_styles=construct_dynamic_styles,
        generate_detail_view=fake_generate_detail_view,
        normalize_label_for_match=lambda label: str(label or "").strip().lower(),
        volume_ranking_snapshot=lambda items: [{"target_key": row.get("target_key")} for row in items if isinstance(row, dict)],
    )

    detail_target_keys = {row.get("target_key") for row in result["details"] if row.get("target_key")}
    assert detect_calls == []
    assert detail_target_keys == {
        "internal_item-1_sofa_001",
        "internal_item-2_table_002",
        "internal_item-3_floor-lamp_003",
    }
    assert [row["style_name"] for row in result["details"]][:3] == [
        "High Angle Overview",
        "Side Composition (Focus Left)",
        "Side Composition (Focus Right)",
    ]
    assert recorded_styles[4]["detail_mode"] == "product_identity_lock"
    assert recorded_styles[4]["target_box_source"] == "product_reference_localization"
    assert recorded_crop_preferences[4] is True
    assert all(
        row.get("box_source") == "product_reference_localization"
        for row in result["furniture_data"]
    )


def test_external_generate_details_job_uses_model_generation_for_detail_targets(tmp_path):
    image_path = tmp_path / "detail-src.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    recorded_styles = {}
    recorded_crop_preferences = {}

    def fake_generate_detail_view(original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs):
        recorded_styles[int(index)] = dict(style_config)
        recorded_crop_preferences[int(index)] = kwargs.get("prefer_crop_extract")
        return {
            "path": original_image_path,
            "style_name": style_config.get("name"),
            "aspect_ratio": style_config.get("ratio"),
        }

    result = run_generate_details_job(
        {
            "image_url": str(image_path),
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
            "audience": "external",
        },
        normalize_audience=lambda audience: audience or "external",
        build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
        persist_job_result=lambda payload, audience=None: None,
        materialize_input=lambda url, prefix: url,
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
        log_section=lambda message: None,
        detect_furniture_boxes=lambda path: [{"label": "Accent Chair", "box_2d": [100, 100, 700, 700]}],
        canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: {**item, "description": "single accent chair", "crop_path": str(image_path)},
        attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
        construct_dynamic_styles=construct_dynamic_styles,
        generate_detail_view=fake_generate_detail_view,
        normalize_label_for_match=lambda label: str(label or "").strip().lower(),
        volume_ranking_snapshot=lambda items: [{"target_key": row.get("target_key")} for row in items if isinstance(row, dict)],
    )

    assert [row["style_name"] for row in result["details"]] == ["Detail: Accent Chair"]
    assert recorded_styles[1].get("simple_scene_detail") is True
    assert recorded_crop_preferences == {1: False}


def test_external_product_backed_details_prefer_verified_crop_extract(tmp_path):
    image_path = tmp_path / "detail-src.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    recorded_crop_preferences = {}
    recorded_styles = {}

    def fake_generate_detail_view(original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs):
        recorded_styles[int(index)] = dict(style_config)
        recorded_crop_preferences[int(index)] = kwargs.get("prefer_crop_extract")
        return {
            "path": original_image_path,
            "style_name": style_config.get("name"),
            "aspect_ratio": style_config.get("ratio"),
        }

    result = run_generate_details_job(
        {
            "image_url": str(image_path),
            "furniture_data": [
                {
                    "label": "Gubi F300 Lounge Chair",
                    "target_key": "cart_37694_gubi-f300_001",
                    "crop_path": str(image_path),
                    "category": "lounge_chair",
                    "category_canonical": "lounge_chair",
                }
            ],
            "audience": "external",
        },
        normalize_audience=lambda audience: audience or "external",
        build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
        persist_job_result=lambda payload, audience=None: None,
        materialize_input=lambda url, prefix: url,
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
        log_section=lambda message: None,
        detect_furniture_boxes=lambda path: [],
        detect_item_bbox_norm=lambda staged_path, crop_path, label, item_context=None, timeout_sec=None: (0.12, 0.14, 0.30, 0.42),
        canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
        construct_dynamic_styles=construct_dynamic_styles,
        generate_detail_view=fake_generate_detail_view,
        normalize_label_for_match=lambda label: str(label or "").strip().lower(),
        volume_ranking_snapshot=lambda items: [{"target_key": row.get("target_key")} for row in items if isinstance(row, dict)],
    )

    assert [row["style_name"] for row in result["details"]] == ["Detail: lounge chair"]
    assert recorded_styles[1]["detail_mode"] == "product_identity_lock"
    assert recorded_styles[1]["target_product_label"] == "Gubi F300 Lounge Chair"
    assert recorded_styles[1]["target_box_source"] == "product_reference_localization"
    assert recorded_crop_preferences == {1: True}


def test_external_cart_product_details_prefer_current_render_crop_extract(tmp_path):
    image_path = tmp_path / "detail-src.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    recorded_crop_preferences = {}
    recorded_styles = {}

    def fake_generate_detail_view(original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs):
        recorded_styles[int(index)] = dict(style_config)
        recorded_crop_preferences[int(index)] = kwargs.get("prefer_crop_extract")
        return {
            "path": original_image_path,
            "style_name": style_config.get("name"),
            "aspect_ratio": style_config.get("ratio"),
        }

    result = run_generate_details_job(
        {
            "image_url": str(image_path),
            "furniture_data": [
                {
                    "label": "HAY Bowler Table",
                    "target_key": "cart_product-39553_bowler_003",
                    "crop_path": str(image_path),
                    "category": "side_table",
                    "category_canonical": "side_table",
                    "box_2d": [603, 612, 716, 668],
                }
            ],
            "audience": "external",
        },
        normalize_audience=lambda audience: audience or "external",
        build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
        persist_job_result=lambda payload, audience=None: None,
        materialize_input=lambda url, prefix: url,
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
        log_section=lambda message: None,
        detect_furniture_boxes=lambda path: [
            {
                "label": "HAY Bowler Table",
                "box_2d": [603, 612, 716, 668],
                "box_source": "detail_current_image_analysis",
            },
            {
                "label": "Side Table",
                "box_2d": [603, 612, 716, 668],
                "box_source": "detail_current_image_analysis",
            },
        ],
        canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}_{str(label or '').strip().lower().replace(' ', '-')}",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
        construct_dynamic_styles=construct_dynamic_styles,
        generate_detail_view=fake_generate_detail_view,
        normalize_label_for_match=lambda label: str(label or "").strip().lower(),
        volume_ranking_snapshot=lambda items: [{"target_key": row.get("target_key")} for row in items if isinstance(row, dict)],
    )

    assert [row["style_name"] for row in result["details"]][:1] == ["Detail: side table"]
    assert recorded_styles[1]["target_key"] == "cart_product-39553_bowler_003"
    assert recorded_styles[1]["detail_mode"] == "product_identity_lock"
    assert recorded_styles[1]["target_product_label"] == "HAY Bowler Table"
    assert recorded_styles[1]["target_box_source"] == "detail_current_image_analysis"
    assert recorded_crop_preferences[1] is True


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


def test_run_generate_details_job_required_details_ignores_deadline_budget(monkeypatch, tmp_path):
    image_path = tmp_path / "detail-src.png"
    image_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    monkeypatch.setattr("application.details.detail_workflow.time.time", lambda: 100.0)
    generated_styles = []

    def fake_generate_detail_view(original_image_path, style_config, unique_id, index, furniture_data=None, **kwargs):
        generated_styles.append(dict(style_config))
        return {
            "path": original_image_path,
            "style_name": style_config.get("name"),
            "aspect_ratio": style_config.get("ratio"),
        }

    result = run_generate_details_job(
        {
            "image_url": str(image_path),
            "furniture_data": [{"label": "Accent Chair", "target_key": "detail_001"}],
            "audience": "external",
            "require_details": True,
            "absolute_deadline_ts": 103.0,
            "minimum_detail_budget_sec": 5.0,
        },
        normalize_audience=lambda audience: audience or "external",
        build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
        persist_job_result=lambda payload, audience=None: None,
        materialize_input=lambda url, prefix: url,
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{Path(path).name}" if path else None,
        log_section=lambda message: None,
        detect_furniture_boxes=lambda path: [{"label": "Accent Chair", "box_2d": [100, 100, 700, 700]}],
        canonical_category=lambda label: str(label or "").strip().lower().replace(" ", "_"),
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
        max_concurrency_analysis=1,
        analyze_cropped_item=lambda path, item: item,
        attach_volume_ranks=lambda items: [{**item, "volume_rank": index + 1} for index, item in enumerate(items)],
        construct_dynamic_styles=construct_dynamic_styles,
        generate_detail_view=fake_generate_detail_view,
        normalize_label_for_match=lambda label: label.strip().lower(),
        volume_ranking_snapshot=lambda items: [{"target_key": row.get("target_key")} for row in items if isinstance(row, dict)],
    )

    assert len(result["details"]) == 1
    assert generated_styles[0]["name"] == "Detail: Accent Chair"
