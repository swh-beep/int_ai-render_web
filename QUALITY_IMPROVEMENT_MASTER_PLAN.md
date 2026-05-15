# Quality Improvement Master Plan

## Goal

Improve the image quality reliability of the current render engine without changing any protected service contract.

This is not a feature rewrite plan. This is a quality stabilization plan for an engine that is already connected to live services.

## Non-Negotiables

The following must not change unless explicitly re-approved later:

- Route paths
- Request schema field names
- Response JSON field names or nesting
- RQ and Redis usage semantics
- S3 persistence and public URL semantics
- External preset and cart flow contracts
- Existing service wiring and route surface behavior

Primary guardrail document:

- `REFACTOR_CONTRACTS.md`

## Problem Framing

The five reported issues are not classic "wrong answer" bugs. They are quality failures or quality drift in generated images.

That means:

- code-level correctness is necessary but not sufficient
- the final image itself must be part of the acceptance criteria
- one lucky run is not enough because the issues are intermittent
- human visual review must be formalized, not treated as an informal final glance
- intermediate quality review will be owned by the agent, not pushed to the user
- the user is expected to review only the final report and final result set

## Catalog Diversity Assumption

This engine must generalize across a large production catalog, not a tiny demo set.

Planning assumptions:

- more than 10,000 furniture combinations exist in real usage
- thousands of sofa variants exist
- hundreds of lighting variants exist
- geometry, silhouette, category mix, material finish, and room pairing are highly variable

Therefore:

- no code change may be justified only because it improves one or two local sample assets
- local samples are only entry points for debugging and regression capture
- acceptance must be based on generalized behavior across a deliberately diverse stress set

## Target Issues

1. Scale-guide grid lines sometimes leak into the final main render.
2. Detail shots sometimes make furniture appear abnormally oversized.
3. Main render scale sometimes feels subtly unrealistic versus the real room dimensions.
4. Internal render placement instructions are sometimes not followed well enough.
5. Internal image edit sometimes does not follow the user prompt well enough.

## Working Principle

Every improvement slice must use both of these evaluation tracks:

1. Machine-checkable validation
2. Human visual validation

Neither track alone is enough.

In this plan, "human visual validation" means agent-owned visual QC during development and a user-owned final acceptance pass only at the end.

## Quality Review Rubric

Each tracked case must be reviewed against a fixed rubric.

### Human Review Ratings

Use exactly one of these ratings per criterion:

- `clear_fail`
- `borderline`
- `acceptable`
- `strong`

### Human Visual Criteria

Issue 1: grid leak

- If any visible grid line or guide color remains in the final main render, the case is `clear_fail`.

Issue 2: detail oversize

- If the target furniture in the detail shot looks noticeably larger than the same object in the main render, beyond what the crop alone explains, the case is `clear_fail`.

Issue 3: subtle scale realism

- In a 3m wall with a 2.9m sofa, the sofa should read as almost wall-filling with minimal side breathing room.
- A 1500mm storage unit should not visually read as wall-spanning in the same room unless the room dimensions support that.
- If a large furniture item feels miniaturized just to create empty space, the case is `clear_fail`.

Issue 4: placement adherence

- If a prompt requests left/right/center/back-wall/window-side placement and the final result violates the intended side or anchor, the case is `clear_fail`.

Issue 5: edit adherence

- If the requested remove/replace/rearrange/resize change is not clearly visible in the output, the case is `clear_fail`.

### QC Ownership

During execution:

- the agent performs the ongoing visual QC
- the user is not asked to review intermediate outputs
- the user receives only the final report, representative result images, and residual-risk summary

That means every phase must leave behind evidence strong enough for the agent to justify its own pass or fail decision.

### Machine-Checkable Criteria

- image output exists
- expected output count exists
- critical metadata exists when already part of the current contract
- repeated-run failure rate is tracked
- scale-check failure count is tracked
- detail target metadata consistency is tracked
- mask-outside preservation can be measured for masked edit flows

## Required QA Artifacts

For every tracked QA run, save the following under `outputs/qa_runs/...`:

- input image
- empty-room result
- scale-guide image if generated
- all main render variants
- selected final main render
- detail outputs
- edit outputs
- cutout references used by the run
- manifest JSON
- metadata snapshot JSON
- human review sheet

For comparison-ready review, create a simple board layout with:

- source
- guidance artifact
- final output
- overlay or metadata summary

## Repetition Policy

