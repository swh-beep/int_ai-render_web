# Render Engine Master Design

**Date:** 2026-03-31

**Owner:** Codex planning session

## Goal

Create a long-horizon development design for the render engine that preserves all existing service contracts while improving:

1. efficient refactoring
2. physically credible scale realism
3. faithful use of product image and dimension inputs
4. internal placement accuracy
5. internal image-studio intent adherence
6. natural multi-angle internal detail cuts

## Product Surfaces That Must Not Break

The following surfaces are protected and must keep their existing request and response contracts:

- internal web app
- `/api/external/render/cart`
- `/api/external/render/preset`

Protected contract means:

- route paths remain unchanged
- request field names remain unchanged
- response field names and nesting remain unchanged
- Redis/RQ queue semantics remain unchanged unless separately re-approved
- storage and public URL semantics remain unchanged unless separately re-approved

## Locked Assumptions

- External `/cart` and `/preset` product dimensions are considered operationally trustworthy because they are entered from real furniture specs.
- Shared test assets live under [localtest_image](/Users/User/Desktop/AI 프로젝트/localtest_image).
- The standard room-photo baseline uses fixed room dimensions of `10000 x 5500 x 3000 mm`.
- Product-level dimension references already exist in the current project path or input chain and should be treated as the primary truth source when present.
- Deployment is blocked until all planned verification gates pass. No ad hoc commit, push, or rollout should happen before the full verification stack is clean.

## Problem Statement

The current engine is already useful and connected to live services, but quality control is still mostly prompt-driven instead of enforcement-driven.

The main observed gaps are:

- scale validation exists in code but is not reliably active
- product dimensions are preserved in metadata but not strongly enforced in image verification
- placement intent is parsed but not geometrically verified after generation
- internal image-studio modes behave mostly as prompt variants, not as distinct products with distinct acceptance rules
- internal detail cuts 1 to 3 often fail to look like natural photographs from different viewpoints in the same space
- `main.py` is still the primary composition choke point, which slows safe iteration

## Design Principles

### 1. Contract safety over elegance

Do not use schema changes to solve quality or architecture issues.

### 2. Enforcement over instruction

Prompts can help, but final acceptance must come from validators, repeated runs, and artifact review.

### 3. Image-first verification

This engine ships images, not only JSON. Visual quality is part of correctness.

### 4. Incremental migration

Refactor only in slices that can be verified independently.

### 5. Agent-owned QC

Intermediate QC is owned by the agent. The user reviews only final candidate sets and final residual risks.

## Current-State Summary

### Architecture

- `main.py` still owns too much composition and dependency wiring.
- Route handlers are thinner than before, but route dependencies are still assembled centrally.
- Render, detail, and media workflows are partially extracted into `application/...`, which is the right direction.

### Quality

- Scale realism is the most critical quality gap because validation is not consistently enforceable.
- Dimension fidelity and object identity are partially represented in metadata, but not fully scored against image outputs.
- Placement adherence for internal flows is still mostly text-guided.
- Internal detail angles and image-studio modes do not have sufficiently strong post-generation validation.

### QC and release gating

- The repo already contains a useful QA harness, review-sheet generation, and repeated-run packaging.
- The current system is advisory rather than truly release-blocking.
- Artifact retention is at risk because `outputs/` is mutable and subject to cleanup.

## Target Outcome

The target engine should behave like a contract-safe image system with four layers:

1. protected route surface
2. deterministic workflow and normalization stages
3. quality validators with retry and rejection logic
4. repeatable QA and release gating

## Recommended Strategy

Use a dual-track program:

- quality-enforcement track first
- architecture-reduction track second

The quality-enforcement track unlocks goals 2 through 6.
The architecture-reduction track makes those improvements safer to maintain and ship.

## Master Tracks

### Track A. Contract Freeze

Purpose:

- lock all protected surfaces before high-risk changes

Primary files:

- [application/http/queue_route_handlers.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/http/queue_route_handlers.py)
- [render_route_services.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/render_route_services.py)
- [api_models.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/api_models.py)
- [tests/test_route_helpers.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/tests/test_route_helpers.py)
- [live_validate_render_flows.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/live_validate_render_flows.py)

Design:

- add contract tests for request and response shapes
- add protected response snapshots for cart, preset, internal render, detail, and image-studio async routes
- explicitly freeze status-code behavior and queue-result payload semantics

Exit condition:

- any contract regression is caught automatically before quality work continues

### Track B. QC System Reconstruction

Purpose:

- make image quality observable, repeatable, and blockable

Primary files:

- [quality_qa_runner.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/quality_qa_runner.py)
- [live_validate_render_flows.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/live_validate_render_flows.py)
- [shared/quality_qa_support.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/shared/quality_qa_support.py)
- [shared/quality_review.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/shared/quality_review.py)

Design:

- standardize per-run packaging for every tracked case
- separate smoke validation from repeated-run QA
- add machine scoring layers for:
  - grid leak
  - scale realism
  - dimension fidelity
  - placement adherence
  - detail drift
  - edit mask preservation
  - studio-mode rule compliance
- archive final QA bundles outside volatile cleanup paths

Repeat policy:

- baseline case: 3 runs
- intermittent or high-risk case: 5 runs
- release-candidate set: 7 runs where needed

Exit condition:

- QA becomes a real stop/go gate, not just a reporting utility

### Track C. Scale Engine Recovery

Purpose:

- make main-render scale physically credible and enforceable

Primary files:

- [application/render/render_scale_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/render_scale_stage.py)
- [application/render/render_analysis_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/render_analysis_stage.py)
- [application/render/scale_validation_support.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/scale_validation_support.py)
- [application/render/furnished_generation_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/furnished_generation_stage.py)
- [application/render/render_room_workflow.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/render_room_workflow.py)

Design:

- treat internal room dimensions as absolute truth
- treat external product dimensions as trusted truth and combine them with room-photo geometry estimation
- build a real room-geometry model sufficient for wall span, depth regime, floor extent, and anchor inference
- define enforceable ratio bands for:
  - furniture to wall span
  - furniture to floor occupancy
  - furniture to neighboring furniture
- run post-generation scale scoring and reject outputs that violate hard bounds

Success criteria:

- large furniture no longer shrinks for aesthetic empty space
- room-filling products read as room-filling
- furniture-to-furniture hierarchy remains plausible

### Track D. Product Image and Dimension Fidelity

Purpose:

- make the final outputs visibly match the real products and their dimensions

Primary files:

- [render_route_services.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/render_route_services.py)
- [application/render/furniture_specs_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/furniture_specs_stage.py)
- [application/render/postprocess_support.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/postprocess_support.py)
- [application/details/detail_generation_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/details/detail_generation_stage.py)
- [application/details/detail_result_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/details/detail_result_stage.py)

Design:

- normalize every product into a trusted internal representation:
  - identity
  - dimensions
  - category
  - aspect prior
  - quantity
  - volume rank
- use trusted dimensions as hard constraints, not descriptive hints
- compare main-render crops and detail outputs for drift
- reject outputs where product identity or scale visibly diverges from the trusted reference

Success criteria:

- real products remain recognizable
- dimension relationships remain visible
- detail outputs stay faithful to the same object and same room

### Track D-2. Internal Detail Cuts 1 to 3 Natural Angle Redesign

Purpose:

- make detail cuts 1 to 3 look like natural photos shot from different viewpoints in the same room

Primary files:

- [application/details/detail_generation_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/details/detail_generation_stage.py)
- [application/details/detail_style_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/details/detail_style_stage.py)
- [application/details/detail_result_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/details/detail_result_stage.py)
- [application/details/detail_workflow.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/details/detail_workflow.py)

Design:

- define each cut as a camera intent, not just a style prompt
- preserve room identity, object identity, structural continuity, and placement continuity across the set
- generate cuts as a coherent set:
  - cut 1: natural alternative hero angle
  - cut 2: closer but still plausible viewpoint
  - cut 3: complementary angle with consistent architecture
- explicitly forbid:
  - fake zoom crops
  - warped room geometry
  - object-size drift between cuts
  - contradictory wall, window, or furniture positions

QC criteria:

- angle diversity
- room identity lock
- camera plausibility
- no fake crop
- set consistency

Success criteria:

- the three detail cuts read as a believable photo set from the same space

### Track E. Internal Placement Enforcement

Purpose:

- make user placement intent visibly correct in internal web app outputs

Primary files:

- [application/render/placement_support.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/placement_support.py)
- [application/render/furnished_generation_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/furnished_generation_stage.py)
- [application/render/render_analysis_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/render/render_analysis_stage.py)

Design:

- convert placement text into structured constraints
- express side, wall anchor, centering, clearance, near-window, avoid-window, and spacing rules explicitly
- score final outputs against those constraints
- reject outputs that silently recenter or restage against user intent

Success criteria:

- left, right, center, back-wall, and window-related requests become visually reliable

### Track F. Internal Image Studio Mode Separation

Purpose:

- make frontal, edit, and decorate behave like separate products with separate guarantees

Primary files:

- [static/js/image_studio.js](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/static/js/image_studio.js)
- [application/http/queue_route_handlers.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/http/queue_route_handlers.py)
- [application/media/frontal_generation_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/media/frontal_generation_stage.py)
- [application/media/image_edit_generation_stage.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/media/image_edit_generation_stage.py)

Design:

- frontal:
  - treat as multi-photo reconstruction
  - optimize for spatial consistency and realistic camera position
- edit:
  - treat as targeted transformation
  - preserve non-target areas and non-target objects
- decorate:
  - treat as additive staging
  - preserve room structure and existing scene continuity
- add mode-specific postchecks instead of shared prompt-only behavior

Success criteria:

- each mode has distinct acceptance rules and measurable pass/fail outcomes

### Track G. Efficient Refactoring

Purpose:

- reduce architecture friction without destabilizing protected flows

Primary files:

- [main.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/main.py)
- [application/job_entrypoints.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/job_entrypoints.py)
- [application/http/queue_route_handlers.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/application/http/queue_route_handlers.py)
- [storage_helpers.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/storage_helpers.py)
- [request_helpers.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/request_helpers.py)
- [preset_helpers.py](/Users/User/Desktop/AI 프로젝트/int_ai-render_web/preset_helpers.py)

Design:

- reduce `main.py` by removing composition-only helpers first
- keep workflow logic where it already has a stable home
- move route registration into dedicated router registration helpers only after contract tests are frozen
- extract shared runtime wiring only after quality tracks have a stable verification loop

Migration order:

1. contract freeze
2. pure helper extraction
3. route registration extraction
4. runtime wrapper extraction
5. residual utility cleanup

Success criteria:

- `main.py` becomes an application composition shell
- quality work becomes easier to test and review

## QC Framework

### Machine gates

1. contract gate
2. artifact existence gate
3. repeated-run convergence gate
4. scale realism gate
5. product fidelity gate
6. placement adherence gate
7. detail-angle consistency gate
8. image-studio mode gate

### Agent QC

The agent performs first-pass visual review using the standard ratings:

- `clear_fail`
- `borderline`
- `acceptable`
- `strong`

The agent owns intermediate QC and rejects runs that should not reach the user.

### User QC

The user reviews only:

- final candidate outputs
- final report
- final residual-risk summary

## Stop/Go Release Gates

Do not permit release if any of the following is true:

- protected schema changed
- smoke validation failed
- required artifact bundle missing
- any tracked case has a `clear_fail`
- repeat count is below the required threshold
- budget cap is exceeded
- output bundle is not archived for later inspection

Release is allowed only when:

- protected surfaces remain unchanged
- all deterministic tests pass
- smoke validation passes
- repeated-run QA passes
- agent QC produces no hard failures
- final evidence bundle is archived

## Multi-Agent Operating Model

### Explorer agents

Use for:

- failure clustering
- surface-specific investigation
- QA bucket design
- regression triage

### Worker agents

Use for:

- bounded implementation slices with disjoint write scopes

### Reviewer agents

Use for:

- contract review
- regression review
- QC evidence review

### Parallelism rule

Only run write-capable agents in parallel when their write scopes do not overlap.

## Document Consolidation Policy

The existing refactor and quality-plan documents should not be deleted immediately.

They should be:

1. superseded by this master design
2. cross-checked for any still-useful constraints
3. folded into a future implementation plan
4. removed only after the implementation plan is accepted and no critical guidance remains stranded in older files

## Recommended Phase Order

1. Track A. Contract Freeze
2. Track B. QC System Reconstruction
3. Track C. Scale Engine Recovery
4. Track D. Product Image and Dimension Fidelity
5. Track D-2. Internal Detail Cuts 1 to 3 Natural Angle Redesign
6. Track E. Internal Placement Enforcement
7. Track F. Internal Image Studio Mode Separation
8. Track G. Efficient Refactoring
9. Final release-candidate verification

## Why This Order

- Quality must become measurable before it can improve safely.
- Scale realism is upstream of nearly every other image-quality judgment.
- Product fidelity and natural detail cuts depend on stable scale.
- Placement and image-studio adherence should be enforced after the shared render foundation becomes less noisy.
- Refactoring should support the quality work, not compete with it.

## Scope Boundaries

### In scope

- contract-preserving engine improvement
- quality validators
- repeated QA
- detail-angle redesign
- internal placement accuracy
- image-studio adherence
- safe architecture reduction

### Out of scope

- changing public contracts
- rewriting the engine from scratch
- unbounded model-provider migration as part of the same program
- deployment before full verification completion

## Approval Gate

After this design is accepted:

- write one implementation plan that translates the tracks above into executable task slices
- then decide which historical plan documents can be archived or removed

