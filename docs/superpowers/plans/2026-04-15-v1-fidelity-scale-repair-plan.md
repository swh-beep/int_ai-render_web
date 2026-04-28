# 2026-04-15 V1 Fidelity/Scale Repair Plan

## 대상 케이스
- replay case: `9ffde1c0`
- 선택 결과: `outputs/job_9ffde1c0_artifacts/selected_v1_for_compare.png`
- 비교 기준 원본:
  - sofa: `outputs/job_9ffde1c0_artifacts/cart_item_2_1776063898_27ad1f91_de-sede_ds-686.png`
  - mirror: `outputs/job_9ffde1c0_artifacts/cart_item_4_1776063898_27ad1f91_gubbi_asd_mirror.png`
  - Akari mini lamp: `outputs/job_9ffde1c0_artifacts/cart_item_7_1776063898_27ad1f91_nasedition_5151-ad.png`
  - rug: `outputs/job_9ffde1c0_artifacts/cart_item_8_1776063898_27ad1f91_nas_edition_A_rug_seri.png`
  - side table: `outputs/job_9ffde1c0_artifacts/cart_item_9_1776063898_27ad1f91_nas_edition_tete77.png`

## 현재 판단
- side table, rug, tiny lamp, mirror, sofa 모두 원본 대비 fidelity가 부족하다.
- 원인은 단일 문제가 아니다.
  - reference feature 추출이 구조적으로 약하다.
  - item-level diagnostics가 guide leak에서 무너진다.
  - repair target selection이 critical item을 빠뜨릴 수 있다.
  - localized repair가 topology-preserving edit가 아니라 generic generative edit에 가깝다.
  - strict/internal에서도 critical item이 unresolved면 실패작이 최종 선택될 수 있다.

## 관찰된 핵심 결함
1. sofa
- 원본 핵심은 중앙 backrest gap과 낮고 둥근 segmented silhouette인데, 현재 feature에는 `round`, `leather` 수준만 남는다.
- 결과적으로 “비슷한 검은 소파”로 일반화된다.

2. side table
- 원본 핵심은 삼각형 상판보다도 `cantilever chrome frame`, `dual-post support geometry`다.
- 현재 feature에는 `triangular`, `metal`, `chrome`만 있어 support topology가 보존되지 않는다.

3. rug
- 원본 패턴은 동심원과 외곽 스트라이프가 핵심인데, 현재는 `round`, `thin` 정도만 유지된다.
- 스케일도 matched-only rule에 크게 의존해 oversized rug를 충분히 막지 못한다.

4. Akari mini lamp
- 원본은 아주 작은 3단 paper lantern 형태인데, 현재 feature가 거의 비어 있다.
- tiny item인데도 absolute size clamp와 family-specific repair가 없다.

5. mirror
- 원본은 비대칭 rounded black frame + reflective plane이다.
- 현재는 `round`, `mirror`, `reflective` 수준만 남아 outline과 reflection fidelity가 약하다.

## 목표
1. 원본 제품 topology를 더 정확히 기술하는 reference feature를 만든다.
2. guide leak가 생겨도 item-level diagnostics를 잃지 않게 한다.
3. critical item은 매칭 실패여도 repair 대상에서 빠지지 않게 한다.
4. family-specific localized repair를 추가해 generic redraw를 줄인다.
5. critical item unresolved 상태에서는 strict/internal 최종 선택을 막는다.

## Task 1. Reference Feature Extraction 강화
### 변경
- `application/render/reference_features_stage.py`
- `application/render/render_analysis_stage.py`

### 작업
- feature schema를 확장한다.
  - `topology_cues`
  - `support_geometry`
  - `opening_or_gap_features`
  - `pattern_cues`
  - `reflection_constraints`
- 현재처럼 `distinctive_parts`, `preserve_rules`가 비면 끝내지 말고 second-pass extraction을 추가한다.
- crop-derived feature가 있으면 text keyword merge보다 우선한다.

### family별 추출 목표
- sofa: backrest gap, arm count, seat segmentation, base silhouette
- side table: tabletop shape, support topology, leg/frame geometry
- rug: ring count, border pattern, center/edge contrast
- tiny lamp: lobe count, stacked silhouette, absolute-small cue
- mirror: outline shape, frame thickness, reflection constraint

