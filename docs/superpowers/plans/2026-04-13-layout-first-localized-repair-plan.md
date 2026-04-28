# 2026-04-13 Layout-First + Review + Localized Repair Plan

## 목표
- 디테일 fidelity와 스케일 품질을 같이 올린다.
- 특정 상품 하드코딩이 아니라 모든 가구 family에 통하는 구조로 간다.
- 외부 `/cart`, `/preset` 계약은 유지한다.

## 원칙
- `rank`보다 `review gate`가 먼저다.
- `best-effort`는 마지막 fallback으로만 쓴다.
- 내부용 response/debug만 확장하고 외부 surface는 그대로 둔다.

## Task 1. Baseline 고정
- 대상
  - `99b7e7db` live fixture
  - 최근 실패 job fixture 추가
- 작업
  - 현재 selected variant의 `unmatched`, `scale_fail`, `shape_drift`를 fixture로 고정
  - replay helper를 정리해서 같은 입력으로 반복 검증 가능하게 만든다
- 산출물
  - failing regression tests
- 게이트
  - 새 구조 들어가기 전 실패가 재현돼야 함

## Task 2. Identity Profile Stage 추가
- 목적
  - item이 category/label만 남고 generic object로 풀리는 문제 차단
- 작업
  - 각 item마다 공통 profile 생성
  - profile 필드
    - `target_key`
    - `name`
    - `category`
    - `family`
    - `dims_mm`
    - `crop_path`
    - `shape_cues`
    - `material_cues`
    - `silhouette_summary`
    - `reflective/is_wall_attached/is_floor_contact` flags
- 적용 위치
  - item analysis 결과 직후
  - 이후 generation / validation / ranking 전 단계에서 재사용
- 게이트
  - furniture_data에 profile 핵심 필드가 남아야 함

## Task 3. Review-First Candidate Gate
- 목적
  - 예쁜 후보를 먼저 고르는 구조 제거
- 작업
  - 각 variant에 대해 아래 순서로 리뷰
    1. remap coverage
    2. identity fidelity
    3. scale rules
    4. placement plausibility
  - review score와 failed rules를 variant metadata에 구조화
  - `rank_best_variant`는 review 통과 후보만 받도록 변경
- 게이트
  - unmatched 많은 후보가 ranking으로 앞에 오지 않아야 함

## Task 4. Layout-First Scale Gate
- 목적
  - 생성 전부터 배치/크기 envelope를 고정
- 작업
  - `primary_scale`, room dims, wall/floor plane으로 item별 목표 envelope 계산
  - 최소 정보
    - anchor width ratio
    - floor footprint ratio
    - relative height bands
    - wall-attached vertical zone
  - prompt에는 숫자 나열 대신 envelope summary만 넣고
  - validator는 같은 envelope를 기준으로 판정
- 게이트
  - rug / tiny lamp / storage / wall decor 계열에 공통 적용 가능해야 함

## Task 5. Localized Repair Stage
- 목적
  - 전체 scene regeneration만으로는 디테일 보존이 안 되는 문제 보정
- 작업
  - first-pass 결과에서 failed item만 다시 고른다
  - item별 localized re-edit 또는 targeted repair 호출
  - repair 대상 우선순위
    - sofa/table/chair
    - rug
    - tiny lamp
    - storage
- 게이트
  - all-fail이면 기존 best-effort 유지
  - 일부 item만 실패한 경우 전체 재생성 대신 repair path를 탄다

## Task 6. Mirror Special Path
- 목적
  - 거울 반사는 일반 furniture fidelity와 다른 문제로 분리
- 작업
  - `mirror` family는 일반 object fidelity와 별도 취급
  - 최소 규칙
    - wall-attached 강제
    - mirror frame silhouette fidelity
    - reflection는 “opposite wall consistency” review만 수행
  - reflection mismatch는 hard fail이 아니라 strong penalty로 먼저 시작
- 게이트
  - mirror가 decor로 떨어지지 않아야 함

## Task 7. Response / Debug 정리
- 작업
  - 내부용 payload에 variant review summary 추가
  - item별 `matched/unmatched/repaired` 상태 추가
  - selected_result 선택 이유를 내부용으로 명시
- 게이트
  - 외부 `/cart`, `/preset` response shape 불변

## Task 8. Verification
- 테스트
  - scale validation
  - render postprocess
  - live quality fixtures
  - route smoke
- 수동 검증
  - 같은 localhost 입력셋으로 replay
  - sofa detail / side table detail / rug scale / tiny lamp / mirror 확인
- 완료 조건
  - failing live fixture가 전부 green 또는 최소한 `xfail`에서 해제
  - selected variant가 `scale_check_failed=false`에 더 가깝게 내려와야 함

## 실행 순서
1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7
8. Task 8

## 리뷰 게이트
- 각 Task 끝날 때
  - targeted test
  - self-review
  - blocker 없으면 다음 Task 진행
