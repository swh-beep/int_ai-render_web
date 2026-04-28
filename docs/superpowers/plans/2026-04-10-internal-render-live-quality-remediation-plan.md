# Internal Render Live Quality Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove scale-guide leakage from internal renders, harden live item-scale/fidelity validation, and verify fixes against the exact local test dataset that produced the bad output.

**Architecture:** Keep the current internal `/async/render` surface and itemized payload shape, but split remediation into three layers: generation input cleanup, post-render validation hardening, and live-case regression. The render model should no longer see a raw fluorescent guide image, and the validator should use stable per-item identities plus category-aware pairwise checks so obviously overscaled rugs and tiny lamps cannot silently pass.

**Tech Stack:** FastAPI, RQ worker jobs, Gemini multimodal generation/analysis, Python validation helpers, pytest/unittest, local Redis/S3-backed job results.

---

## Locked Regression Case

Use this exact internal local-web run as the primary confidence case for the full remediation:

- Request ID: `99b7e7db`
- Job ID: `36da5f1f-9c5a-4829-88cb-00005ef4bb94`
- Room dimensions: `4000*4000*2400`
- Input room image:
  - `outputs/raw_1775800687_b83aab93_민정님고객_aitest_origin.png`
- Input item images:
  - `outputs/cart_item_1_1775800687_f8794b6b_sofa.png`
  - `outputs/cart_item_2_1775800687_f8794b6b_암체어.jpg`
  - `outputs/cart_item_3_1775800687_f8794b6b_KKK_AASDtable.png`
  - `outputs/cart_item_4_1775800687_f8794b6b_nas_edition_A_rug_seri.png`
  - `outputs/cart_item_5_1775800687_f8794b6b_test_test_storage.png`
  - `outputs/cart_item_6_1775800687_f8794b6b_nas_edition_tete77.png`
  - `outputs/cart_item_7_1775800687_f8794b6b_papa_longcastle.png`
  - `outputs/cart_item_8_1775800687_f8794b6b_Dds_D_ark_lamp.png`
  - `outputs/cart_item_9_1775800687_f8794b6b_nasedition_5151-ad.png`
- Bad outputs to compare against:
  - `C:/Users/User/Downloads/Result_After_1775801068535.jpg`
  - `C:/Users/User/Downloads/Result_After_1775801069975.jpg`
  - `C:/Users/User/Downloads/Result_After_1775801072007.jpg`
- Scale guide artifact currently leaked from:
  - `outputs/scale_guide_99b7e7db.png`
- Replay determinism fields to pin in the fixture:
  - model name used for Stage 2 furnishing
  - selected variant filename and SHA-256
  - selected result URL at failure time
  - source item `target_key -> input file path` mapping
  - source item `target_key -> requested_dims_mm` mapping
  - any saved validation/debug metadata that existed on the failed run

Use at least one known-good internal case as a control case during final verification so this remediation does not overfit only the bad room:

- Control case:
  - `tests/fixtures/internal_scale_case_114358b6.json`
  - its previously archived result/debug images under `outputs/scale_debug/`

## Non-Negotiable Constraints

- Do not change external `/api/external/render/cart` request or response contracts.
- Do not change external `/api/external/render/preset` request or response contracts.
- Do not reintroduce moodboard upload into the internal web flow.
- Keep the internal web item payload rule: every item requires `width_mm`, `depth_mm`, `height_mm`.

## Success Criteria

1. No selected internal render result may contain the visible yellow scale grid.
2. The side table reference with a rounded-triangle black top and chrome cantilever frame must survive generation without major topology drift.
3. In the locked regression case, the `1000x1000` rug must render only slightly larger than the `900x900` coffee table, not close to sofa-scale.
4. In the locked regression case, the `100x100x100` glowing lamp must remain visibly tiny relative to the sofa, sideboard, and arc lamp.
5. The chosen result must expose enough internal diagnostics to explain why it passed or failed validation.

