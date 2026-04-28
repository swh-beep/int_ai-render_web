import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "internal_scale_case_114358b6.json"


def load_internal_scale_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_review_reference_fidelity_uses_archetype_strategy_without_runtime_error(monkeypatch):
    from application.render import scale_validation_support as svs

    outputs_dir = Path(__file__).resolve().parent.parent / "outputs"
    outputs_dir.mkdir(exist_ok=True)
    reference_path = outputs_dir / "test_ref_identity.png"
    rendered_path = outputs_dir / "test_rendered_identity.png"

    Image.new("RGB", (32, 32), "white").save(reference_path)
    Image.new("RGB", (32, 32), "black").save(rendered_path)

    monkeypatch.setattr(svs, "crop_bbox_norm_image", lambda *args, **kwargs: str(rendered_path))

    class _Resp:
        text = '{"same_object": false, "shape_match": false, "material_match": false}'

    issues = svs._review_reference_fidelity(
        str(rendered_path),
        {
            "label": "Side Table",
            "target_key": "side-table-1",
            "category": "table",
            "crop_path": str(reference_path),
            "identity_profile": {"material_cues": ["chrome"], "distinctive_parts": ["triangular top"]},
            "product_identity": {"support_geometry": ["cantilever"], "preserve_rules": ["keep support geometry"]},
            "archetype_strategy": {"required_parts": ["triangular top"], "forbidden_substitutions": ["round top"]},
        },
        (0.1, 0.1, 0.9, 0.9),
        call_gemini_with_failover=lambda *args, **kwargs: _Resp(),
        analysis_model_name="stub",
        safe_json_from_model_text=json.loads,
    )

    assert "reference_shape_drift:side-table-1" in issues
    assert "reference_material_drift:side-table-1" in issues

    for path in (reference_path, rendered_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def test_review_reference_fidelity_marks_unresolved_when_model_output_is_invalid(monkeypatch):
    from application.render import scale_validation_support as svs

    outputs_dir = Path(__file__).resolve().parent.parent / "outputs"
    outputs_dir.mkdir(exist_ok=True)
    reference_path = outputs_dir / "test_ref_identity_invalid.png"
    rendered_path = outputs_dir / "test_rendered_identity_invalid.png"

    Image.new("RGB", (24, 24), "white").save(reference_path)
    Image.new("RGB", (24, 24), "gray").save(rendered_path)

    monkeypatch.setattr(svs, "crop_bbox_norm_image", lambda *args, **kwargs: str(rendered_path))

    class _Resp:
        text = "not-json"

    issues = svs._review_reference_fidelity(
        str(rendered_path),
        {
            "label": "Mirror",
            "target_key": "mirror-1",
            "category": "mirror",
            "crop_path": str(reference_path),
            "identity_profile": {"material_cues": ["reflective"], "distinctive_parts": ["rounded border"]},
            "product_identity": {"preserve_rules": ["preserve outline"]},
            "archetype_strategy": {"required_parts": ["reflective face"], "forbidden_substitutions": ["poster"]},
        },
        (0.1, 0.1, 0.9, 0.9),
        call_gemini_with_failover=lambda *args, **kwargs: _Resp(),
        analysis_model_name="stub",
        safe_json_from_model_text=lambda text: None,
    )

    assert issues == ["reference_review_unresolved:mirror-1"]

    for path in (reference_path, rendered_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def test_internal_scale_fixture_contains_problem_case_metadata():
    fixture = load_internal_scale_fixture()

    assert fixture["job_id"] == "114358b6-3d75-4cb9-bfa2-fc9b938e1655"
    assert fixture["room_dims_mm"] == {"width_mm": 8000, "depth_mm": 8000, "height_mm": 12000}
    assert fixture["room_planes"]["y_top"] == 0.08
    assert fixture["detected_boxes_norm"]["Sofa"] == [0.06, 0.46, 0.57, 0.84]
    assert fixture["primary_item"]["category"] == "sofa"
    assert fixture["primary_item"]["dims_mm"]["width_mm"] == 2400
    assert fixture["rug_item"]["dims_mm"]["width_mm"] == 1000
    assert len(fixture["items"]) == 5


def test_validate_scale_from_detection_map_flags_primary_and_rug_rules():
    fixture = load_internal_scale_fixture()

    from application.render.scale_validation_support import validate_scale_from_detection_map

    ok, issues, diagnostics = validate_scale_from_detection_map(
        fixture["items"],
        fixture["room_dims_mm"],
        room_planes=fixture["room_planes"],
        detected_boxes=fixture["detected_boxes_norm"],
    )

    assert ok is False
    assert issues
    assert "primary_width_vs_room_width" in diagnostics["failed_rules"]
    assert "rug_vs_anchor_footprint" in diagnostics["failed_rules"]


def test_validate_scale_from_detection_map_accepts_reasonable_anchor_case():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Sofa",
            "target_key": "anchor_sofa",
            "source_index": 0,
            "dims_mm": {"width_mm": 2000, "depth_mm": 800, "height_mm": 750},
        },
        {
            "label": "Rug",
            "target_key": "rug_01",
            "source_index": 1,
            "is_rug": True,
            "dims_mm": {"width_mm": 1200, "depth_mm": 1200, "height_mm": 10},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 6000, "height_mm": 2800},
        room_planes={"y_top": 0.12, "y_bottom": 0.88},
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": [0.20, 0.52, 0.60, 0.82]},
            {"label": "Rug", "target_key": "rug_01", "source_index": 1, "bbox_norm": [0.22, 0.62, 0.46, 0.92]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is True
    assert issues == []
    assert diagnostics["failed_rules"] == []
    assert diagnostics["matched_items"]["anchor_sofa"]["bbox_norm"] == [0.20, 0.52, 0.60, 0.82]


def test_validate_scale_from_detection_map_emits_scale_plan_measurements():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Sofa",
            "target_key": "anchor_sofa",
            "source_index": 0,
            "dims_mm": {"width_mm": 2000, "depth_mm": 800, "height_mm": 750},
        },
        {
            "label": "Lamp",
            "target_key": "lamp_01",
            "source_index": 1,
            "dims_mm": {"width_mm": 250, "depth_mm": 250, "height_mm": 900},
        },
    ]
    scale_plan = {
        "strict_scale_requested": True,
        "strict_scale_ready": True,
        "wall_span_norm": [0.0, 1.0],
        "room_planes": {"y_top": 0.12, "y_bottom": 0.88},
        "items": [
            {
                "label": "Sofa",
                "target_key": "anchor_sofa",
                "source_index": 0,
                "room_width_ratio": 0.4,
                "room_height_ratio": 0.2679,
                "relative_to_anchor": {"width_ratio": 1.0, "height_ratio": 1.0},
            },
            {
                "label": "Lamp",
                "target_key": "lamp_01",
                "source_index": 1,
                "room_width_ratio": 0.05,
                "room_height_ratio": 0.3214,
                "relative_to_anchor": {"width_ratio": 0.125, "height_ratio": 1.2},
            },
        ],
    }

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 4000, "height_mm": 2800},
        room_planes={"y_top": 0.12, "y_bottom": 0.88},
        scale_plan=scale_plan,
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": [0.20, 0.52, 0.60, 0.72]},
            {"label": "Lamp", "target_key": "lamp_01", "source_index": 1, "bbox_norm": [0.68, 0.46, 0.73, 0.70]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is True
    assert issues == []
    assert diagnostics["ratio_qc_summary"]["measurement_count"] >= 4
    metrics = {row["metric"] for row in diagnostics["scale_plan_measurements"]}
    assert "room_width_ratio" in metrics
    assert "anchor_width_ratio" in metrics


def test_validate_furnished_scale_rejects_strict_scale_contract_not_ready(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fail_if_called(*args, **kwargs):
        raise AssertionError("detect_item_bbox_norm should not run when strict scale contract is not ready")

    monkeypatch.setattr(support, "detect_item_bbox_norm", fail_if_called)

    ok, issues, diagnostics = support.validate_furnished_scale(
        "ignored.png",
        {
            "items": [
                {
                    "label": "Sofa",
                    "target_key": "anchor_sofa",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
                }
            ]
        },
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        {"y_top": 0.10, "y_bottom": 0.88},
        include_diagnostics=True,
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": False, "missing_requirements": ["missing_anchor"]},
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert issues == ["strict_scale_contract_not_ready"]
    assert diagnostics["rule_details"]["missing_requirements"] == ["missing_anchor"]


def test_validate_furnished_scale_rejects_strict_scale_contract_not_ready_from_scale_plan_even_when_geometry_contract_exists(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fail_if_called(*args, **kwargs):
        raise AssertionError("detect_item_bbox_norm should not run when any strict contract is not ready")

    monkeypatch.setattr(support, "detect_item_bbox_norm", fail_if_called)

    ok, issues, diagnostics = support.validate_furnished_scale(
        "ignored.png",
        {
            "items": [
                {
                    "label": "Sofa",
                    "target_key": "anchor_sofa",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
                }
            ]
        },
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        {"y_top": 0.10, "y_bottom": 0.88},
        include_diagnostics=True,
        scale_plan={"strict_scale_requested": True, "strict_scale_ready": False, "missing_requirements": ["missing_anchor"]},
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True},
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert issues == ["strict_scale_contract_not_ready"]
    assert diagnostics["rule_details"]["missing_requirements"] == ["missing_anchor"]


def test_validate_furnished_scale_uses_batch_detect_before_item_bbox_fallback(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fail_if_called(*args, **kwargs):
        raise AssertionError("detect_item_bbox_norm should not run when batch detect already found the shortlist items")

    monkeypatch.setattr(support, "detect_item_bbox_norm", fail_if_called)
    staged_path = tmp_path / "render.png"
    Image.new("RGB", (320, 200), color=(255, 255, 255)).save(staged_path, format="PNG")

    ok, issues, diagnostics = support.validate_furnished_scale(
        str(staged_path),
        {
            "items": [
                {
                    "label": "Sofa",
                    "category": "sofa",
                    "category_canonical": "sofa",
                    "target_key": "anchor_sofa",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2000, "depth_mm": 900, "height_mm": 800},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                },
                {
                    "label": "Rug",
                    "category": "rug",
                    "category_canonical": "rug",
                    "target_key": "rug_01",
                    "source_index": 1,
                    "dims_mm": {"width_mm": 1100, "depth_mm": 1100, "height_mm": 12},
                    "two_pass_strategy": {"pass_role": "pass1_footprint"},
                },
            ],
            "primary_scale": {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0},
        },
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        {"y_top": 0.10, "y_bottom": 0.88},
        include_diagnostics=True,
        detect_furniture_boxes=lambda *args, **kwargs: [
            {"label": "Sofa", "box_2d": [520, 200, 820, 600]},
            {"label": "Rug", "box_2d": [620, 220, 920, 460]},
        ],
        remap_model_name="remap",
        remap_detect_timeout_sec=15,
        remap_detect_retry=0,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert "detect_item_bbox_norm should not run" not in " ".join(issues)
    assert diagnostics["batch_detect_used"] is True
    assert diagnostics["batch_detect_row_count"] == 2


def test_validate_furnished_scale_uses_item_bbox_fallback_for_unresolved_critical_after_batch(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    calls = []

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        calls.append(item_label)
        if item_label == "Mirror":
            return (0.62, 0.20, 0.82, 0.62)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)
    staged_path = tmp_path / "render.png"
    Image.new("RGB", (320, 200), color=(255, 255, 255)).save(staged_path, format="PNG")

    ok, issues, diagnostics = support.validate_furnished_scale(
        str(staged_path),
        {
            "items": [
                {
                    "label": "Sofa",
                    "category": "sofa",
                    "category_canonical": "sofa",
                    "target_key": "anchor_sofa",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2000, "depth_mm": 900, "height_mm": 800},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                },
                {
                    "label": "Mirror",
                    "category": "mirror",
                    "category_canonical": "mirror",
                    "target_key": "mirror_01",
                    "source_index": 1,
                    "dims_mm": {"width_mm": 600, "depth_mm": 20, "height_mm": 900},
                    "two_pass_strategy": {"pass_role": "pass2_wall"},
                    "archetype_strategy": {"strictness": "critical", "structural_archetype": "reflective_wall_object"},
                },
            ],
            "primary_scale": {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0},
        },
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        {"y_top": 0.10, "y_bottom": 0.88},
        include_diagnostics=True,
        detect_furniture_boxes=lambda *args, **kwargs: [
            {"label": "Sofa", "box_2d": [520, 200, 820, 700]},
        ],
        remap_model_name="remap",
        remap_detect_timeout_sec=15,
        remap_detect_retry=0,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert "Mirror" in calls
    assert diagnostics["batch_detect_used"] is True


def test_normalize_detection_rows_preserves_detection_metadata():
    from application.render.scale_validation_support import _normalize_detection_rows

    rows = _normalize_detection_rows(
        detected_rows=[
            {
                "label": "Sideboard",
                "category": "storage",
                "category_canonical": "storage",
                "family": "storage",
                "box_2d": [520, 700, 900, 980],
                "bbox_norm": [0.7, 0.52, 0.98, 0.9],
            }
        ]
    )

    assert rows == [
        {
            "label": "Sideboard",
            "category": "storage",
            "category_canonical": "storage",
            "family": "storage",
            "target_key": None,
            "source_index": None,
            "bbox_norm": (0.7, 0.52, 0.98, 0.9),
            "box_2d": [520, 700, 900, 980],
            "_row_index": 0,
        }
    ]


def test_validate_furnished_scale_uses_raw_batch_rows_when_batch_remap_is_empty(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    staged_path = tmp_path / "render.png"
    Image.new("RGB", (320, 200), color=(255, 255, 255)).save(staged_path, format="PNG")

    def fake_match_items_to_detected_rows(analyzed_items, detected_rows, **kwargs):
        return [
            {
                "item": dict(item or {}),
                "src_idx": idx,
                "picked_row": None,
                "match_score": 0.0,
                "match_strategy": "unmatched",
            }
            for idx, item in enumerate(analyzed_items or [])
        ]

    monkeypatch.setattr(support, "match_items_to_detected_rows", fake_match_items_to_detected_rows)
    monkeypatch.setattr(support, "detect_item_bbox_norm", lambda *args, **kwargs: None)

    ok, issues, diagnostics = support.validate_furnished_scale(
        str(staged_path),
        {
            "items": [
                {
                    "label": "Sofa",
                    "category": "sofa",
                    "category_canonical": "sofa",
                    "target_key": "anchor_sofa",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 1600, "depth_mm": 900, "height_mm": 720},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                },
                {
                    "label": "Mirror",
                    "category": "mirror",
                    "category_canonical": "mirror",
                    "target_key": "mirror_01",
                    "source_index": 1,
                    "dims_mm": {"width_mm": 320, "depth_mm": 20, "height_mm": 864},
                    "two_pass_strategy": {"pass_role": "pass2_wall"},
                },
                {
                    "label": "Rug",
                    "category": "rug",
                    "category_canonical": "rug",
                    "target_key": "rug_01",
                    "source_index": 2,
                    "dims_mm": {"width_mm": 2200, "depth_mm": 2200, "height_mm": 10},
                    "two_pass_strategy": {"pass_role": "pass1_footprint"},
                },
            ],
            "primary_scale": {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0},
        },
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        {"y_top": 0.10, "y_bottom": 0.90},
        include_diagnostics=True,
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True},
        detect_furniture_boxes=lambda *args, **kwargs: [
            {"label": "Sofa", "box_2d": [520, 200, 820, 600]},
            {"label": "Mirror", "box_2d": [200, 700, 560, 780]},
            {"label": "Rug", "box_2d": [620, 180, 920, 730]},
        ],
        remap_model_name="gpt-5.4",
        remap_detect_timeout_sec=15,
        remap_detect_retry=0,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gpt-5.4",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert "no_matched_items" not in set(diagnostics.get("failed_rules") or [])
    assert diagnostics["batch_detect_used"] is True
    assert diagnostics["batch_detect_row_count"] == 3
    assert diagnostics["batch_detect_matched_row_count"] == 0
    assert set(diagnostics["matched_items"]) == {"anchor_sofa", "mirror_01", "rug_01"}


def test_validate_furnished_scale_prefers_gemini_detection_model_for_strict_openai_analysis(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    staged_path = tmp_path / "render.png"
    Image.new("RGB", (320, 200), color=(255, 255, 255)).save(staged_path, format="PNG")

    batch_models: list[str] = []
    bbox_models: list[str] = []

    def fake_detect_rows_from_render(*args, **kwargs):
        batch_models.append(str(kwargs.get("model_name")))
        return []

    def fake_detect_item_bbox_norm(*args, **kwargs):
        bbox_models.append(str(kwargs.get("analysis_model_name")))
        return None

    monkeypatch.setattr(support, "detect_rows_from_render", fake_detect_rows_from_render)
    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    ok, issues, diagnostics = support.validate_furnished_scale(
        str(staged_path),
        {
            "items": [
                {
                    "label": "Sofa",
                    "category": "sofa",
                    "category_canonical": "sofa",
                    "target_key": "anchor_sofa",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 1600, "depth_mm": 900, "height_mm": 720},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                }
            ],
            "primary_scale": {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0},
        },
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        {"y_top": 0.10, "y_bottom": 0.90},
        include_diagnostics=True,
        geometry_contract={"strict_scale_requested": True, "strict_scale_ready": True},
        detect_furniture_boxes=lambda *args, **kwargs: [],
        remap_model_name="gpt-5.4",
        remap_detect_timeout_sec=15,
        remap_detect_retry=0,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gpt-5.4",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert diagnostics["failed_rules"] == ["no_matched_items"]
    assert batch_models == ["gemini-3.1-pro-preview"]
    assert bbox_models == ["gemini-3.1-pro-preview"]


def test_validate_furnished_scale_short_circuits_on_geometry_hard_fail_when_batch_detect_fails(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    calls = []

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        calls.append(item_label)
        boxes = {
            "Sofa": (0.18, 0.50, 0.58, 0.74),
            "Lamp": (0.70, 0.46, 0.76, 0.72),
            "Mirror": (0.70, 0.18, 0.84, 0.62),
        }
        return boxes.get(item_label)

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)
    staged_path = tmp_path / "render.png"
    Image.new("RGB", (320, 200), color=(255, 255, 255)).save(staged_path, format="PNG")

    ok, issues, diagnostics = support.validate_furnished_scale(
        str(staged_path),
        {
            "items": [
                {
                    "label": "Sofa",
                    "category": "sofa",
                    "category_canonical": "sofa",
                    "target_key": "anchor_sofa",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2000, "depth_mm": 900, "height_mm": 800},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                },
                {
                    "label": "Lamp",
                    "category": "lamp",
                    "category_canonical": "lamp",
                    "target_key": "lamp_01",
                    "source_index": 1,
                    "dims_mm": {"width_mm": 250, "depth_mm": 250, "height_mm": 900},
                    "two_pass_strategy": {"pass_role": "pass2_small"},
                    "archetype_strategy": {"strictness": "critical", "structural_archetype": "tiny_absolute_scale_object"},
                },
                {
                    "label": "Mirror",
                    "category": "mirror",
                    "category_canonical": "mirror",
                    "target_key": "mirror_01",
                    "source_index": 2,
                    "dims_mm": {"width_mm": 600, "depth_mm": 20, "height_mm": 900},
                    "two_pass_strategy": {"pass_role": "pass2_wall"},
                    "archetype_strategy": {"strictness": "critical", "structural_archetype": "reflective_wall_object"},
                },
                {
                    "label": "Cushion",
                    "category": "decor",
                    "category_canonical": "decor",
                    "target_key": "decor_01",
                    "source_index": 3,
                    "dims_mm": {"width_mm": 450, "depth_mm": 450, "height_mm": 120},
                    "two_pass_strategy": {"pass_role": "pass2_detail"},
                    "archetype_strategy": {"strictness": "normal", "structural_archetype": "decor_object"},
                },
            ],
            "primary_scale": {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0},
        },
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        {"y_top": 0.10, "y_bottom": 0.88},
        include_diagnostics=True,
        detect_furniture_boxes=lambda *args, **kwargs: [],
        remap_model_name="remap",
        remap_detect_timeout_sec=15,
        remap_detect_retry=0,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert "Sofa" in calls
    assert "Lamp" in calls
    assert "Mirror" not in calls
    assert "Cushion" not in calls
    assert diagnostics.get("cheap_first_short_circuit") is True
    assert diagnostics["matched_items"]["anchor_sofa"]["bbox_norm"] == [0.18, 0.50, 0.58, 0.74]


def test_validate_furnished_scale_bounds_model_timeouts_to_remaining_deadline(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    monkeypatch.setattr(support.time, "time", lambda: 100.0)

    batch_timeouts = []
    bbox_timeouts = []

    def fake_detect_rows_from_render(*args, **kwargs):
        batch_timeouts.append(float(kwargs["timeout_sec"]))
        return []

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        bbox_timeouts.append(float(kwargs["timeout_sec"]))
        if item_label == "Sofa":
            return (0.20, 0.52, 0.60, 0.72)
        return None

    monkeypatch.setattr(support, "detect_rows_from_render", fake_detect_rows_from_render)
    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)
    staged_path = tmp_path / "render.png"
    Image.new("RGB", (320, 200), color=(255, 255, 255)).save(staged_path, format="PNG")

    ok, issues, diagnostics = support.validate_furnished_scale(
        str(staged_path),
        {
            "items": [
                {
                    "label": "Sofa",
                    "category": "sofa",
                    "category_canonical": "sofa",
                    "target_key": "anchor_sofa",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2000, "depth_mm": 900, "height_mm": 800},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                }
            ],
            "primary_scale": {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0},
        },
        {"width_mm": 5000, "depth_mm": 4000, "height_mm": 2800},
        {"y_top": 0.12, "y_bottom": 0.88},
        include_diagnostics=True,
        detect_furniture_boxes=lambda *args, **kwargs: [],
        remap_detect_timeout_sec=60,
        remap_detect_retry=0,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
        absolute_deadline_ts=108.0,
    )

    assert ok is True
    assert issues == []
    assert batch_timeouts == [8.0]
    assert bbox_timeouts == [8.0]
    assert diagnostics["matched_items"]["anchor_sofa"]["bbox_norm"] == [0.20, 0.52, 0.60, 0.72]


def test_validate_scale_from_detection_map_flags_rug_footprint_mismatch():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Table",
            "target_key": "anchor_table",
            "source_index": 0,
            "dims_mm": {"width_mm": 1100, "depth_mm": 700, "height_mm": 760},
        },
        {
            "label": "Rug",
            "target_key": "rug_02",
            "source_index": 1,
            "is_rug": True,
            "dims_mm": {"width_mm": 2000, "depth_mm": 1600, "height_mm": 10},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        room_planes={"y_top": 0.10, "y_bottom": 0.86},
        detected_rows=[
            {"label": "Table", "target_key": "anchor_table", "source_index": 0, "bbox_norm": [0.36, 0.58, 0.56, 0.80]},
            {"label": "Rug", "target_key": "rug_02", "source_index": 1, "bbox_norm": [0.14, 0.52, 0.76, 0.92]},
        ],
        primary_target_key="anchor_table",
    )

    assert ok is False
    assert issues
    assert "rug_vs_anchor_footprint" in diagnostics["failed_rules"]


def test_validate_scale_from_detection_map_rejects_mixed_incomplete_payload_before_primary_item():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        (
            0,
            {
                "label": "Lamp",
                "target_key": "lamp_incomplete",
                "source_index": 0,
                "dims_mm": {"width_mm": 100, "depth_mm": 100, "height_mm": 0},
            },
        ),
        (
            1,
            {
                "label": "Sofa",
                "target_key": "anchor_sofa",
                "source_index": 1,
                "dims_mm": {"width_mm": 2000, "depth_mm": 800, "height_mm": 750},
            },
        ),
        (
            2,
            {
                "label": "Table",
                "target_key": "table_01",
                "source_index": 2,
                "dims_mm": {"width_mm": 900, "depth_mm": 600, "height_mm": 700},
            },
        ),
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2800},
        room_planes={"y_top": 0.12, "y_bottom": 0.88},
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 1, "bbox_norm": [0.10, 0.50, 0.52, 0.80]},
            {"label": "Table", "target_key": "table_01", "source_index": 2, "bbox_norm": [0.58, 0.50, 0.82, 0.80]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is False
    assert issues == ["incomplete_items_missing_required_dimensions"]
    assert diagnostics["failed_rules"] == ["incomplete_items_missing_required_dimensions"]
    assert diagnostics["matched_items"] == {}


def test_validate_scale_from_detection_map_rejects_all_incomplete_payload():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Lamp",
            "target_key": "lamp_01",
            "source_index": 0,
            "dims_mm": {"width_mm": 120, "depth_mm": 90, "height_mm": 0},
        },
        {
            "label": "Mirror",
            "target_key": "mirror_01",
            "source_index": 1,
            "dims_mm": {"width_mm": 600, "depth_mm": 25, "height_mm": 0},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        room_planes={"y_top": 0.12, "y_bottom": 0.88},
        detected_rows=[
            {"label": "Lamp", "target_key": "lamp_01", "source_index": 0, "bbox_norm": [0.10, 0.50, 0.20, 0.80]},
            {"label": "Mirror", "target_key": "mirror_01", "source_index": 1, "bbox_norm": [0.30, 0.40, 0.44, 0.70]},
        ],
        primary_target_key="lamp_01",
    )

    assert ok is False
    assert issues == ["incomplete_items_missing_required_dimensions"]
    assert diagnostics["failed_rules"] == ["incomplete_items_missing_required_dimensions"]
    assert diagnostics["matched_items"] == {}


@pytest.mark.parametrize(
    "label,dims_mm",
    [
        ("Mirror", {"width_mm": 600, "depth_mm": 25, "height_mm": 0}),
        ("Poster", {"width_mm": 600, "depth_mm": 25, "height_mm": 0}),
        ("Art", {"width_mm": 600, "depth_mm": 25, "height_mm": 0}),
        ("Wall-mounted", {"width_mm": 600, "depth_mm": 25, "height_mm": 0}),
        ("Rug", {"width_mm": 1200, "depth_mm": 900, "height_mm": 0}),
    ],
)
def test_validate_scale_from_detection_map_requires_height_for_all_item_types(label, dims_mm):
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Anchor Table",
            "target_key": "anchor_table",
            "source_index": 0,
            "dims_mm": {"width_mm": 1400, "depth_mm": 800, "height_mm": 760},
        },
        {
            "label": label,
            "target_key": f"{label.lower()}_01",
            "source_index": 1,
            "dims_mm": dims_mm,
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        room_planes={"y_top": 0.10, "y_bottom": 0.86},
        detected_rows=[
            {"label": "Anchor Table", "target_key": "anchor_table", "source_index": 0, "bbox_norm": [0.30, 0.52, 0.52, 0.82]},
        ],
        primary_target_key="anchor_table",
    )

    assert ok is False
    assert issues == ["incomplete_items_missing_required_dimensions"]
    assert diagnostics["failed_rules"] == ["incomplete_items_missing_required_dimensions"]
    assert diagnostics["matched_items"] == {}


def test_validate_scale_from_detection_map_keeps_duplicate_labels_distinct():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Chair",
            "target_key": "chair_left",
            "source_index": 0,
            "dims_mm": {"width_mm": 650, "depth_mm": 650, "height_mm": 900},
        },
        {
            "label": "Chair",
            "target_key": "chair_right",
            "source_index": 1,
            "dims_mm": {"width_mm": 650, "depth_mm": 650, "height_mm": 900},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2500},
        detected_rows=[
            {"label": "Chair", "target_key": "chair_left", "source_index": 0, "bbox_norm": [0.10, 0.52, 0.22, 0.82]},
            {"label": "Chair", "target_key": "chair_right", "source_index": 1, "bbox_norm": [0.30, 0.52, 0.42, 0.82]},
        ],
        primary_target_key="chair_left",
    )

    assert ok is True
    assert issues == []
    assert diagnostics["matched_items"]["chair_left"]["bbox_norm"] == [0.10, 0.52, 0.22, 0.82]
    assert diagnostics["matched_items"]["chair_right"]["bbox_norm"] == [0.30, 0.52, 0.42, 0.82]


