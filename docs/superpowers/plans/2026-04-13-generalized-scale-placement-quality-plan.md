# Generalized Internal Render Scale And Placement Quality Plan

## Goal

Improve internal render scale and placement quality without overfitting to the `99b7e7db` case or to any specific SKU. The validator and candidate-selection path should generalize across item mixes, room sizes, and category families.

## Architecture Summary

The current internal `/async/render` path is live again, but quality control is still too permissive because it relies on noisy post-render detection and a small set of hard-coded pairwise rules. The next remediation phase should shift from narrow rule additions to a broader scoring pipeline:

1. Stabilize source-to-render matching so every source item is paired by persistent identity before any scale or fidelity rule runs.
2. Replace pass/fail checks that only cover a few item types with a category-agnostic relative-scale score across all matched items.
3. Add placement plausibility checks that operate on broad geometry classes such as floor item, wall item, hanging light, and surface item.
4. Rank or reject candidates by aggregate diagnostics rather than by a single narrow rule.

This keeps the existing internal route surface and worker topology intact while moving quality logic into reusable analysis helpers.

## File Map

### Modify
- `application/render/scale_validation_support.py`
- `application/render/furnished_generation_stage.py`
- `application/render/render_result_stage.py`
- `application/render/furniture_specs_stage.py`
- `application/render/render_analysis_stage.py`
- `tests/test_scale_validation_support.py`
- `tests/test_internal_live_quality_contracts.py`
- `tests/test_internal_scale_contracts.py`

### Reuse As Locked Inputs
- `tests/fixtures/internal_live_case_99b7e7db.json`
- `tests/fixtures/internal_scale_case_114358b6.json`
- `outputs/live_retest_99b7e7db_after_fix.json`
- `outputs/scale_debug/latest_internal_scale_job_114358b6.json`

## Ordered Tasks

### Task 1: Generalize Item Identity And Match Coverage

Objective:
- Ensure every source item either gets a stable render match or produces an explicit unmatched diagnostic.

Work:
- Rework the detection-map matching path in `application/render/scale_validation_support.py` so it prefers persistent identity fields in a fixed order and records why a match failed.
- Add diagnostics for unmatched critical items instead of silently returning success when match coverage is weak.
- Separate category label noise from identity matching so bad detector labels do not erase source-item accountability.

Verification:
- Add tests proving that noisy detected labels still preserve `target_key`-based matching.
- Add tests proving unmatched critical items produce diagnostics rather than silent pass.

### Task 2: Replace Narrow Pairwise Rules With Relative-Scale Families

Objective:
- Score scale quality across all matched items, not just rugs and tiny lamps.

Work:
- Add generalized relative-scale rules in `application/render/scale_validation_support.py` for:
  - anchor-to-secondary width ratios
  - anchor-to-secondary height ratios
  - surface-item-to-support ratios
  - low-profile floor-item footprint ratios
- Keep rules category-family-based, not SKU-based.
- Preserve per-rule diagnostics so final selection can explain why a candidate is weak.

Verification:
- Extend `tests/test_scale_validation_support.py` with mixed-category cases that do not reuse the exact live bad-case numbers.
- Keep `99b7e7db` as one regression, but add at least one control fixture and at least one synthetic mixed-item fixture.

### Task 3: Add Placement Plausibility Scoring

Objective:
- Catch obviously implausible placements even when raw scale ratios look acceptable.

Work:
- Classify items into broad placement families:
  - floor furniture
  - floor lighting
  - wall decor
  - surface items
- Add generic plausibility checks such as:
  - wall decor should attach to wall regions, not floor clusters
  - floor items should intersect floor-support zones
  - tiny lights should not dominate the primary anchor in image height
  - rugs should remain under or around seating/table groupings rather than become room-scale islands
- Feed placement diagnostics into candidate selection instead of only using binary validator failure.

Verification:
- Add focused tests for wall decor, floor lamp, rug, and side-table placement families.
- Confirm diagnostics stay structured in render response metadata.

### Task 4: Rank Candidates By Aggregate Quality, Not First Pass

Objective:
- Choose the least-bad or best candidate using aggregate quality diagnostics instead of accepting the first candidate that dodges current narrow rules.

Work:
- In `application/render/furnished_generation_stage.py`, compute a candidate quality score from:
  - guide-leak status
  - unmatched critical item count
  - scale rule failures
  - placement plausibility failures
  - direct-reference fidelity failures
- Update response metadata in `application/render/render_result_stage.py` so selected variant reasoning is visible.
- Keep best-effort behavior: if all candidates are imperfect, still return the highest-scoring furnished render instead of the empty room.

Verification:
- Add regression tests that confirm candidate ranking prefers lower-diagnostic variants.
- Confirm empty-room fallback only occurs on true generation failure, not because all furnished candidates are merely imperfect.

## Verification Strategy

Run after each task:

```powershell
python -m pytest tests\test_scale_validation_support.py tests\test_internal_live_quality_contracts.py tests\test_internal_scale_contracts.py -q
```

Run before claiming completion:

```powershell
python -m pytest tests\test_internal_scale_contracts.py tests\test_scale_validation_support.py tests\test_internal_live_quality_contracts.py tests\test_detail_chain_contracts.py -q
python -m unittest tests.test_internal_web_static_contracts tests.test_internal_web_itemized_flow_contracts tests.test_internal_render_upload_validation tests.test_internal_render_form_parser tests.test_internal_itemized_render_payloads tests.test_route_helpers tests.test_route_surface_smoke tests.test_detail_metadata tests.test_detail_chain_contracts tests.test_external_route_contracts
```

Live validation:
- Re-run the exact `99b7e7db` local dataset through `/async/render`.
- Compare the selected result against the current furnished replay result in `outputs/live_retest_99b7e7db_after_fix.json`.
- Confirm the selected result remains furnished and shows improved broad scale/placement behavior.
- Re-check the archived control case `114358b6` to ensure no regression from generalized rules.

## Risks And Open Assumptions

- Detection noise is still the largest structural risk. If post-render localization remains unstable, quality scoring can still drift even with better rules.
- Over-penalizing imperfect but acceptable candidates could reintroduce empty-room fallback unless candidate ranking stays best-effort.
- Broad placement families must stay simple; too many family-specific thresholds will become disguised SKU overfitting.
- This plan assumes internal render only. External `/api/external/render/cart` and `/api/external/render/preset` contracts remain untouched.

## Acceptance Criteria

- Validator exceptions must fail closed with explicit diagnostics. No validator path may silently return success on internal errors.
- Match coverage must be explicit for all complete items. Every complete item must end up either in `matched_items` or in `unmatched_items`; ambiguous label-only duplicates must not count as covered.
- Primary anchor resolution must be explicit. If requested selectors miss, diagnostics must record whether a fallback anchor was used.
- Label-only matching is allowed only for unique unlabeled items and unique detected labels. Duplicate label-only cases must surface as coverage failure.
- Placement checks must stay family-based. New rules may target broad families such as `wall_attached`, `rug`, and `floor_placed`, but must not target named SKUs or fixture-specific ids.
- Candidate ordering must prefer lower-diagnostic furnished variants, while preserving best-effort furnished output over empty-room fallback.