## Expected Files

### Modify
- `application/render/render_analysis_stage.py`
- `application/render/furnished_generation_stage.py`
- `application/render/scale_validation_support.py`
- `application/render/render_result_stage.py`
- `application/render/furniture_specs_stage.py`
- `tests/test_internal_scale_contracts.py`
- `tests/test_scale_validation_support.py`
- `tests/test_detail_chain_contracts.py` if result metadata changes affect detail handoff

### Create
- `tests/fixtures/internal_live_case_99b7e7db.json`
- `tests/test_internal_live_quality_contracts.py`

### Optional
- `live_validate_render_flows.py`

## Task 1: Freeze The Exact Live Regression Fixture

**Files:**
- Create: `tests/fixtures/internal_live_case_99b7e7db.json`
- Create: `tests/test_internal_live_quality_contracts.py`
- Modify: `application/render/render_result_stage.py`

- [ ] **Step 1: Capture the exact locked regression metadata as a local fixture.**

Fixture contents:
- room dimensions
- selected result URL
- selected result artifact hash
- stage-2 model/config identity
- `furniture_data`
- `volume_ranking`
- item `target_key` values
- source item dimensions
- source item file mapping
- if present, saved validation/debug metadata

- [ ] **Step 2: Write a failing test that proves the current live regression case is bad.**

Assertions:
- selected result carries a `scale_guide_url`
- rug width ratio versus sofa is far above expected
- tiny lamp height ratio versus sofa is far above expected

- [ ] **Step 3: Run the focused regression fixture test and confirm red.**

Run:
```powershell
python -m pytest tests\test_internal_live_quality_contracts.py -v
```

- [ ] **Step 4: Ensure result payloads keep enough validation/debug metadata for live replay.**

Implementation direction:
- keep selected variant index
- keep validation rule ids
- keep per-item matched boxes or a compact diagnostics snapshot
- keep enough data to reproduce which variant passed and why

- [ ] **Step 5: Re-run the focused regression fixture test.**

Run:
```powershell
python -m pytest tests\test_internal_live_quality_contracts.py -v
```

## Task 1 Review Gate

- [ ] **Spec review:** confirm the fixture is the real `99b7e7db` case, not a synthetic approximation.
- [ ] **Code-quality review:** confirm no external response surface changed.

## Task 2: Remove Scale Guide Leakage From Generation

**Files:**
- Modify: `application/render/render_analysis_stage.py`
- Modify: `application/render/furnished_generation_stage.py`
- Modify: `tests/test_internal_scale_contracts.py`
- Modify: `tests/test_internal_live_quality_contracts.py`

- [ ] **Step 1: Write a failing test that proves internal generation still receives the raw guide image.**

Assertion:
- `generate_furnished_room(...)` prompt payload currently includes the guide image object when `scale_guide_path` exists.

- [ ] **Step 2: Write a failing test that proves a guide-contaminated result is not explicitly rejected.**

Assertion:
- current internal best-result path can be returned even if the selected image obviously contains fluorescent guide lines.

- [ ] **Step 3: Run the Task 2 tests and confirm red.**

Run:
```powershell
python -m pytest tests\test_internal_scale_contracts.py tests\test_internal_live_quality_contracts.py -v
```

- [ ] **Step 4: Replace raw guide-image injection with non-renderable guidance.**

Implementation direction:
- stop passing the raw `scale_guide_path` image into the furnishing model
- replace it with text-only spatial guidance derived from room dimensions, wall span, and primary item scale
- preserve the guide image as a debug artifact only

- [ ] **Step 5: Add a post-render leakage guard.**

Implementation direction:
- reject candidate renders using a structural guide-leak detector, not just a yellow-pixel heuristic
- compare against the generated guide geometry/layout or a calibrated guide-signature check
- reject and retry contaminated variants before final ranking

- [ ] **Step 6: Re-run Task 2 tests and confirm green.**