def test_validate_scale_from_detection_map_treats_source_index_zero_as_primary():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Table",
            "target_key": "table_01",
            "source_index": 1,
            "dims_mm": {"width_mm": 1600, "depth_mm": 900, "height_mm": 750},
        },
        {
            "label": "Sofa",
            "target_key": "sofa_00",
            "source_index": 0,
            "dims_mm": {"width_mm": 2000, "depth_mm": 800, "height_mm": 780},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 2800},
        detected_rows=[
            {"label": "Table", "target_key": "table_01", "source_index": 1, "bbox_norm": [0.10, 0.52, 0.48, 0.82]},
            {"label": "Sofa", "target_key": "sofa_00", "source_index": 0, "bbox_norm": [0.54, 0.52, 0.86, 0.82]},
        ],
        primary_source_index=0,
    )

    assert ok is True
    assert issues == []
    assert diagnostics["matched_items"]["sofa_00"]["bbox_norm"] == [0.54, 0.52, 0.86, 0.82]
    assert diagnostics["matched_items"]["table_01"]["bbox_norm"] == [0.10, 0.52, 0.48, 0.82]


def test_validate_scale_from_detection_map_rejects_ambiguous_label_only_duplicates():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Chair",
            "dims_mm": {"width_mm": 600, "depth_mm": 600, "height_mm": 900},
        },
        {
            "label": "Chair",
            "dims_mm": {"width_mm": 620, "depth_mm": 620, "height_mm": 920},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2500},
        detected_rows=[
            {"label": "Chair", "bbox_norm": [0.12, 0.52, 0.22, 0.82]},
            {"label": "Chair", "bbox_norm": [0.34, 0.52, 0.46, 0.82]},
        ],
    )

    assert ok is False
    assert issues == ["no_matched_items"]
    assert diagnostics["failed_rules"] == ["no_matched_items"]


