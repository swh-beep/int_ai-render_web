# 사내웹 Itemized Render 구조 개편 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사내웹 메인 렌더 입력을 무드보드 업로드 방식에서 가구별 이미지 + 카테고리 + 수량 + W/D/H 필수 입력 방식으로 전환하고, `/async/render`, `/api/external/render/cart`, `/api/external/render/preset` 세 표면을 하나의 공통 렌더 커맨드 형태로 정규화한다.

**Architecture:** `/async/render`는 브라우저용 `multipart/form-data` 어댑터로 유지하고, 내부에서는 기존 렌더 워크플로우가 이미 이해하는 `moodboard_items` 기반 payload로 변환한다. `/api/external/render/cart`, `/api/external/render/preset`의 public request/response 계약은 절대 바꾸지 않고, `/api/internal/render`만 제거한다. detail 생성/재생성 체인은 `furniture_data`를 주 경로로 계속 재사용하고, 사내웹에서는 `moodboard_url` 의존을 제거한다.

**Tech Stack:** FastAPI, Pydantic, RQ job payload builder, 정적 HTML/CSS, 바닐라 JavaScript, pytest/unittest

**Execution Note:** 이 저장소 규칙상 커밋 단계는 모두 사용자 명시 승인 후에만 실행한다. 아래 commit step은 승인 이후에만 수행한다.

---

## 범위 고정

- 외부 `/api/external/render/cart` 계약은 요청/응답 필드 이름과 비동기 큐 동작을 포함해 그대로 유지한다.
- 외부 `/api/external/render/preset` 계약도 그대로 유지한다.
- 사내웹은 `/async/render`를 계속 사용하되, 더 이상 `moodboard` 파일을 받지 않는다.
- 사내웹 item card는 `image`, `category`, `qty`, `width_mm`, `depth_mm`, `height_mm`를 모두 필수로 받는다.
- 카테고리 목록은 현재 `DEFAULT_CART_LIMITS` 실사용 목록 13개를 그대로 노출한다.
- detail 생성과 단일 detail 재생성은 메인 렌더의 `furniture_data`를 재사용하는 현재 흐름을 깨지 않는다.
- `/api/internal/render`와 이에 종속된 모델/라우트/헬퍼는 제거한다.

## 구현 전제

- 브라우저는 `/async/render`로 아래 필드를 전송한다.
  - `file`: 방 원본 이미지
  - `room`, `style`, `variant`, `dimensions`, `placement`
  - `items_json`: 가구 메타데이터 JSON 문자열
  - `item_images`: item card 순서와 동일한 반복 파일 필드
- `items_json`의 각 row는 아래 키를 가진다.

```json
[
  {
    "client_id": "item-1",
    "name": "Boucle Sofa",
    "category": "sofa",
    "qty": 2,
    "dims_mm": {
      "width_mm": 2200,
      "depth_mm": 950,
      "height_mm": 760
    }
  }
]
```

- `items_json` 배열 순서와 `item_images` 반복 필드 순서는 반드시 같아야 한다.
- 내부 공통 렌더 커맨드는 기존 `render` payload envelope과 `RenderWorkflowRequest.moodboard_items`를 재사용한다.

## 파일 구조

### Create

- `application/http/internal_render_form_parser.py`
  - `/async/render` 전용 multipart 입력 파서
  - `items_json` 파싱, 필수 필드 검증, 업로드 개수 정합성 검증 담당
- `tests/test_internal_render_form_parser.py`
  - parser 성공/실패 케이스 검증
- `tests/test_internal_itemized_render_payloads.py`
  - 사내웹 itemized payload builder와 파일 저장 helper 검증
- `tests/test_route_surface_smoke.py`
  - `/api/internal/render` 제거와 surviving route surface 유지 여부를 검증
- `tests/test_internal_web_static_contracts.py`
  - 정적 HTML/JS에 itemized UI id가 존재하고 moodboard 전용 id가 제거됐는지 검증
- `tests/test_external_route_contracts.py`
  - external preset/cart handler 응답 형태와 queue payload shape 불변을 검증
- `tests/test_render_response_contract.py`
  - 메인 render 응답에 `moodboard_url`, `furniture_data`, `volume_ranking`가 유지되는지 검증

### Modify

- `main.py:1590-1757`
  - `_queue_route_deps()` wiring 갱신
  - `/async/render` form signature 변경
  - `/api/internal/render` route 제거
- `application/http/queue_route_handlers.py:11-43,105-237,315-331`
  - dependency dataclass에 parser/itemized helper 주입
  - `handle_render_room_async()`를 itemized multipart 기준으로 재작성
  - `handle_api_internal_render()` 제거
- `render_route_services.py:23-309`
  - `InternalRenderRequest` 기반 helper 제거
  - room 파일 저장 helper와 item 파일 저장 helper 분리
  - 사내웹 itemized payload builder 추가
- `api_models.py:14-61`
  - `InternalRenderRequest` 제거
  - detail request 모델은 유지
