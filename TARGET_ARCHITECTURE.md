# Target Architecture

## Goal

Turn the repo into a structure that looks intentionally designed, while preserving the current external contracts and allowing gradual migration.

This project should not jump to a pure greenfield rewrite. The target is a pragmatic structure that combines:

- layered architecture
- DDD-lite boundaries
- adapter separation for external integrations

Full DDD is possible in theory, but it is not the best first target here because this codebase is integration-heavy:

- Gemini and image generation APIs
- S3 storage and result publishing
- Redis/RQ queue orchestration
- internal web routes plus external API routes

For this repo, the right target is:

- thin route layer
- explicit workflow/application layer
- isolated infrastructure adapters
- smaller domain/pipeline modules

## Recommended Structure

```text
int_ai-render_web/
  main.py
  worker.py
  api/
    routes/
      internal_routes.py
      external_routes.py
      media_routes.py
      detail_routes.py
      video_routes.py
    dependencies/
      auth.py
      request_context.py
  application/
    render/
      render_workflow.py
      render_with_details_workflow.py
      empty_room_workflow.py
      finalize_workflow.py
    details/
      detail_generation_workflow.py
      regenerate_detail_workflow.py
    media/
      image_edit_workflow.py
      frontal_view_workflow.py
    presets/
      preset_resolution_service.py
    cart/
      cart_render_workflow.py
      cart_item_preparation.py
  domain/
    render/
      render_types.py
      detail_types.py
      style_shot_builder.py
      ranking_policy.py
    catalog/
      style_catalog.py
      room_catalog.py
    cart/
      cart_rules.py
  infrastructure/
    ai/
      gemini_client.py
      magnific_client.py
      freepik_video_client.py
    storage/
      s3_storage.py
      result_store.py
    queue/
      rq_queue.py
      job_status_store.py
    files/
      local_file_store.py
  contracts/
    api_models.py
    internal_payloads.py
    workflow_payloads.py
  shared/
    settings.py
    logging_utils.py
    path_rules.py
    errors.py
    utils.py
  static/
  assets/
  outputs/
```

## Layer Responsibilities

### `main.py`

Keep only:

- app creation
- middleware
- static mounts
- route registration

`main.py` should stop containing business logic.

### `api/routes`

Keep only:

- HTTP entrypoints
- request parsing
- auth/dependency checks
- enqueue or workflow invocation
- response shaping

Routes should not contain image processing or multi-step orchestration.

### `application`

This is the most important target layer.

Use this for:

- render orchestration
- detail orchestration
- cart render orchestration
- finalize/upscale orchestration

This is where current large `job_*` functions should gradually move.

### `domain`

Keep domain-specific rules here:

- shot generation rules
- ranking logic
- naming logic for detail targets
- cart policy and style policy

Do not put S3, Redis, or Gemini calls here.

### `infrastructure`

Everything external goes here:

- Gemini call wrappers
- S3 upload/download/publish
- queue helpers
- local file helpers

This layer should absorb provider-specific details.

### `contracts`

Use for typed payloads and contracts:

- API request/response models
- internal workflow payload shapes
- job payload schemas

Current dict-heavy flows should gradually move here.

### `shared`

Use for repo-wide support code:

- settings/env parsing
- logging
- shared path/prefix rules
- shared exceptions

## Naming Rules

Use Python `snake_case` for files and folders.

Prefer names by responsibility:

- routes: `*_routes.py`
- workflows: `*_workflow.py`
- integrations: `*_client.py`, `*_storage.py`, `*_queue.py`
- policy/rules: `*_policy.py`, `*_rules.py`
- contracts/types: `*_models.py`, `*_payloads.py`, `*_types.py`

Avoid vague names like:

- `helpers.py`
- `utils2.py`
- `misc.py`
- `services.py`

If a file is named `helpers`, it should be temporary only.

## Current File Mapping

Current files should eventually move roughly like this:

- `api_models.py` -> `contracts/api_models.py`
- `request_helpers.py` -> `api/dependencies/auth.py` or `application/cart/cart_rules.py`
- `preset_helpers.py` -> `application/presets/preset_resolution_service.py`
- `storage_helpers.py` -> `infrastructure/storage/s3_storage.py` and `infrastructure/storage/result_store.py`
- `render_route_services.py` -> split across `application/render`, `application/details`, `application/media`
- `styles_config.py` -> `domain/catalog/style_catalog.py`
- `worker.py` -> `infrastructure/queue/rq_worker.py` or `worker.py` kept as thin entrypoint

## Migration Order

### Stage 1

Complete route-surface cleanup.

This is nearly done already.

### Stage 2

Extract the first guarded core workflow:

- `job_render`
- then `job_render_with_details`

Split them first into stage functions before moving across folders.

### Stage 3

Extract detail workflows:

- `job_generate_details`
- `job_regenerate_single_detail`

### Stage 4

Extract provider adapters:

- Gemini
- S3
- queue
- upscale/finalize providers

### Stage 5

Move files into final folders once boundaries stabilize.

Do not do large folder moves before the function boundaries are stable.

## Important Rule

The repo should move toward this target incrementally.

Do not optimize for the final folder tree first.
Optimize for stable responsibility boundaries first, then move files into the final structure.

That is the safest way to make the repo feel like it was designed properly from the beginning, without breaking the three active web surfaces.
