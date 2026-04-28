# Internal Render Scale Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사내 `/async/render` 메인 렌더에서 방 치수와 가구 치수가 실제 결과 스케일에 반영되도록 내부 전용 하드 검증과 재시도 경로를 추가하고, 현재 발견된 과대 스케일 회귀를 테스트로 고정한다.

**Architecture:** 외부 `/cart`, `/preset` 계약은 전혀 건드리지 않고, 내부 audience 에서만 `enable_scale_check` 와 후속 validator 를 활성화한다. 기존 프롬프트/스케일 가이드 의존 경로는 유지하되, 렌더 결과를 후처리로 판정하는 수학적 검사 축을 보강해서 "소파가 벽 절반을 먹는" 경우와 "러그가 테이블보다 과대해지는" 경우를 자동 재시도 또는 실패 처리한다.

**Tech Stack:** FastAPI, Python render pipeline stages, Gemini-based bbox detection helpers, unittest/pytest, internal RQ render workflow

**Execution Note:** 각 Task 는 구현 서브에이전트 1개, 스펙 리뷰어 1개, 코드 품질 리뷰어 1개 순서로 통과시킨 뒤 다음 Task 로 넘어간다. 외부 `/cart`, `/preset` request/response 계약은 어떤 Task 에서도 변경하지 않는다.

---

## Scope Lock

- 내부 audience (`audience == "internal"`) 에만 스케일 보정 로직을 적용한다.
- 외부 `/api/external/render/cart` 와 `/api/external/render/preset` 의 public 계약과 응답 shape 는 유지한다.
- 현재 S3 job `114358b6-3d75-4cb9-bfa2-fc9b938e1655` 에서 드러난 문제를 회귀 기준으로 삼는다.
- 이번 작업은 "스케일 판정과 재시도" 가 핵심이다. 프론트 입력 구조나 외부 API 계약은 수정 범위가 아니다.
- detail 생성 체인은 메인 렌더 `furniture_data` 재사용 경로를 유지해야 한다.

## Known Findings To Fix

- `application/render/render_audience_stage.py` 에서 현재 모든 audience 가 `enable_scale_check=False` 다.
- `application/render/furnished_generation_stage.py` 의 `validate_furnished_scale(...)` 호출은 dead path 이다.
- `application/render/scale_validation_support.py` 의 현재 validator 는 주로 "상대 높이" 비교만 하고, 벽 점유율이나 러그/테이블 footprint 관계를 직접 잡지 못한다.
- `application/render/scale_guide_support.py` 의 가이드는 바닥 500mm 격자만 그리므로, 현재 복층/대공간에서 높이 체감 보정이 약하다.
- 최신 내부 렌더 결과에서 2400mm sofa 가 후면 벽 폭을 과도하게 차지하고, 1000mm rug 가 950mm table 대비 과대하게 보인다.

## Files To Touch

### Create

- `tests/test_internal_scale_contracts.py`
  - 내부 audience 전용 scale gate, retry, diagnostics 회귀 테스트
- `tests/test_scale_validation_support.py`
  - validator 단위 테스트
- `tests/fixtures/internal_scale_case_114358b6.json`
  - 최신 문제 job 에서 필요한 치수/box/room metadata 만 최소 fixture 로 추출

### Modify

- `application/render/render_audience_stage.py`
  - audience 별 `enable_scale_check` 정책
- `application/render/scale_validation_support.py`
  - 내부 전용 하드 스케일 판정 규칙 추가
- `application/render/furnished_generation_stage.py`
  - 스케일 판정 실패 시 retry / fail 처리와 diagnostics 적재
- `application/render/render_bootstrap_stage.py`
  - scale diagnostics summary 초기화
- `application/render/render_variant_stage.py`
  - variant worker 결과를 thread-safe 하게 집계할 수 있는 구조로 변경
- `application/render/render_response_stage.py`
  - summary logging 만 유지하고 external result surface 는 건드리지 않는지 확인
- `application/render/render_room_workflow.py`
  - 새 diagnostics 와 retry metadata 전달