- `static/index.html:60-120`
  - 무드보드 업로드 섹션 제거
  - furniture item section, 반복 card container, template 추가
- `static/css/style.css`
  - 기존 neutral tone을 유지하는 item card 스타일 추가
- `static/js/script.js:200-225,393-406,533-950,1176-1323`
  - moodboard 상태 제거
  - item card 상태, validation, form serialization, detail continuity 로직 추가
- `tests/test_route_helpers.py`
  - 외부 `/cart` 계약 불변 검증 강화
- `tests/test_detail_metadata.py`
  - `furniture_data` 우선 경로가 유지되는지 보강

## 사전 확인 명령

- [ ] 현재 라우트 표면을 캡처한다.

```powershell
@'
from main import app
print(sorted(route.path for route in app.routes if getattr(route, "path", None)))
'@ | python -
```

기대 결과: `/async/render`, `/api/internal/render`, `/api/external/render/cart`, `/api/external/render/preset`, `/generate-details`, `/regenerate-single-detail`가 보인다.

### Task 1: 사내웹 itemized multipart 파서 추가

**Files:**
- Create: `application/http/internal_render_form_parser.py`
- Test: `tests/test_internal_render_form_parser.py`

- [ ] **Step 1: failing test를 먼저 작성한다.**

```python
import json
from io import BytesIO

import pytest
from starlette.datastructures import UploadFile

from application.http.internal_render_form_parser import parse_internal_render_items_form


def _upload(name: str) -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(b"fake-image-bytes"))


def test_parse_internal_render_items_form_accepts_valid_rows():
    rows = parse_internal_render_items_form(
        items_json=json.dumps(
            [
                {
                    "client_id": "item-1",
                    "name": "Boucle Sofa",
                    "category": "sofa",
                    "qty": 2,
                    "dims_mm": {
                        "width_mm": 2200,
                        "depth_mm": 950,
                        "height_mm": 760,
                    },
                }
            ]
        ),
        item_images=[_upload("sofa.png")],
    )

    assert rows == [
        {
            "client_id": "item-1",
            "name": "Boucle Sofa",
            "category": "sofa",
            "qty": 2,
            "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 760},
            "upload_index": 0,
        }
    ]


def test_parse_internal_render_items_form_rejects_missing_required_dimensions():
    with pytest.raises(ValueError, match="Item 1 is missing required dims: depth_mm, height_mm"):
        parse_internal_render_items_form(
            items_json=json.dumps(
                [
                    {
                        "client_id": "item-1",
                        "category": "chair",
                        "qty": 1,
                        "dims_mm": {"width_mm": 600},
                    }
                ]
            ),
            item_images=[_upload("chair.png")],
        )


def test_parse_internal_render_items_form_rejects_count_mismatch():
    with pytest.raises(ValueError, match="item_images count must match items_json count"):
        parse_internal_render_items_form(
            items_json=json.dumps(
                [
                    {
                        "client_id": "item-1",
                        "category": "chair",
                        "qty": 1,
                        "dims_mm": {"width_mm": 600, "depth_mm": 600, "height_mm": 800},
                    }
                ]
            ),
            item_images=[],
        )
```

- [ ] **Step 2: test가 실제로 실패하는지 확인한다.**

Run: `pytest tests/test_internal_render_form_parser.py -v`

Expected: `ModuleNotFoundError` 또는 `ImportError`로 `application.http.internal_render_form_parser`가 아직 없어서 실패한다.

- [ ] **Step 3: parser를 최소 구현한다.**

```python
import json
from typing import Any

from fastapi import UploadFile


_REQUIRED_DIM_KEYS = ("width_mm", "depth_mm", "height_mm")


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and value > 0


def parse_internal_render_items_form(*, items_json: str, item_images: list[UploadFile]) -> list[dict]:
    try:
        raw_items = json.loads(items_json or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError("items_json must be valid JSON") from exc

    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("At least one furniture item is required")

    if len(raw_items) != len(item_images):
        raise ValueError("item_images count must match items_json count")

    normalized: list[dict] = []
    for index, row in enumerate(raw_items, start=1):
        dims = row.get("dims_mm") or {}
        missing = [key for key in _REQUIRED_DIM_KEYS if not _positive_int(dims.get(key))]
        if missing:
            raise ValueError(f"Item {index} is missing required dims: {', '.join(missing)}")

        qty = row.get("qty")
        if not _positive_int(qty):
            raise ValueError(f"Item {index} has invalid qty")

        category = str(row.get("category") or "").strip()
        if not category:
            raise ValueError(f"Item {index} is missing category")

        normalized.append(
            {
                "client_id": str(row.get("client_id") or f"item-{index}"),
                "name": str(row.get("name") or "").strip() or None,
                "category": category,
                "qty": int(qty),
                "dims_mm": {
                    "width_mm": int(dims["width_mm"]),
                    "depth_mm": int(dims["depth_mm"]),
                    "height_mm": int(dims["height_mm"]),
                },
                "upload_index": index - 1,
            }
        )

    return normalized
```

