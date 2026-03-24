# AI Render Engine Refactor Final Report

## Status

As of March 13, 2026, the tracked safe-slice refactor plan, the Phase 2 optional cleanup plan, and the Phase 3 architecture hardening plan for the active render/detail/media/video flows are complete.

This refactor was executed under a strict zero-contract-change rule:

- protected route paths unchanged
- protected request model fields unchanged
- protected response JSON field names and nesting unchanged
- queue, Redis, and S3 result persistence semantics unchanged

## Goal

The goal was to reduce `main.py` without breaking any of the three active web surfaces:

- internal web
- domestic homepage integration
- global homepage integration

The approach was incremental extraction with validation after each slice, not a greenfield rewrite.

## What Was Refactored

### Route and Workflow Boundaries

The protected route surface was preserved while orchestration moved into dedicated workflow modules:

- `application/render/render_workflow.py`
- `application/details/detail_workflow.py`
- `application/details/regenerate_detail_workflow.py`
- `application/render/empty_room_workflow.py`
- `application/media/frontal_view_workflow.py`
- `application/media/image_edit_workflow.py`
- `application/render/finalize_workflow.py`
- `application/render/upscale_workflow.py`
- `application/render/moodboard_workflow.py`
- `application/video/*`

### Render Pipeline Decomposition

`render_room` was split into explicit stages and then wrapped by a dedicated workflow:

- `application/render/render_bootstrap_stage.py`
- `application/render/render_audience_stage.py`
- `application/render/render_input_stage.py`
- `application/render/render_empty_stage.py`
- `application/render/render_scale_stage.py`
- `application/render/render_analysis_stage.py`
- `application/render/render_variant_stage.py`
- `application/render/render_postprocess_stage.py`
- `application/render/render_response_stage.py`
- `application/render/render_room_workflow.py`

The FastAPI route in `main.py` now acts as a thin compatibility wrapper around the same request and response contract.

### Legacy AI Helper Extraction

The remaining high-value generation and analysis helpers were moved out of `main.py` into dedicated stage modules:

- `application/render/empty_room_generation_stage.py`
- `application/render/item_analysis_stage.py`
- `application/render/furniture_specs_stage.py`
- `application/render/furnished_generation_stage.py`
- `application/media/frontal_generation_stage.py`
- `application/media/image_edit_generation_stage.py`

This completed the planned extraction of the remaining legacy AI generation and item-analysis helpers.

### Phase 2 Shared Support Extraction

The remaining live-path utility clusters were reduced further during Phase 2:

- `shared/image_canvas.py`
- `application/render/dimension_support.py`
- `application/render/postprocess_support.py`
- `application/render/scale_validation_support.py`
- `application/render/scale_guide_support.py`

`main.py` now keeps compatibility wrappers for those clusters instead of owning their full implementations.

### Infrastructure Adapters

Provider-specific behavior is now isolated behind dedicated adapters:

- `infrastructure/ai/gemini_client.py`
- `infrastructure/ai/gemini_policy.py`
- `infrastructure/ai/gemini_prompts.py`
- `infrastructure/ai/magnific_client.py`
- `infrastructure/ai/freepik_kling_client.py`

### Phase 3 Hardening Additions

Phase 3 closed the remaining maintainability gaps that were intentionally left after the contract-preserving refactor baseline:

- `application/render/render_workflow_contracts.py`
- `application/job_entrypoints.py`
- `application/http/queue_route_handlers.py`
- `tests/test_video_kling.py`
- `tests/test_render_postprocess.py`
- `tests/test_detail_metadata.py`
- `tests/test_route_helpers.py`

This phase added:

- typed render workflow dependency containers
- isolated queue/job entrypoint bodies
- isolated queue route handler bodies
- deterministic regression coverage for critical support modules
- corrected dynamic Kling endpoint construction and dynamic video validation coverage

## Current Shape of `main.py`

`main.py` now primarily contains:

- app bootstrap and middleware
- route registration
- environment/config assembly
- thin compatibility wrappers around extracted workflows, stages, queue handlers, and job entrypoints
- a reduced set of compatibility helpers for extracted support modules

The previous inactive legacy `generate_furnished_room(...)` body was removed after the extracted path passed validation.

After the final Phase 3 extraction set, `main.py` is down to approximately `1621` lines and no longer owns the queue route bodies or queue job execution bodies directly.

## Protected Contract Result

The protected routes and payloads remained stable throughout the refactor:

- internal async render/detail/media routes unchanged
- external preset/cart routes unchanged
- `/jobs/{job_id}` polling shape unchanged
- internal static page entrypoints unchanged

No request payload or response payload breaking change was introduced as part of this refactor.

## Validation

## Static Validation

Static validation passed after the final extraction set:

- `main.py`
- `application/job_entrypoints.py`
- `application/http/queue_route_handlers.py`
- `application/render/render_workflow_contracts.py`
- `application/render/item_analysis_stage.py`
- `application/render/furniture_specs_stage.py`
- `application/render/furnished_generation_stage.py`
- `application/media/frontal_generation_stage.py`
- `application/media/image_edit_generation_stage.py`
- `application/video/source_generation_workflow.py`
- `infrastructure/ai/freepik_kling_client.py`
- `render_route_services.py`

Validation method:

- `python -m py_compile ...`

## Deterministic Test Validation

A repeatable automated regression suite now exists for critical refactored boundaries.

Validation method:

- `.venv\\Scripts\\python.exe -m unittest discover -s tests -v`

Covered areas:

- Kling endpoint and polling behavior
- dynamic video source-generation success/failure propagation
- render postprocess box remap and volume metadata
- detail target metadata and regenerate resolution
- route helper behavior for auth, preset resolution, and external cart payload construction

## Live Validation

Full protected-flow live validation was rerun multiple times during the refactor, including a final Phase 3 rerun on March 13, 2026, using:

- `live_validate_render_flows.py`
- mode: `sync-enqueue-route-validation`

Validated flows:

- internal main render
- internal upscale
- internal finalize-download
- internal empty-room
- internal frontal-view
- internal image-edit
- internal moodboard options
- internal detail generation
- internal single-detail regeneration
- video source generation
- dynamic video source generation
- video compile
- external preset render
- external cart render

Final validation result: `PASS`

Key final observed results:

- internal detail count: `16`
- external preset detail count: `9`
- external cart detail count: `4`
- internal dynamic video source status: `COMPLETED`
- external preset `render_box_sources`: `{"main_render": 11}`
- external preset `detail_box_sources`: `{"main_render": 11}`
- external cart `used_cutout_reference_count`: `4`
- external cart `targeted_detail_count`: `4`

These results confirm that the protected external targeting and volume metadata behavior remained intact after the last extraction set.

## Known Existing Runtime Behavior

The refactor intentionally preserved pre-existing behavior, including existing fallback behavior:

- `/async/finalize-download` may still fall back to the generated empty-room URL when the upstream empty-room Magnific request is rejected
- provider-side flakes such as zero-byte or invalid intermediate images can still appear on first run and may pass on a clean rerun without code changes
- text-read and dimension-read warnings may still appear in logs for incomplete source imagery, but these did not constitute refactor regressions during the final validation

## Remaining Optional Cleanup

The tracked refactor, follow-up optional cleanup, and Phase 3 hardening scope are complete.

The last legacy geometry-overlay helpers that were disconnected from the live render path were deleted, and the one remaining active scale-guide helper was moved into `application/render/scale_guide_support.py`.

No tracked helper cluster remains in `main.py` for this refactor scope. Any future work would be elective architecture polish, not unfinished Phase 1, Phase 2, or Phase 3 refactor work.

## Handoff Summary

If work resumes later, the correct starting point is no longer route-contract stabilization. That part is done.

Any future work should now be treated as non-blocking architecture polish on top of a validated, contract-preserving refactor baseline.
