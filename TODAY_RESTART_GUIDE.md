# AI Render Refactor Restart Guide

## Goal

Resume the external `preset/cart` validation work for the AI render engine refactor without reopening the broader refactor scope.

Today's focus is validation, not new extraction work.

## Current State

The route-contract-preserving refactor is already far along.

The specific patch from the previous session is also already implemented:

- main-render box remap now applies across internal and external flows
- volume-based ranking metadata is attached to analyzed furniture items
- detail and regenerate responses expose stronger verification metadata

Static validation was already completed with `py_compile`.
Local route validation has also been rerun and now records proof fields for external preset/cart flows in `live_validation_report.json`.

## Validation Targets

### 1. External preset flow

Endpoint:

- `POST /api/external/render/preset`

Queue result path:

- route enqueues `job_render_with_details`
- render stage produces `render`
- detail stage produces `details`

Minimal request shape:

```json
{
  "image_url": "<public room image url>",
  "preset_id": "livingroom_french-modern_1"
}
```

Primary success conditions:

- job completes successfully
- `render.result_url` exists
- `render.furniture_data` exists
- each remapped item that survived main-render box refresh exposes `box_source = "main_render"`
- `render.volume_ranking` exists
- `details.details` has at least one generated detail item
- `details.furniture_boxes` and `details.volume_ranking` are present

### 2. External cart flow

Endpoint:

- `POST /api/external/render/cart`

Queue result path:

- route enqueues `job_render_with_details`
- render stage uses item-image-based cart preparation
- detail stage reuses analyzed item data from the render result

Minimal request shape:

```json
{
  "image_url": "<public room image url>",
  "room": "livingroom",
  "style": "French Modern",
  "variant": "1",
  "items": [
    {
      "id": "product-1",
      "name": "Product 1",
      "category": "chair",
      "image_url": "<public product image url>",
      "qty": 1,
      "dims_mm": {
        "width_mm": 600,
        "depth_mm": 660,
        "height_mm": 660
      }
    }
  ]
}
```

Primary success conditions:

- job completes successfully
- `render.result_url` exists
- `render.furniture_data` exists
- `details.used_cutout_references` is populated from item-image-based analysis
- `details.used_cutout_references[*].box_source` reflects cart/item-image lineage rather than preset cutout lineage
- `details.details[*].target_box_2d` and `target_box_source` exist for detail shots
- `details.volume_ranking` exists

## Exact Fields To Check

### Render result

Check these fields first:

- `render.result_url`
- `render.furniture_data`
- `render.volume_ranking`

For each `render.furniture_data[*]`, inspect:

- `label`
- `target_key`
- `box_2d`
- `source_box_2d`
- `box_source`
- `volume_rank`
- `volume_proxy`
- `volume_rank_basis`

Expected interpretation:

- `box_source = "main_render"` means the item box was refreshed from the generated main render
- `source_box_2d` preserves the original source/reference box when available
- `volume_rank_basis = "dims_mm"` is preferred
- `volume_rank_basis = "box_area_2d"` is acceptable fallback

### Detail result

Check these fields next:

- `details.details`
- `details.furniture_boxes`
- `details.used_cutout_references`
- `details.volume_ranking`

For each `details.details[*]`, inspect:

- `style_name`
- `target_label`
- `target_key`
- `target_box_2d`
- `target_source_box_2d`
- `target_box_source`
- `target_volume_rank`
- `target_volume_proxy`

For each `details.used_cutout_references[*]`, inspect:

- `label`
- `target_key`
- `crop_url`
- `box_2d`
- `source_box_2d`
- `box_source`
- `volume_rank`
- `volume_proxy`
- `volume_rank_basis`

## Code Anchors

Relevant route entrypoints:

- `main.py`: `/api/external/render/preset`, `/api/external/render/cart`

Relevant render-stage logic:

- `main.py`: `render_room(...)`
- `main.py`: `_attach_volume_ranks(...)`
- `main.py`: `_volume_ranking_snapshot(...)`
- `main.py`: main-render box refresh inside `render_room(...)`

Relevant detail-stage logic:

- `application/render/render_workflow.py`: `run_render_with_details_job(...)`
- `application/details/detail_workflow.py`: `run_generate_details_job(...)`
- `application/details/detail_result_stage.py`: output shaping for `furniture_boxes`, `used_cutout_references`, `details`, `volume_ranking`
- `application/details/regenerate_detail_workflow.py`: regenerate response metadata

## Recommended Restart Sequence

1. Confirm the environment for real external validation.

- server running
- Redis available
- external API key available
- public image URLs available

2. Run one real `preset` request.

- capture `job_id`
- poll `/jobs/{job_id}` until complete
- inspect `render.furniture_data[*].box_source`
- confirm at least one relevant item shows `main_render`

3. Run one real `cart` request.

- capture `job_id`
- poll `/jobs/{job_id}` until complete
- inspect `details.used_cutout_references`
- confirm references came from item-image-based analysis

4. Decide whether the external detail count cap stays at 9.

Current behavior:

- external flow keeps only `Detail:` styles
- hard cap is `[:9]`

Decision:

- keep the external detail cap at 9

5. Only after real validation passes, continue the broader refactor.

Next broader candidate from the refactor plan:

- continue decomposing remaining high-coupling render analysis/preparation around `render_room`

## Blockers

Current blocker from the last session:

- none for local validation

The next unresolved item is the next refactor slice selection, not validation of the previous patch.