- `application/render/render_postprocess_stage.py`
  - 내부 보정 이후 결과 정렬 변화가 외부 흐름에 새지 않는지 확인
- `tests/test_external_route_contracts.py`
  - 외부 계약 불변 회귀를 다시 돌릴 최소 smoke 보강
- `tests/test_render_postprocess.py`
  - 결과 정렬과 box remap 안정성 회귀
- `live_validate_render_flows.py`
  - 외부 `/cart`, `/preset` smoke 를 마지막에 다시 확인

## Task Gate Rules

모든 Task 는 아래 순서로 진행한다.

1. 구현 서브에이전트가 해당 Task 만 수행
2. 스펙 리뷰어가 "플랜 요구사항과 일치하는지" 확인
3. 코드 품질 리뷰어가 "구현 리스크와 회귀" 확인
4. 둘 다 통과하면 다음 Task 로 이동

병렬은 탐색/리뷰에만 허용하고, 실제 쓰기 작업은 Task 당 하나의 서브에이전트만 수행한다.

### Task 1: 회귀 fixture 와 실패 테스트를 먼저 고정

**Files:**
- Create: `tests/fixtures/internal_scale_case_114358b6.json`
- Create: `tests/test_scale_validation_support.py`
- Create: `tests/test_internal_scale_contracts.py`

- [ ] **Step 1: 최신 문제 사례를 최소 fixture 로 추출하는 failing test 를 작성한다.**

```python
import json
from pathlib import Path


def test_internal_scale_fixture_contains_problem_case_metadata():
    fixture = json.loads(
        Path("tests/fixtures/internal_scale_case_114358b6.json").read_text(encoding="utf-8")
    )

    assert fixture["job_id"] == "114358b6-3d75-4cb9-bfa2-fc9b938e1655"
    assert fixture["room_dims_mm"] == {"width_mm": 8000, "depth_mm": 8000, "height_mm": 12000}
    assert fixture["primary_item"]["category"] == "sofa"
    assert fixture["primary_item"]["dims_mm"]["width_mm"] == 2400
    assert fixture["rug_item"]["dims_mm"]["width_mm"] == 1000
    assert len(fixture["items"]) == 5
```

- [ ] **Step 2: 내부 audience 스케일 게이트가 현재 꺼져 있음을 드러내는 failing test 를 작성한다.**

```python
from application.render.render_audience_stage import run_render_audience_stage


def test_internal_audience_must_enable_scale_check():
    result = run_render_audience_stage(
        audience="internal",
        normalize_audience=lambda aud: aud or "internal",
        build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
    )

    assert result.enable_scale_check is True
```

- [ ] **Step 3: 최신 사례의 normalized boxes 로 과대 스케일을 잡아내는 failing validator test 를 작성한다.**

```python
import json
from pathlib import Path

from application.render.scale_validation_support import validate_scale_from_detection_map


def test_validate_scale_from_detection_map_flags_primary_wall_occupancy_and_rug_footprint():
    fixture = json.loads(
        Path("tests/fixtures/internal_scale_case_114358b6.json").read_text(encoding="utf-8")
    )

    ok, issues, diagnostics = validate_scale_from_detection_map(
        fixture["items"],
        fixture["room_dims_mm"],
        fixture["room_planes"],
        fixture["detected_boxes_norm"],
    )

    assert ok is False
    assert "primary_width_vs_room_width" in diagnostics["failed_rules"]
    assert "rug_vs_anchor_footprint" in diagnostics["failed_rules"]
```

- [ ] **Step 4: 테스트를 실행해서 지금은 실패함을 확인한다.**

Run: `pytest tests/test_scale_validation_support.py tests/test_internal_scale_contracts.py -v`

Expected:
- `test_internal_audience_must_enable_scale_check` 는 `False is not True` 로 실패
- `validate_scale_from_detection_map` import 또는 assertion 이 실패
- fixture 파일이 없어서 실패

- [ ] **Step 5: 최소 fixture 를 저장하고, 테스트가 fixture 를 읽을 수 있게 만든다.**

