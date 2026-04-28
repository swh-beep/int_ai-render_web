# 2026-04-13 Model + Taxonomy + Matching Plan

## Status
- Completed on 2026-04-13
- Verification
  - `pytest`: `103 passed`
  - `pytest live-quality bundle`: `66 passed, 2 xfailed`
  - `unittest`: `19 tests OK`

## 목표
- 분석 계열 Gemini 기본 모델을 `gemini-3.1-pro-preview`로 통일한다.
- 사내웹 입력 카테고리를 실제 운영 기준에 맞게 정리한다.
- `mirror`, `storage`, `stool`/`pouf` 계열을 명시적으로 지원한다.
- 후단 matching이 label drift에 덜 흔들리도록 category-family 중심 보정을 추가한다.

## 제약
- 메인 생성 모델 `gemini-3.1-flash-image-preview`는 유지한다.
- 외부 `/cart`, `/preset` 계약은 건드리지 않는다.
- 특정 테스트 케이스 하드코딩은 넣지 않는다.

## Task 1. 모델 기본값 교체
- `ANALYSIS_MODEL_NAME`
- `DETECT_FURNITURE_MODEL_NAME`
- `ROOM_ONLY_MODEL_NAME`
- `RANK_MODEL_NAME`
- `REMAP_MODEL_NAME`

전부 기본값을 `gemini-3.1-pro-preview`로 맞춘다.

## Task 2. 입력 taxonomy 정리
- 사내웹 category dropdown 재정의
- 제거/병합
  - `dining_table` -> `table`
  - `dining_chair` -> `chair`
- 추가
  - `mirror`
  - `storage`
  - `stool`
- 유지
  - `sofa`
  - `sectional`
  - `lounge_chair`
  - `table`
  - `bed`
  - `rug`
  - `lamp`
  - `floor_lamp`
  - `table_lamp`
  - `decor`

## Task 3. canonical family 확장
- `mirror`를 `decor`로 떨어뜨리지 않게 canonical 우선순위 강화
- `storage` family를 명시적으로 유지
- `stool`, `pouf`, `ottoman`을 동일 seating family로 인식
- `lounge_chair`와 `lounge_sofa`는 matching family에서 같은 `lounge_seating` 계열로 취급

## Task 4. matching 안정화
- remap scoring에 label 외 family score를 추가
- `target_key`/`source_index` 실패 시 label-only fallback을 더 보수적으로 제한
- source category family와 detected label family가 일치하면 재매핑 가산점
- `mirror`는 wall-attached + reflective object family로 우선 유지

## Task 5. 검증
- static category contract 테스트 갱신
- internal form/payload 테스트 갱신
- canonical/matching/scale validation 테스트 추가
- 로컬 동일 입력셋 기준 핵심 regressions 재확인
