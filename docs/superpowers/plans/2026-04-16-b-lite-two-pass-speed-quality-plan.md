## Goal

`Existing`와 `B` 중 운영 가능성이 있는 경로를 `B-lite`로 수렴시킨다. 목표는 다음 두 가지를 동시에 만족하는 것이다.

- internal itemized와 external `/cart`, `/preset` 모두에서 총 runtime을 `600초 이하`, 일반 목표를 `300초~450초` 범위로 낮춘다.
- 현재보다 더 일관된 스케일/디테일 품질을 확보한다. 특히 large anchor object의 room ratio와 small/wall archetype의 absolute size/fidelity가 더 이상 inner-loop Gemini review에 의해 과도하게 지연되지 않도록 한다.
- external no-dims 경로는 internal explicit-dims와 같은 strict gate를 쓰지 않고, `confidence-aware geometry policy`로 분기한다.

이번 라운드의 범위는 `family hardcode`가 아니라 `archetype-based two-pass` 구조를 도입하는 것이다.

## Architecture Summary

현재 병목은 `3 variants x 3 attempts`, full-scene validation 반복, item-by-item bbox/fidelity review, repair 후 full revalidate, late QC selection이다. `B-lite`는 이를 다음 구조로 바꾼다.

1. `Two-pass strategy`
   - Pass 1: room scale를 결정하는 `anchor / footprint-defining` objects만 렌더
   - Pass 2: `small absolute-scale`, `wall-attached`, `support-geometry-sensitive` objects만 localized repair 또는 completion으로 추가

2. `Anchor-eligible selection`
   - `primary_scale_anchor`는 더 이상 단순 volume/category score로 뽑지 않는다.
   - `floor_lamp`, `mirror`, `rug`, `decor`, tiny object는 앵커 후보에서 제외한다.
   - `sofa/lounge_sofa/bed/storage/table/desk` 등 room scale representative만 후보로 본다.
   - sparse cart/preset처럼 대표 후보가 하나도 없으면 `non-rug floor-placed complete-dims object > support-sensitive complete-dims object > any complete-dims non-decor object` 순으로 fallback 한다.

3. `Cheap-first QC`
   - Pass 1 후보에 대해 먼저 deterministic geometry QC만 수행한다.
   - geometry pass를 통과하거나 근접한 후보에만 expensive bbox/fidelity review를 허용한다.

4. `Partial revalidate`
   - Pass 2 repair 후에는 전체 scene 재검증을 하지 않는다.
   - 수정된 target archetype + anchor + rug 정도만 부분 재검증한다.
   - 대신 `lightweight scene safety scan`을 추가해서 수정하지 않은 pass1 객체의 bbox drift, overlap burst, wall/floor violation이 새로 생기지 않았는지 본다.

5. `Replay/runtime harness`
   - replay harness는 pass별 시간, full render count, repair count, validation count를 기록한다.
   - 최종 보고는 `wall-clock`, `per-pass timings`, `variant/attempt counts`, `selected reason`까지 남긴다.

## File Map

- [application/render/furniture_specs_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furniture_specs_stage.py)
  - `primary_scale` 선정 로직이 있는 초기 scale anchor 후보 계산부
- [application/render/scale_plan_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_plan_support.py)
  - `select_scale_anchor()` 와 scale plan anchor payload 구성부
- [application/render/placement_plan_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\placement_plan_stage.py)
  - placement family / small item clamp 생성부
- [application/render/render_room_workflow.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_room_workflow.py)
  - analysis -> placement -> variant -> QC -> postprocess orchestration
- [application/render/render_variant_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_variant_stage.py)
  - variant count / worker count / generation entrypoint
- [application/render/furnished_generation_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furnished_generation_stage.py)
  - full render attempt loop, localized repair, repair 후 revalidate
- [application/render/scale_validation_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_validation_support.py)
  - bbox remap, fidelity review, geometry/placement QC
- [application/render/render_postprocess_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_postprocess_stage.py)
  - winner selection 시 redundant re-detect 제거 후보
- [tools/replay/exactness_qc_replay.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tools\replay\exactness_qc_replay.py)
  - replay harness 공용 진입점
- [tests/test_internal_scale_contracts.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_internal_scale_contracts.py)
- [tests/test_placement_plan_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_placement_plan_stage.py)
- `new`: [application/render/two_pass_strategy_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\two_pass_strategy_stage.py)
  - archetype grouping, anchor-eligibility, pass partition 책임
- `new`: [tests/test_two_pass_strategy_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_two_pass_strategy_stage.py)
  - two-pass partition과 anchor eligibility 단위 테스트

## Ordered Tasks

1. **Phase 0: Baseline guardrails**
   - `primary dims missing`, bad anchor selection, replay timing 누락 여부를 다시 확인한다.
   - `tools/replay/exactness_qc_replay.py`에 pass별 runtime/event counters를 심을 위치를 고정한다.
   - 검증:
     - `python -m pytest tests/test_internal_scale_contracts.py -k "scale or replay" -q`