```json
{
  "job_id": "114358b6-3d75-4cb9-bfa2-fc9b938e1655",
  "room_dims_mm": { "width_mm": 8000, "depth_mm": 8000, "height_mm": 12000 },
  "room_planes": { "y_top": 0.08, "y_bottom": 0.86 },
  "primary_item": {
    "label": "Sofa",
    "category": "sofa",
    "dims_mm": { "width_mm": 2400, "depth_mm": 1000, "height_mm": 850 }
  },
  "rug_item": {
    "label": "Rug",
    "category": "rug",
    "dims_mm": { "width_mm": 1000, "depth_mm": 1000, "height_mm": 10 }
  },
  "items": [
    { "label": "Sofa", "category": "sofa", "dims_mm": { "width_mm": 2400, "depth_mm": 1000, "height_mm": 850 } },
    { "label": "Sofa 2", "category": "sofa", "dims_mm": { "width_mm": 1100, "depth_mm": 1100, "height_mm": 1100 } },
    { "label": "Table", "category": "table", "dims_mm": { "width_mm": 950, "depth_mm": 950, "height_mm": 600 } },
    { "label": "Rug", "category": "rug", "dims_mm": { "width_mm": 1000, "depth_mm": 1000, "height_mm": 10 } },
    { "label": "Lounge Chair", "category": "lounge_chair", "dims_mm": { "width_mm": 1100, "depth_mm": 1100, "height_mm": 1100 } }
  ],
  "detected_boxes_norm": {
    "Sofa": [0.06, 0.46, 0.57, 0.84],
    "Sofa 2": [0.60, 0.46, 0.78, 0.82],
    "Table": [0.37, 0.66, 0.56, 0.84],
    "Rug": [0.22, 0.70, 0.72, 0.97],
    "Lounge Chair": [0.74, 0.49, 0.93, 0.84]
  }
}
```

- [ ] **Step 6: Task 1 테스트를 다시 실행한다.**

Run: `pytest tests/test_scale_validation_support.py tests/test_internal_scale_contracts.py -v`

Expected: 여전히 일부 실패한다. 아직 구현 전이므로 정상이다. 단, fixture 읽기 실패는 없어야 한다.

### Task 2: 내부 audience 에서만 scale gate 를 다시 연결

**Files:**
- Modify: `application/render/render_audience_stage.py`
- Modify: `application/render/render_bootstrap_stage.py`
- Modify: `tests/test_internal_scale_contracts.py`
- Modify: `tests/test_external_route_contracts.py`

- [ ] **Step 1: 내부 audience 만 scale check 를 켜고 외부는 그대로 유지하는 failing test 를 작성한다.**

```python
from application.render.render_audience_stage import run_render_audience_stage


def test_external_audiences_keep_scale_check_disabled():
    for audience in ("external", "korea", "global", "preset"):
        result = run_render_audience_stage(
            audience=audience,
            normalize_audience=lambda aud: aud,
            build_s3_prefix=lambda aud, category, suffix=None: f"{aud}/{category}/{suffix or 'root'}",
        )
        assert result.enable_scale_check is False
```

- [ ] **Step 2: bootstrap summary 가 기존 키 체계 안에서 scale counter 를 초기화하는 failing test 를 작성한다.**

```python
from application.render.render_bootstrap_stage import _build_summary


def test_build_summary_has_scale_retry_counter():
    summary = _build_summary()
    assert summary["scalecheck_fail"] == 0
    assert summary["scalecheck_retry"] == 0
```

- [ ] **Step 3: 테스트를 실행해서 실패를 확인한다.**

Run: `pytest tests/test_internal_scale_contracts.py tests/test_external_route_contracts.py -v`

Expected:
- internal audience gate test 는 실패
- summary 필드 부족으로 실패

- [ ] **Step 4: audience 정책과 bootstrap summary 를 최소 구현한다.**

```python
# application/render/render_audience_stage.py
return RenderAudienceStageResult(
    audience=aud,
    enable_scale_check=(aud == "internal"),
    prefix_main_user=build_s3_prefix(aud, "mainrendered", "user-photos"),
    prefix_main_empty=build_s3_prefix(aud, "mainrendered", "empty"),
    prefix_main_rendered=build_s3_prefix(aud, "mainrendered", "rendered"),
    prefix_customize=build_s3_prefix(aud, "customize"),
)
```

