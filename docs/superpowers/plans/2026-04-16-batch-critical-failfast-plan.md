## Goal

현재 렌더 파이프라인의 주병목인 `item-by-item bbox/detect`, `모든 아이템 후속 review`, `늦은 geometry 실패 판정`을 제거해서 다음 세 가지를 동시에 달성하는 실행 플랜이다.

- internal / external `/cart` / external `/preset` 모두에서 wall-clock을 강하게 줄인다.
- geometry contract를 더 앞단에서 fail-fast 하도록 바꿔서 비싸게 실패하지 않게 만든다.
- 후속 review는 `critical archetype`과 `unresolved item`에만 제한해서 품질 검사 비용을 통제한다.

외부 호출 계약은 절대 변경하지 않는다.

## Architecture Summary

현재 병목은 [application/render/scale_validation_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_validation_support.py) 안의 두 단계 반복 구조다.

1. `geometry_shortlist`에 대해 `detect_item_bbox_norm(...)`를 아이템별 호출
2. 이후 다시 `scoped_items` 전체에 대해 `detect_item_bbox_norm(...)`를 아이템별 호출
3. 그 후 `reference fidelity`까지 아이템별 호출

이 구조는 `gpt-5.4 xhigh` 같이 비싼 분석 모델과 만나면 `ItemBBox / DetectFurniture / ReferenceFeatures` inner-loop가 폭발한다.

이번 플랜은 이를 세 층으로 바꾼다.

1. `geometry fail-fast`
   - 생성 직후 가장 먼저 `batch detect + deterministic geometry QC`만 수행
   - 여기서 `primary_width_vs_room_width`, `primary_anchor_unmatched`, `no_matched_items`, `rug_vs_anchor_footprint`, `tiny_item_vs_anchor_height` 같은 규칙이 치명적으로 깨지면 즉시 탈락

2. `batch detect/remap`
   - [application/render/postprocess_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\postprocess_support.py) 쪽의 batch detect/remap 아이디어를 shared helper로 끌어올린다.
   - 후보 이미지 1장당 `detect_furniture_boxes(...)` 1회 + row-to-item remap 1회만 허용한다.
   - `detect_item_bbox_norm(...)`는 batch detect에서 unresolved critical item만 fallback으로 사용한다.

3. `critical-only follow-up review`
   - `reference fidelity`와 `per-item bbox fallback`은 전부가 아니라
     - primary anchor
     - rug
     - tiny absolute-size object
     - support-geometry-sensitive object
     - wall-attached reflective object
     - batch remap에서 unmatched인 critical item
     에만 적용한다.

이 구조의 목표는 `정확도는 유지하고, 호출 수를 강하게 줄이는 것`이다.

## File Map

- [application/render/scale_validation_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_validation_support.py)
  - 현재 item-by-item detect / geometry QC / reference review의 핵심 병목
- [application/render/postprocess_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\postprocess_support.py)
  - `refresh_item_boxes_from_main_render(...)`와 remap scoring이 이미 존재하는 batch detect/remap 후보
- [application/render/item_analysis_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\item_analysis_stage.py)
  - `detect_furniture_boxes(...)` 원천 구현
- [application/render/furnished_generation_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furnished_generation_stage.py)
  - variant당 render/repair loop, `_validate_candidate(...)` 호출 경계
- [application/render/render_variant_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_variant_stage.py)
  - variant count, worker count
- [application/render/render_room_workflow.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_room_workflow.py)
  - strict/internal selection policy와 candidate orchestration
- `new`: [application/render/batch_detection_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\batch_detection_support.py)
  - shared batch detect/remap/row normalization helper
- `new`: [tests/test_batch_detection_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_batch_detection_support.py)
  - batch detect helper unit tests
- [tests/test_b_lite_runtime_contracts.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_b_lite_runtime_contracts.py)
- [tests/test_render_postprocess.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_render_postprocess.py)
- [tests/test_internal_scale_contracts.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_internal_scale_contracts.py)

## Ordered Tasks

1. **Phase 0: Contract freeze + baseline evidence**
   - 외부 `/cart`, `/preset` route surface는 수정 금지로 유지한다.
   - [tools/replay/exactness_qc_replay.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tools\replay\exactness_qc_replay.py) 기준 현재 병목 태그를 다시 고정한다.
   - 특히 `Analysis.ItemBBox`, `Analysis.DetectFurniture`, `Analysis.ReferenceFeatures`, `RankBestVariant` 호출 수와 총 runtime을 summary에 남길 수 있게 계측 포인트를 정리한다.
   - 검증:
     - replay report 기존 케이스 1회 재확인

