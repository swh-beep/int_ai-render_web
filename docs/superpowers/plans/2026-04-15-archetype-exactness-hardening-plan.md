# 2026-04-15 Archetype Exactness Hardening Plan

## Goal
- 목표는 단순히 “괜찮아 보이는 렌더”가 아니라 다음 4가지를 동시에 만족하는 엔진으로 올리는 것이다.
  1. 내부 explicit room dims 입력에서는 공간치수와 가구치수가 결과에 정확히 반영된다.
  2. 외부 `/cart`, `/preset` 계약은 절대 변경하지 않는다.
  3. 외부 no-dims 입력에서도 내부적으로 room-dimension contract를 생성해 일관된 geometry QC를 수행한다.
  4. 가구 디테일은 제품명 하드코딩이 아니라 일반화된 아키타입 전략으로 보존한다.

## Architecture Summary

### 1. Boundary Freeze
- 외부 API 경계는 그대로 유지한다.
  - `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\api_models.py`
  - `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\main.py`
  - `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\render_route_services.py`
  - `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\http\queue_route_handlers.py`
- exactness 관련 새 정보는 외부 request/response schema에 추가하지 않는다.
- branching은 route 바깥이 아니라 render workflow 내부에서만 일어난다.

### 2. Canonical Contracts
- 현재 분산된 geometry/fidelity 정보를 아래 4개 canonical contract로 정리한다.
  - `RoomDimsContract`
  - `SceneGeometryContract`
  - `ProductArchetypeContract`
  - `PlacementGeometryContract`
- 현재 `scale_plan`, `scene_contract`, `placement_plan`, `layout_envelope`, `placement_contract`로 흩어진 규칙은 최종적으로 한 방향 데이터 흐름으로 수렴시킨다.

### 3. Deterministic First, Model Review Last
- 생성 전에 고정해야 하는 것
  - room size
  - camera/wall span 기반 normalized geometry
  - anchor selection
  - item-to-room ratio
  - item-to-item ratio
  - allowed placement bands
- 생성 후에는 “Gemini가 맞는지 다시 말해주는 심사”가 아니라 “측정 -> 계약 비교 -> 필요한 경우만 repair” 구조로 바꾼다.
- Gemini 기반 review는 마지막 unresolved item에만 사용한다.

### 4. Family Hardcode 금지, Archetype Strategy 사용
- 제품명/SKU/job id 기준 분기 금지
- 대신 아래 generalized archetype strategy를 사용한다.
  - `topology_sensitive_seating`
  - `support_geometry_object`
  - `thin_floor_footprint_object`
  - `reflective_wall_object`
  - `tiny_absolute_scale_object`
  - `block_storage_object`
- category는 strategy classifier의 한 입력일 뿐, 최종 전략 결정은 dims + product features + placement role까지 같이 본다.

### 5. QC Selection Policy
- strict/internal explicit-dims path에서는 `all_failed_weighted_fallback`을 성공처럼 반환하지 않는다.
- external no-dims path는 `range_based_geometry_mode`로 운영하되, item-to-item ratio와 critical fidelity는 계속 강하게 본다.
- selection은 무조건 `review -> repair -> re-measure -> final choose` 순서로 고정한다.

## File Map

### Existing files to modify
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\reference_features_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\room_dimension_estimation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scene_contract_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\product_identity_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\placement_plan_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furniture_specs_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_room_workflow.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furnished_generation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_validation_support.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_postprocess_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_response_stage.py`

### New files to add
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\geometry_contract_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\archetype_strategy_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\geometry_measurement_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\repair_strategy_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\qc_gate_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tools\replay\exactness_qc_replay.py`

