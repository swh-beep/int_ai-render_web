# 사내웹 아이템 단위 렌더 구조 개편 설계

## 목표

사내웹의 무드보드 업로드 기반 입력 방식을 가구 아이템 단위 입력 방식으로 교체한다. 이를 통해 수동 화이트보드 합성, 컷아웃 분리, OCR 기반 치수 추출 의존을 제거한다.

## 문제 정의

현재 사내웹은 여러 가구 이미지와 치수 텍스트가 한 장의 무드보드 이미지 안에 들어가는 구조에 의존한다. 이 구조는 다음 문제를 반복적으로 만든다.

- 사용자가 렌더 전에 화이트보드 형태의 무드보드를 직접 만들어야 한다.
- 업로드 이후에도 가구 컷아웃 분리 과정이 필요하다.
- 치수 텍스트가 빠지거나 위치가 들쭉날쭉하다.
- OCR이 치수를 자주 놓치거나 불완전하게 읽는다.
- 사내웹 입력 모델이 더 구조화된 외부 `/cart` 흐름과 어긋나 있다.

결과적으로 불필요한 수작업이 많고, 입력 품질이 약하며, 렌더 분석 단계에서도 해석 불확실성이 커진다.

## 확정된 방향

세 개의 표면 엔드포인트는 유지하되, 내부에서는 하나의 공통 렌더 커맨드로 정규화한다.

- 사내웹은 계속 `/async/render`를 사용한다.
- 외부 국내 웹은 계속 `/api/external/render/cart`를 사용한다.
- 외부 해외 웹은 계속 `/api/external/render/preset`을 사용한다.
- `/api/internal/render`는 제거한다.

즉, 바깥쪽 엔드포인트는 각자 역할을 유지하고, 안쪽 렌더 파이프라인만 공통 구조로 통합한다.

## 하드 제약

외부 웹 두 개가 사용하는 계약은 절대 바꾸지 않는다.

다음 엔드포인트의 외부 계약은 그대로 유지되어야 한다.

- `/api/external/render/cart`
- `/api/external/render/preset`

여기서 “계약 유지”는 다음 전부를 포함한다.

- 요청 필드 이름
- 요청 전송 방식
- 응답 필드 이름
- 응답 envelope 구조
- 비동기 job 동작 방식

즉, 내부 리팩터링은 가능하지만 외부 호출자가 체감하는 계약 변화는 허용하지 않는다.

## 목표 사용자 흐름

### 사내웹

사내웹 렌더 페이지는 기존의 방 이미지 업로드, 공간 치수 입력, 스타일 선택 흐름은 유지한다. 대신 무드보드 업로드를 제거하고, 반복 가능한 가구 아이템 입력 UI로 교체한다.

새로운 사용자 흐름은 다음과 같다.

1. 방 이미지를 업로드한다.
2. 공간 치수를 입력한다.
3. 배치 지시 문구를 입력한다.
4. 방 타입, 스타일, 변형(variant)을 선택한다.
5. 하나 이상의 가구 아이템을 추가한다.
6. 각 아이템마다 이미지, 카테고리, 수량, 치수를 입력한다.
7. 렌더를 실행한다.

### 가구 아이템 카드

각 가구 아이템 카드는 다음 필드를 가진다.

- 가구 이미지 업로드
- 카테고리 dropdown
- 수량 입력
- 너비(mm) 입력
- 깊이(mm) 입력
- 높이(mm) 입력
- 아이템 삭제 버튼

가구 아이템 치수는 모두 필수다.

이미지는 컷아웃 이미지와 일반 상품 이미지 둘 다 허용한다.

## 카테고리 정책

사내웹 카테고리 dropdown은 현재 cart 계열 흐름에서 쓰는 실사용 카테고리를 그대로 노출한다.

- `sofa`
- `sectional`
- `lounge_chair`
- `chair`
- `dining_chair`
- `table`
- `dining_table`
- `bed`
- `rug`
- `lamp`
- `floor_lamp`
- `table_lamp`
- `decor`

dropdown 동작 방식은 단순하게 유지한다.

- 기본은 닫힌 상태
- 클릭하면 열림
- 하나를 선택하면 즉시 닫힘

비주얼 스타일은 기존 사내웹 CSS 문법을 그대로 따른다. 새 디자인 방향을 도입하지 않는다.

## 입력 계약 설계

### 표면 엔드포인트 계약

표면 엔드포인트는 각자 자연스러운 전송 방식을 유지한다.

