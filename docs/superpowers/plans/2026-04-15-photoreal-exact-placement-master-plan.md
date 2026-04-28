# 2026-04-15 Photoreal Exact Placement Master Plan

## Goal
- 목표는 “그럴듯한 인테리어 이미지”가 아니다.
- 목표는 다음 4가지를 동시에 만족하는 결과다.
  1. 입력한 **공간 치수**가 결과 이미지에 물리적으로 맞아야 한다.
  2. 입력한 **가구 치수**가 결과 이미지에 물리적으로 맞아야 한다.
  3. **가구와 가구 사이의 실제 비율**이 정확해야 한다.
  4. **원본 제품의 디테일/형상/재질/패턴**이 결과 이미지에서 동일해야 한다.
- 사용자가 기대하는 기준은 다음이다.
  - 고객이 실제 동일 가구를 동일 공간에 배치하고 사진을 찍었을 때, 결과 이미지와 매우 가깝게 보여야 한다.

## Current State
- 현재 파이프라인은 본질적으로 `scene-wide generation -> detection/remap -> validation -> repair -> least-bad selection` 구조다.
- 이 구조는 “전체 장면을 다시 생성”하는 데 강하고, “같은 제품을 정확히 같은 크기로 넣는 것”에는 구조적으로 약하다.
- 현재 보정으로 좋아진 점은 있다.
  - strict scale contract
  - plan-vs-measurement QC
  - localized repair
  - family-specific rule 일부
- 하지만 아직도 남는 한계는 명확하다.
  - generation 전에 geometry를 **결정적으로 고정**하지 못한다.
  - 제품 fidelity를 **실루엣/토폴로지 수준**으로 고정하지 못한다.
  - post-render detection이 흔들리면 QC도 같이 흔들린다.
  - all-fail 상태에서도 least-bad 결과를 반환할 수 있다.

## Proposed Approach
- 지금 필요한 건 prompt 보강이 아니라 **구조 전환**이다.
- 최종 접근은 아래 5단계 파이프라인이다.

1. `Deterministic Scene Contract`
- 방 치수, 카메라, 평면, 가구별 예상 점유율/높이/앵커 관계를 먼저 고정한다.
- generation은 이 계약을 바꾸지 못하고, 이 계약 안에서만 이미지를 만들어야 한다.

2. `Product Reference Canonicalization`
- 각 가구를 단순 label/category가 아니라 “제품 identity object”로 만든다.
- shape/material/pattern/support geometry/openings/reflection rule까지 구조적으로 추출한다.

3. `Layout-First Placement`
- 먼저 방 안에 가구가 어디에 얼마나 차지해야 하는지 2D/2.5D layout를 결정한다.
- 그 다음에만 각 가구를 배치/삽입/수정한다.
- scene-wide redraw는 주 경로가 아니라 background harmonization 정도로만 남긴다.

4. `Family-Specific Insertion / Localized Repair`
- rug, mirror, sofa, side table, tiny lamp 같은 fidelity-critical family는 공통 generative redraw에 맡기지 않는다.
- family별 insertion/edit/compositing 경로를 둔다.

5. `Hard QC Gate Before Selection`
- geometry, ratio, fidelity가 통과한 후보만 최종 선택 대상으로 올린다.
- strict/internal에서는 critical item이 unresolved면 결과를 성공으로 반환하지 않는다.

## Architecture Summary

### A. Scene Contract Layer
- 새 1급 산출물: `scene_contract`
- 포함 정보:
  - room dimensions
  - floor / wall / ceiling planes
  - camera estimate
  - per-item layout envelope
  - pairwise ratio contract
  - allowed placement zones
  - critical item set
- 이 contract는 generation prompt의 힌트가 아니라 **validator와 insertion engine의 직접 입력**이 된다.

### B. Product Identity Layer
- 새 1급 산출물: `product_identity`
- 포함 정보:
  - product name
  - canonical family
  - exact dims
  - topology cues
  - support geometry
  - openings / gaps
  - pattern cues
  - reflection constraints
  - preserve rules