- [ ] **Step 4: parser test만 다시 돌려 통과를 확인한다.**

Run: `pytest tests/test_internal_render_form_parser.py -v`

Expected: 3개 테스트가 모두 `PASSED`.

- [ ] **Step 5: 사용자 승인 후 커밋한다.**

```powershell
git add application/http/internal_render_form_parser.py tests/test_internal_render_form_parser.py
git commit -m "feat: add internal itemized render form parser"
```

### Task 2: 사내웹 itemized payload builder와 파일 저장 helper 분리

**Files:**
- Modify: `render_route_services.py:23-152`
- Test: `tests/test_internal_itemized_render_payloads.py`

- [ ] **Step 1: builder와 helper에 대한 failing test를 작성한다.**

```python
from render_route_services import build_internal_itemized_async_render_job_payload


def test_build_internal_itemized_async_render_job_payload_maps_items_to_moodboard_items():
    payload = build_internal_itemized_async_render_job_payload(
        raw_path="outputs/raw_room.png",
        item_specs=[
            {
                "client_id": "item-1",
                "name": "Boucle Sofa",
                "category": "sofa",
                "qty": 2,
                "dims_mm": {"width_mm": 2200, "depth_mm": 950, "height_mm": 760},
                "upload_index": 0,
            }
        ],
        item_paths=["outputs/item_1.png"],
        room="livingroom",
        style="Customize",
        variant="1",
        dimensions="3000 x 3500 x 2400 mm",
        placement="Keep the sofa on the left wall",
        resolve_image_url=lambda path, prefix=None: f"https://cdn.example/{path.split('/')[-1]}",
        build_s3_prefix=lambda audience, category, suffix=None: f"{audience}/{category}/{suffix or 'root'}",
        build_item_target_key=lambda source, index, label=None, category=None, item_id=None: f"{source}_{category}_{index:03d}",
    )

    item = payload["moodboard_items"][0]
    assert payload["audience"] == "internal"
    assert payload["file_path"] == "https://cdn.example/raw_room.png"
    assert item["qty"] == 2
    assert item["category"] == "sofa"
    assert item["target_key"] == "internal_sofa_001"
```

- [ ] **Step 2: failing 상태를 확인한다.**

Run: `pytest tests/test_internal_itemized_render_payloads.py -v`

Expected: `ImportError` 또는 `AttributeError`로 `build_internal_itemized_async_render_job_payload`가 아직 없어서 실패한다.

- [ ] **Step 3: room 저장 helper, item 저장 helper, itemized builder를 구현한다.**

```python
def persist_internal_room_upload(file: UploadFile) -> str:
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time())
    raw_path = os.path.join("outputs", f"raw_{timestamp}_{unique_id}_{_safe_upload_name(file, 'input.png')}")
    with open(raw_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return raw_path


def persist_internal_item_uploads(item_images: list[UploadFile]) -> list[str]:
    unique_id = uuid.uuid4().hex[:8]
    timestamp = int(time.time())
    saved_paths: list[str] = []
    for index, upload in enumerate(item_images, start=1):
        path = os.path.join("outputs", f"item_{timestamp}_{unique_id}_{index}_{_safe_upload_name(upload, f'item_{index}.png')}")
        with open(path, "wb") as buffer:
            shutil.copyfileobj(upload.file, buffer)
        saved_paths.append(path)
    return saved_paths


def build_internal_itemized_async_render_job_payload(
    *,
    raw_path: str,
    item_specs: list[dict],
    item_paths: list[str],
    room: str,
    style: str,
    variant: str,
    dimensions: str,
    placement: str,
    resolve_image_url: Callable[[str | None, str | None], str | None],
    build_s3_prefix: Callable[[str, str, str | None], str],
    build_item_target_key: Callable[..., str],
) -> dict:
    audience = "internal"
    file_ref = resolve_image_url(raw_path, build_s3_prefix(audience, "mainrendered", "user-photos"))
    moodboard_items = []
    for index, spec in enumerate(item_specs, start=1):
        item_path = item_paths[spec["upload_index"]]
        item_ref = resolve_image_url(item_path, build_s3_prefix(audience, "customize", "item-images")) or item_path
        label = spec.get("name") or spec["category"]
        moodboard_items.append(
            {
                "label": label,
                "path": item_ref,
                "dims_mm": spec["dims_mm"],
                "qty": spec["qty"],
                "category": spec["category"],
                "item_id": spec["client_id"],
                "payload_index": index,
                "target_key": build_item_target_key(
                    "internal",
                    index,
                    label=label,
                    category=spec["category"],
                    item_id=spec["client_id"],
                ),
            }
        )
    return {
        "file_path": file_ref or raw_path,
        "moodboard_items": moodboard_items,
        "room": room,
        "style": style,
        "variant": variant,
        "dimensions": dimensions,
        "placement": placement,
        "audience": audience,
    }
```

- [ ] **Step 4: payload builder test를 다시 돌려 통과를 확인한다.**

