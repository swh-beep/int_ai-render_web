# CHECKLIST.md

## Done
- [x] 요구사항 재정의(외부 cart/preset 동일 반영 + volume rank)
- [x] PLAN/CONTEXT/CHECKLIST 갱신
- [x] volume rank 유틸 추가 (`_attach_volume_ranks`, `_volume_ranking_snapshot`)
- [x] detail 스타일 생성 순서를 `volume_rank` 기준으로 정렬(동률/누락 시 fallback)
- [x] 메인 박스 remap 조건 audience 공통화 (`render_room`)
- [x] 메인 결과에 `volume_ranking` 포함
- [x] 디테일 결과 메타 확장
  - [x] `furniture_boxes`에 box + volume 메타
  - [x] `used_cutout_references`에 참조 이미지 + box + volume 메타
  - [x] detail별 `target_*`(label/box/source/volume) 메타
  - [x] `volume_ranking` 포함
- [x] regenerate 단건 응답에도 `volume_ranking` + target 메타 확장
- [x] 정적 검증: `python3 -m py_compile main.py` 통과
- [x] 로컬 route validation 보강: external preset/cart에서 remap 증거 필드와 detail 메타를 직접 assert
- [x] 로컬 external preset 1건 검증: `render.furniture_data`에 `box_source=main_render` 확인
- [x] 로컬 external cart 1건 검증: `details.used_cutout_references`가 cart item 기반 target key로 채워짐 확인
- [x] external detail 개수 정책 고정: 최대 9장 유지
- [x] 로컬 validation 리포트 갱신: `live_validation_report.json`

## Next
- [ ] 다음 리팩토링 후보 검토: `render_room` 주변의 남은 고결합 render analysis/preparation 단계 분해

## Blocked
- [ ] 없음