- 현재 `reference_features`/`identity_profile`는 이 목표에 비해 너무 약하다.

### C. Placement Layer
- 새 1급 산출물: `placement_plan`
- 포함 정보:
  - anchor item placement
  - pairwise relative size
  - floor footprint
  - wall attachment targets
  - small-item absolute size clamps
- 이 단계에서 소파 폭 vs 방 폭, 러그 vs 테이블 footprint, tiny lamp 절대 크기 같은 조건을 먼저 고정한다.

### D. Rendering Layer
- generation 경로를 3개로 나눈다.
  1. empty room / background harmonization
  2. item insertion / family-specific edit
  3. optional global style harmonization
- 핵심은 “가구를 다시 그리는 것”이 아니라 “가구를 같은 제품으로 넣는 것”이다.

### E. QC Layer
- QC는 3축으로 분리한다.
  1. geometry QC
  2. inter-item ratio QC
  3. product fidelity QC
- 이 3개 중 하나라도 critical fail이면 rank 대상에서 제외한다.

## File Map

### Existing files to change
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_analysis_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\reference_features_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\item_analysis_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furnished_generation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_validation_support.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_room_workflow.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_postprocess_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_response_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\empty_room_generation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\main.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\render_route_services.py`

### New modules to add
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scene_contract_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\placement_plan_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\product_identity_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\localized_repair_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\family_repair_support.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\geometry_qc_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\fidelity_qc_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tools\replay\exactness_replay_harness.py`