Because the issues are intermittent, each important case must be run multiple times.

- normal tracked case: at least 3 runs
- intermittent or high-risk case: at least 5 runs
- phase-exit candidate set: enough repeated runs to establish a visible trend, not a single anecdotal win

Do not accept a slice based on a single successful run.

## Test Budget Cap

Hard cap for the entire quality-improvement task:

- total model-call budget: 200
- count by actual external model invocation attempt, not by top-level HTTP request
- Gemini: count each `generate_content(...)` attempt
- failover and retry attempts count separately
- do not cross the cap even if a phase is incomplete

Initial budget allocation:

- Phase 0 QA foundation: 20
- Phase 1 grid leak and scale realism: 95
- Phase 2 detail oversize: 40
- Phase 3 placement adherence: 25
- Phase 4 edit adherence: 15
- reserve: 5

Operational rule:

- every phase slice must record budget before and after repeated validation
- if a slice overruns its allocation, stop and reassess before continuing
- use the reserve only for ambiguity resolution, regression confirmation, or recovery from intermittent drift

## Baseline Cases

Use the current local test assets first:

- `../localtest_image/room_photo.png`
- `../localtest_image/customize_moodboard.png`
- `../localtest_image/preset_product_1.png`
- `../localtest_image/preset_product_2.png`
- `../localtest_image/preset_product_3.png`
- `../localtest_image/preset_product_4.png`

Then add purpose-built scale and placement stress cases as needed.

Important:

- these assets are for bootstrap and regression capture only
- they are not the target distribution
- no logic may be specialized to these exact images or these exact products

## Generalization Strategy

Every phase must test against a diversity matrix, not just a fixed happy-path set.

### Diversity Matrix Axes

- category mix: sofa, chair, table, storage, bed, rug, light, decor-heavy mixes
- size regime: undersized, near-wall-filling, dense multi-item, sparse large-item
- geometry: straight, curved, modular, low-profile, tall storage, thin-leg, bulky massing
- quantity: single hero item, balanced set, crowded composition
- room scale: small room, medium room, large room, narrow wall span, wide wall span
- placement demand: left, right, centered, back-wall anchored, window-adjacent, spacing-sensitive
- edit demand: remove only, replace only, resize only, rearrange only, mixed-intent edit
- audience path: internal and external where relevant

### Sampling Policy

- start with local assets for reproducibility
- expand to a rotating stress set that represents the diversity matrix
- keep adding failure cases found during development into the stress set
- do not let one category dominate the evidence set just because it is easier to reproduce

### Anti-Overfit Rule

Reject any change that improves a narrow sample while increasing the chance of category-specific heuristics such as:

- product-type specific sizing hacks
- hard-coded assumptions tied to one furniture silhouette
- logic tailored to a single room photo or one fixed moodboard layout
- prompt clauses that implicitly favor one sample style at the expense of broader behavior

## Relevant Code Areas

Main render:

- `application/render/render_room_workflow.py`
- `application/render/render_analysis_stage.py`
- `application/render/render_variant_stage.py`
- `application/render/furnished_generation_stage.py`
- `application/render/furniture_specs_stage.py`
- `application/render/render_response_stage.py`
- `application/render/scale_validation_support.py`

Detail generation:

- `application/details/detail_workflow.py`
- `application/details/detail_generation_stage.py`
- `application/details/detail_style_stage.py`
- `application/details/detail_result_stage.py`

Internal image edit:

- `application/media/image_edit_generation_stage.py`
- `infrastructure/ai/gemini_prompts.py`

Validation harness:

- `live_validate_render_flows.py`
- `tests/test_render_postprocess.py`
- `tests/test_detail_metadata.py`
- `tests/test_route_helpers.py`

## Phase Plan

### Phase 0: QA Foundation

Goal:

- make image quality regressions observable and comparable before changing behavior

Tasks:

- extend local QA output collection without changing public contracts
- save manifest and metadata snapshots per run
- define review sheets for all 5 issues
- add comparison board generation for human review
- add repeated-run execution support for intermittent issues
- add a diversity-matrix-based stress set definition
- add a way to tag QA runs by category mix, scale regime, placement regime, and edit regime

Definition of done:

- one command can produce a QA run package
- one QA package includes both machine-readable metadata and human review surfaces
- one QA package makes it clear which diversity-matrix bucket the run belongs to

### Phase 1: Fix Issue 1 and Issue 3 Together