Run: `pytest tests/test_internal_itemized_render_payloads.py -v`

Expected: builder 검증 테스트가 `PASSED`.

- [ ] **Step 5: 사용자 승인 후 커밋한다.**

```powershell
git add render_route_services.py tests/test_internal_itemized_render_payloads.py
git commit -m "feat: add internal itemized render payload builder"
```

### Task 3: `/async/render`를 itemized 입력으로 전환하고 `/api/internal/render`를 제거

**Files:**
- Modify: `main.py:1590-1757`
- Modify: `application/http/queue_route_handlers.py:11-43,105-237`
- Modify: `api_models.py:14-24`
- Create: `tests/test_route_surface_smoke.py`

- [ ] **Step 1: route surface와 handler wiring에 대한 failing test를 작성한다.**

```python
from main import app


def test_route_surface_keeps_external_endpoints_and_removes_internal_route():
    paths = {route.path for route in app.routes}
    assert "/async/render" in paths
    assert "/api/external/render/cart" in paths
    assert "/api/external/render/preset" in paths
    assert "/generate-details" in paths
    assert "/regenerate-single-detail" in paths
    assert "/api/internal/render" not in paths
```

- [ ] **Step 2: route smoke test가 실패하는지 확인한다.**

Run: `pytest tests/test_route_surface_smoke.py -v`

Expected: 현재는 `/api/internal/render`가 존재하므로 `AssertionError`로 실패한다.

- [ ] **Step 3: `main.py`, dependency wiring, handler를 itemized 기준으로 변경한다.**

```python
@app.post("/async/render")
@async_wrap
def render_room_async(
    file: UploadFile = File(...),
    room: str = Form(...),
    style: str = Form(...),
    variant: str = Form(...),
    items_json: str = Form(...),
    item_images: List[UploadFile] = File(...),
    dimensions: str = Form(""),
    placement: str = Form(""),
):
    return handle_render_room_async(
        file=file,
        room=room,
        style=style,
        variant=variant,
        items_json=items_json,
        item_images=item_images,
        dimensions=dimensions,
        placement=placement,
        deps=_queue_route_deps(),
    )
```

```python
def handle_render_room_async(
    *,
    file: UploadFile,
    room: str,
    style: str,
    variant: str,
    items_json: str,
    item_images: list[UploadFile],
    dimensions: str,
    placement: str,
    deps: QueueRouteDependencies,
) -> JSONResponse:
    item_specs = deps.parse_internal_render_items_form(items_json=items_json, item_images=item_images)
    raw_path = deps.persist_internal_room_upload(file)
    item_paths = deps.persist_internal_item_uploads(item_images)
    payload = deps.build_internal_itemized_async_render_job_payload(
        raw_path=raw_path,
        item_specs=item_specs,
        item_paths=item_paths,
        room=room,
        style=style,
        variant=variant,
        dimensions=dimensions,
        placement=placement,
        resolve_image_url=deps.resolve_image_url,
        build_s3_prefix=deps.build_s3_prefix,
        build_item_target_key=deps.build_item_target_key,
    )
    return _enqueue_or_error(deps.job_render, payload, queue_name=deps.rq_queue_render, deps=deps)
```

```python
@dataclass
class QueueRouteDependencies:
    parse_internal_render_items_form: Callable[..., list[dict]]
    persist_internal_room_upload: Callable[..., str]
    persist_internal_item_uploads: Callable[..., list[str]]
    build_internal_itemized_async_render_job_payload: Callable[..., dict]
```

- [ ] **Step 4: `/api/internal/render` 관련 타입과 helper를 제거한다.**

```python
# api_models.py
class PresetRenderRequest(BaseModel):
    image_url: str
    ...


# 삭제 대상
# class InternalRenderRequest(BaseModel): ...
```

```python
# render_route_services.py
# 삭제 대상
# def build_internal_render_job_payload(req: InternalRenderRequest) -> dict: ...
```

```python
# main.py
# 삭제 대상
# @app.post("/api/internal/render")
# def api_internal_render(...): ...
```

- [ ] **Step 5: route smoke와 parser/builder 테스트를 함께 돌린다.**

Run: `pytest tests/test_internal_render_form_parser.py tests/test_internal_itemized_render_payloads.py tests/test_route_surface_smoke.py -v`

Expected: 세 파일의 테스트가 모두 `PASSED`.

- [ ] **Step 6: 사용자 승인 후 커밋한다.**

```powershell
git add main.py application/http/queue_route_handlers.py render_route_services.py api_models.py tests/test_internal_render_form_parser.py tests/test_internal_itemized_render_payloads.py tests/test_route_surface_smoke.py
git commit -m "refactor: route internal web renders through itemized async payload"
```

### Task 4: 사내웹 HTML/CSS를 무드보드 UI에서 item card UI로 교체

**Files:**
- Modify: `static/index.html:60-120`
- Modify: `static/css/style.css`
- Test: `tests/test_internal_web_static_contracts.py`

