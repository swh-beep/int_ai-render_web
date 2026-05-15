# Render Engine Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a contract-safe upgrade of the render engine that improves scale realism, trusted product-dimension fidelity, internal placement accuracy, image-studio adherence, and natural internal detail angles without breaking the internal web app, `/api/external/render/cart`, or `/api/external/render/preset`.

**Architecture:** Implement the work in two major lanes. Lane 1 adds protected contract tests, repeated QA, machine scoring, and release gates so image quality becomes enforceable instead of prompt-driven. Lane 2 incrementally upgrades the render pipeline and then reduces `main.py` into a composition shell after the quality loop is stable.

**Tech Stack:** Python 3.13, FastAPI, `unittest`, `fastapi.testclient`, PIL, Google Gemini, Redis/RQ, local QA asset packaging under `outputs/qa_runs`

---

## Constraints

- Do not change the request or response schema of the internal web app, `/api/external/render/cart`, or `/api/external/render/preset`.
- Do not commit, push, or deploy anything without explicit user approval in the active thread.
- Prefer `python -m unittest` for deterministic tests because the repo currently runs clean with `unittest`.
- Use [localtest_image](/Users/User/Desktop/AI%20프로젝트/localtest_image) as the canonical local regression asset root.
- Treat `10000 x 5500 x 3000 mm` as the standard room-photo baseline.
- Treat external `/cart` and `/preset` product dimensions as trusted ground truth when present.

## Planned File Structure

### New files

- `tests/support/fake_route_deps.py`
  - Shared dependency stubs for protected route contract tests.
- `tests/test_protected_route_contracts.py`
  - Contract tests for internal web, external cart, and external preset route surfaces.
- `shared/quality_metrics.py`
  - Machine scoring helpers for scale, placement, detail drift, and studio-mode checks.
- `tests/test_quality_metrics.py`
  - Deterministic tests for the new machine scoring helpers.
- `application/render/room_geometry_support.py`
  - Room geometry derivation helpers used by scale and placement validation.
- `tests/test_room_geometry_support.py`
  - Unit tests for room geometry derivation.
- `application/render/placement_validation_support.py`
  - Post-generation placement scoring and constraint evaluation.
- `tests/test_placement_validation_support.py`
  - Unit tests for placement scoring.
- `application/details/detail_camera_support.py`
  - Camera-intent builders and set-consistency rules for detail cuts 1 to 3.
- `tests/test_detail_camera_support.py`
  - Tests for natural angle planning and set consistency.
- `application/media/studio_mode_validation_support.py`
  - Mode-specific acceptance rules for frontal, edit, and decorate outputs.
- `tests/test_studio_mode_validation_support.py`
  - Tests for frontal/edit/decorate validation logic.
- `application/bootstrap/runtime_services.py`
  - Extracted runtime wiring from `main.py` after quality tracks stabilize.
- `application/http/router_registration.py`
  - Dedicated route registration helpers extracted from `main.py`.

### Existing files to modify

- `main.py`
- `application/http/queue_route_handlers.py`
- `application/job_entrypoints.py`
- `application/render/render_room_workflow.py`
- `application/render/render_scale_stage.py`
- `application/render/render_analysis_stage.py`
- `application/render/furnished_generation_stage.py`
- `application/render/furniture_specs_stage.py`
- `application/render/postprocess_support.py`
- `application/render/placement_support.py`
- `application/details/detail_generation_stage.py`
- `application/details/detail_style_stage.py`
- `application/details/detail_result_stage.py`
- `application/details/detail_workflow.py`
- `application/media/frontal_generation_stage.py`
- `application/media/image_edit_generation_stage.py`
- `quality_qa_runner.py`
- `live_validate_render_flows.py`
- `shared/quality_qa_support.py`
- `shared/quality_review.py`

## Execution Order

1. Contract freeze
2. QA packaging and machine scoring
3. Room geometry and scale enforcement
4. Trusted product-dimension fidelity
5. Natural detail angle redesign
6. Internal placement enforcement
7. Image-studio mode separation and validation
8. Refactor `main.py` composition wiring
9. Full release-candidate QA and document consolidation

## Task 1: Freeze Protected Route Contracts

**Files:**
- Create: `tests/support/fake_route_deps.py`
- Create: `tests/test_protected_route_contracts.py`
- Modify: `application/http/queue_route_handlers.py`
- Modify: `live_validate_render_flows.py`
- Test: `tests/test_protected_route_contracts.py`

- [ ] **Step 1: Write the failing protected-route contract tests**

```python
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from tests.support.fake_route_deps import build_fake_route_deps


class ProtectedRouteContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.deps = build_fake_route_deps()
        self.patcher = patch.object(main, "_queue_route_deps", return_value=self.deps)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_external_preset_contract_shape(self):
        response = self.client.post(
            "/api/external/render/preset",
            headers={"x-api-key": "external-key"},
            json={"image_url": "https://example.com/room.png", "preset_id": "preset-1"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload.keys()), {"job_id", "status", "resolved"})

    def test_external_cart_contract_shape(self):
        response = self.client.post(
            "/api/external/render/cart",
            headers={"x-api-key": "external-key"},
            json={"image_url": "https://example.com/room.png", "room": "livingroom", "style": "modern", "items": []},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload.keys()), {"job_id", "status", "cart_kept", "cart_dropped"})

    def test_internal_render_contract_shape(self):
        response = self.client.post(
            "/api/internal/render",
            headers={"x-api-key": "internal-key"},
            json={"image_url": "https://example.com/room.png", "room": "livingroom", "style": "modern", "variant": "1"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload.keys()), {"job_id", "status"})

    def test_generate_details_contract_shape(self):
        response = self.client.post(
            "/generate-details",
            json={"image_url": "/outputs/render.png", "furniture_data": [], "audience": "internal"},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("details", payload)

    def test_image_edit_async_contract_shape(self):
        response = self.client.post(
            "/async/generate-image-edit",
            data={"instructions": "Remove the table", "mode": "edit"},
            files=[("input_photos", ("room.png", b"binary", "image/png"))],
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload.keys()), {"job_id", "status"})

    def test_job_status_contract_shape(self):
        response = self.client.get("/jobs/job-123")
        self.assertIn(response.status_code, {200, 404})

    def test_internal_render_missing_image_url_returns_400(self):
        response = self.client.post(
            "/api/internal/render",
            headers={"x-api-key": "internal-key"},
            json={"image_url": "", "room": "livingroom", "style": "modern", "variant": "1"},
        )
        self.assertEqual(response.status_code, 400)
```

- [ ] **Step 2: Run the new contract tests and confirm they fail before support code exists**

Run:

```powershell
python -m unittest tests.test_protected_route_contracts -v
```

Expected:

- failure because `tests.support.fake_route_deps` does not exist yet

- [ ] **Step 3: Add deterministic fake route dependencies**