```python
# application/render/render_bootstrap_stage.py
return {
    ...
    "scalecheck_fail": 0,
    "scalecheck_retry": 0,
}
```

- [ ] **Step 5: Task 2 에서는 workflow 시그니처를 바꾸지 않는다.**

Note: 이 단계에서는 validator 로직 자체를 바꾸지 않고, `enable_scale_check` gate 와 `scalecheck_retry` counter 초기화만 먼저 연다. `render_variant_stage` / `render_room_workflow` 시그니처 변경은 Task 4 에서 처리한다.

- [ ] **Step 6: Task 2 테스트를 다시 실행한다.**

Run: `pytest tests/test_internal_scale_contracts.py tests/test_external_route_contracts.py -v`

Expected:
- internal audience 는 `enable_scale_check=True`
- external routes/tests 는 그대로 통과
- bootstrap summary counter 가 생겨 통과

### Task 3: 벽 점유율과 러그 footprint 규칙을 validator 에 추가

**Files:**
- Modify: `application/render/scale_validation_support.py`
- Modify: `tests/test_scale_validation_support.py`

- [ ] **Step 1: primary width 와 rug footprint 규칙에 대한 failing 단위 테스트를 추가한다.**

```python
def test_primary_width_rule_allows_reasonable_anchor():
    ok, issues, diagnostics = validate_scale_from_detection_map(
        items=[
            {"label": "Sofa", "category": "sofa", "dims_mm": {"width_mm": 2400, "depth_mm": 1000, "height_mm": 850}},
            {"label": "Table", "category": "table", "dims_mm": {"width_mm": 950, "depth_mm": 950, "height_mm": 600}},
        ],
        room_dims={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        detected_boxes={
            "Sofa": [0.20, 0.52, 0.42, 0.84],
            "Table": [0.46, 0.64, 0.58, 0.84],
        },
    )

    assert ok is True
    assert diagnostics["failed_rules"] == []


def test_rug_rule_flags_when_rug_wider_than_expected_vs_table():
    ok, issues, diagnostics = validate_scale_from_detection_map(
        items=[
            {"label": "Table", "category": "table", "dims_mm": {"width_mm": 950, "depth_mm": 950, "height_mm": 600}},
            {"label": "Rug", "category": "rug", "dims_mm": {"width_mm": 1000, "depth_mm": 1000, "height_mm": 10}},
        ],
        room_dims={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        detected_boxes={
            "Table": [0.42, 0.67, 0.56, 0.84],
            "Rug": [0.20, 0.70, 0.78, 0.96],
        },
    )

    assert ok is False
    assert "rug_vs_anchor_footprint" in diagnostics["failed_rules"]


def test_duplicate_labels_do_not_overwrite_each_other_when_keys_are_distinct():
    ok, issues, diagnostics = validate_scale_from_detection_map(
        items=[
            {"label": "Sofa", "target_key": "internal_sofa_001", "category": "sofa", "dims_mm": {"width_mm": 2400, "depth_mm": 1000, "height_mm": 850}},
            {"label": "Sofa", "target_key": "internal_sofa_002", "category": "sofa", "dims_mm": {"width_mm": 1100, "depth_mm": 1100, "height_mm": 1100}},
        ],
        room_dims={"width_mm": 8000, "depth_mm": 8000, "height_mm": 3000},
        room_planes={"y_top": 0.1, "y_bottom": 0.9},
        detected_rows=[
            {"match_key": "internal_sofa_001", "label": "Sofa", "box": [0.10, 0.50, 0.48, 0.84]},
            {"match_key": "internal_sofa_002", "label": "Sofa", "box": [0.56, 0.49, 0.74, 0.83]},
        ],
    )

    assert ok is False
    assert diagnostics["matched_count"] == 2
```