def test_validate_scale_from_detection_map_matches_unique_family_when_label_drifts():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Anchor Sofa",
            "target_key": "anchor_sofa",
            "source_index": 0,
            "category": "sofa",
            "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 820},
        },
        {
            "label": "Standing Reflector",
            "target_key": "mirror_01",
            "source_index": 1,
            "category": "mirror",
            "dims_mm": {"width_mm": 700, "depth_mm": 40, "height_mm": 1800},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2600},
        room_planes={"y_top": 0.10, "y_bottom": 0.86},
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": [0.20, 0.52, 0.56, 0.84]},
            {"label": "Mirror", "bbox_norm": [0.72, 0.18, 0.84, 0.76]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is True
    assert issues == []
    assert diagnostics["matched_items"]["mirror_01"]["match_key"] == "family::mirror"
    assert diagnostics["matched_items"]["mirror_01"]["match_strategy"] == "family_unique"
    assert diagnostics["matched_items"]["mirror_01"]["match_confidence"] == pytest.approx(0.72)


def test_validate_scale_from_detection_map_emits_common_unmatched_issue_records_with_family_override():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Anchor Sofa",
            "target_key": "anchor_sofa",
            "source_index": 0,
            "category": "sofa",
            "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 820},
        },
        {
            "label": "Mirror",
            "target_key": "mirror_01",
            "source_index": 1,
            "category": "mirror",
            "dims_mm": {"width_mm": 700, "depth_mm": 40, "height_mm": 1800},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2600},
        room_planes={"y_top": 0.10, "y_bottom": 0.86},
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": [0.20, 0.52, 0.56, 0.84]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is False
    assert "unmatched_source_items" in diagnostics["failed_rules"]
    unmatched_record = next(row for row in diagnostics["issue_records"] if row["rule_id"] == "unmatched_item")
    assert unmatched_record["evidence"]["family_override_rule"] == "mirror_unmatched"