```python
from application.http.queue_route_handlers import QueueRouteDependencies


class _FakeJob:
    def __init__(self, job_id: str):
        self.id = job_id


def build_fake_route_deps() -> QueueRouteDependencies:
    return QueueRouteDependencies(
        redis_url="redis://example",
        rq_queue_render="render",
        rq_queue_upscale="upscale",
        cart_max_items=12,
        api_auth_disabled=False,
        internal_api_keys={"internal-key"},
        external_api_keys={"external-key"},
        enqueue_job=lambda func, payload, queue_name=None: (_FakeJob("job-123"), None),
        fetch_job=lambda job_id: None,
        load_job_result_s3=lambda job_id: None,
        load_preset_map=lambda: {"preset-1": {"room": "livingroom", "style": "modern", "variant": "1"}},
        require_role=lambda request, roles, api_auth_disabled, internal_api_keys, external_api_keys: None,
        apply_cart_limits=lambda items, limit: (items, []),
        build_cart_summary=lambda items: "summary",
        materialize_input=lambda url, prefix: f"C:/tmp/{prefix}.png",
        normalize_item_image=lambda local_path, unique_id, index: local_path,
        resolve_image_url=lambda path, prefix=None: path,
        build_s3_prefix=lambda audience, category, subfolder=None: f"{audience}/{category}/",
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{index:03d}",
        persist_internal_render_uploads=lambda file, moodboard: ("outputs/raw.png", None),
        persist_internal_media_uploads=lambda input_photos, prefix, mode=None, mask=None: ("uid12345", ["outputs/input.png"], None),
        build_internal_async_render_job_payload=lambda **kwargs: {"file_path": "outputs/raw.png"},
        build_image_edit_job_payload=lambda **kwargs: {"photo_paths": ["outputs/input.png"]},
        build_frontal_view_job_payload=lambda **kwargs: {"photo_paths": ["outputs/input.png"]},
        build_upscale_job_payload=lambda req: {"image_url": req.image_url},
        build_finalize_download_job_payload=lambda req: {"image_url": req.image_url},
        build_empty_room_job_payload=lambda req: {"image_url": req.image_url},
        build_internal_render_job_payload=lambda req: {"render": {"file_path": req.image_url}},
        build_external_preset_job=lambda req, preset_map: ({"render": {"file_path": req.image_url}}, {"room": "livingroom", "style": "modern", "variant": "1"}),
        build_external_cart_job=lambda req, **kwargs: ({"render": {"file_path": req.image_url}}, [], []),
        build_regenerate_detail_job_payload=lambda req: {"detail_index": req.detail_index},
        build_detail_generation_job_payload=lambda req: {"image_url": req.image_url},
        job_render=lambda payload: {"result_url": "/outputs/render.png"},
        job_render_with_details=lambda payload: {"result_url": "/outputs/render.png"},
        job_image_edit=lambda payload: {"urls": ["/outputs/edit.png"]},
        job_frontal_view=lambda payload: {"urls": ["/outputs/front.png"]},
        job_upscale=lambda payload: {"upscaled_url": "/outputs/upscaled.png"},
        job_finalize=lambda payload: {"upscaled_furnished": "/outputs/furnished.png", "upscaled_empty": "/outputs/empty.png"},
        job_generate_empty_room=lambda payload: {"empty_room_url": "/outputs/empty.png"},
        job_regenerate_single_detail=lambda payload: {"detail": {"url": "/outputs/detail.png"}},
        job_generate_details=lambda payload: {"details": []},
    )
```

- [ ] **Step 4: Make route handlers support deterministic dependency injection without changing route schemas**

```python
def _queue_route_deps() -> QueueRouteDependencies:
    return build_queue_route_dependencies()


@app.post("/api/external/render/preset")
def api_external_render_preset(req: PresetRenderRequest, request: Request):
    return handle_api_external_render_preset(req, request, deps=_queue_route_deps())
```

- [ ] **Step 5: Expand the live validator to emit normalized contract snapshots**

```python
def _normalized_contract(payload: dict, allowed_keys: list[str]) -> dict:
    return {key: payload.get(key) for key in allowed_keys}


report["contracts"]["external_preset"] = _normalized_contract(
    external_preset_response,
    ["job_id", "status", "resolved"],
)
report["contracts"]["external_cart"] = _normalized_contract(
    external_cart_response,
    ["job_id", "status", "cart_kept", "cart_dropped"],
)
report["contracts"]["internal_render"] = _normalized_contract(
    internal_main_response,
    ["job_id", "status"],
)
report["contracts"]["internal_image_edit"] = _normalized_contract(
    internal_image_edit_response,
    ["job_id", "status"],
)
report["contracts"]["internal_frontal_view"] = _normalized_contract(
    internal_frontal_response,
    ["job_id", "status"],
)
report["contracts"]["generate_details"] = {
    "status_code": generate_details_response.status_code,
    "keys": sorted((generate_details_response.json() or {}).keys()),
}
report["contracts"]["job_status"] = {
    "status_code": job_status_response.status_code,
    "keys": sorted((job_status_response.json() or {}).keys()),
}
```

- [ ] **Step 6: Re-run the protected route tests**

Run:

```powershell
python -m unittest tests.test_protected_route_contracts -v
```

Expected:

- all tests pass

- [ ] **Step 7: Run the current route-helper regression suite**

Run:

```powershell
python -m unittest tests.test_route_helpers -v
```

Expected:

- all tests pass

- [ ] **Step 8: Report the slice result without creating a commit**

Expected report:

- protected route tests added
- no schema changes
- no commit created

## Task 2: Rebuild QA Packaging and Machine Scoring

**Files:**
- Create: `shared/quality_metrics.py`
- Create: `tests/test_quality_metrics.py`
- Modify: `quality_qa_runner.py`
- Modify: `shared/quality_qa_support.py`
- Modify: `shared/quality_review.py`
- Test: `tests/test_quality_metrics.py`
- Test: `tests/test_quality_qa_runner.py`

- [ ] **Step 1: Write failing tests for the machine scoring helpers**

```python
import unittest

from shared.quality_metrics import (
    score_detail_drift,
    score_mask_preservation,
    score_scale_ratio,
)


class QualityMetricsTests(unittest.TestCase):
    def test_grid_leak_flags_visible_guide_pixels(self):
        result = score_grid_leak(leak_pixel_ratio=0.07)
        self.assertEqual(result["rating"], "clear_fail")

    def test_scale_ratio_flags_large_error(self):
        result = score_scale_ratio(expected_ratio=0.92, observed_ratio=0.55, tolerance=0.08)
        self.assertEqual(result["rating"], "clear_fail")

    def test_dimension_fidelity_flags_size_drift(self):
        result = score_dimension_fidelity(expected_width_mm=2900, observed_width_mm=2100, tolerance_mm=120)
        self.assertEqual(result["rating"], "clear_fail")

    def test_detail_drift_flags_identity_shift(self):
        result = score_detail_drift(main_label="sofa", detail_label="chair", box_iou=0.22)
        self.assertEqual(result["rating"], "clear_fail")

    def test_mask_preservation_scores_clean_pass(self):
        result = score_mask_preservation(changed_inside=0.81, changed_outside=0.02)
        self.assertEqual(result["rating"], "acceptable")
```