- [ ] **Step 2: 테스트를 실행해서 현재 validator 가 규칙을 못 잡는지 확인한다.**

Run: `pytest tests/test_scale_validation_support.py -v`

Expected:
- `validate_scale_from_detection_map` 없음 또는 규칙 assertion 실패

- [ ] **Step 3: label 충돌을 피하는 detection-row 기반 순수 validator helper 를 추가한다.**

```python
def validate_scale_from_detection_map(items, room_dims, room_planes, detected_rows=None, detected_boxes=None):
    diagnostics = {"failed_rules": [], "ratios": {}, "matched_count": 0}
    issues = []

    room_w = int((room_dims or {}).get("width_mm") or 0)
    if room_w <= 0:
        return True, [], diagnostics

    def _box_width(box):
        return max(1e-6, float(box[2]) - float(box[0]))

    indexed_rows = {}
    for index, row in enumerate(detected_rows or []):
        key = str(row.get("match_key") or row.get("target_key") or row.get("source_index") or f"row-{index}").strip()
        if key:
            indexed_rows[key] = row

    def _lookup_box(item):
        keys = [
            str(item.get("target_key") or "").strip(),
            str(item.get("source_index") or "").strip(),
            str(item.get("label") or "").strip(),
        ]
        for key in keys:
            if key and key in indexed_rows:
                diagnostics["matched_count"] += 1
                return indexed_rows[key]["box"]
        if detected_boxes:
            return detected_boxes.get(str(item.get("label") or "").strip())
        return None

    primary = next((item for item in items if item.get("category") in {"sofa", "sectional", "bed", "dining_table", "table"}), None)
    if primary:
        box = _lookup_box(primary)
        expected = int((primary.get("dims_mm") or {}).get("width_mm") or 0) / room_w
        observed = _box_width(box) if box else 0.0
        diagnostics["ratios"]["primary_width_vs_room_width"] = {"expected": expected, "observed": observed}
        if box and observed > max(expected * 1.55, expected + 0.08):
            diagnostics["failed_rules"].append("primary_width_vs_room_width")
            issues.append(f'{primary["label"]} is too wide for the room width')

    rugs = [item for item in items if item.get("category") == "rug"]
    anchors = [item for item in items if item.get("category") in {"table", "dining_table", "sofa", "sectional"}]
    if rugs and anchors:
        rug = rugs[0]
        anchor = anchors[0]
        rug_box = _lookup_box(rug)
        anchor_box = _lookup_box(anchor)
        if rug_box and anchor_box:
            rug_w = _box_width(rug_box)
            anchor_w = _box_width(anchor_box)
            rug_expected = int((rug.get("dims_mm") or {}).get("width_mm") or 0)
            anchor_expected = int((anchor.get("dims_mm") or {}).get("width_mm") or 0)
            expected_ratio = (rug_expected / anchor_expected) if rug_expected and anchor_expected else 0.0
            observed_ratio = rug_w / max(anchor_w, 1e-6)
            diagnostics["ratios"]["rug_vs_anchor_footprint"] = {"expected": expected_ratio, "observed": observed_ratio}
            if expected_ratio and observed_ratio > max(expected_ratio * 1.45, expected_ratio + 0.25):
                diagnostics["failed_rules"].append("rug_vs_anchor_footprint")
                issues.append(f'{rug["label"]} footprint is too large vs {anchor["label"]}')

    return (len(issues) == 0), issues, diagnostics
```

- [ ] **Step 4: 기존 `validate_furnished_scale(...)` 가 detection rows 를 만들어 새 helper 를 사용하도록 연결한다.**

```python
detected_rows = []
for item in complete_items + rug_items:
    ...
    if bbox:
        detected_rows.append(
            {
                "match_key": str(item.get("target_key") or item.get("source_index") or label),
                "label": label,
                "box": bbox,
            }
        )

ok, issues, diagnostics = validate_scale_from_detection_map(
    items=items,
    room_dims=room_dims,
    room_planes=room_planes,
    detected_rows=detected_rows,
)
```

