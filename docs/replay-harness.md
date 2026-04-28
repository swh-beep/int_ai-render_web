# Replay Harness

## 목적
- case-specific replay 스크립트가 제품 코드 판단에 섞이지 않게 분리한다.
- live artifact 경로와 로직 fixture를 분리한다.
- replay 복원 실수는 manifest 단계에서 먼저 막는다.

## 구조
- generic harness: [tools/replay/internal_render_replay.py](/C:/Users/User/.codex/worktrees/int_ai-render_web/render-engine-master-execution/tools/replay/internal_render_replay.py)
- case manifest: [tests/replay_cases](/C:/Users/User/.codex/worktrees/int_ai-render_web/render-engine-master-execution/tests/replay_cases)
- pure logic fixture: [tests/fixtures/internal_live_case_99b7e7db.json](/C:/Users/User/.codex/worktrees/int_ai-render_web/render-engine-master-execution/tests/fixtures/internal_live_case_99b7e7db.json)

## 원칙
- replay는 `manifest -> direct job payload -> job_render(..., persist_result=False)` 경로로만 실행한다.
- `tests/fixtures/*`에는 로직 검증 데이터만 둔다.
- live artifact path, room/item 원본 파일 경로, replay 보고서 경로는 `tests/replay_cases/*/manifest.json`으로 분리한다.
- `outputs/job_*/*.py`는 유지보수 대상이 아니라 deprecated wrapper만 둔다.

## 실행
```powershell
& "C:\Users\User\Desktop\AI 프로젝트\int_ai-render_web\.venv\Scripts\python.exe" `
  "C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tools\replay\internal_render_replay.py" `
  "C:\Users\User\.codex\worktrees\int_ai-render_web\render-engine-master-execution\tests\replay_cases\9ffde1c0\manifest.json"
```

## 검증
- manifest validation 실패 시 replay 실행 전에 non-zero exit code로 종료한다.
- replay QC는 manifest에 선언된 room/item 파일만 기준으로 한다.