- `/async/render`는 브라우저 파일 업로드이므로 `multipart`를 유지한다.
- `/api/external/render/cart`는 URL과 구조화된 아이템 payload를 받으므로 `JSON`을 유지한다.
- `/api/external/render/preset`은 preset 해석 기반이므로 `JSON`을 유지한다.

### 공통 내부 커맨드

위 엔드포인트들은 내부에서 하나의 공통 렌더 커맨드로 정규화된다. 개념적으로는 다음 필드를 가진다.

- 방 이미지 소스
- room type
- style
- variant
- room dimensions
- placement instructions
- audience
- reference source

`reference source`는 태그된 형태로 분기한다.

- `items`
- `preset`

사내웹은 더 이상 moodboard 기반 reference mode를 사용하지 않는다.

### 사내웹 아이템 payload 형태

사내웹 아이템 payload는 현재 render 파이프라인이 이미 활용 중인 cart 스타일 구조에 맞춘다.

```json
{
  "id": "generated-client-item-id",
  "category": "sofa",
  "image": "<uploaded file>",
  "qty": 1,
  "dims_mm": {
    "width_mm": 2200,
    "depth_mm": 900,
    "height_mm": 750
  },
  "name": "optional display name"
}
```

브라우저 어댑터에서는 업로드된 파일들을 이후에 현재 렌더 워크플로우가 이미 이해하는 `moodboard_items` 형태의 내부 참조로 물질화한다.

## 아키텍처 변경 방향

### 유지할 것

- 표면별 라우트 분리
- 기존 render workflow core
- 기존 cart 스타일 item analysis 경로
- 기존 preset resolution 경로

### 제거할 것

- 사내웹 메인 렌더용 무드보드 업로드 경로
- 사용자 입력 치수를 OCR로 복원하던 의존
- `/api/internal/render`

### 재사용할 것

현재 render core는 이미 아이템 단위 reference 경로를 지원한다. 이번 개편은 사내웹 전용 새 워크플로우를 만드는 대신, 그 경로를 사내웹에도 재사용하는 것이 핵심이다.

재사용 핵심 지점은 다음과 같다.

- itemized reference preparation
- item 기반 analysis path
- 명시적 `dims_mm` 전달
- category 기반 target key 생성
- 더 강한 제품 identity metadata를 활용하는 detail generation

## 검증 규칙

### 사내웹 요청 검증

다음 조건을 모두 만족하지 않으면 렌더 요청을 막는다.

- 방 이미지 존재
- room type 선택
- style 선택
- variant 선택
- 가구 아이템 1개 이상 존재

각 가구 아이템은 다음 조건을 모두 만족하지 않으면 무효 처리한다.

- 이미지 존재
- 카테고리 선택
- 수량은 최소 `1`
- width(mm) 입력
- depth(mm) 입력
- height(mm) 입력

room dimensions는 별도의 방 단위 입력으로 유지하며, 기존처럼 render pipeline에 전달한다.

### UX 레벨 검증

사내웹은 요청을 보내기 전에 프론트에서 submission을 차단해야 한다. 에러는 아이템 카드 단위로 보여줘서 어느 줄이 비어 있는지 바로 알 수 있어야 한다.

## API 및 어댑터 전략

### `/async/render`

이 엔드포인트는 사내웹 전용 업로드 어댑터로 유지한다. 다만 이제는 단일 무드보드 파일이 아니라 반복 가능한 가구 아이템 입력을 받을 수 있어야 한다.

책임은 다음으로 제한한다.

- 브라우저 업로드 수신
- 업로드 파일 저장
- 사내웹 아이템 카드 입력을 cart 유사 item reference로 정규화
- 공통 내부 렌더 커맨드 호출

즉, cart 분석 로직을 복제하는 비즈니스 로직이 이 안에 들어가면 안 된다.

### `/api/external/render/cart`

이 엔드포인트는 외부 structured-item 어댑터로 그대로 유지한다. 현재처럼 cart 스타일 reference를 만들고, 그 public request/response 계약은 바뀌지 않아야 한다.

### `/api/external/render/preset`

이 엔드포인트는 preset 어댑터로 그대로 유지한다. preset metadata를 해석한 뒤 공통 내부 렌더 커맨드로 매핑하되, itemized request인 척하지 않는다. 이 public request/response 계약도 바뀌지 않아야 한다.

### `/api/internal/render`

이 엔드포인트는 제거한다. 제거 이유는 다음과 같다.