Run:
```powershell
python -m pytest tests\test_internal_scale_contracts.py tests\test_internal_live_quality_contracts.py -v
```

## Task 2 Review Gate

- [ ] **Spec review:** confirm the guide still exists for debugging but can no longer leak into the selected render.
- [ ] **Code-quality review:** confirm no external route behavior changes.

## Task 3: Harden Exact Item Fidelity Checks For Direct References

**Files:**
- Modify: `application/render/furnished_generation_stage.py`
- Modify: `application/render/scale_validation_support.py`
- Modify: `tests/test_internal_live_quality_contracts.py`

- [ ] **Step 1: Write a failing test for direct-reference topology drift.**

Locked check:
- the cantilever side table reference must not be accepted if the rendered crop changes the support topology in a major way

- [ ] **Step 2: Run the focused fidelity test and confirm red.**

Run:
```powershell
python -m pytest tests\test_internal_live_quality_contracts.py -v
```

- [ ] **Step 3: Add a post-render fidelity review step for direct-reference items.**

Implementation direction:
- pair every rendered crop to the exact source reference by `target_key`, with `source_index` as the secondary fallback
- never allow category-only pairing when a stable id exists
- compare rendered crop vs source cutout per matched item
- use a multimodal pass to decide whether silhouette/topology/material are still the same object
- return structured rule ids such as `reference_shape_drift` or `reference_material_drift`

- [ ] **Step 4: Only gate the most identity-sensitive categories first.**

Initial categories:
- `table`
- `floor_lamp`
- `lounge_chair`
- `sofa`

- [ ] **Step 5: Re-run the focused fidelity test and confirm green.**

Run:
```powershell
python -m pytest tests\test_internal_live_quality_contracts.py -v
```

## Task 3 Review Gate

- [ ] **Spec review:** confirm the new fidelity pass catches “different object” drift without requiring pixel-perfect matching.
- [ ] **Code-quality review:** confirm retry behavior is still bounded and diagnostics stay structured.

## Task 4: Replace Weak Scale Validation With Stable Pairwise Checks

**Files:**
- Modify: `application/render/scale_validation_support.py`
- Modify: `application/render/furniture_specs_stage.py`
- Modify: `tests/test_scale_validation_support.py`
- Modify: `tests/test_internal_live_quality_contracts.py`

- [ ] **Step 1: Write failing tests for the locked live regressions.**

Cases:
- `1000x1000` rug versus `900x900` coffee table should fail if observed footprint ratio is far above expected
- `100x100x100` tiny lamp should fail if observed height ratio versus sofa or sideboard is grossly inflated
- duplicate same-category lights with radically different real dimensions must not collapse into one loose “light” expectation

- [ ] **Step 2: Run the validator test modules and confirm red.**

Run:
```powershell
python -m pytest tests\test_scale_validation_support.py tests\test_internal_live_quality_contracts.py -v
```

- [ ] **Step 3: Promote stable identity keys through the validator.**

Implementation direction:
- prefer `target_key` and original `source_index` matches
- stop relying on free-form labels when a stable key exists
- keep pairwise checks on the final matched identity map

- [ ] **Step 4: Add pairwise footprint and height rules beyond the single primary anchor.**

Implementation direction:
- rug vs designated table/centerpiece footprint rule
- tiny-object prominence cap for decor/light items
- pairwise same-category ratio checks when two direct-reference items have extreme size separation

- [ ] **Step 5: Add fallback validation using persisted `furniture_data.box_2d` when the fresh detector is noisy.**

Implementation direction:
- if render-time redetection misses an item, compare against the selected result’s persisted postprocess boxes before declaring success
- if a critical item still has no reliable match after both fresh detection and persisted-box fallback, fail validation with a structured `critical_item_unmatched` rule instead of silently passing

- [ ] **Step 6: Re-run Task 4 tests and confirm green.**

