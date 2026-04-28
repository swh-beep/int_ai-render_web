# 2026-04-14 Hard Scale Contract Plan

## Goal
- Make internal renders treat room dimensions and furniture dimensions as a strict contract, not a soft prompt hint.
- Make inter-item ratios derive from a single immutable scale plan shared by generation, validation, replay, and QC.
- Run replay/QC after each task and use the result to decide the next correction.

## Architecture Summary
- Current pipeline:
  - input dims -> prompt context
  - image generation
  - noisy post-render detection
  - best-effort fallback selection
- Target pipeline:
  - strict internal scale contract
  - immutable scale plan
  - generation with scale plan context
  - measurement against the same scale plan
  - repair from measured failures
  - harder selection/QC

## File Map
- `application/render/render_scale_stage.py`
- `application/render/render_analysis_stage.py`
- `application/render/render_room_workflow.py`
- `application/render/render_workflow_contracts.py`
- `application/render/furniture_specs_stage.py`
- `application/render/furnished_generation_stage.py`
- `application/render/scale_validation_support.py`
- `application/render/render_response_stage.py`
- `tools/replay/internal_render_replay.py`
- `tests/test_internal_scale_contracts.py`
- `tests/test_scale_validation_support.py`
- `tests/test_internal_live_quality_contracts.py`

## Ordered Tasks
1. Add strict internal scale contract.
   - Internal requests must surface `strict_scale` and `strict_scale_ready`.
   - Ready means: valid room dims, full W/D/H on all items, deterministic anchor.
   - Verification:
     - unit tests for readiness calculation
     - replay report includes contract flags
2. Build immutable scale plan.
   - Persist room dims, room planes, wall span, anchor item, and per-item target ratios.
   - Reuse the same plan in replay/debug output.
   - Verification:
     - unit tests for scale plan contents
     - replay report includes scale plan
3. Replace validator center with plan-vs-measurement checks.
   - Compare measured candidate boxes against scale plan ratios rather than only ad hoc family rules.
   - Keep existing rules as compatibility checks, but base weighting on scale plan mismatches.
   - Verification:
     - regression tests for anchor width, rug footprint, tiny-item height, relative height
4. Tighten selection for strict internal mode.
   - If a candidate has full diagnostics, use weighted scale-plan violations for selection.
   - Preserve best-effort fallback only as explicit last resort, but expose exact failure reason in response/replay.
   - Verification:
     - unit tests for selector behavior
5. Replay/QC loop after each task.
   - Run `tools/replay/internal_render_replay.py` on `tests/replay_cases/9ffde1c0/manifest.json`
   - Record:
     - selected result reason
     - primary width vs room width
     - rug vs anchor footprint
     - tiny lamp vs anchor height
     - whether diagnostics are complete or truncated
   - Use that evidence to choose the next correction.

## Verification Strategy
- Focused:
  - `python -m pytest tests\test_internal_scale_contracts.py tests\test_scale_validation_support.py -q`
- Replay:
  - `python tools/replay/internal_render_replay.py tests/replay_cases/9ffde1c0/manifest.json --report-path <...>`
- QC:
  - compare selected result image and replay report after each task

## Risks and Open Assumptions
- Exact physical scale still cannot be guaranteed while the core renderer is a single generative image call.
- Room planes remain model-estimated, so height-zone checks are approximate.
- Selection can become more honest before the generated image becomes better; early tasks may increase failure visibility before they improve output quality.
