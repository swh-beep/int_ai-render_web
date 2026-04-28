# 2026-04-15 Photoreal Exact Placement Execution Plan

## Goal
- 배포 목표는 아래를 동시에 만족하는 엔진 업그레이드다.
  1. **사내웹 입력**에서는 공간치수와 가구치수가 결과에 정확히 반영된다.
  2. **외부 `/cart`와 `/preset` 계약은 변경하지 않는다.**
  3. 외부 호출에서 공간치수가 없더라도 엔진이 동작한다.
  4. 가구 디테일은 원본 제품과 최대한 동일하게 유지된다.
  5. strict/internal에서는 실패작을 성공으로 반환하지 않는다.

## Architecture Summary

### 1. Boundary Freeze
- `/api/external/render/cart`
- `/api/external/render/preset`
- 위 두 계약은 유지한다.
- request/response schema 변경 없음.
- 모든 업그레이드는 **route 내부 normalization 이후**에서 처리한다.

### 2. Internal Canonical Model
- 외부/내부 진입점은 모두 내부 공통 DTO로 정규화한다.
- 핵심 내부 산출물:
  - `normalized_render_request`
  - `room_dims_contract`
  - `scene_contract`
  - `product_identity`
  - `placement_plan`
  - `geometry_qc`
  - `fidelity_qc`

### 3. Dual Geometry Source
- geometry source를 3개로 나눈다.
  - `explicit`
    - 사용자가 room dims를 직접 줌
  - `estimated`
    - room photo 기반 추정
  - `unknown`
    - 추정 신뢰도도 낮음
- strictness는 geometry source에 따라 달라진다.
  - internal + explicit => `strict_geometry`
  - external + estimated high/medium => `range_based_geometry`
  - external + unknown/low => `advisory_room_geometry + strict item-ratio QC`

### 4. Geometry-First, Generation-Second
- scene-wide generation은 더 이상 주 placement 엔진이 아니다.
- 순서는 아래로 고정한다.
  1. `room_dims_contract`
  2. `scene_contract`
  3. `product_identity`
  4. `placement_plan`
  5. item-level insertion / localized repair
  6. geometry/fidelity QC
  7. rank

### 5. Family-Specific Critical Path
- 아래 family는 generic redraw로만 처리하지 않는다.
  - `sofa`
  - `mirror`
  - `rug`
  - `table` with topology-sensitive support geometry
  - `tiny floor_lamp` / `tiny table_lamp`
- 이 family들은:
  - dedicated feature extraction
  - dedicated placement rules
  - dedicated repair policy
  - dedicated QC gate
  를 갖는다.

## File Map

### Boundary and normalization
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\api_models.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\main.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\render_route_services.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\preset_helpers.py`

### Existing render pipeline
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_scale_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\room_analysis.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_analysis_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\reference_features_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\item_analysis_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furnished_generation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_validation_support.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_room_workflow.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_postprocess_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_response_stage.py`

### New modules to add
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_contracts.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\room_dimension_estimation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scene_contract_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\placement_plan_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\product_identity_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\localized_repair_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\family_repair_support.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\geometry_qc_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\fidelity_qc_stage.py`

### Replay and QC
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tools\replay\exactness_replay_harness.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\replay_cases\9ffde1c0\manifest.json`

