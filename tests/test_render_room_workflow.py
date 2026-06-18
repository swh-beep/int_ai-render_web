import io
import shutil
import time
import unittest
from pathlib import Path

from application.render.render_room_workflow import run_render_room_workflow
from application.render.render_workflow_contracts import (
    RenderWorkflowAnalysisServices,
    RenderWorkflowDependencies,
    RenderWorkflowGenerationServices,
    RenderWorkflowPostprocessServices,
    RenderWorkflowRequest,
    RenderWorkflowRuntime,
    RenderWorkflowStorageServices,
)


class _StubUpload:
    filename = "room.png"

    def __init__(self):
        self.file = io.BytesIO(b"room")


class _SummaryRef:
    def set(self, value):
        return value

    def get(self):
        return {}


class _StubLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None


class RenderRoomWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.outputs = Path("outputs")
        self.outputs.mkdir(exist_ok=True)
        self.created_paths = []

    def tearDown(self):
        for path in self.created_paths:
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass
        shutil.rmtree(Path("outputs/test_workflow_tmp"), ignore_errors=True)

    def _touch(self, name: str) -> str:
        path = self.outputs / "test_workflow_tmp" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"img")
        self.created_paths.append(str(path))
        return str(path)

    def _run_workflow_case(
        self,
        *,
        audience: str,
        unique_id: str,
        moodboard_items=None,
        analyze_cropped_item=None,
        refresh_item_boxes_from_main_render=None,
        polish_main_image=None,
    ):
        generated_calls = []
        self.generated_call_sources = []
        self.generated_call_kwargs = []
        polished_calls = []
        item_ref_path = self._touch("item.png")

        def generate_furnished_room(*args, **kwargs):
            variant_id = str(args[3])
            path = self._touch(f"{variant_id}.png")
            generated_calls.append(path)
            self.generated_call_sources.append(args[0])
            self.generated_call_kwargs.append(dict(kwargs))
            return path

        def default_polish_main_image(source_path, **kwargs):
            polished_calls.append((source_path, dict(kwargs)))
            if audience == "internal":
                return self._touch(f"{Path(source_path).stem}_polished.png")
            return self._touch("polished-best.png")

        polish_main_image = polish_main_image or default_polish_main_image

        generation = RenderWorkflowGenerationServices(
            generate_empty_room=lambda *args, **kwargs: (self._touch("empty.png"), self._touch("empty-raw.png")),
            generate_furnished_room=generate_furnished_room,
        )
        setattr(generation, "polish_main_image", polish_main_image)

        deps = RenderWorkflowDependencies(
            runtime=RenderWorkflowRuntime(
                style_map={"Customize": "custom style"},
                generate_unique_id=lambda: unique_id,
                time_now=time.time,
                log_section=lambda *_args, **_kwargs: None,
                summary_ref=_SummaryRef(),
                reset_summary_token=lambda *_args, **_kwargs: None,
                logger=_StubLogger(),
                log_brief=True,
                log_summary=False,
                use_s3_moodboard=False,
                max_concurrency_analysis=1,
                cart_max_analysis_workers=1,
                total_timeout_limit_sec=900.0,
            ),
            storage=RenderWorkflowStorageServices(
                normalize_audience=lambda audience: audience or "external",
                build_s3_prefix=lambda audience, category, subcategory=None: "/".join(
                    [part for part in [audience, category, subcategory] if part]
                )
                + "/",
                standardize_image=lambda raw_path: raw_path,
                materialize_input=lambda src, prefix: item_ref_path if prefix == "mb" else src,
                resolve_image_url=lambda path, s3_prefix_override=None: f"url://{s3_prefix_override or ''}{Path(path).name}",
                find_s3_moodboard_key=lambda *_args, **_kwargs: None,
                s3_public_url=lambda key: f"url://{key}",
            ),
            analysis=RenderWorkflowAnalysisServices(
                parse_room_dimensions_mm=lambda _dimensions: {},
                room_dims_valid_fn=lambda _dims: False,
                build_item_target_key=lambda source, index, **_kwargs: f"{source}-{index}",
                canonical_category=lambda category: category or "",
                detect_furniture_boxes=lambda _path: [],
                analyze_room_structure=lambda *_args, **_kwargs: {"room_text": "", "windows_present": False},
                analyze_cropped_item=analyze_cropped_item or (lambda *_args, **_kwargs: {"description": "chair"}),
                normalize_dims_dict=lambda dims: dims or {},
                parse_object_dimensions_mm=lambda _text: {},
                build_furniture_specs_json=lambda items: {"items": items},
                create_scale_guide_overlay_with_model=lambda *_args, **_kwargs: None,
                match_aspect_to_target=lambda path, _target: path,
            ),
            generation=generation,
            postprocess=RenderWorkflowPostprocessServices(
                rank_best_variant=lambda candidates, _items: 1,
                refresh_item_boxes_from_main_render=refresh_item_boxes_from_main_render
                or (lambda _path, items: items),
                attach_volume_ranks=lambda items: items,
                volume_ranking_snapshot=lambda _items: [],
            ),
        )

        payload = run_render_room_workflow(
            RenderWorkflowRequest(
                file=_StubUpload(),
                room="livingroom",
                style="Customize",
                variant="1",
                audience=audience,
                moodboard_items=moodboard_items or [{"label": "Chair", "path": "https://example.com/chair.png"}],
            ),
            deps,
        )
        return payload, generated_calls, polished_calls

    def test_external_cart_generates_three_candidates_then_returns_selected_best_without_pre_polish(self):
        payload, generated_calls, polished_calls = self._run_workflow_case(audience="external", unique_id="abc12345")
        self.assertEqual(len(generated_calls), 3)
        self.assertEqual(polished_calls, [])
        self.assertEqual(payload["result_urls"], ["url://external/mainrendered/rendered/abc12345_v2.png"])
        self.assertEqual(payload["result_url"], "url://external/mainrendered/rendered/abc12345_v2.png")

    def test_internal_render_returns_ranked_candidates_without_pre_polish(self):
        payload, generated_calls, polished_calls = self._run_workflow_case(audience="internal", unique_id="int12345")

        self.assertEqual(len(generated_calls), 3)
        self.assertEqual(polished_calls, [])
        self.assertEqual(
            payload["result_urls"],
            [
                "url://internal/mainrendered/rendered/int12345_v2.png",
                "url://internal/mainrendered/rendered/int12345_v1.png",
                "url://internal/mainrendered/rendered/int12345_v3.png",
            ],
        )
        self.assertEqual(payload["result_url"], "url://internal/mainrendered/rendered/int12345_v2.png")

    def test_external_render_adds_pass2_detail_items_after_primary_pass(self):
        def analyze_cropped_item(_path, item, *_args, **_kwargs):
            label = str((item or {}).get("label") or "")
            if "Artwork" in label:
                return {
                    **dict(item or {}),
                    "description": "Framed blue artwork",
                    "category": "artwork",
                    "category_canonical": "artwork",
                    "dims_mm": {"width_mm": 700, "depth_mm": 20, "height_mm": 900},
                }
            return {
                **dict(item or {}),
                "description": "Large cream sofa",
                "category": "sofa",
                "category_canonical": "sofa",
                "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 780},
            }

        payload, generated_calls, polished_calls = self._run_workflow_case(
            audience="external",
            unique_id="twopass01",
            moodboard_items=[
                {"label": "Sofa", "path": "https://example.com/sofa.png"},
                {"label": "Artwork", "path": "https://example.com/art.png"},
            ],
            analyze_cropped_item=analyze_cropped_item,
        )

        self.assertEqual(len(generated_calls), 4)
        self.assertEqual(Path(generated_calls[-1]).name, "twopass01_p2_v21.png")
        self.assertEqual(Path(self.generated_call_sources[-1]).name, "twopass01_v2.png")
        self.assertEqual(self.generated_call_kwargs[-1]["max_generation_attempts"], 1)
        self.assertFalse(self.generated_call_kwargs[-1]["enable_scale_check"])
        self.assertEqual(polished_calls, [])
        self.assertEqual(payload["result_url"], "url://external/mainrendered/rendered/twopass01_p2_v21.png")

    def test_external_render_refreshes_item_boxes_after_unpolished_pass2(self):
        def analyze_cropped_item(_path, item, *_args, **_kwargs):
            label = str((item or {}).get("label") or "")
            if "Layer Lamp" in label:
                return {
                    **dict(item or {}),
                    "description": "Stacked layered table lamp",
                    "category": "table_lamp",
                    "category_canonical": "table_lamp",
                    "dims_mm": {"width_mm": 300, "depth_mm": 300, "height_mm": 500},
                    "requires_identity_validation": True,
                    "reference_features": {
                        "silhouette_cues": ["stacked layered shade profile", "parallel horizontal discs"],
                        "distinctive_parts": ["visible vertical rods"],
                    },
                }
            return {
                **dict(item or {}),
                "description": "Large cream sofa",
                "category": "sofa",
                "category_canonical": "sofa",
                "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 780},
            }

        refresh_calls = []

        def refresh_boxes(path, items):
            refresh_calls.append(
                (
                    Path(path).name,
                    [
                        (item.get("target_key"), item.get("label"), item.get("category"), item.get("category_canonical"), item.get("box_source"), item.get("box_2d"))
                        for item in items
                    ],
                )
            )
            if len(refresh_calls) == 1:
                return items
            return [
                (
                    {
                        **dict(item),
                        "box_2d": [320, 120, 520, 240],
                        "box_source": "main_render",
                        "box_label_detected": "Layer Lamp",
                    }
                    if item.get("label") == "Layer Lamp"
                    else item
                )
                for item in items
            ]

        payload, _generated_calls, _polished_calls = self._run_workflow_case(
            audience="external",
            unique_id="pass2box",
            moodboard_items=[
                {"label": "Sofa", "path": "https://example.com/sofa.png", "target_key": "cart_sofa_001"},
                {"label": "Layer Lamp", "path": "https://example.com/layer-lamp.png", "target_key": "cart_layer-lamp_002"},
            ],
            analyze_cropped_item=analyze_cropped_item,
            refresh_item_boxes_from_main_render=refresh_boxes,
        )

        self.assertGreaterEqual(len(refresh_calls), 2)
        self.assertEqual(self.generated_call_kwargs[-1]["max_generation_attempts"], 1)
        self.assertFalse(self.generated_call_kwargs[-1]["enable_scale_check"])
        layer_lamp = next(row for row in payload["furniture_data"] if row.get("label") == "Layer Lamp")
        self.assertEqual(layer_lamp["box_source"], "main_render")
        self.assertEqual(layer_lamp["box_label_detected"], "Layer Lamp")

    def test_external_render_marks_missing_pass2_identity_without_whole_image_repair(self):
        def analyze_cropped_item(_path, item, *_args, **_kwargs):
            label = str((item or {}).get("label") or "")
            if "Layer Lamp" in label:
                return {
                    **dict(item or {}),
                    "description": "Stacked layered table lamp",
                    "category": "table_lamp",
                    "category_canonical": "table_lamp",
                    "dims_mm": {"width_mm": 300, "depth_mm": 300, "height_mm": 500},
                    "requires_identity_validation": True,
                    "reference_features": {
                        "silhouette_cues": ["stacked layered shade profile", "parallel horizontal discs"],
                        "distinctive_parts": ["visible vertical rods"],
                    },
                }
            return {
                **dict(item or {}),
                "description": "Large cream sofa",
                "category": "sofa",
                "category_canonical": "sofa",
                "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 780},
            }

        refresh_calls = []

        def refresh_boxes(path, items):
            refresh_calls.append(
                (
                    Path(path).name,
                    [
                        (item.get("target_key"), item.get("label"), item.get("category"), item.get("category_canonical"), item.get("box_source"), item.get("box_2d"))
                        for item in items
                    ],
                )
            )
            if len(refresh_calls) < 3:
                return items
            return [
                (
                    {
                        **dict(item),
                        "box_2d": [320, 120, 520, 240],
                        "box_source": "main_render",
                        "box_label_detected": "Layer Lamp",
                    }
                    if item.get("label") == "Layer Lamp"
                    else item
                )
                for item in items
            ]

        payload, generated_calls, _polished_calls = self._run_workflow_case(
            audience="external",
            unique_id="p2repair",
            moodboard_items=[
                {"label": "Sofa", "path": "https://example.com/sofa.png", "target_key": "cart_sofa_001"},
                {"label": "Layer Lamp", "path": "https://example.com/layer-lamp.png", "target_key": "cart_layer-lamp_002"},
            ],
            analyze_cropped_item=analyze_cropped_item,
            refresh_item_boxes_from_main_render=refresh_boxes,
        )

        self.assertEqual(len(generated_calls), 4, refresh_calls)
        self.assertGreaterEqual(len(refresh_calls), 2, refresh_calls)
        self.assertEqual(self.generated_call_kwargs[-1]["max_generation_attempts"], 1)
        self.assertFalse(self.generated_call_kwargs[-1]["enable_scale_check"])
        self.assertNotIn("FOCUSED PASS2 IDENTITY REPAIR", self.generated_call_kwargs[-1]["placement_instructions"])
        layer_lamp = next(row for row in payload["furniture_data"] if row.get("label") == "Layer Lamp")
        self.assertTrue(layer_lamp["pass2_identity_unlocalized"])
        self.assertEqual(layer_lamp["pass2_identity_failure_reason"], "missing_product_localization")
        self.assertEqual(layer_lamp["box_source"], "source_reference")
