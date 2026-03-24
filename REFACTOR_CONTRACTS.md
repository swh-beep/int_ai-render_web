# Refactor Contracts

This refactor must preserve existing behavior for all three web surfaces and their backing routes.

## Protected Internal Web Routes

These are used directly by the internal web static JS and must not change path, request shape, or result semantics.

- `/`
- `/image-studio`
- `/video-studio`
- `/room-types`
- `/styles/{room_type}`
- `/api/thumbnails/{room_name}/{style_name}`
- `/api/outputs/list`
- `/api/outputs/upload`
- `/jobs/{job_id}`
- `/async/render`
- `/async/generate-frontal-view`
- `/async/generate-image-edit`
- `/async/generate-empty-room`
- `/async/upscale`
- `/async/finalize-download`
- `/generate-details`
- `/regenerate-single-detail`
- `/video-mvp/generate-sources`
- `/video-mvp/compile`
- `/video-mvp/status/{job_id}`

## Protected External API Routes

- `/api/external/render/preset`
- `/api/external/render/cart`

## Keep for Safety

- `/api/internal/render`

## Invariants

1. Route paths do not change.
2. Request model field names do not change.
3. Response JSON field names and nesting do not change.
4. Job polling via `/jobs/{job_id}` keeps the same shape.
5. Static page paths and JS entrypoints do not change.
6. RQ queue names, Redis usage, and S3 result persistence behavior do not change unless explicitly planned.

## Completed Safe Slices