2. **Phase 1: Shared batch detect/remap helper 추출**
   - `postprocess_support`에 흩어진 row normalization / label-family remap scoring을 [application/render/batch_detection_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\batch_detection_support.py)로 끌어낸다.
   - helper가 제공해야 하는 최소 API:
     - `detect_rows_from_render(...)`
     - `match_items_to_detected_rows(...)`
     - `build_matched_items_from_rows(...)`
   - `scale_validation_support`와 `postprocess_support`가 같은 remap 규칙을 쓰게 한다.
   - 검증:
     - `python -m pytest tests/test_batch_detection_support.py tests/test_render_postprocess.py -q`

3. **Phase 2: Geometry fail-fast를 batch detect 기반으로 교체**
   - [application/render/scale_validation_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_validation_support.py)에서 첫 번째 `geometry_shortlist` 검출 루프를 batch detect 1회로 바꾼다.
   - `geometry_shortlist` 대상은 유지하되, 아이템별 `detect_item_bbox_norm(...)` 호출을 제거한다.
   - `primary_anchor_unmatched`, `no_matched_items`, `primary_width_vs_room_width`, `rug_vs_anchor_footprint`, `tiny_item_vs_anchor_height`, `critical_ratio_fail_count`가 뜨면 즉시 short-circuit 한다.
   - 검증:
     - `python -m pytest tests/test_internal_scale_contracts.py -k "geometry or shortlist or short_circuit" -q`

4. **Phase 3: Critical-only follow-up detect**
   - geometry fail-fast를 통과한 후보만 다음 단계로 보낸다.
   - 두 번째 `scoped_items` 전체 detect 루프를 없애고, 아래에만 fallback detect를 허용한다.
     - primary anchor
     - unmatched critical item
     - rug
     - tiny absolute-size object
     - wall-attached reflective object
     - support-geometry-sensitive object
   - non-critical / already matched item은 추가 detect 금지
   - 검증:
     - `python -m pytest tests/test_internal_scale_contracts.py tests/test_b_lite_runtime_contracts.py -k "critical or unmatched or fallback" -q`

5. **Phase 4: Critical-only reference review**
   - [application/render/scale_validation_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_validation_support.py)의 `_should_run_reference_review(...)`를 tighter policy로 바꾼다.
   - `reference fidelity`는 매칭된 critical item과 unresolved critical item만 review한다.
   - already matched non-critical pass2 item은 review 생략
   - 검증:
     - unit test로 review 대상 축소 확인
     - replay에서 `Analysis.ReferenceFeatures` 호출 감소 확인

6. **Phase 5: Render loop budget hardening**
   - [application/render/furnished_generation_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furnished_generation_stage.py)에서 B-lite strict 경로의 variant/repair loop와 validation budget을 명시적으로 강제한다.
   - 목표:
     - variant당 `batch detect 1회`
     - unresolved critical fallback detect만 추가
     - review는 critical-only
   - `RankBestVariant`는 strict에서 hard/soft QC 후보가 없을 때만 수행하거나 생략한다.
   - 검증:
     - replay wall-clock 감소
     - `python -m pytest tests/test_b_lite_runtime_contracts.py -q`

7. **Phase 6: Replay/QC loop**
   - 아래 3개를 `TOTAL_TIMEOUT_LIMIT=600`으로 다시 돌린다.
     - internal strict compare
     - external `/cart`
     - external `/preset`
   - 각 케이스에서
     - total_seconds
     - 가장 비싼 tag
     - best image
     - fail/pass reason
     를 바탕화면 패키지로 정리한다.
   - 검증:
     - [b_lite_phase7_20260416](C:\Users\User\Desktop\b_lite_phase7_20260416)와 같은 형식의 결과 패키지 생성

## Verification Strategy

- 단위 테스트
  - `python -m pytest tests/test_batch_detection_support.py -q`
  - `python -m pytest tests/test_render_postprocess.py tests/test_internal_scale_contracts.py tests/test_b_lite_runtime_contracts.py -q`
- 컴파일/문법
  - `python -m py_compile application\\render\\batch_detection_support.py application\\render\\scale_validation_support.py application\\render\\furnished_generation_stage.py`
- 실제 replay
  - internal strict 1회
  - external cart 1회
  - external preset 1회
- reviewer gate
  - Phase 1/2
  - Phase 3/4
  - Phase 5/6
  묶음마다 reviewer `PASS`를 받고 다음 단계로 진행

## Risks and Open Assumptions

- `batch detect`로 줄여도 모델 자체가 detection을 놓치면 unmatched가 남을 수 있다. 이 경우 unresolved critical fallback detect가 품질 방어선이 된다.
- external no-dims 경로는 geometry confidence가 낮기 때문에 internal strict와 같은 hard gate를 그대로 적용하지 않는다.
- 이 플랜은 현재의 가장 큰 병목 제거 플랜이다. 소파/미러/러그 exactness를 완전히 사진 수준으로 맞추는 compositing lane은 별도 후속 과제다.
