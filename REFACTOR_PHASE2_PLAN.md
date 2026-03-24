# Refactor Phase 2 Plan

## Goal

Reduce the remaining shared utility and domain-helper weight inside `main.py` after the protected-route refactor baseline has already been completed and validated.

This phase is optional cleanup, not contract stabilization.

## Current State

The protected render/detail/media/video flows are already refactored and validated.

What remains in `main.py` is mostly:

- shared image/aspect helpers
- dimension parsing and normalization helpers
- post-render ranking and box-remap helpers
- scale-guide and scale-validation helpers

These are still valid runtime code, but they no longer need to stay in the entrypoint file.

## Approach

Continue using safe slices with the same rules:

- no route path changes
- no request model changes
- no response payload changes
- move code in small clusters
- validate immediately after each slice

## Planned Slices

### Slice 1

Extract shared image/aspect helpers from `main.py` into a dedicated shared module.

Candidates:

- `standardize_image(...)`
- `_set_png_dpi(...)`
- `standardize_image_to_reference_canvas(...)`
- `standardize_image_to_target_canvas(...)`
- `match_aspect_to_target(...)`
- `pad_image_to_target_canvas(...)`

### Slice 2

Extract dimension parsing and normalization helpers into a dedicated render-support module.

Candidates:

- `parse_object_dimensions_mm(...)`
- `parse_room_dimensions_mm(...)`
- `_normalize_dims_dict(...)`
- `_dims_has_positive_values(...)`
- `_is_two_dim_ok_label(...)`
- `_available_dim_axes(...)`
- `_dims_to_str(...)`

### Slice 3

Extract post-render ranking and remap logic into a dedicated render-postprocess support module.

Candidates:

- `_rank_best_variant_flash(...)`
- `_refresh_item_boxes_from_main_render(...)`
- matching/category helper functions tied to remap and ranking

### Slice 4

Extract scale-guide and scale-validation logic into a dedicated render-scale support module.

Candidates:

- `detect_back_wall_span_norm(...)`
- `detect_windows_present(...)`
- bbox localization helpers
- `validate_furnished_scale(...)`
- remaining active scale-guide helpers that are still used by render stages

## Validation

Every slice must pass:

- `py_compile`
- protected-flow live validation when the slice touches active render/media behavior

## Current Progress

- Slice 1 started on March 13, 2026
- `shared/image_canvas.py` created and the image/aspect helper implementations moved there
- `main.py` now keeps compatibility wrappers for that cluster
- static validation passed for the Slice 1 extraction
- protected-flow live validation rerun passed after the Slice 1 extraction
- Slice 2 completed: dimension parsing and normalization rules moved into `application/render/dimension_support.py`
- Slice 2 validation passed: `py_compile` + protected-flow live validation
- Slice 3 completed: ranking, category normalization, target-key, and main-render remap rules moved into `application/render/postprocess_support.py`
- Slice 3 validation passed: `py_compile` + protected-flow live validation
- Slice 4 completed for the live-path scale cluster: window detection, bbox localization, scale reordering, and furnished scale validation moved into `application/render/scale_validation_support.py`
- Slice 4 validation passed: `py_compile` + protected-flow live validation
- Final cleanup pass completed: the active scale-guide helper moved into `application/render/scale_guide_support.py`, and the disconnected legacy geometry-overlay helpers were deleted from `main.py`
- Final cleanup validation passed: `py_compile` + protected-flow live validation

## Status

Phase 2 is complete as of March 13, 2026.

## Final Cleanup Result

The optional cleanup pass finished with a split between active and dead code:

- Active helper moved:
  - `create_scale_guide_overlay_with_model(...)` -> `application/render/scale_guide_support.py`
- Dead legacy geometry cluster deleted from `main.py`:
  - `detect_room_planes_norm(...)`
  - `create_scale_guide_image(...)`
  - `estimate_room_dimensions_from_image(...)`
  - `estimate_vanishing_point_from_edges(...)`
  - `create_scale_guide_overlay(...)`

This leaves no tracked Phase 2 residual helper cluster in `main.py`.

## Exit Condition

This phase is complete when the remaining high-value shared logic clusters are no longer implemented directly inside `main.py`, and the route entrypoint is reduced further without changing protected contracts.