### Tests to add or expand
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_geometry_contract_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_archetype_strategy_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_geometry_measurement_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_repair_strategy_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_qc_gate_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_external_route_contracts.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_internal_exactness_contracts.py`

## Ordered Tasks

### Phase 0. Safety Rails and Replay Baseline
#### Task 0.1 Freeze external boundary
- `/api/external/render/cart`, `/api/external/render/preset` request/response snapshot tests를 추가/보강한다.
- exactness 관련 내부 필드는 절대 외부 response에 새지 않게 막는다.
- `GET /jobs/{job_id}` finished payload도 external contract gate에 포함한다.
- 모든 phase는 external enqueue response + job poll payload를 같이 검증한다.

#### Task 0.2 Build stable replay matrix
- 최소 3개 replay case를 고정한다.
  - internal explicit dims hard case
  - external cart no-dims case
  - external preset case
- 각 case는 결과 이미지 3장 + selected result + diagnostics를 남긴다.

#### Task 0.3 Fix known contract corruption before deeper work
- `primary W/D/H missing`가 왜 발생하는지 먼저 수정한다.
- `primary_item`, `placement_plan.anchor_item_key`, `furniture_specs_json`의 dims sync를 강제한다.

#### Verification
- `pytest tests\test_external_route_contracts.py tests\test_internal_exactness_contracts.py -q`
- `python tools\replay\exactness_qc_replay.py ...`

---

### Phase 1. Canonical Geometry Contract Unification
#### Task 1.1 Add `geometry_contract_stage.py`
- room dims, anchor, pairwise ratios, absolute clamps, placement bands를 하나의 solver-ready contract로 합친다.
- 입력:
  - `room_dims_contract`
  - `scene_contract`
  - `placement_plan`
  - analyzed items
- 출력:
  - `geometry_contract`

#### Task 1.2 Remove duplicated ratio sources
- validator가 `scale_plan`, `layout_envelope`, `placement_contract.room_ratio_targets`, `scene_contract.pairwise_ratio_contracts`를 따로따로 참고하지 않게 정리한다.
- 최종 참조원은 `geometry_contract` 하나로 통일한다.

#### Task 1.3 Strict readiness gate
- internal explicit-dims path에서 `geometry_contract`가 incomplete면 generation 시작 전 실패시킨다.

#### Verification
- `pytest tests\test_geometry_contract_stage.py -q`
- internal explicit-dims replay에서 `strict_scale_contract_not_ready`가 사라지는지 확인

---

### Phase 2. Room Geometry Calibration for No-Dims Flows
#### Task 2.1 Upgrade `room_dimension_estimation_stage.py`
- explicit / estimated / unknown 삼분 구조는 유지하되, estimation 결과를 range+confidence만 남기는 게 아니라 calibration metadata까지 포함하게 바꾼다.
- 포함 항목:
  - `camera_height_estimate`
  - `horizon_band`
  - `floor_contact_band`
  - `wall_attachment_band`
  - `wall_span_norm`
  - `anchor_basis`

#### Task 2.2 Estimated-dims policy split
- external no-dims는 exact mm를 주장하지 않는다.
- 대신:
  - room-fit은 range-based
  - item-to-item ratio는 strict
  - tiny absolute items는 absolute clamp + confidence penalty

#### Verification
- `pytest tests\test_room_dimension_estimation_stage.py tests\test_geometry_contract_stage.py -q`
- external no-dims replay에서 internal strict path와 다른 policy가 타는지 확인

---

### Phase 3. Product Identity를 Archetype Contract로 승격
#### Task 3.1 Add `archetype_strategy_stage.py`
- `product_identity`를 바로 prompt로 넣지 말고, 먼저 generalized archetype contract로 변환한다.
- 출력 예시:
  - `structural_archetype`
  - `part_graph`
  - `topology_invariants`
  - `support_constraints`
  - `surface_semantics`
  - `allowed_variation`
  - `forbidden_substitutions`

#### Task 3.2 Expand identity extraction
- `reference_features_stage.py`, `product_identity_stage.py`에서 아래가 빠지지 않게 한다.
  - negative space / openings
  - support count and arrangement
  - top shape
  - frame exposure
  - reflective face vs border
  - rug footprint shape and border pattern

#### Task 3.3 Stop relying on weak text merges
- 지금처럼 free-text label/description에 cue를 섞어 희석시키는 경로를 줄인다.
- crop-derived feature confidence가 높으면 text-derived cue보다 우선한다.

#### Verification
- `pytest tests\test_product_identity_stage.py tests\test_archetype_strategy_stage.py -q`
- replay artifact 기준 critical items의 archetype fields non-empty 확인

---

### Phase 4. Pre-Generation Deterministic Placement Solver
#### Task 4.1 Solve before render
- `placement_plan_stage.py`를 “zone suggestion” 수준에서 “deterministic placement target” 수준으로 올린다.
- per-item target:
  - expected normalized width
  - expected normalized height
  - floor contact band
  - wall attachment band
  - anchor-relative width/height ratio
  - allowed overlap tolerance

#### Task 4.2 Strategy binding
- 각 item은 `archetype_strategy`에 따라 render strategy를 받는다.
- 예:
  - `preserve_topology`
  - `preserve_support_geometry`
  - `preserve_reflective_outline`
  - `preserve_footprint`
  - `preserve_absolute_micro_scale`

#### Verification
- `pytest tests\test_placement_plan_stage.py tests\test_archetype_strategy_stage.py -q`
- replay JSON에 item별 deterministic targets가 남는지 확인

---

### Phase 5. Measurement Layer 분리
#### Task 5.1 Add `geometry_measurement_stage.py`
- post-render 측정을 Gemini prompt에 덜 의존하게 분리한다.
- 우선순위:
  1. deterministic mask/bbox extraction
  2. classical/cv measurement
  3. Gemini fallback only for unresolved items

#### Task 5.2 Replace validator inputs
- `validate_scale_from_detection_map()`가 free-form detection text보다 `geometry_measurement_stage` 결과를 먼저 쓰게 바꾼다.

#### Task 5.3 Measure all contract dimensions
- 현재 validator가 놓치는 항목을 전부 `geometry_contract`와 직접 비교하게 한다.
  - pairwise ratios
  - small item absolute clamps
  - wall occupancy
  - floor footprint
  - collision/spacing

#### Verification
- `pytest tests\test_geometry_measurement_stage.py tests\test_scale_validation_support.py -q`
- replay에서 `unmatched_source_items`가 measurement fallback 감소와 함께 줄어드는지 확인

---

### Phase 6. Archetype-Aware Repair Strategy
#### Task 6.1 Add `repair_strategy_stage.py`
- product-specific family hardcode 대신 archetype-driven repair strategy를 만든다.
- 예:
  - `topology_sensitive_repair`
  - `support_geometry_repair`
  - `reflective_surface_repair`
  - `footprint_rescale_repair`
  - `tiny_absolute_scale_repair`

#### Task 6.2 Localized repair target selection rewrite
- 현재 family priority 고정 순서를 제거한다.
- 새 우선순위는:
  - severity
  - measurement confidence
  - item importance
  - archetype strictness

#### Task 6.3 Repair budget control
- 모든 item을 무한정 재검토하지 않게 한다.
- critical unresolved item만 repair 대상이 되고, review budget을 명시적으로 가진다.

#### Verification
- `pytest tests\test_repair_strategy_stage.py tests\test_internal_scale_contracts.py -q`
- replay runtime이 줄고, selected candidate가 후반 hanging 없이 종료되는지 확인

---

### Phase 7. QC Gate Rewrite
#### Task 7.1 Add `qc_gate_stage.py`
- geometry QC와 fidelity QC를 분리된 gate로 둔다.
- geometry fail과 fidelity fail을 구분해 결과 선택에 반영한다.

#### Task 7.2 Remove least-bad success illusion
- internal strict explicit-dims path에서는 `all_failed_weighted_fallback`을 성공처럼 반환하지 않는다.
- external no-dims path는 fallback을 허용하되 QC 레벨을 명시적으로 남긴다.

#### Task 7.3 Guide leak hard fail
- `scale_guide_leak_detected`는 review budget을 더 쓰기 전에 candidate 폐기로 처리한다.

#### Verification
- `pytest tests\test_qc_gate_stage.py tests\test_render_postprocess.py -q`
- replay에서 selected result reason이 stricter policy를 따르는지 확인

---

### Phase 8. Iterative Replay and QC Loop
#### Task 8.1 Per-phase live replay
- 각 주요 phase 끝날 때마다 아래를 반복한다.
  - replay 실행
  - 결과 이미지 3장 저장
  - failed rules 분석
  - 왜 성공/실패했는지 memo 작성
- memo는 `docs\superpowers\plans\...` 하단에 append한다.

#### Task 8.2 Overfitting guard
- 특정 case만 좋아지고 control case가 나빠지면 그 phase는 merge하지 않는다.
- 체크 항목:
  - internal explicit-dims case
  - external cart no-dims case
  - external preset case

#### Verification
- replay matrix 3개 모두 실행
- selected result + failed rules + runtime + unmatched count 비교표 작성

---

### Phase 9. Deployment Gate
#### Task 9.1 Final acceptance criteria
- internal explicit-dims case에서:
  - room width/height 계약 위반 없음
  - rug footprint, tiny absolute item, anchor ratios 위반 없음
  - critical topology/material drift 없음
- external no-dims case에서:
  - contract unchanged
  - runtime budget 내 완료
  - item-to-item ratio 위반 감소

#### Task 9.2 Rollout plan
- internal audience first
- external cart next
- external preset last

#### Verification
- final replay matrix
- contract smoke
- local manual render smoke

## Verification Strategy
- 모든 phase는 세 층으로 검증한다.
  1. unit/contract test
  2. replay harness
  3. actual image QC
- 과최적화 방지를 위해 phase별로 holdout case를 둔다.
  - internal explicit-dims hard case
  - external cart no-dims case
  - external preset case
  - archetype holdout case 1개 이상
- holdout은 제품명/sku를 직접 참조하지 않고 archetype bucket 기준으로 분리한다.
- replay harness는 항상 아래를 남긴다.
  - selected result reason
  - variant diagnostics
  - unmatched items
  - weighted issue score
  - runtime
  - 결과 이미지 3장
- 품질 평가는 “좋아 보인다”가 아니라 아래 기준으로 본다.
  - room/item ratio contract
  - item-to-item ratio contract
  - archetype invariant preservation
  - unresolved item count
  - runtime budget

## Risks and Open Assumptions
- 단일 사진 기반 external no-dims path는 본질적으로 exact mm 보장이 어렵다.
  - 이 경로는 exactness가 아니라 confidence-bearing geometry contract로 운영해야 한다.
- 현재 pipeline은 Gemini 기반 generation을 유지한다.
  - deterministic contract를 넣어도 완전한 물리적 일치를 100% 보장하지는 못한다.
- 따라서 strict/internal explicit-dims path와 external no-dims path의 acceptance bar는 다르게 가져가야 한다.
- 비디오 관련 파일은 현재 다른 에이전트가 수정 중일 수 있으므로 이 플랜 범위에서 제외한다.

## Recommended Execution Order
1. Phase 0
2. Phase 1
3. Phase 2
4. Phase 3
5. Phase 4
6. Phase 5
7. Phase 6
8. Phase 7
9. Phase 8
10. Phase 9

이 순서를 택한 이유는 external no-dims calibration contract를 늦게 붙이면 geometry/repair/QC 레이어에 internal-only 가정이 스며들어 이후 retrofitting 비용이 커지기 때문이다.