- Move Pydantic request models out of `main.py` into a dedicated module.
- Move external auth/cart helper functions into a dedicated module.
- Move preset map loading and preset resolution logic into a dedicated module.
- Move S3/result URL and persistence helper functions into a dedicated module.
- Move internal render and external preset route payload assembly into a dedicated module.
- Move external cart route preparation and payload assembly into a dedicated module.
- Move internal async render/detail entrypoint upload persistence and payload assembly into a dedicated module.
- Move internal media entrypoint upload persistence and payload assembly into a dedicated module.
- Move remaining thin-wrapper payload builders into a dedicated module.
- Move the first guarded core workflow into `application/render` while preserving job payloads and live behavior.
- Move guarded detail generation into `application/details/detail_workflow.py` while preserving route semantics and result structure.
- Move guarded detail regeneration into `application/details/regenerate_detail_workflow.py` while preserving route semantics and result structure.
- Move guarded internal empty-room generation into `application/render/empty_room_workflow.py` while preserving route semantics and result structure.
- Move guarded internal frontal-view generation into `application/media/frontal_view_workflow.py` while preserving route semantics and result structure.
- Move guarded internal image-edit generation into `application/media/image_edit_workflow.py` while preserving route semantics and result structure.
- Move guarded internal finalize generation into `application/render/finalize_workflow.py` while preserving route semantics and result structure.
- Move guarded internal upscale generation into `application/render/upscale_workflow.py` while preserving route semantics and result structure.
- Keep `application/render/render_workflow.py` behavior-identical while splitting its internal stages into explicit helper boundaries.
- Keep render orchestration behavior-identical while moving stage preparation and result-shaping helpers into dedicated `application/render` stage modules.
- Keep Magnific behavior-identical while moving its HTTP/poll/download path into `infrastructure/ai/magnific_client.py` and preserving the legacy `call_magnific_api(...)` interface inside `main.py`.
- Keep Gemini behavior-identical while moving its failover/configure/generate path into `infrastructure/ai/gemini_client.py` and preserving the legacy `call_gemini_with_failover(...)` interface inside `main.py`.
- Keep Gemini safety behavior-identical while moving repeated safety policy definitions into `infrastructure/ai/gemini_policy.py`.
- Keep the safer Gemini prompt builders behavior-identical while moving them into `infrastructure/ai/gemini_prompts.py` and preserving prompt text at each call site.
- Keep detail generation and single-detail regeneration behavior-identical while moving their internal analysis/result/style-resolution stages into dedicated `application/details` helper modules.
- Keep render reference selection behavior-identical while moving moodboard/preset/customize reference preparation into `application/render/reference_preparation.py`.
- Keep `video-mvp` source generation and compile behavior-identical while moving the background workflows into `application/video` and the Freepik/Kling create/poll adapter into `infrastructure/ai/freepik_kling_client.py`.
- Keep `/generate-moodboard-options` behavior-identical while moving moodboard generation into `application/render/moodboard_workflow.py`.
- Keep `render_room` split-analysis/specs-preparation behavior-identical while moving its room/item analysis fan-out and furniture-spec bundle preparation into `application/render/render_analysis_stage.py`.
- Keep `render_room` post-generation ranking/box-refresh/volume-attachment behavior-identical while moving that post-process stage into `application/render/render_postprocess_stage.py`.
- Keep `render_room` three-variant generation fan-out behavior-identical while moving that Stage 2 generation loop into `application/render/render_variant_stage.py`.
- Keep `render_room` final response/url shaping behavior-identical while moving final URL resolution and payload assembly into `application/render/render_response_stage.py`, and keep summary context reset semantics unchanged with `_reset_summary_token(...)`.
- Keep `render_room` request bootstrap behavior-identical while moving unique-id generation, request-start logging, and summary-context setup into `application/render/render_bootstrap_stage.py`.
- Keep `render_room` audience and prefix policy behavior-identical while moving audience normalization, S3 prefix setup, and the disabled scale-check default into `application/render/render_audience_stage.py`.
- Keep `render_room` upload-preparation behavior-identical while moving raw file persistence and `standardize_image(...)` setup into `application/render/render_input_stage.py`.
- Keep `render_room` intermediate empty-room generation behavior-identical while moving the Stage 1 empty-room call and raw-image fallback into `application/render/render_empty_stage.py`.
- Keep `render_room` room-dimension and Stage 1 default-state behavior-identical while moving dimension parsing, scale-guide gating, and downstream default initialization into `application/render/render_scale_stage.py`.
- Keep `render_room` route behavior-identical while moving the remaining orchestration glue into `application/render/render_room_workflow.py` and leaving the FastAPI route as a thin wrapper around the same request/response contract.
- Keep the legacy `generate_empty_room(...)` helper behavior-identical while moving its Stage 1 generation internals into `application/render/empty_room_generation_stage.py` and preserving the public wrapper signature in `main.py`.
- Keep moodboard item analysis behavior-identical while moving `detect_furniture_boxes(...)`, `_crop_item_with_padding(...)`, and `analyze_cropped_item(...)` into `application/render/item_analysis_stage.py`.
- Keep furniture scale metadata behavior-identical while moving volume proxying, rug/category handling, and furniture spec JSON shaping into `application/render/furniture_specs_stage.py`.
- Keep Stage 2 furnished-room generation behavior-identical while moving the legacy `generate_furnished_room(...)` helper into `application/render/furnished_generation_stage.py` and preserving the public wrapper signature in `main.py`.
- Keep frontal-view reconstruction behavior-identical while moving `generate_frontal_room_from_photos(...)` into `application/media/frontal_generation_stage.py`.
- Keep image-edit / decorate behavior-identical while moving `process_image_edit_logic(...)` into `application/media/image_edit_generation_stage.py`.
- Keep sync finalize/upscale route behavior-identical while collapsing their thin `main.py` implementations onto the already extracted finalize/upscale workflows.
- Remove inactive legacy `generate_furnished_room(...)` code from `main.py` only after the extracted path passes static and live validation.
- Fix post-extraction runtime regressions discovered by live validation before continuing with the next slice.

## Observed Existing Behavior

- `/async/finalize-download` can fall back to the generated empty-room URL if the empty-room Magnific request is rejected upstream. This fallback remains allowed because it is established runtime behavior that existed before the current refactor slice.

## Current Status

The tracked safe slices for the protected routes are complete as of March 12, 2026. Remaining `main.py` cleanup candidates are optional internal utility extractions, not required contract-preserving slices.