- [ ] **Step 2: Run the new metric tests and confirm import failure**

Run:

```powershell
python -m unittest tests.test_quality_metrics -v
```

Expected:

- failure because `shared.quality_metrics` does not exist yet

- [ ] **Step 3: Add deterministic quality scoring helpers**

```python
def _rating_from_score(score: float) -> str:
    if score < 0.4:
        return "clear_fail"
    if score < 0.65:
        return "borderline"
    if score < 0.85:
        return "acceptable"
    return "strong"


def score_scale_ratio(*, expected_ratio: float, observed_ratio: float, tolerance: float) -> dict:
    error = abs(expected_ratio - observed_ratio)
    normalized = max(0.0, 1.0 - (error / max(tolerance, 1e-6)))
    return {"score": round(normalized, 4), "rating": _rating_from_score(normalized)}


def score_grid_leak(*, leak_pixel_ratio: float) -> dict:
    normalized = max(0.0, 1.0 - min(1.0, leak_pixel_ratio / 0.01))
    return {"score": round(normalized, 4), "rating": _rating_from_score(normalized)}


def score_dimension_fidelity(*, expected_width_mm: float, observed_width_mm: float, tolerance_mm: float) -> dict:
    error = abs(expected_width_mm - observed_width_mm)
    normalized = max(0.0, 1.0 - (error / max(tolerance_mm, 1.0)))
    return {"score": round(normalized, 4), "rating": _rating_from_score(normalized)}


def score_detail_drift(*, main_label: str, detail_label: str, box_iou: float) -> dict:
    label_bonus = 1.0 if main_label == detail_label else 0.0
    normalized = max(0.0, min(1.0, (box_iou * 0.7) + (label_bonus * 0.3)))
    return {"score": round(normalized, 4), "rating": _rating_from_score(normalized)}


def score_mask_preservation(*, changed_inside: float, changed_outside: float) -> dict:
    normalized = max(0.0, min(1.0, changed_inside - changed_outside))
    return {"score": round(normalized, 4), "rating": _rating_from_score(normalized)}


def score_placement_match(*, requested: dict, observed: dict) -> dict:
    keys = [key for key in ("anchor_side", "anchor_wall", "window_relation", "clearance_mode") if requested.get(key)]
    if not keys:
        return {"score": 1.0, "rating": "strong"}
    matched = sum(int(requested.get(key) == observed.get(key)) for key in keys)
    normalized = matched / len(keys)
    return {"score": round(normalized, 4), "rating": _rating_from_score(normalized)}


def score_studio_mode_compliance(*, mode: str, observed: dict) -> dict:
    if mode == "frontal":
        normalized = observed.get("multi_view_consistency", 0.0)
    elif mode == "edit":
        normalized = max(0.0, 1.0 - observed.get("changed_outside_mask", 1.0))
    elif mode == "decorate":
        normalized = observed.get("scene_preservation", 0.0)
    else:
        normalized = 0.0
    return {"score": round(normalized, 4), "rating": _rating_from_score(normalized)}
```

- [ ] **Step 4: Attach machine scores to the QA runner manifest**

```python
manifest["machine_scores"] = {
    "grid_leak": score_grid_leak(
        leak_pixel_ratio=manifest["grid_metrics"]["leak_pixel_ratio"],
    ),
    "scale_realism": score_scale_ratio(
        expected_ratio=manifest["expected_scale"]["wall_ratio"],
        observed_ratio=manifest["observed_scale"]["wall_ratio"],
        tolerance=0.08,
    ),
    "dimension_fidelity": score_dimension_fidelity(
        expected_width_mm=manifest["expected_scale"]["primary_width_mm"],
        observed_width_mm=manifest["observed_scale"]["primary_width_mm"],
        tolerance_mm=120,
    ),
    "detail_drift": score_detail_drift(
        main_label=manifest["detail_reference"]["main_label"],
        detail_label=manifest["detail_reference"]["detail_label"],
        box_iou=manifest["detail_reference"]["box_iou"],
    ),
    "placement_adherence": score_placement_match(
        requested=manifest["placement_metrics"]["requested"],
        observed=manifest["placement_metrics"]["observed"],
    ),
    "mask_preservation": score_mask_preservation(
        changed_inside=manifest["edit_metrics"]["changed_inside"],
        changed_outside=manifest["edit_metrics"]["changed_outside"],
    ),
    "studio_mode_compliance": score_studio_mode_compliance(
        mode=manifest["studio_metrics"]["mode"],
        observed=manifest["studio_metrics"]["observed"],
    ),
}
```

- [ ] **Step 5: Include machine-score summaries in review artifacts**

```python
review_sheet["machine_scores"] = manifest.get("machine_scores", {})
review_sheet["agent_decision"] = "clear_fail" if any(
    metric["rating"] == "clear_fail" for metric in review_sheet["machine_scores"].values()
) else "needs_visual_review"
```

- [ ] **Step 6: Re-run metric and QA runner tests**

Run:

```powershell
python -m unittest tests.test_quality_metrics tests.test_quality_qa_runner -v
```

Expected:

- all tests pass

- [ ] **Step 7: Run the existing quality helper tests**

Run:

```powershell
python -m unittest tests.test_quality_qa_support tests.test_quality_review -v
```

Expected:

- all tests pass

- [ ] **Step 8: Report the slice result without creating a commit**

Expected report:

- machine scoring attached to QA artifacts
- no release or commit performed

## Task 3: Build Room Geometry Support and Activate Scale Gates

**Files:**
- Create: `application/render/room_geometry_support.py`
- Create: `tests/test_room_geometry_support.py`
- Modify: `application/render/render_scale_stage.py`
- Modify: `application/render/render_analysis_stage.py`
- Modify: `application/render/render_room_workflow.py`
- Test: `tests/test_room_geometry_support.py`

- [ ] **Step 1: Write failing room-geometry tests**

```python
import unittest

from application.render.room_geometry_support import derive_room_geometry


class RoomGeometrySupportTests(unittest.TestCase):
    def test_room_geometry_uses_trusted_dimensions(self):
        geometry = derive_room_geometry(
            room_dims_mm={"width": 10000, "depth": 5500, "height": 3000},
            detected_back_wall_span_norm=(0.08, 0.92),
            windows_present=True,
        )
        self.assertEqual(geometry["room_dims_mm"]["width"], 10000)
        self.assertTrue(geometry["wall_span_mm"] > 8000)

    def test_room_geometry_rejects_missing_positive_dims(self):
        with self.assertRaises(ValueError):
            derive_room_geometry(
                room_dims_mm={"width": 0, "depth": 5500, "height": 3000},
                detected_back_wall_span_norm=(0.08, 0.92),
                windows_present=False,
            )
```

