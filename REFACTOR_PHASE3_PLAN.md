# Refactor Phase 3 Plan

## Goal

Move the AI render project from a "contract-preserving refactor baseline" to a more maintainable architecture baseline without changing external API contracts.

Phase 3 is not about feature expansion. It is about closing the remaining structural and validation gaps that were left after Phase 1 and Phase 2.

## Current State

As of March 13, 2026:

- Phase 1 and Phase 2 refactor work are complete
- protected render/detail/media flows are validated
- external preset/cart targeting and volume metadata are preserved
- `main_backup.py` has been removed as dead backup code

The remaining issues are now concentrated in four areas:

1. video/Kling correctness and deterministic validation
2. oversized callable dependency boundary in `run_render_room_workflow(...)`
3. residual `main.py` responsibility sink
4. missing small, repeatable automated tests for critical refactored paths

## Guardrails

Phase 3 should continue under the same stability rules unless a slice explicitly says otherwise:

- no route path changes
- no request model changes
- no response payload shape changes
- no queue payload contract changes
- no provider prompt behavior changes except where required to fix a concrete bug
- each slice must be independently reversible

## Non-Goals

Phase 3 should not:

- redesign product behavior
- change the protected external API contract
- rewrite the whole app into a new framework layout
- replace Redis/RQ end-to-end unless a later slice explicitly targets video job durability

## Proposed Approach

Keep the same safe-slice style used in earlier phases, but change the emphasis:

- fix correctness bugs first
- add deterministic tests before large structural movement where possible
- reduce boundary width before splitting more files
- keep each slice small enough to validate immediately

## Planned Slices

### Slice 1: Video Correctness Hardening

Target:

- Kling/Freepik dynamic source generation path

Work:

- fix the malformed default `KLING_ENDPOINT`
- add a targeted non-static source-generation validation path
- add failure-path checks for Kling task creation and polling
- keep current video route payloads unchanged

Files likely involved:

- `main.py`
- `infrastructure/ai/freepik_kling_client.py`
- `application/video/source_generation_workflow.py`
- `live_validate_render_flows.py`

Validation:

- `py_compile`
- targeted pytest for Kling client and source-generation workflow
- protected video route validation rerun

### Slice 2: Render Workflow Dependency Boundary

Target:

- `application/render/render_room_workflow.py`

Work:

- replace the current large callable dependency bag with a smaller typed dependency container
- reduce the number of function-level parameters crossing from `main.py`
- keep `render_room(...)` request/response behavior identical

Files likely involved:

- `application/render/render_room_workflow.py`
- new render service/context module
- `main.py`

Validation:

- `py_compile`
- protected render flow live validation

### Slice 3: `main.py` Responsibility Reduction

Target:

- route/bootstrap/job organization

Work:

- split route groups out of `main.py`
- isolate job entrypoints and queue helpers more clearly
- leave `main.py` as app assembly, config bootstrap, and compatibility surface

Candidate extraction groups:

- render/detail routes
- media routes
- video routes
- job entrypoints / queue integration helpers

Validation:

- `py_compile`
- protected route live validation

### Slice 4: Deterministic Regression Coverage

Target:

- critical refactored support modules and boundary helpers

Work:

- add a real `tests/` suite
- cover box remap and target metadata correctness
- cover external preset/cart negative and limit cases
- cover auth and invalid-input route helpers where feasible
- cover video failure/status propagation behavior without requiring live provider calls

Priority modules:

- `application/render/postprocess_support.py`
- `application/render/render_postprocess_stage.py`
- `application/details/detail_result_stage.py`
- `application/details/regenerate_detail_resolution.py`
- `render_route_services.py`
- `request_helpers.py`
- `preset_helpers.py`

Validation:

- `pytest`
- existing protected-flow live validation kept as a final integration backstop

## Suggested Execution Order

Recommended order:

1. Slice 1
2. Slice 4 (minimal test scaffold before wider movement)
3. Slice 2
4. Slice 3

Reason:

- Slice 1 fixes a concrete correctness risk now
- Slice 4 adds guardrails before deeper architectural movement
- Slice 2 narrows the render boundary before more file extraction
- Slice 3 becomes safer once the boundary and tests are improved

## Risks

Primary risks:

- runtime dependency drift while shrinking workflow signatures
- import cycles during route extraction
- false confidence if video validation still only exercises static clips
- over-refactoring `main.py` without enough new tests

Mitigations:

- keep slices small
- validate after each slice
- prefer typed containers over ad hoc parameter growth
- add focused tests before large route decomposition

## Exit Condition

Phase 3 is complete when:

- Kling dynamic video path is correctly configured and deterministically tested
- `run_render_room_workflow(...)` no longer exposes a broad callable dependency surface
- `main.py` is reduced further and no longer acts as the default sink for new logic
- a repeatable `tests/` suite exists for critical refactored paths
- protected live validation still passes without payload regressions

## Completion Status

As of March 13, 2026, Phase 3 is complete.

### Slice 1: Video Correctness Hardening

Completed:

- fixed malformed default `KLING_ENDPOINT`
- added `build_kling_endpoint(...)`
- hardened dynamic source-generation failure propagation
- added targeted non-static source-generation validation path

Primary files:

- `infrastructure/ai/freepik_kling_client.py`
- `application/video/source_generation_workflow.py`
- `live_validate_render_flows.py`
- `main.py`

### Slice 4: Deterministic Regression Coverage

Completed:

- added a repeatable `tests/` suite using `unittest`
- covered Kling client behavior, dynamic source-generation success/failure propagation, render postprocess metadata, detail metadata, route helper behavior

Primary files:

- `tests/test_video_kling.py`
- `tests/test_render_postprocess.py`
- `tests/test_detail_metadata.py`
- `tests/test_route_helpers.py`

Note:

- `pytest` was not available in the local virtual environment, so the deterministic suite was implemented with `unittest` instead

### Slice 2: Render Workflow Dependency Boundary

Completed:

- replaced the large callable bag on `run_render_room_workflow(...)` with typed request/dependency containers
- reduced direct function-level dependency crossing from `main.py`

Primary files:

- `application/render/render_workflow_contracts.py`
- `application/render/render_room_workflow.py`
- `main.py`

### Slice 3: `main.py` Responsibility Reduction

Completed:

- moved queue/job execution bodies into `application/job_entrypoints.py`
- extracted queue route bodies into `application/http/queue_route_handlers.py`
- kept `main.py` route decorators and compatibility wrappers stable
- fixed Pydantic v2 deprecation in `render_route_services.py` by replacing `.dict()` with `.model_dump()`
- changed queue-route dependency assembly to lazy construction so validation monkeypatches on `main.py` continue to work

Primary files:

- `application/job_entrypoints.py`
- `application/http/queue_route_handlers.py`
- `render_route_services.py`
- `main.py`

## Final Validation

Phase 3 final validation passed on March 13, 2026.

Validation set:

- `python -m py_compile`
- `.venv\\Scripts\\python.exe -m unittest discover -s tests -v`
- `.venv\\Scripts\\python.exe live_validate_render_flows.py`

Confirmed in the final protected-flow rerun:

- internal render/detail/media flows passed
- external preset/cart flows passed
- static video source generation passed
- dynamic video source generation passed
- final video compile passed

Final status: `PASS`
