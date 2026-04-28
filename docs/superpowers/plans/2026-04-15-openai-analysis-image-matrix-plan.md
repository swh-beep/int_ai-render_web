## Goal

`9ffde1c0_compare` replay fixture를 기준으로 4가지 실험 조합을 동일 조건에서 재실행하고, 사용자가 직접 비교할 수 있도록 각 실험당 3장씩 총 12장의 이미지를 바탕화면 폴더에 정리한다.

실험 조합:
- Existing: Gemini analysis + Gemini generation/repair
- A: OpenAI analysis (`gpt-5.4`, `xhigh` 우선) + Gemini generation/repair
- B: OpenAI analysis (`gpt-5.4`, `xhigh` 우선) + Gemini main generation + OpenAI repair image
- C: OpenAI analysis (`gpt-5.4`, `xhigh` 우선) + OpenAI main generation + OpenAI repair image

## Architecture Summary

- 분석 provider는 이미 `ANALYSIS_PROVIDER` + `OPENAI_ANALYSIS_*`로 분기된다.
- 이미지 생성/편집 provider는 아직 Gemini 단일 경로다. 이를 실험용 provider seam으로 일반화한다.
- 외부 `/cart`, `/preset` 호출 계약은 유지한다. 변경 범위는 내부 provider wiring, replay harness, 실험 artifact 정리로 한정한다.
- 이미지 provider는 실험 목적상 `OPENAI_IMAGE_PROVIDER`, `OPENAI_IMAGE_MODEL_NAME`, `OPENAI_MAIN_IMAGE_PROVIDER`, `OPENAI_REPAIR_IMAGE_PROVIDER` 환경변수로 제어한다.

## File Map

- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\main.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\infrastructure\ai\openai_analysis_client.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\infrastructure\ai\openai_image_client.py` (new)
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\infrastructure\ai\image_provider_dispatch.py` (new)
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\empty_room_generation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\render\furnished_generation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\media\image_edit_generation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\application\details\detail_generation_stage.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tools\replay\exactness_qc_replay.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_openai_analysis_client.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_analysis_provider_dispatch.py`
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_openai_image_client.py` (new)
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\test_image_provider_dispatch.py` (new)
- `C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\replay_cases\9ffde1c0_compare\manifest.json`

## Ordered Tasks

1. Phase 1: `gpt-5.4` 분석 reasoning effort를 `xhigh` 우선으로 올리고, unsupported fallback을 `high`로 둔다.
   - `.env`와 provider wiring을 맞춘다.
   - 검증: analysis dispatch/unit tests, smoke call.
   - 리뷰: reasoning 설정이 실제 payload에 반영되는지 reviewer PASS.

2. Phase 2: OpenAI 이미지 generation/edit 클라이언트를 추가한다.
   - `openai_image_client.py`에서 OpenAI 이미지 응답을 현재 Gemini 이미지 응답처럼 읽을 수 있는 adapter shape로 감싼다.
   - `image_provider_dispatch.py`에서 `main`과 `repair`를 분리 라우팅한다.
   - 검증: unit tests for payload building and response normalization.
   - 리뷰: external contracts untouched + adapter safety reviewer PASS.

3. Phase 3: render/image-edit/detail generation 경로를 새 image provider seam으로 연결한다.
   - main generation과 repair edit를 분리 provider로 호출 가능하게 한다.
   - Existing/A/B/C 조합을 환경변수만으로 바꿀 수 있게 한다.
   - 검증: py_compile + targeted pytest.
   - 리뷰: no regression reviewer PASS.

4. Phase 4: replay harness와 artifact collection을 정리한다.
   - 4개 조합 각각 report/output을 고정 경로에 저장한다.
   - 각 조합의 best image와 3장 세트를 바탕화면 폴더로 복사한다.
   - 검증: dry-run path checks.
   - 리뷰: fairness/artifact completeness reviewer PASS.

5. Phase 5: Existing, A, B, C를 실제 replay한다.
   - 동일 fixture, 동일 room dims, 동일 item dims로 4회 실행.
   - 각 run의 총 시간, best image, report, QC reason을 저장한다.
   - 검증: each replay report produced, 12 images copied.
   - 리뷰: result summary reviewer PASS with caveats if any.

## Verification Strategy

- Unit:
  - `python -m pytest tests\test_openai_analysis_client.py tests\test_analysis_provider_dispatch.py tests\test_openai_image_client.py tests\test_image_provider_dispatch.py -q`
- Compile:
  - `python -m py_compile ...`
- Replay:
  - `python tools\replay\exactness_qc_replay.py tests\replay_cases\9ffde1c0_compare\manifest.json --report-path <per-experiment-report>`
- Artifact:
  - Desktop folder contains 12 images:
    - `existing_v1/v2/v3`
    - `A_v1/v2/v3`
    - `B_v1/v2/v3`
    - `C_v1/v2/v3`

## Risks and Open Assumptions

- OpenAI image API가 현재 mixed multi-image prompt를 완전히 같은 품질로 처리하지 못할 수 있다.
- `xhigh` reasoning effort가 API 계정/모델 조합에서 제한될 수 있다. 이 경우 `high` fallback을 기록한다.
- B/C는 실험 목적이므로 기존 production default를 강제 변경하지 않는다.
- OpenAI image provider는 experiment seam만 먼저 넣고, production 승격은 결과 확인 후 결정한다.