주의:
- 기존 높이 비율 로직을 완전히 버리지 말고, 새 helper 내부에서 병합하거나 `legacy_height_ratio` 규칙으로 유지한다.
- rug 는 기존 `complete_items` 에서 제외되므로 별도 수집이 필요하다.

- [ ] **Step 5: validator 테스트를 다시 실행한다.**

Run: `pytest tests/test_scale_validation_support.py -v`

Expected:
- reasonable anchor 케이스는 통과
- 최신 사례와 rug 과대 사례는 실패 규칙을 잡는다

### Task 4: 내부 메인 렌더 retry / fail 정책을 generation stage 에 연결

**Files:**
- Modify: `application/render/furnished_generation_stage.py`
- Modify: `application/render/render_variant_stage.py`
- Modify: `application/render/render_room_workflow.py`
- Modify: `tests/test_internal_scale_contracts.py`

- [ ] **Step 1: variant 결과가 thread-safe 하게 scale 메타데이터를 반환하는 failing test 를 작성한다.**

```python
from application.render.render_variant_stage import _generate_one_variant


def test_generate_one_variant_returns_scale_metadata():
    result = _generate_one_variant(
        0,
        ...,
        generate_furnished_room=lambda *args, **kwargs: {
            "path": "outputs/result.png",
            "scale_check_failed": True,
            "scale_failed_rules": ["primary_width_vs_room_width"],
        },
    )

    assert result["path"] == "outputs/result.png"
    assert result["scale_check_failed"] is True
    assert result["scale_failed_rules"] == ["primary_width_vs_room_width"]
```

- [ ] **Step 2: best-effort 정책을 드러내는 failing workflow test 를 작성한다.**

```python
def test_internal_workflow_returns_best_effort_result_after_scale_failures():
    result = run_render_room_workflow(... fake variants all scale_check_failed ...)
    assert result["result_url"] is not None
    assert result["message"] == "Complete"
```

- [ ] **Step 3: 테스트를 실행해서 helper 가 없음을 확인한다.**

Run: `pytest tests/test_internal_scale_contracts.py -v`

Expected: `ImportError` 또는 helper 부재로 실패

- [ ] **Step 4: generation stage 와 variant stage 가 structured result 를 주고받게 바꾼다.**

```python
# application/render/furnished_generation_stage.py
return {
    "path": result_path,
    "scale_check_failed": not ok,
    "scale_issues": list(issues or []),
    "scale_failed_rules": list((diagnostics or {}).get("failed_rules") or []),
}
```

```python
# application/render/render_variant_stage.py
result = generate_furnished_room(...)
if isinstance(result, str):
    return {"path": result, "scale_check_failed": False, "scale_issues": [], "scale_failed_rules": []}
return result
```

- [ ] **Step 5: workflow 에서 futures 완료 후 summary 를 집계하고 best-effort 정책을 적용한다.**

```python
# application/render/render_room_workflow.py
variant_results = run_render_variant_stage(...)
generated_results = [row["path"] for row in variant_results if row.get("path")]
scale_failures = [row for row in variant_results if row.get("scale_check_failed")]
summary["scalecheck_fail"] += len(scale_failures)
summary["scalecheck_retry"] += max(0, len(scale_failures) - 1)
```

정책 확정:
- 내부 렌더는 기존 variant loop 안에서 최대 3회까지 scale retry 한다.
- 3회 모두 실패해도 **best-effort 이미지를 반환**한다.
- diagnostics 는 response surface 에 추가하지 않고 로그와 테스트 집계에서만 확인한다.

- [ ] **Step 6: Task 4 테스트를 다시 실행한다.**

Run: `pytest tests/test_internal_scale_contracts.py -v`

Expected:
- variant 결과가 structured metadata 를 반환한다
- workflow 가 best-effort 결과를 유지한다
- summary counter 는 futures 완료 후 안정적으로 집계된다

### Task 5: 외부 계약 불변과 전체 회귀를 다시 묶어 검증

**Files:**
- Modify: `tests/test_external_route_contracts.py`
- Modify: `tests/test_detail_chain_contracts.py`
- Modify: `tests/test_internal_scale_contracts.py`
- Modify: `tests/test_render_postprocess.py`
- Modify: `live_validate_render_flows.py`