### Tests to add
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_render_contracts.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_room_dimension_estimation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_scene_contract_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_product_identity_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_placement_plan_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_family_repair_support.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_geometry_qc_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_fidelity_qc_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_internal_exactness_contracts.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_external_contract_stability.py`

## Ordered Tasks

### Phase 0. Contract Freeze and Runtime Policy
#### Task 0.1 Freeze external boundary
- `/cart`, `/preset` request/response schema freeze.
- DTO 변경 금지.
- 현재 optional `dimensions` 필드도 그대로 유지하되, 새 필드를 추가하지 않는다.

#### Task 0.2 Add internal normalized DTO
- `render_contracts.py` 추가.
- `NormalizedRenderRequest`
- `RoomDimsContract`
- `SceneContract`
- `ProductIdentity`
- `PlacementPlan`

#### Task 0.3 Add runtime policy model
- `strict_fidelity_mode`
- `range_based_geometry_mode`
- `advisory_geometry_mode`

#### Verification
- `pytest tests\test_render_contracts.py -q`
- `pytest tests\test_external_contract_stability.py -q`

---

### Phase 1. Route Normalization Without API Changes
#### Task 1.1 Internal route normalization
- `/async/render` -> explicit room dims path
- 내부 audience는 explicit dims가 있으면 strict geometry 사용

#### Task 1.2 External `/cart` normalization
- `/cart` request는 계약 변경 없이 내부 공통 DTO로 normalize
- `dimensions`가 비어 있으면 `room_dims_contract.source = estimated | unknown`

#### Task 1.3 External `/preset` normalization
- `/preset` request는 계약 변경 없이 normalize
- preset map에 `dimensions`가 있으면 explicit로 사용
- 없으면 추정 path 사용

#### Verification
- `pytest tests\test_route_helpers.py tests\test_external_contract_stability.py -q`

---

### Phase 2. Room Dimension Estimation for External Flows
#### Task 2.1 Add room dimension estimation stage
- `room_dimension_estimation_stage.py` 신설
- 입력:
  - room image
  - room analysis result
  - optional known anchors
- 출력:
  - `room_dims_contract`
    - `source`
    - `confidence`
    - `dims_mm_center`
    - `dims_mm_range`
    - `estimation_basis`
    - `strict_scale_mode`

#### Task 2.2 Estimation basis design
- 우선순위:
  1. explicit room dims
  2. preset-provided dims
  3. photo + architectural priors
  4. photo + anchor priors
  5. weak fallback

#### Task 2.3 Confidence tiers
- `high`
- `medium`
- `low`
- `none`

#### Task 2.4 External validation policy binding
- `high/medium` => range-based room QC 활성화
- `low/none` => room-fit hard fail 비활성화, 대신 inter-item ratio QC 강화

#### Verification
- `pytest tests\test_room_dimension_estimation_stage.py -q`
- replay harness로 external-no-dim case 검증

---

### Phase 3. Scene Contract Stage
#### Task 3.1 Build deterministic scene contract
- `scene_contract_stage.py` 추가
- room planes
- wall span
- camera estimate
- placement zones
- critical item list
- room dims source/confidence

#### Task 3.2 Persist scene contract
- workflow, response, debug payload에 남긴다.

#### Verification
- `pytest tests\test_scene_contract_stage.py -q`

---

### Phase 4. Product Identity Canonicalization
#### Task 4.1 Create product identity stage
- `product_identity_stage.py` 추가
- 기존 `reference_features`/`identity_profile`를 흡수 정리

#### Task 4.2 Expand reference feature schema
- `topology_cues`
- `support_geometry`
- `opening_or_gap_features`
- `pattern_cues`
- `reflection_constraints`

#### Task 4.3 Second-pass extraction for weak identities
- sofa
- side table
- rug
- tiny lamp
- mirror
- storage

#### Task 4.4 Crop-derived feature priority
- generic text keyword보다 crop-derived feature를 우선한다.

#### Verification
- `pytest tests\test_product_identity_stage.py -q`
- replay baseline에서 critical family empty rules 금지

---

### Phase 5. Placement Plan Solver
#### Task 5.1 Add placement plan stage
- `placement_plan_stage.py` 추가
- anchor-first geometry 계획 산출

#### Task 5.2 Pairwise ratio contract
- sofa vs room width
- table vs sofa
- rug vs table
- chair vs sofa
- tiny lamp absolute size
- mirror wall occupancy band

#### Task 5.3 Zone policy
- wall-attached
- floor-placed
- rug
- surface-placed
- small-free-object

#### Verification
- `pytest tests\test_placement_plan_stage.py -q`

---

### Phase 6. Family-Specific Insertion / Repair
#### Task 6.1 Split localized repair from furnished generation
- `localized_repair_stage.py`로 분리
- scene-wide redraw와 item-level repair를 분리

#### Task 6.2 Add family repair support
- `family_repair_support.py`
- family별 hard preserve 규칙

#### Task 6.3 Add secondary localization
- unmatched 또는 low-confidence item은 secondary localization pass 후 repair

#### Task 6.4 Add hybrid insertion path
- priority:
  1. masked edit
  2. constrained insertion
  3. limited harmonization
  4. scene-wide redraw fallback

#### Critical family hard rules
- sofa
  - central gap
  - segmented backrest
  - base silhouette
- side table
  - triangle top
  - cantilever chrome frame
  - dual-post support
- rug
  - circular footprint
  - no oversized anchor ratio
  - border/pattern preservation
- tiny lamp
  - 3-tier lantern silhouette
  - absolute-small scale clamp
- mirror
  - wall-only
  - outline preservation
  - reflection consistency

#### Verification
- `pytest tests\test_family_repair_support.py -q`
- replay before/after image comparison

---

### Phase 7. Geometry QC Rewrite
#### Task 7.1 Add geometry QC stage
- `geometry_qc_stage.py`
- room-fit
- pairwise ratio
- attachment
- unresolved criticals

#### Task 7.2 Support range-based geometry
- explicit dims => exact tolerance
- estimated dims => range tolerance
- unknown dims => room-fit advisory only

#### Task 7.3 Remove matched-only dependency for critical checks
- rug / tiny lamp / relative height는 fallback measurement 허용

#### Verification
- `pytest tests\test_geometry_qc_stage.py -q`

---

### Phase 8. Fidelity QC Rewrite
#### Task 8.1 Add fidelity QC stage
- `fidelity_qc_stage.py`
- topology, pattern, material, reflection 보존 검사

#### Task 8.2 Preserve diagnostics through guide leak / weak match
- item-level review가 `unknown`으로 비지 않게 유지

#### Task 8.3 Family critical fail rules
- sofa topology drift => fail
- side table support geometry drift => fail
- rug footprint/pattern drift => fail
- tiny lamp silhouette/absolute-size drift => fail
- mirror outline/reflection drift => fail

#### Verification
- `pytest tests\test_fidelity_qc_stage.py -q`

---

### Phase 9. Selection Policy Rewrite
#### Task 9.1 Reorder selection
- geometry gate
- fidelity gate
- aesthetic rank

#### Task 9.2 Remove silent success in strict/internal
- critical unresolved면 success 금지

#### Task 9.3 External route safe rollout
- external은 shadow mode부터
- 최초에는 selection behavior 변경 없이 QC만 기록
- metrics 확보 후 high-confidence estimated path부터 승격

#### Verification
- `pytest tests\test_render_postprocess.py tests\test_internal_exactness_contracts.py -q`

---

### Phase 10. Preset Catalog Enrichment
#### Task 10.1 Review preset map capabilities
- preset에 `dimensions`가 있으면 explicit source로 우선 사용

#### Task 10.2 Add optional preset geometry metadata
- preset catalog 내부 metadata 확장
  - room dims
  - anchor ratios
  - critical family hints
- 외부 `/preset` 계약은 그대로 두고 preset data만 강화

#### Task 10.3 Preset fallback policy
- preset metadata가 약하면 estimated room dims path 사용

#### Verification
- preset fixtures 기반 replay

---

### Phase 11. Replay and QC Harness
#### Task 11.1 Generalize replay harness
- `tools\replay\exactness_replay_harness.py`
- 입력:
  - room
  - items
  - room dims source
  - expected critical items
- 출력:
  - final image
  - all variants
  - item-by-item QC
  - room/ratio/fidelity summary

#### Task 11.2 Golden cases
- internal explicit-dims case
- external cart no-dims case
- external preset no-dims case
- critical family stress case

#### Task 11.3 Human QC checklist
- sofa
- side table
- rug
- tiny lamp
- mirror

#### Verification
- each task phase ends with replay + QC snapshot

---

### Phase 12. Deployment Plan
#### Task 12.1 Internal first
- internal explicit-dims flow 먼저 strict mode 배포

#### Task 12.2 External shadow
- external `/cart`, `/preset`는 shadow QC 먼저

#### Task 12.3 External graduated enforcement
- high-confidence estimated geometry부터 room-fit gate 점진 적용

#### Task 12.4 Metrics review before full enforcement
- fail rate
- unresolved critical rate
- ratio violation rate
- family drift rate

## Verification Strategy

### Unit / contract
- `pytest tests\test_render_contracts.py -q`
- `pytest tests\test_room_dimension_estimation_stage.py -q`
- `pytest tests\test_scene_contract_stage.py -q`
- `pytest tests\test_product_identity_stage.py -q`
- `pytest tests\test_placement_plan_stage.py -q`
- `pytest tests\test_family_repair_support.py -q`
- `pytest tests\test_geometry_qc_stage.py -q`
- `pytest tests\test_fidelity_qc_stage.py -q`
- `pytest tests\test_internal_exactness_contracts.py -q`
- `pytest tests\test_external_contract_stability.py -q`

### Replay
- internal explicit case: must use strict geometry
- external no-dims cart case: must use estimated/range geometry
- external no-dims preset case: must use preset metadata or estimated geometry

### Human QC
- sofa topology
- side table support geometry
- rug footprint
- tiny lamp absolute size
- mirror outline/reflection

## Risks and Open Assumptions
- single photo 기반 room dimension inference는 **exact mm 복원**이 아니라 **confidence-bearing approximation**이다.
- 따라서 external no-dims flow는 internal explicit-dims flow와 같은 strictness를 바로 적용할 수 없다.
- `/preset`의 exactness는 preset catalog metadata 품질에 크게 의존한다.
- hybrid insertion/compositing까지 가야 truly exact product fidelity에 근접할 수 있다.
- 그래도 이 구조가 현재 scene-wide redraw 중심 구조보다 훨씬 옳고 배포 가능하다.

## Final Direction
- **strict inside, stable outside**
- **explicit dims면 hard contract**
- **dims 없으면 estimated contract + confidence-aware QC**
- **critical family는 dedicated path**
- **geometry/fidelity 통과 후보만 선택**