def test_validate_scale_from_detection_map_uses_layout_hint_for_surface_placed_items():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Anchor Sofa",
            "target_key": "anchor_sofa",
            "source_index": 0,
            "category": "sofa",
            "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 820},
        },
        {
            "label": "Table Lamp",
            "target_key": "lamp_01",
            "source_index": 1,
            "category": "table_lamp",
            "dims_mm": {"width_mm": 150, "depth_mm": 150, "height_mm": 260},
            "layout_envelope": {"placement_family": "surface_placed"},
            "identity_profile": {"floor_contact_expected": False},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2600},
        room_planes={"y_top": 0.10, "y_bottom": 0.86},
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": [0.20, 0.52, 0.56, 0.84]},
            {"label": "Lamp", "target_key": "lamp_01", "source_index": 1, "bbox_norm": [0.62, 0.48, 0.67, 0.62]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is True
    assert issues == []
    families = {row["item_key"]: row["family"] for row in diagnostics["rule_details"]["placement_family_checks"]}
    assert families["lamp_01"] == "surface_placed"


def test_validate_scale_from_detection_map_prefers_target_key_over_stale_label():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Table",
            "target_key": "table_01",
            "source_index": 0,
            "dims_mm": {"width_mm": 3000, "depth_mm": 1200, "height_mm": 760},
        },
        {
            "label": "Sofa",
            "target_key": "sofa_anchor",
            "source_index": 1,
            "dims_mm": {"width_mm": 1800, "depth_mm": 800, "height_mm": 820},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 2800},
        detected_rows=[
            {"label": "Table", "target_key": "table_01", "source_index": 0, "bbox_norm": [0.08, 0.52, 0.58, 0.82]},
            {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 1, "bbox_norm": [0.72, 0.52, 0.86, 0.82]},
        ],
        primary_target_key="missing_anchor",
        primary_source_index=0,
        primary_label="Table",
    )

    assert ok is True
    assert issues == []
    assert diagnostics["matched_items"]["table_01"]["bbox_norm"] == [0.08, 0.52, 0.58, 0.82]
    assert diagnostics["matched_items"]["sofa_anchor"]["bbox_norm"] == [0.72, 0.52, 0.86, 0.82]
    assert diagnostics["rule_details"]["primary_anchor_resolution"]["fallback_used"] is False


def test_validate_scale_from_detection_map_falls_back_to_source_index_when_target_key_is_stale():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Table",
            "target_key": "table_01",
            "source_index": 1,
            "dims_mm": {"width_mm": 3000, "depth_mm": 1200, "height_mm": 760},
        },
        {
            "label": "Sofa",
            "target_key": "sofa_anchor",
            "source_index": 0,
            "dims_mm": {"width_mm": 1800, "depth_mm": 800, "height_mm": 820},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 2800},
        detected_rows=[
            {"label": "Table", "target_key": "table_01", "source_index": 1, "bbox_norm": [0.08, 0.52, 0.58, 0.82]},
            {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0, "bbox_norm": [0.62, 0.52, 0.92, 0.82]},
        ],
        primary_target_key="stale_anchor",
        primary_source_index=0,
        primary_label="Table",
    )

    assert ok is True
    assert issues == []
    assert diagnostics["rule_details"]["primary_width_vs_room_width"]["item_key"] == "sofa_anchor"
    assert diagnostics["matched_items"]["sofa_anchor"]["bbox_norm"] == [0.62, 0.52, 0.92, 0.82]
    assert diagnostics["rule_details"]["primary_anchor_resolution"]["fallback_used"] is False


def test_validate_scale_from_detection_map_keeps_label_only_flat_rug_in_rug_rule():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Table",
            "target_key": "anchor_table",
            "source_index": 0,
            "dims_mm": {"width_mm": 1400, "depth_mm": 800, "height_mm": 760},
        },
        {
            "label": "Rug",
            "dims_mm": {"width_mm": 1200, "depth_mm": 900, "height_mm": 10},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        detected_rows=[
            {"label": "Table", "target_key": "anchor_table", "source_index": 0, "bbox_norm": [0.30, 0.52, 0.52, 0.82]},
            {"label": "Rug", "bbox_norm": [0.22, 0.60, 0.408, 0.70]},
        ],
        primary_target_key="anchor_table",
    )

    assert ok is True
    assert issues == []
    assert "Rug#1" in diagnostics["matched_items"]
    assert diagnostics["rule_details"]["rug_vs_anchor_footprint"][0]["rug_key"] == "Rug#1"
    assert diagnostics["rule_details"]["rug_vs_anchor_footprint"][0]["observed"] > 0


