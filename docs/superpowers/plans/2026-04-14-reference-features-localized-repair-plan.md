# 2026-04-14 Reference Features + Localized Repair Plan

## Goal
- 소파, 러그, 거울, 램프, 사이드테이블, 스토리지의 디테일 fidelity와 스케일 품질을 구조적으로 올린다.
- 특정 테스트 가구에만 맞춘 규칙이 아니라 family 단위 규칙과 item identity 기반 검증으로 일반화한다.
- 외부 `/cart`, `/preset` 계약은 그대로 둔다.

## Architecture Summary
- 현재 병목은 `scene regeneration -> post-render remap -> best-effort selection` 구조다.
- 해결 방향은 `reference feature extraction -> review-first gate -> localized repair -> rank` 순서다.
- Task 1은 item crop 이미지에서 `reference_features`를 추출해 `identity_profile`에 실어 downstream이 재사용할 수 있게 만드는 것이다.

## File Map
- `application/render/item_analysis_stage.py`
- `application/render/render_analysis_stage.py`
- `application/render/reference_features_stage.py`
- `application/render/furnished_generation_stage.py`
- `application/render/scale_validation_support.py`
- `application/render/render_room_workflow.py`
- `application/render/render_postprocess_stage.py`
- `tests/test_internal_scale_contracts.py`

## Ordered Tasks
1. Task 1: image-based `reference_features` stage 추가
   - item crop 기준 구조 JSON 추출
   - `target_key`, `source_index`, `silhouette`, `material`, `distinctive_parts`, `preserve_rules` 포함
   - `identity_profile`에 병합
   - 검증: `tests/test_internal_scale_contracts.py`
2. Task 2: generation prompt를 `reference_features` 중심으로 교체
   - 텍스트 설명 대신 must-preserve 규칙을 구조화해서 넣기
   - 검증: prompt unit assertion + targeted replay
3. Task 3: post-render matching 안정화
   - `target_key/source_index -> family -> dims ratio -> reference_features` 순서
   - unmatched면 pass하는 경로 축소
   - 검증: replay diagnostics에서 unmatched 감소 확인
4. Task 4: family-specific gates 추가
   - `sofa/loung_sofa`, `rug`, `mirror`, `tiny lamp`, `side-table`, `storage`
   - 검증: failed_rules가 family별로 구조화되는지 확인
5. Task 5: localized repair stage 추가
   - 실패 item bbox만 mask edit
   - 검증: review fail item만 재수정되고 scene 전체는 유지되는지 확인
6. Task 6: debug/response 보강
   - `match_status`, `repair_applied`, `repair_bbox`, `selected_result_reason`
   - 검증: internal replay report 확인

## Verification Strategy
- 각 Task 후 targeted pytest
- live replay는 내부 historical job과 localhost manual case로 확인
- 최소 기준
  - sofa/rug/mirror/lamp/side-table family가 fidelity gate 대상에 포함
  - unmatched 감소
  - all-fail일 때만 best-effort 선택

## Risks and Open Assumptions
- reference feature schema가 흔들리면 downstream gate도 흔들린다.
- localized repair는 latency를 크게 올릴 수 있어서 repair budget이 필요하다.
- historical replay는 original request 값이 일부 저장되지 않아 `room/style/variant`는 명시 replay 값으로 고정한다.