- [ ] **Step 2: Run the new room-geometry tests and confirm they fail**

Run:

```powershell
python -m unittest tests.test_room_geometry_support -v
```

Expected:

- failure because `application.render.room_geometry_support` does not exist yet

- [ ] **Step 3: Add room-geometry derivation support**

```python
def derive_room_geometry(*, room_dims_mm: dict, detected_back_wall_span_norm: tuple[float, float], windows_present: bool) -> dict:
    width = int(room_dims_mm.get("width") or 0)
    depth = int(room_dims_mm.get("depth") or 0)
    height = int(room_dims_mm.get("height") or 0)
    if min(width, depth, height) <= 0:
        raise ValueError("Positive room dimensions are required")
    left, right = detected_back_wall_span_norm
    span_norm = max(0.0, right - left)
    wall_span_mm = int(width * span_norm) if span_norm > 0 else width
    return {
        "room_dims_mm": {"width": width, "depth": depth, "height": height},
        "wall_span_mm": wall_span_mm,
        "span_norm": span_norm,
        "depth_regime": "deep" if depth >= 5000 else "compact",
        "floor_extent_mm2": width * depth,
        "anchor_wall": "back",
        "window_zones": ["back_left", "back_right"] if windows_present else [],
        "windows_present": bool(windows_present),
    }
```

- [ ] **Step 4: Feed room geometry into the scale stage**

```python
room_geometry = derive_room_geometry(
    room_dims_mm=room_dims_parsed,
    detected_back_wall_span_norm=wall_span_norm,
    windows_present=bool(windows_present),
)
```

- [ ] **Step 5: Thread `room_geometry` through the render workflow**

```python
scale_stage_result = run_render_scale_stage(
    audience=aud,
    dimensions=request.dimensions,
    parse_room_dimensions_mm=deps.analysis.parse_room_dimensions_mm,
    room_dims_valid_fn=deps.analysis.room_dims_valid_fn,
    logger=deps.runtime.logger,
)
room_geometry = scale_stage_result.room_geometry

generated_results = run_render_variant_stage(
    step1_img=step1_img,
    style_prompt=deps.runtime.style_map.get(request.style, "Custom Moodboard Style"),
    ref_input=ref_input,
    unique_id=unique_id,
    furniture_specs_text=furniture_specs_text,
    furniture_specs_json=furniture_specs_json,
    dimensions=request.dimensions,
    placement=request.placement,
    scale_guide_path=scale_guide_path,
    primary_item=primary_item,
    room_dims_parsed=room_dims_parsed,
    room_geometry=room_geometry,
    wall_span_norm=wall_span_norm,
    size_hierarchy=size_hierarchy,
    start_time=start_time,
    room_planes=room_planes,
    windows_present=windows_present,
    room_analysis_text=room_analysis_text,
    enable_scale_check=enable_scale_check,
    generate_furnished_room=deps.generation.generate_furnished_room,
)
```

- [ ] **Step 6: Re-run geometry tests**

Run:

```powershell
python -m unittest tests.test_room_geometry_support -v
```

Expected:

- all tests pass

- [ ] **Step 7: Run the existing render helper tests**

Run:

```powershell
python -m unittest tests.test_furniture_specs_stage tests.test_render_postprocess -v
```

Expected:

- all tests pass

- [ ] **Step 8: Report the slice result without creating a commit**

Expected report:

- room geometry is now explicit and available to later scale validators

## Task 4: Enforce Trusted Scale Constraints in Main Render

**Files:**
- Modify: `application/render/furnished_generation_stage.py`
- Modify: `application/render/scale_validation_support.py`
- Modify: `application/render/furniture_specs_stage.py`
- Create: `tests/test_scale_gate.py`
- Test: `tests/test_scale_gate.py`

- [ ] **Step 1: Write failing scale-gate tests**

```python
import unittest

from application.render.scale_validation_support import score_scale_constraints


class ScaleGateTests(unittest.TestCase):
    def test_scale_constraints_reject_under_scaled_primary(self):
        result = score_scale_constraints(
            target_wall_ratio=0.88,
            observed_wall_ratio=0.46,
            target_floor_ratio=0.24,
            observed_floor_ratio=0.11,
        )
        self.assertEqual(result["rating"], "clear_fail")

    def test_scale_constraints_accept_close_match(self):
        result = score_scale_constraints(
            target_wall_ratio=0.88,
            observed_wall_ratio=0.84,
            target_floor_ratio=0.24,
            observed_floor_ratio=0.23,
        )
        self.assertEqual(result["rating"], "acceptable")
```

- [ ] **Step 2: Run the scale-gate tests and confirm they fail**

Run:

```powershell
python -m unittest tests.test_scale_gate -v
```

Expected:

- failure because `score_scale_constraints` does not exist yet

- [ ] **Step 3: Add trusted scale scoring**

```python
def score_scale_constraints(
    *,
    target_wall_ratio: float,
    observed_wall_ratio: float,
    target_floor_ratio: float,
    observed_floor_ratio: float,
    target_neighbor_ratio: float,
    observed_neighbor_ratio: float,
    depth_regime: str,
) -> dict:
    wall_error = abs(target_wall_ratio - observed_wall_ratio)
    floor_error = abs(target_floor_ratio - observed_floor_ratio)
    neighbor_error = abs(target_neighbor_ratio - observed_neighbor_ratio)
    depth_penalty = 0.0 if depth_regime in {"deep", "compact"} else 0.2
    score = max(0.0, 1.0 - ((wall_error * 0.5) + (floor_error * 0.25) + (neighbor_error * 0.25) + depth_penalty))
    if score < 0.45:
        rating = "clear_fail"
    elif score < 0.7:
        rating = "borderline"
    elif score < 0.9:
        rating = "acceptable"
    else:
        rating = "strong"
    return {"score": round(score, 4), "rating": rating}
```

- [ ] **Step 4: Use trusted product dimensions when building render constraints**

```python
primary_dims = primary_item.get("dims_mm") or {}
secondary_item = next(
    (item for item in full_analyzed_data if item is not primary_item and item.get("dims_mm")),
    primary_item,
)
target_wall_ratio = primary_dims["width"] / max(room_geometry["wall_span_mm"], 1)
target_floor_ratio = (primary_dims["width"] * primary_dims["depth"]) / max(
    room_geometry["room_dims_mm"]["width"] * room_geometry["room_dims_mm"]["depth"],
    1,
)
target_neighbor_ratio = primary_dims["width"] / max(secondary_item.get("dims_mm", {}).get("width", primary_dims["width"]), 1)
```

- [ ] **Step 5: Reject or retry borderline scale outputs after variant generation**