def test_validate_scale_from_detection_map_flags_wall_attached_item_near_floor():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Sofa",
            "category": "sofa",
            "target_key": "anchor_sofa",
            "source_index": 0,
            "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 850},
        },
        {
            "label": "Poster",
            "category": "poster",
            "target_key": "poster_01",
            "source_index": 1,
            "dims_mm": {"width_mm": 600, "depth_mm": 50, "height_mm": 900},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2600},
        room_planes={"y_top": 0.10, "y_bottom": 0.86},
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": [0.18, 0.54, 0.58, 0.84]},
            {"label": "Poster", "target_key": "poster_01", "source_index": 1, "bbox_norm": [0.72, 0.50, 0.84, 0.83]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is False
    assert any(issue.startswith("wall_attached_floor_collision: poster_01") for issue in issues)
    assert "placement_family_checks" in diagnostics["rule_details"]


def test_validate_scale_from_detection_map_fails_closed_on_internal_exception(monkeypatch):
    from application.render import scale_validation_support as support

    monkeypatch.setattr(
        support,
        "_normalize_detection_rows",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    ok, issues, diagnostics = support.validate_scale_from_detection_map(
        [],
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2600},
        detected_rows=[],
    )

    assert ok is False
    assert issues == ["scale_validation_exception"]
    assert diagnostics["failed_rules"] == ["scale_validation_exception"]
    assert diagnostics["exception"] == "boom"


def test_validate_scale_from_detection_map_excludes_incomplete_rug_items():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Table",
            "target_key": "anchor_table",
            "source_index": 0,
            "dims_mm": {"width_mm": 1400, "depth_mm": 800, "height_mm": 760},
        },
        {
            "label": "Rug",
            "dims_mm": {"width_mm": 1200, "depth_mm": 900, "height_mm": 0},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        detected_rows=[
            {"label": "Table", "target_key": "anchor_table", "source_index": 0, "bbox_norm": [0.30, 0.52, 0.52, 0.82]},
            {"label": "Rug", "bbox_norm": [0.22, 0.60, 0.408, 0.70]},
        ],
        primary_target_key="anchor_table",
    )

    assert ok is False
    assert issues == ["incomplete_items_missing_required_dimensions"]
    assert diagnostics["failed_rules"] == ["incomplete_items_missing_required_dimensions"]
    assert diagnostics["matched_items"] == {}


def test_validate_scale_from_detection_map_uses_width_for_non_square_rug():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Sofa",
            "target_key": "anchor_sofa",
            "source_index": 0,
            "dims_mm": {"width_mm": 2000, "depth_mm": 1000, "height_mm": 800},
        },
        {
            "label": "Rug",
            "target_key": "rug_01",
            "source_index": 1,
            "is_rug": True,
            "dims_mm": {"width_mm": 1200, "depth_mm": 900, "height_mm": 10},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2800},
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": [0.18, 0.52, 0.58, 0.82]},
            {"label": "Rug", "target_key": "rug_01", "source_index": 1, "bbox_norm": [0.22, 0.58, 0.46, 0.68]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is True
    assert issues == []
    assert "rug_vs_anchor_footprint" in diagnostics["rule_details"]
    assert diagnostics["rule_details"]["rug_vs_anchor_footprint"][0]["rug_key"] == "rug_01"


def test_validate_furnished_scale_flags_obviously_oversized_followup_item(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.50, 0.50, 0.70)
        if item_label == "Chair":
            return (0.55, 0.40, 0.65, 0.604)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "primary": {"label": "Sofa"},
        "items": [
            {
                "label": "Sofa",
                "target_key": "anchor_sofa",
                "source_index": 0,
                "dims_mm": {"width_mm": 2000, "depth_mm": 1000, "height_mm": 900},
            },
            {
                "label": "Chair",
                "target_key": "chair_01",
                "source_index": 1,
                "dims_mm": {"width_mm": 800, "depth_mm": 800, "height_mm": 180},
            },
        ],
    }

    ok, issues = support.validate_furnished_scale(
        "ignored.png",
        furniture_specs_json,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        {"y_top": 0.10, "y_bottom": 0.90},
        primary_label="Sofa",
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="test-model",
        safe_json_from_model_text=lambda text: {},
        log_brief=True,
        logger=DummyLogger(),
    )

    assert ok is False
    assert any("chair_01 taller than expected" in issue for issue in issues)
    assert any("chair_01 exceeds expected room height ratio" in issue for issue in issues)


def test_validate_furnished_scale_flags_subtle_height_overscale(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.50, 0.50, 0.70)
        if item_label == "Chair":
            return (0.55, 0.40, 0.65, 0.471)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "primary": {"label": "Sofa"},
        "items": [
            {
                "label": "Sofa",
                "target_key": "anchor_sofa",
                "source_index": 0,
                "dims_mm": {"width_mm": 2000, "depth_mm": 1000, "height_mm": 900},
            },
            {
                "label": "Chair",
                "target_key": "chair_01",
                "source_index": 1,
                "dims_mm": {"width_mm": 800, "depth_mm": 800, "height_mm": 300},
            },
        ],
    }

    ok, issues = support.validate_furnished_scale(
        "ignored.png",
        furniture_specs_json,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        {"y_top": 0.10, "y_bottom": 0.90},
        primary_label="Sofa",
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="test-model",
        safe_json_from_model_text=lambda text: {},
        log_brief=True,
        logger=DummyLogger(),
    )

    assert ok is False
    assert any("chair_01 taller than expected" in issue for issue in issues)


def test_validate_furnished_scale_flags_rug_in_followup_height_checks(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.50, 0.50, 0.70)
        if item_label == "Rug":
            return (0.20, 0.55, 0.40, 0.92)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "primary": {"label": "Sofa"},
        "items": [
            {
                "label": "Sofa",
                "target_key": "anchor_sofa",
                "source_index": 0,
                "dims_mm": {"width_mm": 2000, "depth_mm": 1000, "height_mm": 900},
            },
            {
                "label": "Rug",
                "target_key": "rug_01",
                "source_index": 1,
                "dims_mm": {"width_mm": 1000, "depth_mm": 1000, "height_mm": 10},
            },
        ],
    }

    ok, issues = support.validate_furnished_scale(
        "ignored.png",
        furniture_specs_json,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        {"y_top": 0.10, "y_bottom": 0.90},
        primary_label="Sofa",
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="test-model",
        safe_json_from_model_text=lambda text: {},
        log_brief=True,
        logger=DummyLogger(),
    )

    assert ok is False
    assert any("rug_01" in issue for issue in issues)


def test_validate_furnished_scale_rejects_all_incomplete_payload(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "primary": {"label": "Lamp"},
        "items": [
            {
                "label": "Lamp",
                "target_key": "lamp_01",
                "source_index": 0,
                "dims_mm": {"width_mm": 120, "depth_mm": 90, "height_mm": 0},
            },
            {
                "label": "Mirror",
                "target_key": "mirror_01",
                "source_index": 1,
                "dims_mm": {"width_mm": 600, "depth_mm": 25, "height_mm": 0},
            },
        ],
    }

    ok, issues = support.validate_furnished_scale(
        "ignored.png",
        furniture_specs_json,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        {"y_top": 0.10, "y_bottom": 0.90},
        primary_label="Lamp",
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="test-model",
        safe_json_from_model_text=lambda text: {},
        log_brief=True,
        logger=DummyLogger(),
    )

    assert ok is False
    assert issues == ["incomplete_items_missing_required_dimensions"]


def test_validate_furnished_scale_rejects_mixed_incomplete_payload(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    furniture_specs_json = {
        "primary": {"label": "Table", "target_key": "anchor_table", "source_index": 0},
        "items": [
            {
                "label": "Table",
                "target_key": "anchor_table",
                "source_index": 0,
                "dims_mm": {"width_mm": 1400, "depth_mm": 800, "height_mm": 760},
            },
            {
                "label": "Mirror",
                "target_key": "mirror_01",
                "source_index": 1,
                "dims_mm": {"width_mm": 600, "depth_mm": 25, "height_mm": 0},
            },
        ],
    }

    def fail_if_called(*args, **kwargs):
        raise AssertionError("detect_item_bbox_norm should not run for incomplete payloads")

    monkeypatch.setattr(support, "detect_item_bbox_norm", fail_if_called)

    ok, issues = support.validate_furnished_scale(
        "unused.png",
        furniture_specs_json,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        {"y_top": 0.12, "y_bottom": 0.88},
        primary_label="Table",
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert issues == ["incomplete_items_missing_required_dimensions"]


def test_validate_furnished_scale_prefers_primary_scale_anchor(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Table":
            return (0.10, 0.50, 0.35, 0.72)
        if item_label == "Sofa":
            return (0.50, 0.50, 0.70, 0.71)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 1},
        "primary_scale": {"label": "Table", "target_key": "table_anchor", "source_index": 0},
        "items": [
            {
                "label": "Table",
                "target_key": "table_anchor",
                "source_index": 0,
                "dims_mm": {"width_mm": 1500, "depth_mm": 800, "height_mm": 760},
            },
            {
                "label": "Sofa",
                "target_key": "sofa_anchor",
                "source_index": 1,
                "dims_mm": {"width_mm": 2400, "depth_mm": 900, "height_mm": 790},
            },
        ],
    }

    ok, issues = support.validate_furnished_scale(
        "unused.png",
        furniture_specs_json,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        {"y_top": 0.12, "y_bottom": 0.88},
        primary_label=None,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is True
    assert issues == []


def test_validate_furnished_scale_rejects_reference_shape_drift_for_sensitive_item(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    staged_path = tmp_path / "staged.png"
    ref_crop_path = tmp_path / "table_ref.png"
    Image.new("RGB", (320, 200), color=(255, 255, 255)).save(staged_path, format="PNG")
    Image.new("RGB", (64, 64), color=(32, 32, 32)).save(ref_crop_path, format="PNG")

    captured = {"prompts": []}

    def fake_detect_item_bbox_norm(staged_path_value, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.48, 0.54, 0.84)
        if item_label == "Side Table":
            return (0.58, 0.62, 0.74, 0.82)
        return None

    def fake_call_gemini_with_failover(model_name, content, *args, **kwargs):
        prompt = content[0]
        if "REFERENCE FURNITURE FIDELITY REVIEW" in prompt:
            captured["prompts"].append(prompt)
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "same_object": False,
                        "shape_match": False,
                        "material_match": True,
                        "reason": "Rendered support geometry no longer matches the reference.",
                    }
                )
            )
        return SimpleNamespace(text="{}")

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0},
        "items": [
            {
                "label": "Sofa",
                "category": "sofa",
                "target_key": "sofa_anchor",
                "source_index": 0,
                "crop_path": str(ref_crop_path),
                "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
            },
                {
                    "label": "Side Table",
                    "category": "table",
                    "target_key": "table_01",
                    "source_index": 1,
                    "crop_path": str(ref_crop_path),
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 500},
                    "two_pass_strategy": {"pass_role": "pass1_footprint"},
                    "archetype_strategy": {"strictness": "standard", "structural_archetype": "support_geometry_sensitive_object"},
                },
            ],
        }

    ok, issues = support.validate_furnished_scale(
        str(staged_path),
        furniture_specs_json,
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 3000},
        {"y_top": 0.12, "y_bottom": 0.88},
        primary_label="Sofa",
        call_gemini_with_failover=fake_call_gemini_with_failover,
        analysis_model_name="test-model",
        safe_json_from_model_text=lambda text: json.loads(text),
        log_brief=True,
        logger=DummyLogger(),
    )

    assert ok is False
    assert any(issue.startswith("reference_shape_drift:") for issue in issues)
    assert any("table_01" in prompt for prompt in captured["prompts"])