- [ ] **Step 1: 정적 UI 계약에 대한 failing test를 작성한다.**

```python
from pathlib import Path


def test_index_html_uses_itemized_furniture_section():
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert 'id="furniture-items-section"' in html
    assert 'id="furniture-items-list"' in html
    assert 'id="add-furniture-item-btn"' in html
    assert 'id="furniture-item-template"' in html
    assert 'id="moodboard-upload-container"' not in html
    assert 'id="open-mb-gen-btn"' not in html
```

- [ ] **Step 2: failing 상태를 확인한다.**

Run: `pytest tests/test_internal_web_static_contracts.py -v`

Expected: 현재 HTML에는 `furniture-items-section`이 없고 `moodboard-upload-container`가 남아 있으므로 실패한다.

- [ ] **Step 3: `#variant-section` 아래에 itemized UI를 추가하고 moodboard UI를 제거한다.**

```html
<section class="style-section hidden" id="variant-section">
    <h2>Select Variant</h2>
    <div class="style-grid" id="variant-grid"></div>

    <div id="furniture-items-section" class="furniture-items-section hidden">
        <div class="furniture-items-header">
            <div>
                <h3>Furniture Items</h3>
                <p>Add each furniture image with category, quantity, and exact dimensions.</p>
            </div>
            <button id="add-furniture-item-btn" class="action-btn secondary" type="button">Add Item</button>
        </div>
        <div id="furniture-items-list" class="furniture-items-list"></div>
    </div>
</section>

<template id="furniture-item-template">
    <article class="furniture-item-card">
        <div class="furniture-item-upload">
            <input type="file" class="furniture-item-file-input" accept="image/*" hidden>
            <button class="furniture-item-upload-trigger" type="button">Upload Furniture Image</button>
            <img class="furniture-item-preview hidden" alt="Furniture preview">
        </div>
        <div class="furniture-item-grid">
            <input class="furniture-item-name dark-input" type="text" placeholder="Optional item name">
            <select class="furniture-item-category dark-input"></select>
            <input class="furniture-item-qty dark-input" type="number" min="1" step="1" placeholder="Qty">
            <input class="furniture-item-width dark-input" type="number" min="1" step="1" placeholder="Width (mm)">
            <input class="furniture-item-depth dark-input" type="number" min="1" step="1" placeholder="Depth (mm)">
            <input class="furniture-item-height dark-input" type="number" min="1" step="1" placeholder="Height (mm)">
        </div>
        <button class="furniture-item-remove icon-btn" type="button">&times;</button>
    </article>
</template>
```

- [ ] **Step 4: 기존 톤을 유지하는 card 스타일을 추가한다.**

```css
.furniture-items-section {
    margin-top: 1.5rem;
    padding: 1.25rem;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
    background: rgba(255, 255, 255, 0.03);
}

.furniture-item-card {
    position: relative;
    display: grid;
    grid-template-columns: 220px 1fr;
    gap: 18px;
    padding: 18px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 14px;
    background: rgba(20, 20, 20, 0.72);
}

.furniture-item-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
}

.furniture-item-upload-trigger,
.furniture-item-preview {
    width: 100%;
    min-height: 180px;
    border-radius: 12px;
}
```

- [ ] **Step 5: 정적 UI 계약 테스트를 다시 돌려 통과를 확인한다.**

Run: `pytest tests/test_internal_web_static_contracts.py -v`

Expected: HTML id 검증이 `PASSED`.

- [ ] **Step 6: 사용자 승인 후 커밋한다.**

```powershell
git add static/index.html static/css/style.css tests/test_internal_web_static_contracts.py
git commit -m "feat: replace moodboard UI with furniture item cards"
```

### Task 5: 프론트 상태/validation/submit/detail 흐름을 itemized 기준으로 재작성

**Files:**
- Modify: `static/js/script.js:200-225,220-263,391-406,790-950,1176-1323`
- Modify: `tests/test_internal_web_static_contracts.py`

- [ ] **Step 1: JS 계약에 대한 failing test를 추가한다.**

```python
from pathlib import Path


def test_script_uses_itemized_render_payload_and_render_context():
    js = Path("static/js/script.js").read_text(encoding="utf-8")
    assert "const ITEM_CATEGORIES =" in js
    assert "const currentRenderContext =" in js
    assert "items_json" in js
    assert "item_images" in js
    assert "selectedMoodboardFile" not in js
```

- [ ] **Step 2: JS 계약 test가 먼저 실패하는지 확인한다.**

Run: `pytest tests/test_internal_web_static_contracts.py -v`

Expected: 현재 스크립트에는 `selectedMoodboardFile`가 남아 있고 `currentRenderContext`가 없으므로 실패한다.

- [ ] **Step 3: item state와 render context를 명시적으로 도입한다.**