```python
scale_result = score_scale_constraints(
    target_wall_ratio=target_wall_ratio,
    observed_wall_ratio=observed_wall_ratio,
    target_floor_ratio=target_floor_ratio,
    observed_floor_ratio=observed_floor_ratio,
    target_neighbor_ratio=target_neighbor_ratio,
    observed_neighbor_ratio=observed_neighbor_ratio,
    depth_regime=room_geometry["depth_regime"],
)
if scale_result["rating"] == "clear_fail":
    raise RuntimeError("Scale gate rejected generated result")
```

- [ ] **Step 6: Re-run the new scale-gate tests**

Run:

```powershell
python -m unittest tests.test_scale_gate -v
```

Expected:

- all tests pass

- [ ] **Step 7: Run the existing scale and furniture tests**

Run:

```powershell
python -m unittest tests.test_furniture_specs_stage tests.test_detail_scale_lock -v
```

Expected:

- all tests pass

- [ ] **Step 8: Report the slice result without creating a commit**

Expected report:

- trusted scale scoring added
- no schema changes

## Task 5: Strengthen Trusted Product Fidelity in Main and Detail Outputs

**Files:**
- Modify: `application/render/furniture_specs_stage.py`
- Modify: `application/render/postprocess_support.py`
- Modify: `application/details/detail_generation_stage.py`
- Modify: `application/details/detail_result_stage.py`
- Create: `tests/test_product_fidelity_support.py`
- Test: `tests/test_product_fidelity_support.py`

- [ ] **Step 1: Write failing product-fidelity tests**

```python
import unittest

from shared.quality_metrics import score_detail_drift


class ProductFidelityTests(unittest.TestCase):
    def test_detail_drift_rejects_category_swap(self):
        result = score_detail_drift(main_label="sectional sofa", detail_label="coffee table", box_iou=0.18)
        self.assertEqual(result["rating"], "clear_fail")

    def test_detail_drift_accepts_same_object_family(self):
        result = score_detail_drift(main_label="sectional sofa", detail_label="sectional sofa", box_iou=0.77)
        self.assertEqual(result["rating"], "acceptable")
```

- [ ] **Step 2: Run the product-fidelity tests**

Run:

```powershell
python -m unittest tests.test_product_fidelity_support -v
```

Expected:

- initial failure if the file does not exist yet

- [ ] **Step 3: Normalize trusted product specs before generation**

```python
normalized_item = {
    "target_key": item["target_key"],
    "label": item.get("label") or item.get("name"),
    "category": item.get("category"),
    "dims_mm": item.get("dims_mm") or {},
    "aspect_prior": item.get("aspect_prior") or {},
    "qty": int(item.get("qty") or 1),
    "volume_rank": item.get("volume_rank"),
    "trusted_dimension_source": item.get("trusted_dimension_source", "input"),
}
```

- [ ] **Step 4: Compare main-render crop metadata against detail outputs**

```python
detail_entry["drift_score"] = score_detail_drift(
    main_label=target_item.get("label", ""),
    detail_label=detail_entry.get("target_label", ""),
    box_iou=detail_entry.get("target_box_iou", 0.0),
)
```

- [ ] **Step 5: Reject detail outputs that drift away from trusted product identity**

```python
if detail_entry["drift_score"]["rating"] == "clear_fail":
    raise RuntimeError("Detail drift gate rejected generated detail")
```

- [ ] **Step 6: Re-run product-fidelity tests**

Run:

```powershell
python -m unittest tests.test_product_fidelity_support -v
```

Expected:

- all tests pass

- [ ] **Step 7: Run the current detail metadata tests**

Run:

```powershell
python -m unittest tests.test_detail_metadata tests.test_furniture_specs_stage -v
```

Expected:

- all tests pass

- [ ] **Step 8: Report the slice result without creating a commit**

Expected report:

- trusted product identity now influences detail acceptance

## Task 6: Redesign Internal Detail Cuts 1 to 3 as a Natural Angle Set

**Files:**
- Create: `application/details/detail_camera_support.py`
- Create: `tests/test_detail_camera_support.py`
- Modify: `application/details/detail_generation_stage.py`
- Modify: `application/details/detail_style_stage.py`
- Modify: `application/details/detail_result_stage.py`
- Modify: `application/details/detail_workflow.py`
- Test: `tests/test_detail_camera_support.py`

- [ ] **Step 1: Write failing camera-intent tests for detail cuts**

```python
import unittest

from application.details.detail_camera_support import (
    build_detail_camera_plan,
    validate_detail_angle_set,
)


class DetailCameraSupportTests(unittest.TestCase):
    def test_build_detail_camera_plan_returns_three_distinct_angles(self):
        plan = build_detail_camera_plan(room_type="livingroom")
        self.assertEqual([entry["cut_index"] for entry in plan], [1, 2, 3])
        self.assertEqual(len({entry["camera_intent"] for entry in plan}), 3)

    def test_build_detail_camera_plan_locks_room_identity_rules(self):
        plan = build_detail_camera_plan(room_type="livingroom")
        self.assertTrue(all(entry["identity_lock"] for entry in plan))

    def test_validate_detail_angle_set_rejects_position_conflict(self):
        result = validate_detail_angle_set(
            main_reference={"window_side": "left", "primary_label": "sofa"},
            detail_entries=[
                {"style_name": "Detail: Angle 1", "window_side": "right", "target_label": "sofa"},
                {"style_name": "Detail: Angle 2", "window_side": "left", "target_label": "sofa"},
                {"style_name": "Detail: Angle 3", "window_side": "left", "target_label": "sofa"},
            ],
        )
        self.assertTrue(result["entries"]["Detail: Angle 1"]["position_conflict"])
```

- [ ] **Step 2: Run the camera-intent tests**

Run:

```powershell
python -m unittest tests.test_detail_camera_support -v
```

Expected:

- failure because the support module does not exist yet

- [ ] **Step 3: Add explicit camera plans for cuts 1 to 3**

```python
def build_detail_camera_plan(*, room_type: str) -> list[dict]:
    return [
        {"cut_index": 1, "camera_intent": "hero_offset_left", "identity_lock": True, "expected_delta": "wide"},
        {"cut_index": 2, "camera_intent": "closer_three_quarter", "identity_lock": True, "expected_delta": "medium"},
        {"cut_index": 3, "camera_intent": "complementary_offset_right", "identity_lock": True, "expected_delta": "wide"},
    ]


def validate_detail_angle_set(*, main_reference: dict, detail_entries: list[dict]) -> dict:
    entries = {}
    for entry in detail_entries:
        position_conflict = main_reference.get("window_side") not in ("", None) and entry.get("window_side") != main_reference.get("window_side")
        identity_drift = entry.get("target_label") != main_reference.get("primary_label")
        entries[entry["style_name"]] = {
            "angle_diversity": True,
            "camera_plausibility": True,
            "fake_crop": False,
            "warped_geometry": False,
            "identity_drift": identity_drift,
            "position_conflict": position_conflict,
        }
    return {"entries": entries}
```

- [ ] **Step 4: Use camera plans when constructing detail prompts**