def test_validate_furnished_scale_validates_rug_only_payload_end_to_end(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Rug":
            return (0.10, 0.55, 0.78, 0.90)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "items": [
            {
                "label": "Rug",
                "target_key": "rug_01",
                "source_index": 0,
                "dims_mm": {"width_mm": 3000, "depth_mm": 2000, "height_mm": 10},
            },
        ],
    }

    ok, issues = support.validate_furnished_scale(
        "ignored.png",
        furniture_specs_json,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        {"y_top": 0.10, "y_bottom": 0.90},
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="test-model",
        safe_json_from_model_text=lambda text: {},
        log_brief=True,
        logger=DummyLogger(),
    )

    assert ok is False
    assert issues
    assert any("primary_width_vs_room_width" in issue for issue in issues)


def test_validate_furnished_scale_rejects_missing_primary_anchor(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return None
        if item_label == "Table":
            return (0.20, 0.50, 0.42, 0.82)
        if item_label == "Chair":
            return (0.55, 0.40, 0.68, 0.86)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "primary": {"label": "Sofa"},
        "items": [
            {
                "label": "Sofa",
                "target_key": "anchor_sofa",
                "source_index": 0,
                "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
            },
            {
                "label": "Table",
                "target_key": "table_01",
                "source_index": 1,
                "dims_mm": {"width_mm": 900, "depth_mm": 600, "height_mm": 700},
            },
            {
                "label": "Chair",
                "target_key": "chair_01",
                "source_index": 2,
                "dims_mm": {"width_mm": 600, "depth_mm": 600, "height_mm": 300},
            },
        ],
    }

    ok, issues = support.validate_furnished_scale(
        "ignored.png",
        furniture_specs_json,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        {"y_top": 0.10, "y_bottom": 0.86},
        primary_label="Sofa",
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="test-model",
        safe_json_from_model_text=lambda text: {},
        log_brief=True,
        logger=DummyLogger(),
    )

    assert ok is False
    assert "no_matched_items" in issues


def test_validate_furnished_scale_rejects_partial_primary_metadata_when_anchor_is_missing(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return None
        if item_label == "Table":
            return (0.14, 0.52, 0.34, 0.82)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "primary": {"target_key": "sofa_anchor"},
        "items": [
            {
                "label": "Table",
                "target_key": "table_01",
                "source_index": 1,
                "dims_mm": {"width_mm": 3000, "depth_mm": 1200, "height_mm": 320},
            },
            {
                "label": "Sofa",
                "target_key": "sofa_anchor",
                "source_index": 0,
                "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
            },
        ],
    }

    ok, issues = support.validate_furnished_scale(
        "ignored.png",
        furniture_specs_json,
        {"width_mm": 6000, "depth_mm": 6000, "height_mm": 3000},
        {"y_top": 0.10, "y_bottom": 0.86},
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="test-model",
        safe_json_from_model_text=lambda text: {},
        log_brief=True,
        logger=DummyLogger(),
    )

    assert ok is False
    assert "no_matched_items" in issues


def test_validate_scale_from_detection_map_flags_unmatched_source_items():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Sofa",
            "target_key": "anchor_sofa",
            "source_index": 0,
            "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
        },
        {
            "label": "Chair",
            "target_key": "chair_01",
            "source_index": 1,
            "dims_mm": {"width_mm": 650, "depth_mm": 650, "height_mm": 900},
        },
        {
            "label": "Floor Lamp",
            "target_key": "lamp_01",
            "source_index": 2,
            "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 1700},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2800},
        room_planes={"y_top": 0.10, "y_bottom": 0.88},
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": [0.10, 0.48, 0.52, 0.82]},
            {"label": "Chair", "target_key": "chair_01", "source_index": 1, "bbox_norm": [0.60, 0.48, 0.74, 0.84]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is False
    assert "unmatched_source_items" in diagnostics["failed_rules"]
    assert any("lamp_01" in item["item_key"] for item in diagnostics["unmatched_items"])


def test_validate_scale_from_detection_map_flags_large_height_ratio_mismatch_both_directions():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    items = [
        {
            "label": "Sofa",
            "target_key": "anchor_sofa",
            "source_index": 0,
            "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
        },
        {
            "label": "Floor Lamp",
            "target_key": "lamp_tall",
            "source_index": 1,
            "dims_mm": {"width_mm": 900, "depth_mm": 400, "height_mm": 2300},
        },
        {
            "label": "Table Lamp",
            "target_key": "lamp_tiny",
            "source_index": 2,
            "dims_mm": {"width_mm": 120, "depth_mm": 120, "height_mm": 120},
        },
    ]

    ok, issues, diagnostics = validate_scale_from_detection_map(
        items,
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2800},
        room_planes={"y_top": 0.10, "y_bottom": 0.88},
        detected_rows=[
            {"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": [0.10, 0.48, 0.52, 0.82]},
            {"label": "Picture Frame", "target_key": "lamp_tall", "source_index": 1, "bbox_norm": [0.62, 0.40, 0.70, 0.58]},
            {"label": "Picture Frame", "target_key": "lamp_tiny", "source_index": 2, "bbox_norm": [0.74, 0.38, 0.82, 0.56]},
        ],
        primary_target_key="anchor_sofa",
    )

    assert ok is False
    assert "relative_height_vs_anchor" in diagnostics["failed_rules"]
    assert any("lamp_tall" in issue for issue in issues)
    assert any("lamp_tiny" in issue for issue in issues)


def test_validate_furnished_scale_allows_low_profile_item_with_small_bbox_jitter(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.40, 0.50, 0.80)
        if item_label == "Tray":
            return (0.58, 0.748, 0.74, 0.7905)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    furniture_specs_json = {
        "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0},
        "items": [
            {
                "label": "Sofa",
                "target_key": "sofa_anchor",
                "source_index": 0,
                "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 800},
            },
            {
                "label": "Tray",
                "target_key": "tray_01",
                "source_index": 1,
                "dims_mm": {"width_mm": 400, "depth_mm": 300, "height_mm": 80},
            },
        ],
    }

    ok, issues = support.validate_furnished_scale(
        "unused.png",
        furniture_specs_json,
        {"width_mm": 5500, "depth_mm": 7000, "height_mm": 3200},
        None,
        primary_label=None,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is True
    assert issues == []


def test_validate_furnished_scale_can_return_diagnostics_when_requested(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.40, 0.50, 0.80)
        if item_label == "Mirror":
            return (0.66, 0.18, 0.84, 0.56)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    ok, issues, diagnostics = support.validate_furnished_scale(
        "unused.png",
        {
            "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0},
            "items": [
                {
                    "label": "Sofa",
                    "target_key": "sofa_anchor",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 800},
                },
                {
                    "label": "Mirror",
                    "target_key": "mirror_01",
                    "source_index": 1,
                    "dims_mm": {"width_mm": 700, "depth_mm": 20, "height_mm": 900},
                },
            ],
        },
        {"width_mm": 5500, "depth_mm": 7000, "height_mm": 3200},
        {"y_top": 0.08, "y_bottom": 0.88},
        primary_label=None,
        include_diagnostics=True,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert diagnostics["matched_items"]["sofa_anchor"]["target_key"] == "sofa_anchor"
    assert isinstance(diagnostics["detected_rows"], list)


def test_validate_furnished_scale_short_circuits_on_primary_width_geometry_fail(monkeypatch):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    detected_labels = []

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        detected_labels.append(item_label)
        if item_label == "Sofa":
            return (0.02, 0.38, 0.96, 0.82)
        if item_label == "Mirror":
            return (0.70, 0.18, 0.84, 0.62)
        return None

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)
    monkeypatch.setattr(
        support,
        "_review_reference_fidelity",
        lambda *args, **kwargs: [],
    )

    ok, issues, diagnostics = support.validate_furnished_scale(
        "unused.png",
        {
            "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0},
            "items": [
                {
                    "label": "Sofa",
                    "target_key": "sofa_anchor",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 800},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                    "archetype_strategy": {"strictness": "critical", "structural_archetype": "topology_sensitive_seating"},
                },
                {
                    "label": "Mirror",
                    "target_key": "mirror_01",
                    "source_index": 1,
                    "category": "mirror",
                    "dims_mm": {"width_mm": 700, "depth_mm": 20, "height_mm": 900},
                    "two_pass_strategy": {"pass_role": "pass2_wall"},
                    "archetype_strategy": {"strictness": "critical", "structural_archetype": "reflective_wall_object"},
                },
            ],
        },
        {"width_mm": 4000, "depth_mm": 4000, "height_mm": 2400},
        {"y_top": 0.10, "y_bottom": 0.88},
        include_diagnostics=True,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is False
    assert "primary_width_vs_room_width" in " ".join(issues)
    assert diagnostics.get("cheap_first_short_circuit") is True
    assert detected_labels == ["Sofa"]


def test_validate_furnished_scale_skips_reference_review_for_noncritical_pass2_item(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    ref_path = tmp_path / "decor_ref.png"
    Image.new("RGB", (32, 32), "white").save(ref_path)

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.40, 0.50, 0.80)
        if item_label == "Decor":
            return (0.60, 0.60, 0.68, 0.80)
        return None

    reviewed_labels = []

    def fake_review_reference_fidelity(staged_path, item, bbox_norm, **kwargs):
        reviewed_labels.append(item.get("label"))
        return []

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)
    monkeypatch.setattr(support, "_review_reference_fidelity", fake_review_reference_fidelity)

    ok, issues = support.validate_furnished_scale(
        "unused.png",
        {
            "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0},
            "items": [
                {
                    "label": "Sofa",
                    "target_key": "sofa_anchor",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 800},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                    "archetype_strategy": {"strictness": "critical", "structural_archetype": "topology_sensitive_seating"},
                },
                {
                    "label": "Decor",
                    "target_key": "decor_01",
                    "source_index": 1,
                    "category": "decor",
                    "crop_path": str(ref_path),
                    "dims_mm": {"width_mm": 300, "depth_mm": 300, "height_mm": 400},
                    "two_pass_strategy": {"pass_role": "pass2_decor"},
                    "archetype_strategy": {"strictness": "standard", "structural_archetype": "generic_furniture_object"},
                    "identity_profile": {"family": "decor"},
                },
            ],
        },
        {"width_mm": 5500, "depth_mm": 7000, "height_mm": 3200},
        None,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is True
    assert issues == []
    assert reviewed_labels == []


