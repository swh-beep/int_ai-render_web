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

## Next
- [ ] 실제 외부 preset 1건 검증: render.furniture_data의 `box_source=main_render` 확인
- [ ] 실제 외부 cart 1건 검증: details.used_cutout_references가 item 이미지 기반으로 채워지는지 확인
- [ ] 필요 시 external detail 개수 정책(현재 9장 제한) 유지/변경 최종 결정

## Blocked
- [ ] 실데이터 외부 호출 job_id(검증용) 필요