- 사내웹이 사용하지 않는다.
- 내부용 render surface가 중복된다.
- 승인된 구조는 브라우저 업로드 어댑터 1개와 외부 JSON 어댑터 2개만 남기는 방향이다.

## UI 구조

사내웹 페이지는 전체 레이아웃과 기존 스타일을 유지한다. 실제 구조 변화는 reference input 영역에 집중된다.

### UI에서 제거할 요소

- 무드보드 업로드 섹션
- 무드보드 preview 섹션
- 메인 렌더 흐름의 무드보드 자동 생성 진입점

### UI에 추가할 요소

- furniture items section
- add-item 버튼
- 반복 가능한 furniture item card
- 카드별 category dropdown
- 카드별 qty 입력
- 카드별 width/depth/height 입력

### 상호작용 모델

- 사용자는 여러 item card를 추가할 수 있다.
- 사용자는 각 item card를 삭제할 수 있다.
- category selector는 클릭 시 열리고, 선택 시 닫힌다.
- render 버튼은 상위 필드와 item 필드가 모두 유효할 때만 활성화된다.

## 마이그레이션 전략

### 1단계: 공통 커맨드 정규화

`/async/render`, `/api/external/render/cart`, `/api/external/render/preset`이 모두 하나의 공통 내부 render command로 매핑되도록 정리한다.

### 2단계: 사내웹 UI 전환

무드보드 업로드 UI를 item card 입력 UI로 교체한다. 이때 기존 style/room/variant 선택과 결과 표시 동작은 유지한다.

### 3단계: 사내웹 어댑터 변경

`/async/render`가 itemized furniture 입력을 받을 수 있도록 확장하고, 이를 공통 render command로 정규화한다.

### 4단계: 정리 작업

`/api/internal/render`와 관련 모델, route wiring, 해당 surface 전용 helper code를 제거한다.

### 5단계: 회귀 검증

최종적으로 세 개의 살아남는 표면을 검증한다.

- 사내웹 render
- external cart render
- external preset render

## 기대 효과

- 사내팀의 사전 수작업 감소
- 화이트보드 무드보드 조합 작업 제거
- 사용자 입력 치수에 대한 OCR 의존 제거
- 가구 metadata 품질과 일관성 향상
- 사내웹과 cart 스타일 render analysis 정렬
- render orchestration 경계에서의 중복 감소

## 리스크

### 프론트 상태 관리 복잡도 증가

사내웹 폼은 더 구조화되고 stateful해진다. 하지만 기존 구조는 그 복잡도를 취약한 수동 무드보드 단계로 떠넘기고 있었기 때문에, 이 증가는 허용 가능한 수준이다.

### 검증 부담 증가

모든 아이템 치수를 필수로 만들면 입력 부담은 늘어난다. 하지만 현재 문제의 핵심이 누락되거나 잘못 배치된 치수 때문에 발생하는 품질 저하이므로, 이 tradeoff는 승인된 방향이다.

### 숨겨진 `/api/internal/render` 호출자 가능성

현재 승인된 설계는 `/api/internal/render`가 실제로 쓰이지 않는다는 운영 가정 위에 서 있다. 저장소 밖에서 숨은 호출자가 있다면 제거 시 깨질 수 있다. 이번 설계는 현재 사용자 판단을 기준으로 제거를 수용한다.

## 범위 밖

- 사내웹 비주얼 언어 재설계
- 외부 preset 흐름의 의미 변경
- `/api/external/render/cart`의 public request/response 계약 변경
- `/api/external/render/preset`의 public request/response 계약 변경
- 세 엔드포인트를 하나의 public API로 합치기
- v1에서 radius 전용 입력 UI 도입
- source metadata 개선 범위를 넘는 detail generation 목표 변경

## 완료 기준

다음을 모두 만족하면 이 설계는 충족된 것으로 본다.

- 사내웹 메인 렌더 흐름에서 무드보드 업로드가 사라진다.
- 사내웹이 이미지, 카테고리, 수량, 필수 치수를 가진 여러 가구 아이템을 입력받을 수 있다.
- `/async/render`가 itemized internal-web 입력을 받아 정규화한다.
- `/api/external/render/cart`와 `/api/external/render/preset`은 계속 동작한다.
- `/api/external/render/cart`의 request/response 계약은 바뀌지 않는다.
- `/api/external/render/preset`의 request/response 계약은 바뀌지 않는다.
- 세 개의 살아남는 표면이 하나의 공통 내부 render command로 합류한다.
- `/api/internal/render`가 제거된다.
- 사내웹 스타일은 기존 CSS와 시각적으로 일관된다.
