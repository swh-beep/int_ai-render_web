# CONTEXT.md

## Why
사용자 요구사항:
- cart/preset(외부 호출)도 내부용 웹과 동일하게 디테일 타겟 일관성 확보
- 가구를 부피 기반으로 순위화해서 결과 검증 가능하게 만들기
- 디테일 결과에서 실제 참조 리소스(컷아웃/아이템)와 타겟 box를 눈으로 확인 가능하게 만들기

## Relevant Flow
1. `/api/external/render/preset`, `/api/external/render/cart`
   - `job_render_with_details` → `job_render`(메인) → `job_generate_details`(디테일)
2. `render_room()`
   - 기존엔 `aud==internal`일 때만 main-render box remap
3. `job_generate_details()`
   - 디테일 결과 생성 + (기존) 컷아웃 참조 수 메타 노출

## Key Decisions
1. **Box remap 전 audience 공통화**
   - internal/external 모두 최종 메인 결과 기준 box 사용
2. **Volume Rank 도입**
   - 1순위: dims 기반 volume proxy
   - fallback: box area proxy
   - 근거 필드(`volume_rank_basis`) 명시
3. **검증 메타 강화**
   - `furniture_boxes`, `used_cutout_references`, `target_*`, `volume_ranking` 반환

## Constraints
- 기존 API 필드는 유지(추가 확장만)
- cart는 아이템 이미지 기반, preset은 무드보드/컷아웃 기반이라는 입력 특성 유지
- 코드 최소 수정 원칙
