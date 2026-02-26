# PLAN.md

## Goal
외부 호출(cart/preset)도 내부웹 패치와 동일한 품질/추적성을 갖도록 정렬한다.

## Scope
### In
1. 메인컷 완료 후 디테일 타겟 좌표(`box_2d`)를 **audience 무관** 메인컷 기준으로 remap
2. 가구별 **부피 기반 순위(volume rank)** 산출/노출
3. 디테일 결과 JSON에 "어떤 컷아웃/아이템 참조를 썼는지" + "타겟 box" 메타데이터 강화
4. cart/preset 경로에서 동일하게 위 정보가 반환되도록 보장

### Out
1. 외부 프론트/백엔드 호출 규약 자체 변경
2. 모델 프롬프트 대규모 리라이트
3. 큐/인프라 구조 변경

## Steps
1. 공통 유틸 추가: 부피 proxy 계산 + rank 부여 + 스냅샷 생성
2. `render_room()` 후처리 수정: remap 범위 internal→all audience, volume rank 적용
3. `job_generate_details()`/`job_regenerate_single_detail()` 메타 필드 확장
4. 정적 검증(py_compile) + 변경 추적 리포트

## DoD
- internal/external 모두 메인 결과의 `furniture_data`에 `box_source=main_render` remap 반영 가능
- `furniture_data`에 `volume_rank/volume_proxy/volume_rank_basis` 확인 가능
- detail 결과 JSON에서 `used_cutout_references`, `target_box_2d`, `target_label` 등 검증 가능
- 문법 검증 통과

## Risks
- 볼륨 치수 누락 아이템의 rank 신뢰도 저하
- 추가 메타 필드로 응답 payload 증가

## Mitigation
- 치수 없으면 `box_area_2d` fallback 사용 + `volume_rank_basis`로 근거 명시
- 기존 필드는 유지하고 확장 필드만 추가(호환성 유지)

## Rollback
- remap 조건 원복(`aud == "internal"`)
- volume rank 유틸/필드 제거
- detail 메타 확장 필드 제거
