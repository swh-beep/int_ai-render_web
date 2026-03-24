# Refactor Plan

## Goal

Reduce `main.py` safely without breaking the internal web, the domestic homepage integration, or the global homepage integration.

## Strategy

Use zero-behavior-change extraction first. Move code only after its contract is frozen.

Architecture target and naming rules are documented in `TARGET_ARCHITECTURE.md`.

## Phases

### Phase 0

- freeze protected routes and invariants
- document internal web direct route usage
- define verification gates

### Phase 1

- extract request models from `main.py`
- keep all route logic and payload assembly unchanged

### Phase 2

- extract pure helpers near the route surface
- likely candidates: audience/auth, preset/cart, S3/result helpers

### Phase 3

- extract route orchestration services
- keep route functions and response shapes intact

### Phase 4

- extract render/detail pipeline helpers gradually
- only one risky flow at a time

## Verification Gates Per Slice

- syntax/import check passes
- protected route paths unchanged
- protected request model fields unchanged
- protected response payload assembly unchanged
- no change to internal web JS call sites

## Completed Slices

- `api_models.py` extraction from `main.py`
- `request_helpers.py` extraction for external auth/cart helper logic
- `preset_helpers.py` extraction for preset map loading and preset resolution logic
- `storage_helpers.py` extraction for S3/result URL and persistence helper logic
- `render_route_services.py` extraction for internal render and external preset route payload assembly
- `render_route_services.py` extraction for external cart route preparation and payload assembly
- `render_route_services.py` extraction for internal async render/detail entrypoint upload persistence and payload assembly
- `render_route_services.py` extraction for internal media entrypoint upload persistence and payload assembly
- `render_route_services.py` extraction for remaining thin wrapper payload builders (`/async/generate-empty-room`, `/async/upscale`, `/async/finalize-download`)
- `application/render/render_workflow.py` extraction for the first guarded core workflow (`job_render`, `job_render_with_details`)
- `application/details/detail_workflow.py` extraction for the guarded detail generation workflow (`job_generate_details`)
- `application/details/regenerate_detail_workflow.py` extraction for `job_regenerate_single_detail`
- `application/render/empty_room_workflow.py` extraction for `job_generate_empty_room`
- `application/media/frontal_view_workflow.py` extraction for `job_frontal_view`
- `application/media/image_edit_workflow.py` extraction for `job_image_edit`
- `application/render/finalize_workflow.py` extraction for `job_finalize`
- `application/render/upscale_workflow.py` extraction for `job_upscale`
- `application/render/render_workflow.py` split into explicit stage helpers for input preparation, resource preparation, cleanup, and detail payload assembly
- `application/render/render_preparation.py` extraction for render input/resource preparation and cleanup stages
- `application/render/render_result_stage.py` extraction for render result URL resolution and detail payload assembly
- `infrastructure/ai/magnific_client.py` extraction for the Magnific HTTP/poll/download adapter while preserving the existing `call_magnific_api(...)` signature in `main.py`
- `infrastructure/ai/gemini_client.py` extraction for the Gemini failover/configure/generate adapter while preserving the existing `call_gemini_with_failover(...)` signature in `main.py`
- `infrastructure/ai/gemini_policy.py` extraction for repeated Gemini safety policy configuration while preserving model behavior at each call site
- `infrastructure/ai/gemini_prompts.py` extraction for the safer prompt builders around frontal-view, image-edit, empty-room, and moodboard generation while preserving prompt text and call-site behavior
- `application/details/detail_analysis_stage.py` extraction for cached-detail analysis selection and fallback item analysis
- `application/details/detail_result_stage.py` extraction for detail result shaping, cutout metadata resolution, and target-box enrichment
- `application/details/regenerate_detail_resolution.py` extraction for single-detail style resolution and target metadata enrichment
- `application/render/reference_preparation.py` extraction for moodboard item reference collection, preset reference selection, and customize moodboard persistence before render analysis
- post-extraction runtime regressions fixed in `main.py` and revalidated end-to-end (`_DIM_2D_OK_PAT`, dimension parsing regex, primary candidate selection)
- route validation expanded to cover `/async/upscale`, `/async/finalize-download`, `/regenerate-single-detail`, `/async/generate-empty-room`, `/async/generate-frontal-view`, and `/async/generate-image-edit`
- additional runtime regression fixed in `generate_frontal_room_from_photos` (`content_list` reconstruction input)
- Magnific adapter extraction revalidated end-to-end across internal main/upscale/finalize/empty-room/frontal-view/image-edit/detail/regenerate-detail and external preset/cart flows
- Gemini adapter extraction revalidated end-to-end across the same protected flows without changing route contracts or payload semantics
- Gemini safety policy extraction revalidated end-to-end across the same protected flows without changing route contracts or payload semantics
- Gemini prompt-builder extraction revalidated end-to-end across the same protected flows without changing route contracts or payload semantics
- detail workflow decomposition revalidated end-to-end across internal detail/regenerate and the full protected route suite without changing route contracts or payload semantics
- render reference-preparation extraction revalidated end-to-end across the protected route suite; one first-run provider flake produced an invalid Stage 1 image, and a clean rerun passed without code changes
- `application/video` extraction for `video-mvp` source-generation and compile workflows, plus `infrastructure/ai/freepik_kling_client.py` for Kling create/poll behavior while preserving route contracts and in-memory status semantics
- live validation expanded to exercise `/video-mvp/generate-sources`, `/video-mvp/compile`, and `/video-mvp/status/{job_id}` with a real static source/compile cycle
- `application/render/moodboard_workflow.py` extraction for `/generate-moodboard-options` while preserving prompt text, upload semantics, and response shape
- live validation expanded to exercise `/generate-moodboard-options` with a real uploaded room image
- `application/render/render_analysis_stage.py` extraction for the split render-analysis/specs-preparation block inside `render_room` while preserving room-analysis outputs, analyzed item payloads, and downstream furniture spec inputs
- `application/render/render_postprocess_stage.py` extraction for best-variant selection, main-render box refresh, and volume-ranking attachment inside `render_room` while preserving downstream response payload semantics
- `application/render/render_variant_stage.py` extraction for the Stage 2 three-variation fan-out inside `render_room` while preserving generation inputs, prompt selection, and result ordering semantics
- `application/render/render_response_stage.py` extraction for final render summary logging and response-payload URL shaping inside `render_room`, plus `_reset_summary_token(...)` to preserve summary context cleanup semantics
- `application/render/render_bootstrap_stage.py` extraction for `render_room` request bootstrap (`unique_id`, start time, summary context) while preserving log labels and summary token semantics
- `application/render/render_audience_stage.py` extraction for `render_room` audience normalization and S3 prefix policy setup while preserving internal/external prefix semantics and the globally disabled scale-check policy
- `application/render/render_input_stage.py` extraction for raw upload persistence and `standardize_image(...)` setup inside `render_room` while preserving output naming and standardized input semantics
- `application/render/render_empty_stage.py` extraction for the intermediate empty-room generation call inside `render_room` while preserving Stage 1 prompt usage, timeout wiring, and raw-image fallback semantics
- `application/render/render_scale_stage.py` extraction for room-dimension parsing, scale-guide eligibility, and Stage 1 default-state initialization inside `render_room` while preserving internal-only guide gating and downstream default values
- `application/render/render_room_workflow.py` extraction for the remaining `render_room` stage-chaining orchestration while preserving request wiring, helper dependencies, summary cleanup, and final payload semantics
- `application/render/empty_room_generation_stage.py` extraction for the legacy `generate_empty_room(...)` helper while preserving prompt usage, retry semantics, raw-image fallback, and aspect-fit handling
- `application/render/item_analysis_stage.py` extraction for `detect_furniture_boxes(...)`, `_crop_item_with_padding(...)`, and `analyze_cropped_item(...)` while preserving item-detection prompts, crop persistence semantics, dimension normalization, and text-read fallback behavior
- `application/render/furniture_specs_stage.py` extraction for dimension proxying, rug/category handling, volume ranking, and furniture spec JSON shaping while preserving downstream Stage 2 scale inputs and external detail metadata
- `application/render/furnished_generation_stage.py` extraction for the legacy `generate_furnished_room(...)` helper while preserving Stage 2 prompt construction, cutout injection, ratio enforcement, and optional scale-check retry semantics
- `application/media/frontal_generation_stage.py` extraction for `generate_frontal_room_from_photos(...)` while preserving the Flash analysis -> Pro image reconstruction flow and final output standardization
- `application/media/image_edit_generation_stage.py` extraction for `process_image_edit_logic(...)` while preserving step detection, mask handling, reference-image padding, and per-step edit/decorate prompt semantics
- direct `main.py` sync finalize/upscale thin wrappers collapsed onto the already extracted `application/render/finalize_workflow.py` and `application/render/upscale_workflow.py` behavior without changing request/response contracts
- inactive legacy `generate_furnished_room(...)` body removed from `main.py` after the extracted stage path passed static and live validation

## Observed Existing Runtime Behavior

- `finalize-download` may return the generated empty-room URL instead of a Magnific-upscaled empty-room URL when the empty-room image is rejected by the upstream Magnific API. This was observed again after the adapter extraction and is treated as pre-existing behavior, not a refactor regression.

## Plan Status

The tracked safe-slice plan for the protected render/detail/media flows is complete as of March 12, 2026.

## Future Optional Cleanup

- further reduce `main.py` by extracting the remaining image/aspect helper cluster
- extract the remaining scale-guide and scale-validation helper cluster if those callbacks need to be reused outside the current render stages
- move the remaining shared utility wrappers into dedicated `shared` or `application/render` modules once their boundaries stabilize