### 성공 기준
- 위 5개 item 모두 `distinctive_parts` 또는 `preserve_rules`가 비지 않는다.
- identity profile이 generic keyword만 남지 않는다.

## Task 2. Guide Leak 시 diagnostics 보존
### 변경
- `application/render/furnished_generation_stage.py`
- `application/render/render_response_stage.py`

### 작업
- `scale_guide_leak_detected`가 나와도 item-level diagnostics를 `{}`로 비우지 않는다.
- 마지막 known-good matching snapshot, issue_records, unmatched list를 carry-forward 한다.
- `selected_item_review`가 전부 `unknown`이 되는 경로를 막는다.

### 성공 기준
- guide leak variant에서도 side table, rug, tiny lamp, mirror, sofa의 item-level review 상태가 남는다.

## Task 3. Critical Item Repair Selection 보강
### 변경
- `application/render/furnished_generation_stage.py`

### 작업
- 현재 `issue_records + unmatched_items` top-N 방식에 `critical family inventory`를 추가한다.
- strict/internal에서는 다음 family를 critical set으로 본다.
  - `sofa`
  - `mirror`
  - `rug`
  - `table` with high topology richness
  - tiny `floor_lamp` / tiny `table_lamp`
- diagnostics가 약해도 critical item이 repair pool에서 빠지지 않게 한다.
- bbox가 없으면 secondary localization pass를 먼저 돌린다.

### 성공 기준
- 이 케이스에서 side table, rug, tiny lamp, mirror, sofa가 repair 대상 우선순위에서 누락되지 않는다.

## Task 4. Family-Specific Localized Repair
### 변경
- `application/render/furnished_generation_stage.py`
- 필요 시 family별 helper 분리

### 작업
- 공통 repair prompt 외에 family override를 둔다.
- sofa
  - central gap / segmented backrest / low curved silhouette 유지
- side table
  - triangle top + chrome cantilever frame + dual-post support 유지
- rug
  - circular outline 유지
  - footprint clamp 우선
  - 패턴/테두리 restyle 허용 범위를 좁힘
- tiny lamp
  - absolute size clamp 먼저
  - 3-tier lantern silhouette 유지
- mirror
  - wall-only
  - outline preservation
  - reflection consistency

### 성공 기준
- repair prompt에 family-specific hard preserve block이 실제로 들어간다.

## Task 5. Scale Rule을 matched-only에서 plan fallback까지 확장
### 변경
- `application/render/scale_validation_support.py`

### 작업
- rug / tiny lamp / relative-height rule이 matched bbox에만 의존하지 않게 한다.
- secondary localization bbox 또는 repair bbox 후보와 `layout_envelope`를 이용한 fallback measurement를 추가한다.
- tiny lamp는 absolute size clamp를 별도 rule로 분리한다.
- rug는 sofa/table anchor 대비 tighter envelope를 사용한다.

### 성공 기준
- tiny lamp oversized와 rug oversized를 detector miss가 있어도 잡을 수 있다.

## Task 6. Critical Item Unresolved Hard Gate
### 변경
- `application/render/render_room_workflow.py`
- 필요 시 `application/render/render_postprocess_stage.py`

### 작업
- strict/internal에서 critical item이 unresolved이거나 topology/scale fail이면 최종 선택 금지.
- `best_effort_least_bad`도 critical unresolved가 남아 있으면 계속 retry하거나 explicit fail로 반환한다.

### 성공 기준
- side table/rug/tiny lamp/mirror/sofa 중 critical unresolved가 남은 실패작이 최종 선택되지 않는다.

## Task 7. QC 방식
각 Task마다 아래를 반복한다.
1. targeted unit/contract test
2. `9ffde1c0` live replay
3. 원본 이미지 대비 수동 QC
4. 성공/실패와 이유 기록
5. 다음 task 조정

## 최종 판정 기준
- sofa: 중앙 gap과 segmented silhouette 유지
- side table: triangle top + chrome cantilever/dual-post geometry 유지
- rug: 1100mm가 table 1000mm 대비 과대하지 않게 보임
- Akari mini lamp: 100mm급 작은 오브젝트로 읽힘
- mirror: outline과 reflection이 원본 의도에서 크게 벗어나지 않음
- strict/internal에서 critical unresolved 없이 최종 선택
