# Internal Render Scale Remediation Plan

> For agentic workers: execute this plan task-by-task with review gates. Each task must follow the sequence: failing tests first, implementation, local verification, spec review, code-quality review, then next task.

## Execution Status

- Completed on 2026-04-09 (Asia/Seoul).
- Task 3 review gate closed after the mixed-incomplete payload bug, `primary_scale` anchor mismatch, and low-profile threshold brittleness were fixed.
- Task 4 regression verified with fresh evidence.
- Verification evidence:
  - Focused validator suite: `27 passed`
  - Main regression suite: `69 passed`
  - Internal/external unittest smoke set: `57 tests OK`
  - Known bad case replay checks: `4 passed`
- Sandbox note:
  - The workspace sandbox blocks pytest's default tmpdir/tempfile behavior under the available Python runtime.
  - Verification used a runtime-only temp-directory monkeypatch for pytest-based suites and a runtime-only `tempfile` shim for the unittest smoke set.

**Goal:** Restore real scale validation in the internal `/async/render` production path, preserve usable best-effort outputs across retry loops, and tighten validator correctness so the previously reviewed defects are actually removed.

**Non-goals**
- Do not change the external `/api/external/render/cart` contract.
- Do not change the external `/api/external/render/preset` contract.
- Do not reopen the internal web itemized UI refactor itself.
- Do not add new public response fields to external routes.

**Decision lock**
- Every item now requires all three dimensions: `width_mm`, `depth_mm`, `height_mm`.
- This applies to `mirror`, `poster`, `art`, `rug`, and all other categories.
- Do not add or preserve any `2D-safe` fallback rule in the validator.

## Findings To Remediate

1. Production scale validation is effectively dead because geometry does not reach the real workflow path.
2. Best-effort retry can lose the last usable image if a later retry render returns `None`.
3. Validator identity resolution can drift after filtering incomplete items.
4. Validator thresholds are permissive enough to let obviously overscaled items pass.
5. Generator-to-validator diagnostics handoff is still more fragile than it should be.

## Expected Files

### Modify
- `application/render/render_analysis_stage.py`
- `application/render/render_room_workflow.py`
- `application/render/furnished_generation_stage.py`
- `application/render/scale_validation_support.py`
- `application/render/render_variant_stage.py`
- `tests/test_internal_scale_contracts.py`
- `tests/test_scale_validation_support.py`
- `tests/test_external_route_contracts.py` if a no-leak assertion needs to expand

### Optional
- `application/render/room_analysis.py`
- `live_validate_render_flows.py`

## Task 1: Restore The Production Geometry Path

**Scope**
- Make geometry produced by room analysis reachable in the real internal render workflow.
- Make scale validation callable even when `room_planes` is unavailable, as long as scale-check prerequisites otherwise exist.
- Do not change external routes.

**Files**
- `application/render/render_analysis_stage.py`
- `application/render/render_room_workflow.py`
- `application/render/furnished_generation_stage.py`
- `tests/test_internal_scale_contracts.py`

- [ ] **Step 1: add failing tests that prove production geometry is not currently reaching the validator.**

Checks:
- `run_render_analysis_stage(...)` exposes `room_planes` and `wall_span_norm` when room analysis returns them.
- `run_render_room_workflow(...)` uses analysis geometry instead of falling back to empty scale-stage defaults.

- [ ] **Step 2: add a failing test that proves internal scale validation does not skip solely because `room_planes` is missing.**

Checks:
- `enable_scale_check=True`
- `furniture_specs_json` exists
- `room_dims_parsed` exists
- validation still runs for width/depth/height-based rules even when `room_planes=None`

- [ ] **Step 3: run the focused test module and confirm the red state.**

Run:
```powershell
python -m pytest tests\test_internal_scale_contracts.py -v
```

- [ ] **Step 4: implement the geometry handoff from analysis to workflow.**

Implementation direction:
- extend the analysis-stage result object with `room_planes` and `wall_span_norm`
- preserve existing behavior when geometry is unavailable
- ensure workflow-level variant generation receives analysis geometry when present

- [ ] **Step 5: loosen the validator entry gate so `room_planes` is not a hard requirement.**

Implementation direction:
- gate on `enable_scale_check`, `furniture_specs_json`, and parsed room dimensions
- let the validator decide which rules can run when `room_planes` is absent

- [ ] **Step 6: rerun Task 1 tests and confirm green.**

Run:
```powershell
python -m pytest tests\test_internal_scale_contracts.py -v
```

## Task 1 Review Gate

- [ ] **Spec review:** confirm the production dead path is actually removed, not only mocked around in tests.
- [ ] **Code-quality review:** confirm no external contract leak and no new workflow coupling regression.

## Task 2: Preserve Best-Effort Output And Stabilize Diagnostics

**Scope**
- Preserve the last usable rendered image through later retry failures.
- Reduce fragile string-based reconstruction of diagnostics where possible.

**Files**
- `application/render/furnished_generation_stage.py`
- `application/render/render_variant_stage.py`
- `tests/test_internal_scale_contracts.py`

- [ ] **Step 1: add a failing test where an early retry produces a usable image, scale fails, and a later retry render returns `None`.**

Expected:
- final best-effort result still returns the earlier usable image path

- [ ] **Step 2: add a failing test for diagnostics handoff stability.**

Expected:
- `scalecheck_failed_rules` contains stable rule ids only
- free-form messages stay in `scalecheck_issues`
- later retries do not erase the best structured failure data unintentionally

