# 2026-04-14 Generalized Repair / Validation Plan

## Goal
- Remove case-driven family priority bias from repair and validation.
- Replace narrow family-only unmatched/fidelity rules with taxonomy defaults plus family overrides.
- Separate replay/debug tooling from product code so live QC does not depend on case-specific scripts.

## Tasks
1. Replace repair target ordering with weighted scoring.
   - Inputs: issue severity, match confidence, item importance.
   - Family can only act as an override multiplier, never as the primary sort key.
2. Generalize validation diagnostics.
   - Emit common issue records and weighted rule details for all taxonomy items.
   - Keep family-specific rules only as additive overrides.
3. Generalize variant review and selection.
   - Aggregate weighted issues instead of fixed family penalty ladders.
   - Keep `best_effort` only as explicit all-fail fallback with clearer reason codes.
4. Separate replay harness from product code.
   - Move case manifests and replay entrypoint out of `outputs/`-owned ad hoc scripts.
   - Keep product tests focused on logic, not live artifact reconstruction.
5. QC.
   - Run focused regression.
   - If environment allows, replay the live `9ffde1c0` case with the generalized harness and compare output images.

## Review Gates
- Gate A: repair scoring no longer uses family-first ordering.
- Gate B: validation emits common unmatched / confidence / placement / scale / fidelity rules.
- Gate C: replay harness no longer depends on case-specific monkeypatch scripts in `outputs/`.
- Gate D: regression green and live replay result captured if network permits.