```javascript
const ITEM_CATEGORIES = [
    "sofa",
    "sectional",
    "lounge_chair",
    "chair",
    "dining_chair",
    "table",
    "dining_table",
    "bed",
    "rug",
    "lamp",
    "floor_lamp",
    "table_lamp",
    "decor",
];

const currentRenderContext = {
    detailSourceUrl: null,
    furnitureData: null,
    moodboardUrl: null,
};

let furnitureItems = [];

function createEmptyFurnitureItem() {
    return {
        clientId: crypto.randomUUID(),
        file: null,
        name: "",
        category: "",
        qty: 1,
        widthMm: "",
        depthMm: "",
        heightMm: "",
    };
}
```

- [ ] **Step 4: render 버튼 readiness와 submit payload를 itemized 기준으로 교체한다.**

```javascript
function isFurnitureItemValid(item) {
    return !!item.file
        && !!item.category
        && Number(item.qty) >= 1
        && Number(item.widthMm) > 0
        && Number(item.depthMm) > 0
        && Number(item.heightMm) > 0;
}

function serializeFurnitureItems() {
    return furnitureItems.map((item) => ({
        client_id: item.clientId,
        name: item.name.trim() || null,
        category: item.category,
        qty: Number(item.qty),
        dims_mm: {
            width_mm: Number(item.widthMm),
            depth_mm: Number(item.depthMm),
            height_mm: Number(item.heightMm),
        },
    }));
}

function checkReady() {
    const hasBaseSelection = !!selectedFile && !!selectedRoom && !!selectedStyle && !!selectedVariant;
    const hasValidItems = furnitureItems.length > 0 && furnitureItems.every(isFurnitureItemValid);
    renderBtn.disabled = !(hasBaseSelection && hasValidItems);
}

function buildItemizedRenderFormData() {
    const formData = new FormData();
    formData.append("file", selectedFile);
    formData.append("room", selectedRoom);
    formData.append("style", selectedStyle);
    formData.append("variant", selectedVariant || "1");
    formData.append("dimensions", document.getElementById("room-dimensions")?.value || "");
    formData.append("placement", document.getElementById("placement-instructions")?.value || "");
    formData.append("items_json", JSON.stringify(serializeFurnitureItems()));
    furnitureItems.forEach((item) => formData.append("item_images", item.file));
    return formData;
}
```

- [ ] **Step 5: detail 생성/재생성은 `furniture_data` 중심으로 유지하고 `moodboard_url`은 nullable field로만 남긴다.**

```javascript
function applyRenderResult(data) {
    currentRenderContext.detailSourceUrl = (data.result_urls && data.result_urls[0]) || data.result_url || null;
    currentRenderContext.furnitureData = Array.isArray(data.furniture_data) ? data.furniture_data : null;
    currentRenderContext.moodboardUrl = data.moodboard_url ?? null;
}

const detailPayload = {
    image_url: currentImgUrl,
    moodboard_url: currentRenderContext.moodboardUrl,
    furniture_data: currentRenderContext.furnitureData,
    audience: "internal",
};
```

- [ ] **Step 6: generic `.drop-zone` 선택을 쓰지 말고 id 기반 바인딩으로 교체한다.**

```javascript
const roomDropZone = document.getElementById("upload-area")?.querySelector(".drop-zone");
const furnitureItemsList = document.getElementById("furniture-items-list");
const addFurnitureItemBtn = document.getElementById("add-furniture-item-btn");

roomDropZone?.addEventListener("click", () => fileInput.click());
addFurnitureItemBtn?.addEventListener("click", () => addFurnitureItemCard(createEmptyFurnitureItem()));
```

기대 결과: 새 item card drop zone이 생겨도 기존 메인 업로드 이벤트가 잘못된 `.drop-zone`에 붙지 않는다.

- [ ] **Step 7: 정적 계약 test를 다시 돌려 통과를 확인한다.**

Run: `pytest tests/test_internal_web_static_contracts.py -v`

Expected: HTML/JS 계약 검증이 모두 `PASSED`.

- [ ] **Step 8: 사용자 승인 후 커밋한다.**

```powershell
git add static/js/script.js tests/test_internal_web_static_contracts.py
git commit -m "feat: submit internal renders as itemized furniture payloads"
```

### Task 6: 외부 `/cart`·`/preset` 계약과 메인 render 응답 계약을 테스트로 고정

**Files:**
- Modify: `tests/test_route_helpers.py`
- Create: `tests/test_external_route_contracts.py`
- Create: `tests/test_render_response_contract.py`

- [ ] **Step 1: 외부 계약과 render response 계약에 대한 failing test를 작성한다.**