- [ ] **Step 3: run the focused Task 2 tests and confirm red.**

Run:
```powershell
python -m pytest tests\test_internal_scale_contracts.py -v
```

- [ ] **Step 4: implement best-effort path preservation.**

Implementation direction:
- track `last_success_path` separately from the current retry result
- return the last usable success path if later renders fail

- [ ] **Step 5: tighten diagnostics handoff.**

Implementation direction:
- prefer structured `failed_rules` propagation over parsing message strings
- keep variant normalization safe for scalar and list metadata shapes

- [ ] **Step 6: rerun Task 2 tests and confirm green.**

Run:
```powershell
python -m pytest tests\test_internal_scale_contracts.py -v
```

## Task 2 Review Gate

- [ ] **Spec review:** confirm best-effort semantics match the approved policy.
- [ ] **Code-quality review:** confirm diagnostics are more structured and not more fragile.

## Task 3: Tighten Validator Correctness Under The 3-Dimension Rule

**Scope**
- Fix identity drift after filtering incomplete items.
- Enforce the new all-items-require-W/D/H rule.
- Tighten height ratio thresholds so obvious overscale cases fail.

**Files**
- `application/render/scale_validation_support.py`
- `tests/test_scale_validation_support.py`

- [ ] **Step 1: add a failing test that reproduces primary identity drift.**

Case:
- one incomplete item before the primary target
- one label-only primary item
- one oversized item later in the list
- helper-level matching succeeds, but the full validator loses the primary identity and incorrectly passes

- [ ] **Step 2: add a failing test that proves every item now requires `width_mm`, `depth_mm`, and `height_mm`.**

Case:
- `mirror`, `poster`, `art`, `wall-mounted`, and `rug` behave exactly like every other item
- any item with `height_mm=0` is treated as incomplete and excluded from validation
- no `2D-safe` fallback path is introduced

- [ ] **Step 3: add a failing test for overly permissive height thresholds.**

Checks:
- clearly oversized observed ratios fail even when expected ratios are small
- current floor constants do not mask obvious failures

- [ ] **Step 4: run the validator test module and confirm the red state.**

Run:
```powershell
python -m pytest tests\test_scale_validation_support.py -v
```

- [ ] **Step 5: fix primary identity resolution and complete-item filtering.**

Implementation direction:
- use stable identity mapping between helper-level matches and full validation
- do not allow filtered-item re-enumeration to drift from original item identity

- [ ] **Step 6: remove 2D-safe completeness exceptions from the validator.**

Implementation direction:
- validator completeness requires `width_mm > 0`, `depth_mm > 0`, `height_mm > 0` for every item
- do not add category-specific exceptions back in
- `rug` follows the same 3-dimension rule as every other item

- [ ] **Step 7: tighten height-ratio threshold logic.**

Implementation direction:
- fail when the observed ratio is clearly above expected ratio plus tolerance
- reduce or remove magic floor constants that soften obviously bad outputs

- [ ] **Step 8: rerun the validator test module and confirm green.**

Run:
```powershell
python -m pytest tests\test_scale_validation_support.py -v
```

## Task 3 Review Gate

- [ ] **Spec review:** confirm the validator now reflects the new W/D/H-for-all rule.
- [ ] **Code-quality review:** confirm fixes do not destabilize duplicate matching, stale target keys, or existing rug coverage.

## Task 4: Full Regression And Confidence Pass

**Scope**
- Re-run the integrated regression surface.
- Reconfirm external route contracts are unchanged.
- Recheck the known bad internal fixture.

**Files**
- `tests/test_external_route_contracts.py` if coverage expansion is needed
- `live_validate_render_flows.py` only if a lightweight live confidence pass is added

- [ ] **Step 1: run the scale/detail/external regression set.**

Run:
```powershell
python -m pytest tests\test_internal_scale_contracts.py tests\test_scale_validation_support.py tests\test_render_postprocess.py tests\test_detail_chain_contracts.py tests\test_external_route_contracts.py tests\test_route_surface_smoke.py tests\test_route_helpers.py tests\test_detail_metadata.py -v
```

- [ ] **Step 2: rerun the internal web and route unittest smoke set.**

Run:
```powershell
python -m unittest tests.test_internal_web_static_contracts tests.test_internal_web_itemized_flow_contracts tests.test_internal_render_upload_validation tests.test_internal_render_form_parser tests.test_internal_itemized_render_payloads tests.test_route_helpers tests.test_route_surface_smoke tests.test_detail_metadata tests.test_detail_chain_contracts tests.test_external_route_contracts
```

- [ ] **Step 3: re-check the known bad case `114358b6-3d75-4cb9-bfa2-fc9b938e1655`.**

Checks:
- fixture or replay path shows the expected scale diagnostics
- best-effort policy still preserves a usable output when retries fail scale checks

- [ ] **Step 4: confirm external no-leak behavior one more time.**

Checks:
- `/api/external/render/cart` response shape is unchanged
- `/api/external/render/preset` response shape is unchanged
- external responses do not expose new internal scale diagnostics fields

## Completion Criteria

- Production internal workflow reaches scale validation through the real geometry path.
- Best-effort retry keeps the last usable render output.
- Validator reflects the all-items-require-W/D/H rule.
- Overscale cases like the reviewed internal example are caught more reliably.
- External `/cart` and `/preset` contracts remain unchanged.