Goal:

- remove grid leak risk and improve real-world scale fidelity in the main render path

Why together:

- both issues come from the same main-render scale guidance and size interpretation path

Tasks:

- separate scale-guide artifacts from generation-facing constraints
- strengthen numeric scale constraints
- lock priority order for dimensions: requested dims, then parsed dims, then bounded fallback
- review primary anchor selection and volume fallback behavior
- reconnect or harden scale validation without changing route contracts
- validate both internal and external paths under the same contract-safe framework
- confirm improvements across multiple furniture categories, not only the bootstrap sofa cases

Definition of done:

- no visible grid leak in tracked cases
- scale stress cases improve by the human rubric
- public response schema remains unchanged
- improvements hold across the chosen diversity buckets instead of one narrow fixture set

### Phase 2: Fix Issue 2

Goal:

- make detail images behave like controlled derivatives of the main render rather than loosely regenerated alternatives

Tasks:

- prefer main-render target crop and reframing where possible
- reduce unnecessary generative freedom in detail creation
- verify target geometry against the main render
- preserve detail metadata that already exists in the current contract
- test oversized-detail regressions across multiple target types such as sofa, storage, chair, and light-adjacent scenes

Definition of done:

- detail oversize failures drop in repeated runs
- detail outputs read as the same room and same object, not a new scene

### Phase 3: Fix Issue 4

Goal:

- make internal placement instructions structurally enforceable instead of loosely descriptive

Tasks:

- parse placement text into structured constraints
- represent side, anchor wall, centering, spacing, near-window, avoid-window constraints explicitly
- inject those constraints into generation prompts
- validate placement results against the intended spatial rule set
- cover both simple and mixed placement instructions across varied room and product combinations

Definition of done:

- tracked placement cases clearly improve on the human rubric
- placement violations become machine-detectable where practical

### Phase 4: Fix Issue 5

Goal:

- preserve user intent across multi-step edit planning and execution

Tasks:

- replace keyword-only step inference with a real edit planner
- preserve the full instruction while extracting operation, scope, order, and mask use
- reduce intent loss in multi-intent edit prompts
- add before-and-after verification for requested edits
- verify behavior on mixed object categories rather than one repeated sample asset

Definition of done:

- mixed edit requests preserve the intended action set
- prompt non-compliance becomes easier to detect and reproduce

## Execution Order

1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 4

Reason:

- the main render scale path is upstream of the detail path
- detail quality cannot be judged reliably while main render scale is unstable
- placement and edit should be addressed after the shared render-scale core is less noisy

## Multi-Agent Operating Model

Main agent responsibilities:

- protect contracts
- integrate findings
- decide priority and accept or reject a slice
- perform the ongoing visual QC on behalf of the project
- escalate to the user only at final review or if a true blocker appears

Sidecar agent responsibilities:

- QA case collection
- visual rubric refinement
- detail or edit specific analysis
- document cleanup recommendations
- diversity stress-set design and failure clustering

Sub-agents must not propose schema changes as part of this plan unless explicitly re-scoped later.

## Test Strategy

Keep existing tests and add targeted tests around the new quality guardrails.

Keep:

- existing contract and metadata tests
- existing live flow validation

Add:

- dimension parsing tests
- primary anchor selection tests
- scale fallback selection tests
- placement parser tests
- detail target crop tests
- edit planner tests
- QA run manifest checks
- diversity-bucket coverage checks for the stress suite where practical

Note:

- these tests support quality work, but they do not replace image review
- they must not become sample-specific assertions tied to one local furniture image unless the assertion is intentionally a regression lock for a known failure

## Document Cleanup Policy

Do not delete historical planning documents until the new master plan is accepted and the required contract information is confirmed to be preserved elsewhere.

Current keep file:

- `REFACTOR_CONTRACTS.md`

Current cleanup candidates after acceptance:

- `PLAN.md`
- `CHECKLIST.md`
- `CONTEXT.md`
- `REFACTOR_PLAN.md`
- `REFACTOR_PHASE2_PLAN.md`
- `REFACTOR_PHASE3_PLAN.md`
- `REFACTOR_FINAL_REPORT.md`
- `TODAY_RESTART_GUIDE.md`

## Immediate Next Action

Start with Phase 0.

That means:

- build the QA run package structure
- define the review sheet format
- make repeated image quality checks possible before changing engine behavior
- define the diversity matrix and anti-overfit rules before the first behavior change
