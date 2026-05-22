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

    def test_external_cart_generates_three_candidates_then_polishes_selected_best(self):
        generated_calls = []
        polished_calls = []
        item_ref_path = self._touch("item.png")

        def generate_furnished_room(*args, **kwargs):
            variant_id = str(args[3])
            path = self._touch(f"{variant_id}.png")
            generated_calls.append(path)
            return path

        def polish_main_image(source_path, **kwargs):
            polished_calls.append((source_path, dict(kwargs)))
            return self._touch("polished-best.png")

        generation = RenderWorkflowGenerationServices(
            generate_empty_room=lambda *args, **kwargs: (self._touch("empty.png"), self._touch("empty-raw.png")),
            generate_furnished_room=generate_furnished_room,
        )
        setattr(generation, "polish_main_image", polish_main_image)

        deps = RenderWorkflowDependencies(
            runtime=RenderWorkflowRuntime(
                style_map={"Customize": "custom style"},
                generate_unique_id=lambda: "abc12345",
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
                analyze_cropped_item=lambda *_args, **_kwargs: {"description": "chair"},
                normalize_dims_dict=lambda dims: dims or {},
                parse_object_dimensions_mm=lambda _text: {},
                build_furniture_specs_json=lambda items: {"items": items},
                create_scale_guide_overlay_with_model=lambda *_args, **_kwargs: None,
                match_aspect_to_target=lambda path, _target: path,
            ),
            generation=generation,
            postprocess=RenderWorkflowPostprocessServices(
                rank_best_variant=lambda candidates, _items: 1,
                refresh_item_boxes_from_main_render=lambda _path, items: items,
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
                audience="external",
                moodboard_items=[{"label": "Chair", "path": "https://example.com/chair.png"}],
            ),
            deps,
        )

        self.assertEqual(len(generated_calls), 3)
        self.assertEqual(polished_calls[0][0], str(self.outputs / "test_workflow_tmp" / "abc12345_v2.png"))
        self.assertEqual(polished_calls[0][1]["unique_id"], "abc12345")
        self.assertEqual(polished_calls[0][1]["audience"], "external")
        self.assertEqual(payload["result_urls"], ["url://external/mainrendered/rendered/polished-best.png"])
        self.assertEqual(payload["result_url"], "url://external/mainrendered/rendered/polished-best.png")