def test_validate_furnished_scale_reviews_identity_rich_noncritical_pass2_item(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    side_ref = tmp_path / "side_ref.png"
    Image.new("RGB", (32, 32), "white").save(side_ref)

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.40, 0.50, 0.80)
        if item_label == "Side Table":
            return (0.62, 0.60, 0.76, 0.81)
        return None

    reviewed_labels = []

    def fake_review_reference_fidelity(staged_path, item, bbox_norm, **kwargs):
        reviewed_labels.append(item.get("label"))
        return []

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)
    monkeypatch.setattr(support, "_review_reference_fidelity", fake_review_reference_fidelity)

    ok, issues = support.validate_furnished_scale(
        "unused.png",
        {
            "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0},
            "items": [
                {
                    "label": "Sofa",
                    "target_key": "sofa_anchor",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 800},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                    "archetype_strategy": {"strictness": "critical", "structural_archetype": "topology_sensitive_seating"},
                },
                {
                    "label": "Side Table",
                    "target_key": "side_01",
                    "source_index": 1,
                    "category": "table",
                    "crop_path": str(side_ref),
                    "dims_mm": {"width_mm": 500, "depth_mm": 500, "height_mm": 520},
                    "two_pass_strategy": {"pass_role": "pass2_detail"},
                    "archetype_strategy": {"strictness": "standard", "structural_archetype": "generic_furniture_object"},
                    "identity_profile": {
                        "family": "table",
                        "distinctive_parts": ["tripod leg"],
                        "material_cues": ["brushed brass"],
                    },
                    "product_identity": {"preserve_rules": ["keep round stone top"]},
                    "layout_envelope": {"room_width_ratio": 0.18, "room_height_ratio": 0.16},
                },
            ],
        },
        {"width_mm": 5500, "depth_mm": 7000, "height_mm": 3200},
        None,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is True
    assert issues == []
    assert reviewed_labels == ["Side Table"]


def test_validate_furnished_scale_skips_reference_review_for_noncritical_pass1_anchor(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    ref_path = tmp_path / "table_ref.png"
    Image.new("RGB", (32, 32), "white").save(ref_path)

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        return (0.10, 0.40, 0.318, 0.80)

    reviewed_labels = []

    def fake_review_reference_fidelity(staged_path, item, bbox_norm, **kwargs):
        reviewed_labels.append(item.get("label"))
        return []

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)
    monkeypatch.setattr(support, "_review_reference_fidelity", fake_review_reference_fidelity)

    ok, issues = support.validate_furnished_scale(
        "unused.png",
        {
            "primary": {"label": "Table", "target_key": "table_anchor", "source_index": 0},
            "items": [
                {
                    "label": "Table",
                    "target_key": "table_anchor",
                    "source_index": 0,
                    "category": "table",
                    "crop_path": str(ref_path),
                    "dims_mm": {"width_mm": 1200, "depth_mm": 800, "height_mm": 740},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                    "archetype_strategy": {"strictness": "standard", "structural_archetype": "generic_furniture_object"},
                    "identity_profile": {"family": "table"},
                },
            ],
        },
        {"width_mm": 5500, "depth_mm": 7000, "height_mm": 3200},
        {"y_top": 0.10, "y_bottom": 0.86},
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is True
    assert issues == []
    assert reviewed_labels == []


def test_validate_furnished_scale_reviews_identity_rich_noncritical_pass1_anchor(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    table_ref = tmp_path / "table_identity_ref.png"
    Image.new("RGB", (32, 32), "white").save(table_ref)

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        return (0.10, 0.40, 0.318, 0.80)

    reviewed_labels = []

    def fake_review_reference_fidelity(staged_path, item, bbox_norm, **kwargs):
        reviewed_labels.append(item.get("label"))
        return []

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)
    monkeypatch.setattr(support, "_review_reference_fidelity", fake_review_reference_fidelity)

    ok, issues = support.validate_furnished_scale(
        "unused.png",
        {
            "primary": {"label": "Table", "target_key": "table_anchor", "source_index": 0},
            "items": [
                {
                    "label": "Table",
                    "target_key": "table_anchor",
                    "source_index": 0,
                    "category": "table",
                    "crop_path": str(table_ref),
                    "dims_mm": {"width_mm": 1200, "depth_mm": 800, "height_mm": 740},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                    "archetype_strategy": {"strictness": "standard", "structural_archetype": "generic_furniture_object"},
                    "identity_profile": {"family": "table", "distinctive_parts": ["arched apron"]},
                    "product_identity": {"preserve_rules": ["keep fluted pedestal"]},
                },
            ],
        },
        {"width_mm": 5500, "depth_mm": 7000, "height_mm": 3200},
        {"y_top": 0.10, "y_bottom": 0.86},
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is True
    assert issues == []
    assert reviewed_labels == ["Table"]


def test_validate_furnished_scale_passes_identity_context_into_item_localization(monkeypatch):
    from application.render import scale_validation_support as support

    captured_contexts = []

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, item_context=None, **kwargs):
        captured_contexts.append({"label": item_label, "context": item_context})
        return (0.10, 0.40, 0.50, 0.80)

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    ok, issues, diagnostics = support.validate_furnished_scale(
        "unused.png",
        {
            "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0},
            "items": [
                {
                    "label": "Sofa",
                    "target_key": "sofa_anchor",
                    "source_index": 0,
                    "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 800},
                    "identity_profile": {
                        "family": "sofa",
                        "preserve_rules": ["keep center backrest gap"],
                    },
                    "layout_envelope": {
                        "room_width_ratio": 0.36,
                        "room_height_ratio": 0.25,
                    },
                },
            ],
        },
        {"width_mm": 5500, "depth_mm": 7000, "height_mm": 3200},
        {"y_top": 0.10, "y_bottom": 0.86},
        primary_label="Sofa",
        include_diagnostics=True,
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is True
    assert issues == []
    assert diagnostics["matched_items"]["sofa_anchor"]["target_key"] == "sofa_anchor"
    assert captured_contexts[0]["context"]["family"] == "sofa"
    assert captured_contexts[0]["context"]["layout_envelope"]["room_width_ratio"] == 0.36
    assert captured_contexts[0]["context"]["preserve_rules"] == ["keep center backrest gap"]


def test_validate_furnished_scale_reviews_primary_item_when_it_is_critical(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    ref_path = tmp_path / "primary_sofa.png"
    Image.new("RGB", (32, 32), "white").save(ref_path)

    def fake_detect_item_bbox_norm(staged_path, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.40, 0.50, 0.80)
        return None

    reviewed_labels = []

    def fake_review_reference_fidelity(staged_path, item, bbox_norm, **kwargs):
        reviewed_labels.append(item.get("label"))
        return []

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)
    monkeypatch.setattr(support, "_review_reference_fidelity", fake_review_reference_fidelity)

    ok, issues = support.validate_furnished_scale(
        "unused.png",
        {
            "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0},
            "items": [
                {
                    "label": "Sofa",
                    "target_key": "sofa_anchor",
                    "source_index": 0,
                    "crop_path": str(ref_path),
                    "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 800},
                    "two_pass_strategy": {"pass_role": "pass1_anchor"},
                    "archetype_strategy": {"strictness": "critical", "structural_archetype": "topology_sensitive_seating"},
                },
            ],
        },
        {"width_mm": 5500, "depth_mm": 7000, "height_mm": 3200},
        {"y_top": 0.10, "y_bottom": 0.86},
        call_gemini_with_failover=lambda *args, **kwargs: None,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: {},
        log_brief=False,
        logger=DummyLogger(),
    )

    assert ok is True
    assert issues == []
    assert reviewed_labels == ["Sofa"]


def test_review_reference_fidelity_treats_string_false_flags_as_failures(tmp_path):
    from application.render import scale_validation_support as support

    staged_path = tmp_path / "staged.png"
    ref_path = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), "white").save(staged_path)
    Image.new("RGB", (64, 64), "white").save(ref_path)

    def fake_call_gemini_with_failover(model_name, content, *args, **kwargs):
        return SimpleNamespace(
            text='{"same_object":"false","shape_match":"false","material_match":"false","reflection_match":"false"}'
        )

    issues = support._review_reference_fidelity(
        str(staged_path),
        {
            "label": "Mirror",
            "target_key": "mirror_01",
            "source_index": 0,
            "crop_path": str(ref_path),
            "category": "mirror",
            "dims_mm": {"width_mm": 700, "depth_mm": 20, "height_mm": 900},
            "identity_profile": {"family": "mirror"},
        },
        (0.10, 0.15, 0.275, 0.55),
        call_gemini_with_failover=fake_call_gemini_with_failover,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: json.loads(text),
    )

    assert "reference_shape_drift:mirror_01" in issues
    assert "reference_material_drift:mirror_01" in issues
    assert "mirror_reflection_drift:mirror_01" in issues