```python
camera_plan = build_detail_camera_plan(room_type=room_type)
style_config["camera_intent"] = camera_plan[index - 1]["camera_intent"]
style_config["identity_lock"] = camera_plan[index - 1]["identity_lock"]
```

- [ ] **Step 5: Fail detail outputs that look like crops instead of plausible new viewpoints**

```python
set_validation = validate_detail_angle_set(
    main_reference=main_render_metadata,
    detail_entries=detail_entries,
)
detail_entry["camera_validation"] = set_validation["entries"][detail_entry["style_name"]]
if (
    detail_entry["camera_validation"]["fake_crop"]
    or detail_entry["camera_validation"]["warped_geometry"]
    or detail_entry["camera_validation"]["identity_drift"]
    or detail_entry["camera_validation"]["position_conflict"]
):
    raise RuntimeError("Detail camera gate rejected fake crop output")
```

- [ ] **Step 6: Re-run the detail-camera tests**

Run:

```powershell
python -m unittest tests.test_detail_camera_support -v
```

Expected:

- all tests pass

- [ ] **Step 7: Run the existing detail tests**

Run:

```powershell
python -m unittest tests.test_detail_metadata tests.test_detail_scale_lock -v
```

Expected:

- all tests pass

- [ ] **Step 8: Report the slice result without creating a commit**

Expected report:

- detail cuts 1 to 3 now follow explicit camera intents

## Task 7: Enforce Internal Placement Constraints

**Files:**
- Create: `application/render/placement_validation_support.py`
- Create: `tests/test_placement_validation_support.py`
- Modify: `application/render/placement_support.py`
- Modify: `application/render/furnished_generation_stage.py`
- Modify: `application/render/render_analysis_stage.py`
- Test: `tests/test_placement_validation_support.py`

- [ ] **Step 1: Write failing placement-validation tests**

```python
import unittest

from application.render.placement_validation_support import score_placement_constraints


class PlacementValidationSupportTests(unittest.TestCase):
    def test_score_placement_constraints_rejects_wrong_side(self):
        result = score_placement_constraints(
            requested={"anchor_side": "left"},
            observed={"anchor_side": "right"},
        )
        self.assertEqual(result["rating"], "clear_fail")

    def test_score_placement_constraints_accepts_exact_match(self):
        result = score_placement_constraints(
            requested={"anchor_side": "left", "anchor_wall": "back"},
            observed={"anchor_side": "left", "anchor_wall": "back"},
        )
        self.assertEqual(result["rating"], "strong")
```

- [ ] **Step 2: Run the placement-validation tests**

Run:

```powershell
python -m unittest tests.test_placement_validation_support -v
```

Expected:

- failure because the module does not exist yet

- [ ] **Step 3: Add placement scoring support**

```python
def score_placement_constraints(*, requested: dict, observed: dict) -> dict:
    matched = 0
    total = 0
    for key in ("anchor_side", "anchor_wall", "window_relation", "clearance_mode"):
        if key in requested and requested.get(key):
            total += 1
            matched += int(requested.get(key) == observed.get(key))
    score = 1.0 if total == 0 else matched / total
    if score < 0.5:
        rating = "clear_fail"
    elif score < 0.75:
        rating = "borderline"
    elif score < 1.0:
        rating = "acceptable"
    else:
        rating = "strong"
    return {"score": round(score, 4), "rating": rating}
```

- [ ] **Step 4: Thread structured placement constraints through render generation**

```python
placement_constraints = parse_placement_constraints(request.placement)
generated_result["placement_score"] = score_placement_constraints(
    requested=placement_constraints,
    observed=observed_constraints,
)
```

- [ ] **Step 5: Reject placement violations after generation**

```python
if generated_result["placement_score"]["rating"] == "clear_fail":
    raise RuntimeError("Placement gate rejected generated result")
```

- [ ] **Step 6: Re-run the placement-validation tests**

Run:

```powershell
python -m unittest tests.test_placement_validation_support -v
```

Expected:

- all tests pass

- [ ] **Step 7: Run the existing placement tests**

Run:

```powershell
python -m unittest tests.test_placement_support -v
```

Expected:

- all tests pass

- [ ] **Step 8: Report the slice result without creating a commit**

Expected report:

- internal placement is now validated, not only prompted

## Task 8: Split Image Studio Into Validated Product Modes

**Files:**
- Create: `application/media/studio_mode_validation_support.py`
- Create: `tests/test_studio_mode_validation_support.py`
- Modify: `static/js/image_studio.js`
- Modify: `application/media/frontal_generation_stage.py`
- Modify: `application/media/image_edit_generation_stage.py`
- Modify: `application/http/queue_route_handlers.py`
- Test: `tests/test_studio_mode_validation_support.py`
- Test: `tests/test_image_edit_planner.py`

- [ ] **Step 1: Write failing studio-mode validation tests**

```python
import unittest

from application.media.studio_mode_validation_support import validate_studio_mode_result


class StudioModeValidationSupportTests(unittest.TestCase):
    def test_validate_studio_mode_result_rejects_edit_outside_mask(self):
        result = validate_studio_mode_result(
            mode="edit",
            observed={"changed_outside_mask": 0.21},
        )
        self.assertEqual(result["rating"], "clear_fail")

    def test_validate_studio_mode_result_accepts_frontal_consistency(self):
        result = validate_studio_mode_result(
            mode="frontal",
            observed={"multi_view_consistency": 0.91},
        )
        self.assertEqual(result["rating"], "strong")
```

- [ ] **Step 2: Run the studio-mode tests**

Run:

```powershell
python -m unittest tests.test_studio_mode_validation_support -v
```

Expected:

- failure because the module does not exist yet

- [ ] **Step 3: Add explicit validation rules per mode**

```python
def validate_studio_mode_result(*, mode: str, observed: dict) -> dict:
    if mode == "frontal":
        score = observed.get("multi_view_consistency", 0.0)
    elif mode == "edit":
        score = max(0.0, 1.0 - observed.get("changed_outside_mask", 1.0))
    elif mode == "decorate":
        score = observed.get("scene_preservation", 0.0)
    else:
        raise ValueError(f"Unsupported mode: {mode}")
    rating = "strong" if score >= 0.9 else "acceptable" if score >= 0.75 else "borderline" if score >= 0.5 else "clear_fail"
    return {"score": round(score, 4), "rating": rating}
```

- [ ] **Step 4: Send explicit mode semantics from the UI**

```javascript
if (this.id === 'edit-image') {
    formData.append('mode', 'edit');
    formData.append('mode_contract', 'targeted_edit_with_preservation');
} else if (this.id === 'decorate-image') {
    formData.append('mode', 'decorate');
    formData.append('mode_contract', 'additive_staging');
}
```

- [ ] **Step 5: Validate mode-specific outputs before returning success**