```python
from application.render.render_response_stage import build_render_response_payload
from render_route_services import build_external_preset_job


def test_build_external_preset_job_keeps_public_shape():
    job_payload, resolved = build_external_preset_job(
        PresetRenderRequest(image_url="https://example.com/room.png", preset_id="preset-1"),
        {"preset-1": {"room": "livingroom", "style": "natural", "variant": "2", "dimensions": "", "placement": ""}},
    )

    assert resolved == {"room": "livingroom", "style": "natural", "variant": "2"}
    assert job_payload["render"]["file_path"] == "https://example.com/room.png"
    assert job_payload["extra"]["resolved"] == resolved


def test_build_render_response_payload_keeps_detail_fields_when_moodboard_is_none():
    payload = build_render_response_payload(
        std_path="room.png",
        step1_img="empty.png",
        scale_guide_path=None,
        generated_results=["result-1.png"],
        moodboard_url=None,
        furniture_data=[
            {
                "label": "Boucle Sofa",
                "target_key": "internal_sofa_001",
                "crop_path": "crop.png",
                "box_2d": [1, 2, 3, 4],
                "source_index": 1,
            }
        ],
        volume_ranking=[{"label": "Boucle Sofa", "volume_rank": 1}],
        prefix_main_user="internal/main/user",
        prefix_main_empty="internal/main/empty",
        prefix_main_rendered="internal/main/rendered",
        resolve_image_url=lambda path, s3_prefix_override=None: f"https://cdn.example/{path}",
    )

    assert payload["moodboard_url"] is None
    assert payload["furniture_data"][0]["target_key"] == "internal_sofa_001"
    assert payload["volume_ranking"] == [{"label": "Boucle Sofa", "volume_rank": 1}]
```

- [ ] **Step 2: handler-level external 응답 형태를 고정하는 test를 추가한다.**

```python
import json
from types import SimpleNamespace

from api_models import CartItem, CartRenderRequest, PresetRenderRequest
from application.http.queue_route_handlers import handle_api_external_render_cart, handle_api_external_render_preset


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {"x-api-key": "external-key"}


def _job(job_id: str = "job-123"):
    return SimpleNamespace(id=job_id)


def test_handle_api_external_render_cart_returns_public_response_shape(fake_deps):
    req = CartRenderRequest(
        image_url="https://example.com/room.png",
        items=[CartItem(id="chair-1", category="chair", image_url="https://example.com/chair.png", qty=1)],
    )
    res = handle_api_external_render_cart(req, _FakeRequest(), deps=fake_deps)
    body = json.loads(res.body)
    assert set(body.keys()) == {"job_id", "status", "cart_kept", "cart_dropped"}


def test_handle_api_external_render_preset_returns_public_response_shape(fake_deps):
    req = PresetRenderRequest(image_url="https://example.com/room.png", preset_id="preset-1")
    res = handle_api_external_render_preset(req, _FakeRequest(), deps=fake_deps)
    body = json.loads(res.body)
    assert set(body.keys()) == {"job_id", "status", "resolved"}
```

- [ ] **Step 3: failing 상태를 확인한다.**

Run: `pytest tests/test_route_helpers.py tests/test_external_route_contracts.py tests/test_render_response_contract.py -v`

Expected: 새 테스트 파일이 없거나 helper assertions가 아직 부족해서 실패한다.

- [ ] **Step 4: 외부 builder/handler와 render response contract test를 모두 통과시킨다.**

```python
# tests/test_route_helpers.py
def test_build_external_cart_job_preserves_target_key_qty_dims_and_payload_index():
    ...
    assert item_refs[0]["qty"] == 1
    assert item_refs[0]["dims_mm"] is not None
    assert item_refs[0]["payload_index"] == 1
```

```python
# tests/test_external_route_contracts.py
def fake_deps():
    return QueueRouteDependencies(
        ...
        enqueue_job=lambda *args, **kwargs: (_job(), None),
        build_external_preset_job=lambda req, preset_map: ({"render": {"file_path": req.image_url}, "extra": {"resolved": {"room": "livingroom", "style": "natural", "variant": "1"}}}, {"room": "livingroom", "style": "natural", "variant": "1"}),
        build_external_cart_job=lambda *args, **kwargs: ({"render": {"file_path": "https://example.com/room.png"}}, [{"id": "chair-1"}], []),
    )
```

기대 결과: preset/cart 응답 키와 queue payload shape를 바꾸지 않았다는 테스트 증거가 생긴다.

- [ ] **Step 5: 계약 테스트를 다시 돌려 통과를 확인한다.**

Run: `pytest tests/test_route_helpers.py tests/test_external_route_contracts.py tests/test_render_response_contract.py -v`

Expected: 외부 contract 및 render response contract 검증이 모두 `PASSED`.

- [ ] **Step 6: 사용자 승인 후 커밋한다.**

```powershell
git add tests/test_route_helpers.py tests/test_external_route_contracts.py tests/test_render_response_contract.py
git commit -m "test: lock external route contracts and render response shape"
```

### Task 7: detail chain 회귀 검증과 최종 확인

**Files:**
- Modify: `tests/test_detail_metadata.py`
- Optional Modify: `live_validate_render_flows.py`

- [ ] **Step 1: detail chain에 필요한 메타데이터가 유지되는 failing test를 추가한다.**