Run:
```powershell
python -m pytest tests\test_scale_validation_support.py tests\test_internal_live_quality_contracts.py -v
```

## Task 4 Review Gate

- [ ] **Spec review:** confirm rug and tiny-lamp regressions are explicitly covered by deterministic checks.
- [ ] **Code-quality review:** confirm stable-key matching does not regress duplicate-item handling.

## Task 5: Validate Against The Same Local Test Data End-To-End

**Files:**
- Modify: `live_validate_render_flows.py` if a helper is needed
- Modify: `tests/test_internal_live_quality_contracts.py` if live replay parsing needs a helper

- [ ] **Step 1: Re-run the internal local-web flow with the exact locked input set.**

Inputs:
- `outputs/raw_1775800687_b83aab93_민정님고객_aitest_origin.png`
- `outputs/cart_item_1_1775800687_f8794b6b_sofa.png`
- `outputs/cart_item_2_1775800687_f8794b6b_암체어.jpg`
- `outputs/cart_item_3_1775800687_f8794b6b_KKK_AASDtable.png`
- `outputs/cart_item_4_1775800687_f8794b6b_nas_edition_A_rug_seri.png`
- `outputs/cart_item_5_1775800687_f8794b6b_test_test_storage.png`
- `outputs/cart_item_6_1775800687_f8794b6b_nas_edition_tete77.png`
- `outputs/cart_item_7_1775800687_f8794b6b_papa_longcastle.png`
- `outputs/cart_item_8_1775800687_f8794b6b_Dds_D_ark_lamp.png`
- `outputs/cart_item_9_1775800687_f8794b6b_nasedition_5151-ad.png`
- room dimensions: `4000*4000*2400`

- [ ] **Step 2: Verify the selected result against the four user-reported failures.**

Checks:
- no visible yellow guide grid
- side table still matches the rounded-triangle top + chrome cantilever form
- rug reads only slightly larger than the coffee table
- tiny glowing lamp stays tiny

- [ ] **Step 3: Run the full regression suite before closing the task.**

Run:
```powershell
python -m pytest tests\test_internal_live_quality_contracts.py tests\test_internal_scale_contracts.py tests\test_scale_validation_support.py tests\test_render_postprocess.py tests\test_detail_chain_contracts.py tests\test_external_route_contracts.py tests\test_route_surface_smoke.py tests\test_route_helpers.py tests\test_detail_metadata.py -v
```

- [ ] **Step 4: Re-check the known-good control case alongside the locked bad case.**

Checks:
- bad case `99b7e7db` no longer shows the four reported failures
- control case `114358b6` does not regress into new false-positive scale/fidelity failures

- [ ] **Step 5: Run the internal/external unittest smoke set.**

Run:
```powershell
python -m unittest tests.test_internal_web_static_contracts tests.test_internal_web_itemized_flow_contracts tests.test_internal_render_upload_validation tests.test_internal_render_form_parser tests.test_internal_itemized_render_payloads tests.test_route_helpers tests.test_route_surface_smoke tests.test_detail_metadata tests.test_detail_chain_contracts tests.test_external_route_contracts
```

- [ ] **Step 6: Archive the new live regression evidence.**

Artifacts:
- selected result image
- rejected contaminated variants if any
- compact diagnostics snapshot
- job id / request id mapping

## Task 5 Review Gate

- [ ] **Spec review:** confirm the same local dataset is the final verification basis.
- [ ] **Code-quality review:** confirm external route contracts stayed unchanged and detail flow still works.

## Final Acceptance Checklist

- [ ] Internal selected render never ships with visible scale guide lines.
- [ ] Direct-reference identity drift is gated for the most important item categories.
- [ ] Pairwise scale rules catch the locked rug and tiny-lamp regression.
- [ ] Result metadata is sufficient to explain validator decisions during live debugging.
- [ ] The exact `99b7e7db` local case is replayed after implementation and compared against the user’s bad outputs.