```python
mode_validation = validate_studio_mode_result(mode=mode, observed=observed_metrics)
if mode_validation["rating"] == "clear_fail":
    raise RuntimeError(f"{mode} mode validation failed")
```

- [ ] **Step 6: Re-run the new mode-validation tests**

Run:

```powershell
python -m unittest tests.test_studio_mode_validation_support -v
```

Expected:

- all tests pass

- [ ] **Step 7: Re-run existing image-edit tests**

Run:

```powershell
python -m unittest tests.test_image_edit_planner tests.test_gemini_budget -v
```

Expected:

- all tests pass

- [ ] **Step 8: Report the slice result without creating a commit**

Expected report:

- frontal, edit, and decorate now have distinct acceptance rules

## Task 9: Refactor `main.py` Into a Composition Shell

**Files:**
- Create: `application/bootstrap/runtime_services.py`
- Create: `application/http/router_registration.py`
- Modify: `main.py`
- Modify: `application/job_entrypoints.py`
- Modify: `application/http/queue_route_handlers.py`
- Modify: `storage_helpers.py`
- Test: `tests/test_route_helpers.py`
- Test: `tests/test_protected_route_contracts.py`

- [ ] **Step 1: Write a failing smoke test that asserts app boot still works after extraction**

```python
import unittest

from fastapi.testclient import TestClient

import main


class AppBootTests(unittest.TestCase):
    def test_app_boots_and_serves_version(self):
        client = TestClient(main.app)
        response = client.get("/version.json")
        self.assertEqual(response.status_code, 200)
```

- [ ] **Step 2: Run the app-boot smoke test**

Run:

```powershell
python -m unittest tests.test_protected_route_contracts -v
```

Expected:

- current tests still pass before refactor

- [ ] **Step 3: Extract runtime service assembly from `main.py`**

```python
def build_image_edit_service(*, build_image_edit_step_prompt, pad_image_to_target_canvas, call_gemini_with_failover, model_name, match_aspect_to_target):
    return lambda photo_paths, instructions, mode, unique_id, index, mask_path=None: process_image_edit_logic_stage(
        photo_paths,
        instructions,
        mode,
        unique_id,
        index,
        build_image_edit_step_prompt=build_image_edit_step_prompt,
        pad_image_to_target_canvas=pad_image_to_target_canvas,
        call_gemini_with_failover=call_gemini_with_failover,
        model_name=model_name,
        match_aspect_to_target=match_aspect_to_target,
        mask_path=mask_path,
    )


def build_frontal_generation_service(*, build_frontal_analysis_prompt, build_frontal_generation_prompt, call_gemini_with_failover, analysis_model_name, model_name, allow_all_safety_settings, standardize_image):
    return lambda photo_paths, unique_id, index: generate_frontal_room_from_photos_stage(
        photo_paths,
        unique_id,
        index,
        build_frontal_analysis_prompt=build_frontal_analysis_prompt,
        build_frontal_generation_prompt=build_frontal_generation_prompt,
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=analysis_model_name,
        model_name=model_name,
        allow_all_safety_settings=allow_all_safety_settings,
        standardize_image=standardize_image,
    )


def build_detail_view_service(*, materialize_input, normalize_label_for_match, allow_harassment_only_safety_settings, call_gemini_with_failover, model_name, generate_detail_view_stage):
    return lambda original_image_path, style_config, unique_id, index, furniture_data=None: generate_detail_view_stage(
        original_image_path,
        style_config,
        unique_id,
        index,
        furniture_data,
        materialize_input=materialize_input,
        normalize_label_for_match=normalize_label_for_match,
        allow_harassment_only_safety_settings=allow_harassment_only_safety_settings,
        call_gemini_with_failover=call_gemini_with_failover,
        model_name=model_name,
    )


def build_runtime_services() -> JobEntrypointServices:
    image_edit_service = build_image_edit_service(
        build_image_edit_step_prompt=build_image_edit_step_prompt,
        pad_image_to_target_canvas=pad_image_to_target_canvas,
        call_gemini_with_failover=call_gemini_with_failover,
        model_name=MODEL_NAME,
        match_aspect_to_target=match_aspect_to_target,
    )
    frontal_service = build_frontal_generation_service(
        build_frontal_analysis_prompt=build_frontal_analysis_prompt,
        build_frontal_generation_prompt=build_frontal_generation_prompt,
        call_gemini_with_failover=call_gemini_with_failover,
        analysis_model_name=ANALYSIS_MODEL_NAME,
        model_name=MODEL_NAME,
        allow_all_safety_settings=allow_all_safety_settings,
        standardize_image=standardize_image,
    )
    detail_view_service = build_detail_view_service(
        materialize_input=_materialize_input,
        normalize_label_for_match=_normalize_label_for_match,
        allow_harassment_only_safety_settings=allow_harassment_only_safety_settings,
        call_gemini_with_failover=call_gemini_with_failover,
        model_name=MODEL_NAME,
        generate_detail_view_stage=generate_detail_view_stage,
    )
    return JobEntrypointServices(
        normalize_audience=_normalize_audience,
        save_job_result=_save_job_result_s3,
        materialize_input=_materialize_input,
        build_s3_prefix=_build_s3_prefix,
        resolve_image_url=resolve_image_url,
        render_room=render_room,
        generate_empty_room=generate_empty_room,
        call_magnific_api=call_magnific_api,
        s3_prefix_from_url=_s3_prefix_from_url,
        process_image_edit_logic=image_edit_service,
        generate_frontal_room_from_photos=frontal_service,
        log_section=log_section,
        detect_furniture_boxes=detect_furniture_boxes,
        canonical_category=_canonical_category,
        build_item_target_key=_build_item_target_key,
        analyze_cropped_item=analyze_cropped_item,
        attach_volume_ranks=_attach_volume_ranks,
        construct_dynamic_styles=construct_dynamic_styles_stage,
        generate_detail_view=detail_view_service,
        normalize_label_for_match=_normalize_label_for_match,
        volume_ranking_snapshot=_volume_ranking_snapshot,
        finalize_request_factory=FinalizeRequest,
        upscale_request_factory=UpscaleRequest,
        max_concurrency_analysis=GEMINI_MAX_CONCURRENCY_ANALYSIS,
    )
```

- [ ] **Step 4: Replace inline service assembly in `main.py` with the extracted bootstrap call**

```python
from application.bootstrap.runtime_services import build_runtime_services


job_entrypoints_module.configure_job_entrypoints(build_runtime_services())
```

- [ ] **Step 5: Keep route registration unchanged while removing only composition clutter**

```python
def _queue_route_deps() -> QueueRouteDependencies:
    return build_queue_route_dependencies()
```

- [ ] **Step 6: Extract route registration into a dedicated helper without changing paths**