2. **Phase 1: Anchor eligibility + two-pass partition**
   - [application/render/two_pass_strategy_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\two_pass_strategy_stage.py) 추가
   - 입력: analyzed items, primary item, placement contract/product identity
   - 출력:
     - `anchor_eligible`
     - `pass_role` (`pass1_anchor`, `pass1_footprint`, `pass2_small`, `pass2_wall`, `pass2_support_sensitive`, `pass2_decor`)
     - `strategy_priority`
   - [application/render/furniture_specs_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furniture_specs_stage.py)와 [application/render/scale_plan_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_plan_support.py)에서 `primary_scale`/`select_scale_anchor()`가 `anchor_eligible=True`만 보도록 변경
   - 검증:
     - `python -m pytest tests/test_two_pass_strategy_stage.py tests/test_placement_plan_stage.py tests/test_internal_scale_contracts.py -k "anchor or pass" -q`

3. **Phase 2: Confidence-aware external no-dims policy**
   - [application/render/render_room_workflow.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_room_workflow.py)와 geometry/QC 관련 stage에서 internal explicit-dims와 external estimated-dims 정책을 분리한다.
   - external `/cart`, `/preset`는 `estimated/high|medium|low confidence`에 따라:
     - anchor ratio tolerance 완화
     - strict hard block 대신 confidence-aware soft block
     - timing 실험에서 pass/fail reason을 별도로 기록
   - 검증:
     - external cart replay 1회
     - external preset replay 1회

4. **Phase 3: Cheap-first QC**
   - [application/render/scale_validation_support.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\scale_validation_support.py)에 `geometry-first shortlist` 추가
   - geometry fail가 명확한 후보는 item bbox/fidelity review로 들어가지 않도록 short-circuit
   - `critical unresolved` archetype만 expensive review 수행
   - 검증:
     - unit tests for short-circuit
     - replay timing delta 확인
     - 성공 기준: internal strict replay에서 expensive fidelity review count가 baseline 대비 40% 이상 감소

5. **Phase 4: Runtime budget reduction**
   - [application/render/render_variant_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_variant_stage.py) 에서 internal strict `B-lite` 프로필 추가
   - 기본값:
     - full variants `2`
     - per-variant full attempt `1`
     - localized repair `1`
   - [application/render/furnished_generation_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furnished_generation_stage.py) 에서 repair 후 full-scene revalidate 대신 partial revalidate 훅 추가
   - 검증:
     - targeted unit tests + one replay with timing report
     - 성공 기준: internal strict replay wall-clock이 `900초 이하`

6. **Phase 5: Pass 2 targeted completion**
   - [application/render/furnished_generation_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furnished_generation_stage.py)에서 pass2 targets만 localized completion/edit에 태우는 경로 추가
   - large-first 결과 이미지를 base로 하고, pass2 small/wall/support archetype만 후속 편집
   - 검증:
     - internal replay
     - one external cart replay
     - 성공 기준: internal strict replay wall-clock이 `600초 이하` 또는 baseline 대비 `50% 이상` 감소
     - 품질 기준: `primary_width_vs_room_width`와 `unmatched_source_items` severity가 baseline 대비 감소

7. **Phase 6: Winner reuse + redundant postprocess removal**
   - [application/render/render_postprocess_stage.py](C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\render_postprocess_stage.py)에서 이미 diagnostics가 붙은 winner에 대해 re-detect를 건너뛴다.
   - 검증:
     - replay wall-clock 감소 확인
     - 성공 기준: postprocess redetect count가 `0`으로 떨어지고 total wall-clock이 추가로 감소

8. **Phase 7: SLA matrix + report package**
   - `Existing` vs `B-lite` 최소 3 replay case를 같은 harness로 측정
   - 각 case에 대해 best image, total time, fail/pass reason, 핵심 diagnostics를 한 폴더에 저장
   - 검증:
     - desktop comparison folder 생성
     - summary json 생성
     - internal / external cart / external preset 3경로 모두 결과 포함

## Verification Strategy

- Unit tests
  - `python -m pytest tests/test_two_pass_strategy_stage.py -q`
  - `python -m pytest tests/test_placement_plan_stage.py tests/test_internal_scale_contracts.py -q`
- Compile/sanity
  - `python -m py_compile application\\render\\two_pass_strategy_stage.py application\\render\\furniture_specs_stage.py application\\render\\scale_plan_support.py`
- Replay validation
  - internal strict case `9ffde1c0` 기반 replay
  - external cart replay
  - external preset regression smoke
- Reviewer gate
  - Phase 1, 2/3, 4/5 묶음마다 reviewer `PASS` 후 다음 phase 진행

## Risks and Open Assumptions

- `pass2 completion`만으로 mirror/support-topology fidelity가 충분히 오르지 않을 수 있다. 이 경우 다음 단계는 compositing lane 검토다.
- external no-dims 경로는 geometry confidence가 낮다. internal strict와 동일 hard gate를 그대로 적용하면 false fail가 증가할 수 있다.
- external no-dims 경로는 geometry confidence가 낮다. 이 플랜은 해당 경로에 strict geometry hard block이 아니라 confidence-aware QC를 적용한다.
- batch bbox/remap가 아직 없는 상태라, Phase 3 short-circuit만으로 600초를 못 맞출 수 있다.
- 이 플랜은 `B-lite`를 현실적인 배포 후보로 만드는 데 초점을 둔다. “실사진과 완전 동일” 목표는 그 다음 compositing 단계가 필요할 수 있다.