### Tests and fixtures
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_scene_contract_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_product_identity_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_placement_plan_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_family_repair_support.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_geometry_qc_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_fidelity_qc_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_internal_exactness_contracts.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\replay_cases\9ffde1c0\manifest.json`

## Ordered Tasks

### Phase 0. Success Contract Definition
#### Task 0.1 Goal formalization
- 현재 “좋아 보임” 기준을 버리고 성공 기준을 명문화한다.
- 필수 pass 항목:
  - room width/height ratio tolerance
  - primary item ratio tolerance
  - rug/table ratio tolerance
  - tiny lamp absolute height tolerance
  - mirror wall/reflection consistency
  - sofa topology preservation
  - side table support geometry preservation

#### Task 0.2 Runtime modes definition
- 런타임 모드 3개 정의:
  - `preview_mode`
  - `production_mode`
  - `strict_fidelity_mode`
- internal web 기본은 `strict_fidelity_mode`로 이동.

#### Verification
- `pytest tests\test_internal_exactness_contracts.py -q`
- 문서형 golden config 검증

---

### Phase 1. Evidence & Benchmark Harness
#### Task 1.1 Replay harness generalization
- case-specific replay 스크립트를 완전히 일반화한다.
- 입력:
  - original room
  - original item images
  - exact dims
  - expected critical items
- 출력:
  - final images
  - per-item QC report
  - fail reason table

#### Task 1.2 Exactness benchmark schema
- benchmark report schema 정의:
  - room ratio errors
  - pairwise ratio errors
  - item match confidence
  - fidelity feature preservation
  - unresolved critical items

#### Task 1.3 Baseline snapshot
- 현재 `9ffde1c0`를 baseline으로 고정.
- 이후 모든 phase에서 같은 harness로 replay.

#### Verification
- replay harness가 동일 case를 deterministic하게 다시 돌리는지 확인
- `tools\replay\exactness_replay_harness.py`

---

### Phase 2. Deterministic Scene Contract
#### Task 2.1 scene_contract stage 신설
- `scene_contract` 생성:
  - room planes
  - room dims
  - camera estimate
  - per-item envelope
  - pairwise ratio constraints
  - critical set

#### Task 2.2 analysis -> workflow 계약 연결
- `render_analysis_stage.py`에서 `scene_contract`를 만들고,
- 이후 generation/QC/response까지 그대로 전달.

#### Task 2.3 response/debug persistence
- `render_response_stage.py`에 `scene_contract`와 핵심 측정치 포함.

#### Verification
- `pytest tests\test_scene_contract_stage.py -q`
- `pytest tests\test_internal_scale_contracts.py -q`
- replay 후 contract dump 확인

---

### Phase 3. Product Identity Canonicalization
#### Task 3.1 reference feature schema 확장
- 현재 `silhouette/material/distinctive/preserve`를 넘어서 아래 필드 추가:
  - `topology_cues`
  - `support_geometry`
  - `opening_or_gap_features`
  - `pattern_cues`
  - `reflection_constraints`

#### Task 3.2 weak extraction second pass
- feature가 약하면 second-pass extraction 실행.
- category별 focus prompt 사용:
  - sofa
  - side table
  - rug
  - tiny lamp
  - mirror

#### Task 3.3 crop-derived priority
- text keyword merge를 낮추고 crop-derived feature를 우선한다.
- generic description이 identity를 희석하지 못하게 한다.

#### Verification
- `pytest tests\test_product_identity_stage.py -q`
- baseline case에서 5개 critical item 모두 empty `preserve_rules/distinctive_parts` 금지

---

### Phase 4. Placement Plan Solver
#### Task 4.1 placement_plan stage 신설
- `scene_contract`와 `product_identity`를 입력으로 받아:
  - anchor selection
  - item footprint
  - wall/floor attachment
  - pairwise ratio
  - small-item absolute clamp
  를 산출한다.

#### Task 4.2 anchor-first rules
- primary anchor를 먼저 고정.
- 예:
  - sofa width vs room width
  - table vs sofa
  - rug vs table
  - lounge chair vs sofa
  - tiny lamp absolute size

#### Task 4.3 placement zones
- wall items, floor items, rug, small decor를 zone으로 나눈다.
- placement plan 밖으로 벗어나면 generation 후보로도 채택 금지.

#### Verification
- `pytest tests\test_placement_plan_stage.py -q`
- baseline case에서 plan ratios가 사람이 기대하는 범위인지 수치 검증

---

### Phase 5. Family-Specific Insertion / Repair
#### Task 5.1 generic localized repair 분리
- 현재 `furnished_generation_stage.py` 내부 repair 로직을 `localized_repair_stage.py`로 분리.
- scene-wide redraw와 item-level repair를 분리한다.

#### Task 5.2 family-specific repair support
- `family_repair_support.py` 도입.
- family별 hard rules:
  - sofa: central gap, segmented backrest, arm/base silhouette
  - side table: triangle top, cantilever chrome frame, dual-post support
  - rug: circular footprint, ring/border pattern, no oversized footprint
  - tiny lamp: 3-tier lantern silhouette, absolute-small size clamp
  - mirror: wall-only, outline preservation, reflection consistency

#### Task 5.3 bbox 없는 item의 secondary localization
- unmatched거나 low-confidence면 secondary localization pass 실행.
- repair 전에 bbox를 다시 찾는다.

#### Task 5.4 hybrid insertion path
- fidelity-critical family는 pure generative edit만 쓰지 않는다.
- 우선순위:
  1. masked edit
  2. constrained insertion
  3. limited harmonization
- scene-wide redraw는 마지막 fallback만 허용.

#### Verification
- `pytest tests\test_family_repair_support.py -q`
- replay로 family별 before/after 비교

---

### Phase 6. Hard Geometry / Ratio QC
#### Task 6.1 geometry_qc stage 신설
- generation 후 QC 분리:
  - room vs anchor ratio
  - pairwise ratio
  - attachment violations
  - unresolved critical items

#### Task 6.2 matched-only rule 제거
- rug/tiny lamp/relative-height는 matched bbox에만 의존하지 않게 한다.
- secondary localization bbox 또는 repair bbox와 plan fallback 측정 추가.

#### Task 6.3 hard gate
- geometry fail이면 ranking 대상에서 제외.

#### Verification
- `pytest tests\test_geometry_qc_stage.py -q`
- baseline case에서 oversized rug / oversized tiny lamp를 의도적으로 잡는 테스트 추가

---

### Phase 7. Hard Fidelity QC
#### Task 7.1 fidelity_qc stage 신설
- item-level topology/pattern/material/reflection 보존을 별도 QC로 분리.

#### Task 7.2 critical family policies
- sofa: gap/segmentation drift면 fail
- side table: support geometry drift면 fail
- mirror: outline/reflection drift면 fail
- rug: pattern + footprint drift면 fail
- tiny lamp: silhouette + absolute size drift면 fail

#### Task 7.3 diagnostics persistence
- guide leak, detector miss가 나도 last-known item diagnostics 보존.
- `selected_item_review`가 `unknown`으로 비는 경로 제거.

#### Verification
- `pytest tests\test_fidelity_qc_stage.py -q`
- baseline case에서 critical item drift가 확실히 issue로 남는지 확인

---

### Phase 8. Selection Policy Rewrite
#### Task 8.1 ranking order 수정
- 순서:
  1. geometry gate
  2. fidelity gate
  3. only then aesthetic ranking

#### Task 8.2 best-effort 축소
- strict/internal에서는 critical unresolved가 남아 있으면 성공 반환 금지.
- 필요하면 explicit fail 또는 degraded status.

#### Task 8.3 repair loop policy
- item-level fail이면 scene rerender가 아니라 localized repair 먼저.
- scene-wide retry는 제한적으로만 허용.

#### Verification
- `pytest tests\test_render_postprocess.py tests\test_internal_exactness_contracts.py -q`
- baseline case에서 실패작이 “least bad success”로 뽑히지 않는지 확인

---

### Phase 9. Live QC Loop
#### Task 9.1 Per-task replay loop
- 각 phase가 끝날 때마다 반드시 실행:
  1. replay
  2. 결과 이미지 저장
  3. 원본과 대조
  4. 성공/실패 원인 기록

#### Task 9.2 Human-readable QC report
- report에는 반드시 포함:
  - selected reason
  - per-item pass/fail
  - ratio error table
  - unresolved critical items
  - before/after delta

#### Task 9.3 Stop criteria
- 아래 모두 통과해야 done:
  - sofa topology pass
  - side table geometry pass
  - rug footprint pass
  - tiny lamp absolute size pass
  - mirror fidelity/reflection pass
  - critical unresolved 0

## Verification Strategy

### Automated
- `pytest tests\test_scene_contract_stage.py -q`
- `pytest tests\test_product_identity_stage.py -q`
- `pytest tests\test_placement_plan_stage.py -q`
- `pytest tests\test_family_repair_support.py -q`
- `pytest tests\test_geometry_qc_stage.py -q`
- `pytest tests\test_fidelity_qc_stage.py -q`
- `pytest tests\test_internal_exactness_contracts.py -q`

### Live replay
- baseline case:
  - `tests\replay_cases\9ffde1c0\manifest.json`
- replay output:
  - exactness report JSON
  - selected image
  - all variant images
  - per-item QC table

### Human QC
- side table: support topology가 원본과 같은지
- rug: table 대비 과대하지 않은지
- tiny lamp: 100mm급 small object로 읽히는지
- mirror: outline과 reflection이 같은지
- sofa: 중앙 gap과 silhouette이 같은지

## Risks and Open Assumptions
- 이 목표는 “prompt engineering”으로 끝날 수 없다.
- 현재 모델만으로 exact product fidelity가 안 나올 수 있다.
- 그래서 hybrid insertion/compositing 경로가 필요할 가능성이 높다.
- 2D 단일 이미지 기반 카메라/평면 추정이 틀리면 deterministic layout도 흔들릴 수 있다.
- 그러나 이 리스크는 지금 구조를 그대로 두는 것보다 훨씬 통제 가능하다.

## Final Assessment
- 기존 보정 플랜은 “품질 향상” 플랜이었다.
- 이번 플랜은 “정확도 보장에 가까워지기 위한 구조 전환” 플랜이다.
- 이 목표를 진짜로 노리려면, 이번 플랜 수준으로 구조를 바꾸는 것이 맞다.