```python
def register_http_routes(app: FastAPI) -> None:
    app.get("/version.json")(version_json)
    app.get("/jobs/{job_id}")(get_job_status)
    app.post("/async/render")(render_room_async)
    app.post("/async/generate-image-edit")(generate_image_edit_async)
    app.post("/async/generate-frontal-view")(generate_frontal_view_async)
    app.post("/async/upscale")(upscale_and_download_async)
    app.post("/async/finalize-download")(finalize_download_async)
    app.post("/async/generate-empty-room")(generate_empty_room_async)
    app.post("/api/internal/render")(api_internal_render)
    app.post("/api/external/render/preset")(api_external_render_preset)
    app.post("/api/external/render/cart")(api_external_render_cart)
    app.post("/regenerate-single-detail")(regenerate_single_detail)
    app.post("/generate-details")(generate_details_endpoint)
```

- [ ] **Step 7: Extract pure helper builders before touching workflow code**

```python
def build_queue_route_dependencies() -> QueueRouteDependencies:
    return QueueRouteDependencies(
        redis_url=REDIS_URL,
        rq_queue_render=RQ_QUEUE_RENDER,
        rq_queue_upscale=RQ_QUEUE_UPSCALE,
        cart_max_items=CART_MAX_ITEMS,
        api_auth_disabled=API_AUTH_DISABLED,
        internal_api_keys=INTERNAL_INTEA_API_KEYS,
        external_api_keys=EXTERNAL_INTEA_API_KEYS,
        enqueue_job=_enqueue_job,
        fetch_job=_fetch_job,
        load_job_result_s3=_load_job_result_s3,
        load_preset_map=_load_preset_map,
        require_role=require_role,
        apply_cart_limits=apply_cart_limits,
        build_cart_summary=build_cart_summary,
        materialize_input=_materialize_input,
        normalize_item_image=lambda local_path, unique_id, index: _normalize_item_image(local_path, unique_id, index, max_size=1024),
        resolve_image_url=resolve_image_url,
        build_s3_prefix=_build_s3_prefix,
        build_item_target_key=_build_item_target_key,
        persist_internal_render_uploads=persist_internal_render_uploads,
        persist_internal_media_uploads=persist_internal_media_uploads,
        build_internal_async_render_job_payload=build_internal_async_render_job_payload,
        build_image_edit_job_payload=build_image_edit_job_payload,
        build_frontal_view_job_payload=build_frontal_view_job_payload,
        build_upscale_job_payload=build_upscale_job_payload,
        build_finalize_download_job_payload=build_finalize_download_job_payload,
        build_empty_room_job_payload=build_empty_room_job_payload,
        build_internal_render_job_payload=build_internal_render_job_payload,
        build_external_preset_job=build_external_preset_job,
        build_external_cart_job=build_external_cart_job,
        build_regenerate_detail_job_payload=build_regenerate_detail_job_payload,
        build_detail_generation_job_payload=build_detail_generation_job_payload,
        job_render=job_render,
        job_render_with_details=job_render_with_details,
        job_image_edit=job_image_edit,
        job_frontal_view=job_frontal_view,
        job_upscale=job_upscale,
        job_finalize=job_finalize,
        job_generate_empty_room=job_generate_empty_room,
        job_regenerate_single_detail=job_regenerate_single_detail,
        job_generate_details=job_generate_details,
    )
```

- [ ] **Step 8: Re-run contract and route-helper tests**

Run:

```powershell
python -m unittest tests.test_route_helpers tests.test_protected_route_contracts -v
```

Expected:

- all tests pass

- [ ] **Step 9: Run the full deterministic unit suite**

Run:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Expected:

- suite passes cleanly

- [ ] **Step 10: Report the slice result without creating a commit**

Expected report:

- `main.py` reduced by composition extraction only
- no route schema changes

## Task 10: Release-Candidate QA, Evidence Archival, and Plan Consolidation

**Files:**
- Modify: `quality_qa_runner.py`
- Modify: `live_validate_render_flows.py`
- Modify: `docs/superpowers/specs/2026-03-31-render-engine-master-design.md`
- Modify: `docs/superpowers/plans/2026-03-31-render-engine-master-implementation.md`
- Test: `quality_qa_runner.py`
- Test: `live_validate_render_flows.py`

- [ ] **Step 1: Add a release-candidate run profile to the QA runner**

```python
RELEASE_CANDIDATE_PROFILE = {
    "repeat_count": 7,
    "cases": [
        "internal_main",
        "internal_detail",
        "internal_edit",
        "external_preset",
        "external_cart",
    ],
}
```

- [ ] **Step 2: Add final artifact archival outside cleanup-sensitive paths**

```python
archive_root = Path("outputs/qa_runs_archive").resolve()
archive_root.mkdir(parents=True, exist_ok=True)
shutil.copytree(run_dir, archive_root / run_dir.name, dirs_exist_ok=True)
```

- [ ] **Step 3: Run the deterministic unit suite before smoke validation**

Run:

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Expected:

- suite passes cleanly

- [ ] **Step 4: Run the route-surface smoke validator**

Run:

```powershell
python live_validate_render_flows.py
```

Expected:

- report written successfully
- no contract failure raised

- [ ] **Step 5: Run the release-candidate QA profile**

Run:

```powershell
python quality_qa_runner.py --suite-name release-candidate --repeat-count 7
```

Expected:

- complete QA bundle written
- no `failure.json`
- machine scores attached
- seven-run release candidate set completed

- [ ] **Step 6: Review the archived evidence bundle and summarize the results**

```python
summary = {
    "unit_tests": "pass",
    "smoke_validation": "pass",
    "qa_bundle_archived": True,
    "clear_fail_count": 0,
}
```

- [ ] **Step 7: Consolidate planning documents only after all gates are green**

```markdown
- mark `docs/superpowers/specs/2026-03-31-render-engine-master-design.md` as the controlling design document
- archive or remove older refactor and quality plans only after confirming no unique constraints remain
```

- [ ] **Step 8: Report completion and explicitly ask for approval before any commit or deployment**

Expected report:

- all gates green
- evidence archived
- no commit, push, or deployment performed

## Self-Review

### Spec coverage check

- Goal 1, efficient refactoring: covered by Task 9 and gated by Task 1
- Goal 2, image quality upgrade with real scale and trusted dimensions: covered by Tasks 3, 4, and 5
- Goal 3, internal placement accuracy: covered by Task 7
- Goal 4, internal image-studio intent adherence: covered by Task 8
- Goal 5, contract preservation: covered by Task 1 and Task 10
- Additional approved detail-angle goal: covered by Task 6

### Placeholder scan

- No `TODO`, `TBD`, or deferred placeholder steps remain
- All tasks contain file paths, code snippets, and concrete commands
- Commit instructions were intentionally replaced with report-and-hold steps because user approval is required before any commit

### Type and naming consistency

- `score_scale_constraints` is introduced in Task 4 and used consistently
- `score_placement_constraints` is introduced in Task 7 and used consistently
- `validate_studio_mode_result` is introduced in Task 8 and used consistently
- `build_detail_camera_plan` is introduced in Task 6 and used consistently