def test_validate_scale_from_detection_map_treats_missing_bbox_norm_on_detected_row_as_unmatched():
    from application.render.scale_validation_support import validate_scale_from_detection_map

    ok, issues, diagnostics = validate_scale_from_detection_map(
        [
            {
                "label": "Sofa",
                "target_key": "anchor_sofa",
                "source_index": 0,
                "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
            }
        ],
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 2800},
        room_planes={"y_top": 0.10, "y_bottom": 0.88},
        detected_rows=[{"label": "Sofa", "target_key": "anchor_sofa", "source_index": 0, "bbox_norm": None}],
        primary_target_key="anchor_sofa",
    )

    assert ok is False
    assert diagnostics["matched_items"] == {}
    assert diagnostics["failed_rules"] == ["no_matched_items"]


def test_review_reference_fidelity_flags_pasted_integration_drift(tmp_path):
    from application.render import scale_validation_support as support

    staged_path = tmp_path / "staged.png"
    ref_path = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), "white").save(staged_path)
    Image.new("RGB", (64, 64), "white").save(ref_path)

    def fake_call_gemini_with_failover(model_name, content, *args, **kwargs):
        return SimpleNamespace(
            text=json.dumps(
                {
                    "same_object": True,
                    "shape_match": True,
                    "material_match": True,
                    "integration_match": False,
                    "grounded_contact": False,
                    "halo_cutout_free": False,
                    "blending_natural": False,
                    "reason": "The chair looks cut out and pasted onto the floor.",
                }
            )
        )

    issues = support._review_reference_fidelity(
        str(staged_path),
        {
            "label": "Chair",
            "target_key": "chair_01",
            "source_index": 0,
            "crop_path": str(ref_path),
            "category": "chair",
            "dims_mm": {"width_mm": 700, "depth_mm": 700, "height_mm": 800},
            "identity_profile": {
                "family": "chair",
                "distinctive_parts": ["rolled back"],
                "material_cues": ["linen"],
            },
            "product_identity": {"preserve_rules": ["keep curved arm profile"]},
            "placement_contract": {"placement_family": "floor_placed", "zone": "adjacent_seating_band"},
        },
        (0.10, 0.15, 0.45, 0.80),
        call_gemini_with_failover=fake_call_gemini_with_failover,
        analysis_model_name="gemini",
        safe_json_from_model_text=lambda text: json.loads(text),
    )

    assert issues == ["reference_integration_drift:chair_01"]


def test_validate_furnished_scale_records_reference_integration_drift(monkeypatch, tmp_path):
    from application.render import scale_validation_support as support

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass

    staged_path = tmp_path / "staged.png"
    ref_crop_path = tmp_path / "chair_ref.png"
    Image.new("RGB", (320, 200), color=(255, 255, 255)).save(staged_path, format="PNG")
    Image.new("RGB", (64, 64), color=(32, 32, 32)).save(ref_crop_path, format="PNG")

    def fake_detect_item_bbox_norm(staged_path_value, ref_item_crop_path, item_label, **kwargs):
        if item_label == "Sofa":
            return (0.10, 0.48, 0.54, 0.84)
        if item_label == "Chair":
            return (0.58, 0.60, 0.74, 0.84)
        return None

    def fake_call_gemini_with_failover(model_name, content, *args, **kwargs):
        prompt = content[0]
        if "REFERENCE FURNITURE FIDELITY REVIEW" in prompt:
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "same_object": True,
                        "shape_match": True,
                        "material_match": True,
                        "integration_match": False,
                        "grounded_contact": False,
                        "halo_cutout_free": False,
                        "blending_natural": False,
                        "reason": "Object looks composited into the room.",
                    }
                )
            )
        return SimpleNamespace(text="{}")

    monkeypatch.setattr(support, "detect_item_bbox_norm", fake_detect_item_bbox_norm)

    ok, issues, diagnostics = support.validate_furnished_scale(
        str(staged_path),
        {
            "primary": {"label": "Sofa", "target_key": "sofa_anchor", "source_index": 0},
            "items": [
                {
                    "label": "Sofa",
                    "category": "sofa",
                    "target_key": "sofa_anchor",
                    "source_index": 0,
                    "crop_path": str(ref_crop_path),
                    "dims_mm": {"width_mm": 2200, "depth_mm": 900, "height_mm": 800},
                },
                {
                    "label": "Chair",
                    "category": "chair",
                    "target_key": "chair_01",
                    "source_index": 1,
                    "crop_path": str(ref_crop_path),
                    "dims_mm": {"width_mm": 750, "depth_mm": 700, "height_mm": 820},
                    "identity_profile": {
                        "family": "chair",
                        "distinctive_parts": ["barrel back"],
                        "material_cues": ["boucle"],
                    },
                    "product_identity": {"preserve_rules": ["keep thick curved backrest"]},
                    "archetype_strategy": {"strictness": "standard", "structural_archetype": "support_geometry_sensitive_object"},
                    "placement_contract": {"placement_family": "floor_placed", "zone": "adjacent_seating_band"},
                },
            ],
        },
        {"width_mm": 5000, "depth_mm": 5000, "height_mm": 3000},
        {"y_top": 0.12, "y_bottom": 0.88},
        primary_label="Sofa",
        include_diagnostics=True,
        call_gemini_with_failover=fake_call_gemini_with_failover,
        analysis_model_name="test-model",
        safe_json_from_model_text=lambda text: json.loads(text),
        log_brief=True,
        logger=DummyLogger(),
    )

    assert ok is False
    assert "reference_integration_drift:chair_01" in issues
    assert "reference_integration_drift" in diagnostics["failed_rules"]
    assert any(str((row or {}).get("rule_kind") or "") == "reference_integration_drift" for row in diagnostics["issue_records"])


def test_furnished_generation_review_summary_buckets_integration_and_reflection_as_fidelity():
    from application.render import furnished_generation_stage as furnished_stage

    bucket_counts = furnished_stage._review_bucket_counts(
        [
            "reference_integration_drift",
            "mirror_reflection_drift",
            "floor_item_floating",
            "primary_width_vs_room_width",
        ]
    )
    assert bucket_counts == {
        "fidelity_fail_count": 2,
        "placement_fail_count": 1,
        "geometry_fail_count": 1,
    }

    summary = furnished_stage._summarize_scale_review(
        {
            "failed_rules": ["reference_integration_drift", "mirror_reflection_drift", "floor_item_floating"],
            "matched_items": {"chair_01": {"target_key": "chair_01"}},
            "unmatched_items": [],
            "issue_records": [
                {"rule_kind": "reference_integration_drift", "weighted_score": 1.0},
                {"rule_kind": "reflection_violation", "weighted_score": 1.0},
                {"rule_kind": "placement_violation", "weighted_score": 0.5},
            ],
        }
    )

    assert summary["fidelity_fail_count"] == 2
    assert summary["placement_fail_count"] == 1
    assert summary["geometry_fail_count"] == 0