- [ ] **Step 1: 외부 contract smoke 와 detail chain smoke 를 같은 회귀 묶음으로 실행하는 명령을 먼저 고정한다.**

Run:

```powershell
pytest tests/test_scale_validation_support.py tests/test_internal_scale_contracts.py tests/test_external_route_contracts.py tests/test_detail_chain_contracts.py tests/test_render_postprocess.py -v
```

Expected:
- 내부 스케일 테스트 통과
- 외부 계약 테스트 통과
- detail chain 테스트 통과

- [ ] **Step 2: render route surface 와 내부 최근 회귀를 같이 확인하는 smoke 를 추가한다.**

```powershell
@'
from main import app
paths = {route.path for route in app.routes}
assert "/api/external/render/cart" in paths
assert "/api/external/render/preset" in paths
assert "/async/render" in paths
print("route surface ok")
'@ | python -
```

- [ ] **Step 3: render postprocess 와 `/jobs/{id}` / live smoke 를 최소 보강한다.**

```python
def test_postprocess_keeps_external_best_pick_shape_stable(...):
    result = pick_best_render_result(...)
    assert "result_url" in result
    assert "furniture_data" in result
```

Run:

```powershell
python live_validate_render_flows.py --mode external
```

Expected:
- external `/cart`, `/preset` smoke 가 기존과 같은 response shape 로 통과
- `/jobs/{id}` 로 조회한 external 결과에도 scale 전용 debug 필드가 새로 노출되지 않음

- [ ] **Step 4: 로컬 manual QA 체크리스트를 실행한다.**

QA checklist:
- 내부 사내웹에서 동일 치수 입력으로 메인 렌더를 재실행했을 때 2400 sofa 가 후면 벽 절반 이상을 덮지 않는다.
- 1000 rug 가 950 table 대비 과대하지 않다.
- scale check 가 실패한 결과는 debug diagnostics 에 failed rule 이 남는다.
- detail 생성은 기존처럼 `furniture_data` 기반으로 이어진다.
- 외부 `/cart`, `/preset` 호출 smoke 가 기존 shape 로 응답한다.

- [ ] **Step 5: 최종 회귀 명령을 저장하고 결과를 기록한다.**

Run:

```powershell
pytest tests/test_scale_validation_support.py tests/test_internal_scale_contracts.py tests/test_external_route_contracts.py tests/test_detail_chain_contracts.py tests/test_render_postprocess.py tests/test_route_surface_smoke.py -v
python live_validate_render_flows.py --mode external
node --check static/js/script.js
```

Expected:
- 전체 테스트 통과
- 프론트 문법 오류 없음

## Resolved Policy

- 내부 메인 렌더가 scale validator 를 최대 횟수까지 재시도한 뒤에도 실패하면 **best-effort 이미지를 반환**한다.
- scale diagnostics 는 external surface 에 노출하지 않는다.

## Self-Review

### 1. Spec coverage

- 내부 전용 scale gate 재활성화: Task 2
- 벽 점유율 / 러그 footprint 규칙: Task 3
- retry / diagnostics: Task 4
- 최신 내부 문제 사례 회귀 고정: Task 1
- 외부 `/cart`, `/preset` 계약 불변: Task 2, Task 5
- detail chain 유지: Task 5

빠진 요구사항 없음.

### 2. Placeholder scan

- 각 Task 에 파일 경로, 테스트 코드, 실행 명령, 기대 결과를 명시했다.
- "적절히 처리" 같은 placeholder 문구 대신 구체 규칙 이름과 helper 예시를 넣었다.
- 정책 미확정 항목은 `Open Question` 으로 분리했다.

### 3. Type consistency

- diagnostics 구조는 `failed_rules`, `issues`, `attempt` 로 Task 3~4 전반에서 동일하게 사용했다.
- internal-only gate 는 `enable_scale_check=(aud == "internal")` 로 고정했다.
- 외부 계약은 route/response shape 변경 금지로 일관되게 유지했다.