```python
from application.render.render_result_stage import build_detail_payload


def test_build_detail_payload_keeps_furniture_data_even_without_moodboard_url():
    payload = build_detail_payload(
        {
            "result_url": "https://cdn.example/result.png",
            "moodboard_url": None,
            "furniture_data": [
                {
                    "label": "Boucle Sofa",
                    "target_key": "internal_sofa_001",
                    "crop_path": "crop.png",
                    "box_2d": [1, 2, 3, 4],
                    "source_box_2d": [1, 2, 3, 4],
                    "box_source": "main_render",
                    "volume_rank": 1,
                    "volume_proxy": 1000,
                }
            ],
        },
        audience="internal",
    )

    assert payload["moodboard_url"] is None
    assert payload["furniture_data"][0]["target_key"] == "internal_sofa_001"
```

- [ ] **Step 2: detail metadata test가 먼저 실패하는지 확인한다.**

Run: `pytest tests/test_detail_metadata.py -v`

Expected: 새 assertion이 없거나 helper import가 빠져 있다면 실패한다.

- [ ] **Step 3: detail 결과와 재생성 경로가 보는 메타 키를 모두 고정한다.**

```python
def test_attach_regenerated_target_metadata_keeps_box_and_volume_fields():
    enriched = attach_regenerated_target_metadata(
        output={"style_name": "Detail: Boucle Sofa"},
        style={"name": "Detail: Boucle Sofa", "target_key": "internal_sofa_001", "target_label": "Boucle Sofa"},
        analyzed_items=[
            {
                "label": "Boucle Sofa",
                "target_key": "internal_sofa_001",
                "box_2d": [10, 10, 20, 20],
                "source_box_2d": [1, 1, 2, 2],
                "box_source": "main_render",
                "volume_rank": 1,
                "volume_proxy": 1000,
            }
        ],
        normalize_label_for_match=lambda text: str(text).strip().lower(),
    )
    assert enriched["target_box_source"] == "main_render"
    assert enriched["target_volume_rank"] == 1
```

- [ ] **Step 4: 전체 대상 테스트와 route smoke를 묶어서 실행한다.**

Run: `pytest tests/test_internal_render_form_parser.py tests/test_internal_itemized_render_payloads.py tests/test_route_surface_smoke.py tests/test_internal_web_static_contracts.py tests/test_route_helpers.py tests/test_external_route_contracts.py tests/test_render_response_contract.py tests/test_detail_metadata.py -v`

Expected: 전체 핵심 회귀 테스트가 모두 `PASSED`.

- [ ] **Step 5: 수동 smoke를 한 번 실행한다.**

```powershell
@'
from main import app
paths = {route.path for route in app.routes}
assert "/api/internal/render" not in paths
assert "/async/render" in paths
assert "/api/external/render/cart" in paths
assert "/api/external/render/preset" in paths
print("route surface ok")
'@ | python -
```

수동 QA 체크리스트:
- 사내웹에서 moodboard 업로드 섹션이 보이지 않는다.
- furniture item card를 2개 이상 추가/삭제할 수 있다.
- category dropdown은 클릭 시 열리고 선택 후 닫힌다.
- W/D/H 중 하나라도 비우면 render 버튼이 비활성화된다.
- 메인 render 완료 후 detail 생성이 정상 동작한다.
- detail retry가 `target_key`를 유지한 채 다시 생성된다.

- [ ] **Step 6: 사용자 승인 후 커밋한다.**

```powershell
git add tests/test_detail_metadata.py
git commit -m "test: preserve detail chain metadata for internal itemized renders"
```

## Self-Review

### 1. Spec coverage

- 사내웹 무드보드 제거: Task 4, Task 5
- 사내웹 itemized 입력 + W/D/H 필수: Task 1, Task 5
- `/api/internal/render` 제거: Task 3
- 외부 `/cart`, `/preset` 계약 불변: Task 6
- 공통 render command로 정규화: Task 2, Task 3
- detail 생성/재생성 continuity 유지: Task 5, Task 7
- 기존 CSS 톤 유지: Task 4

누락 없음. detail 체인과 외부 계약 회귀를 별도 task로 분리해 스펙의 고위험 요구를 직접 커버한다.

### 2. Placeholder scan

- 금지된 placeholder 표현이 남아 있지 않은지 확인했다.
- 각 task에 구체 파일 경로, 테스트 코드, 실행 명령, 기대 결과를 넣었다.
- manual QA도 체크리스트로 구체화했다.

### 3. Type consistency

- 내부 multipart parser가 반환하는 키는 `client_id`, `category`, `qty`, `dims_mm`, `upload_index`로 Task 1에서 정의했고, Task 2 payload builder가 같은 키를 소비하도록 맞췄다.
- render 결과에서 detail chain으로 내려가는 핵심 키는 `moodboard_url`, `furniture_data`, `target_key`, `box_2d`, `source_box_2d`, `box_source`, `volume_rank`, `volume_proxy`로 Task 6~7 전체에서 동일하게 사용했다.
- 외부 route regression test의 public 응답 키는 `job_id`, `status`, `resolved`, `cart_kept`, `cart_dropped`로 고정했다.
