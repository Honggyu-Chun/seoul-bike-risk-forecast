# 백엔드 인수 명세

> Draft v0.1 · 데이터 조회 계약과 AI 호출을 구현할 담당자를 위한 문서다. SQLite는 참조 구현이며 팀 DB 선택을 제한하지 않는다.

## 1. 제공해야 할 경계

팀 백엔드는 수집·저장 계층의 세 내부 API를 구현하거나 참조 서버를 연결한다. AI 서비스는 초기 이력과 변경분을 캐시에 동기화해 공통 특징을 생성한다. 사용자 조회마다 전체 과거 데이터를 다시 읽거나 모델을 재학습하지 않는다.

프론트용 대여소 목록·사용자 인증·AI 중계 API는 애플리케이션 백엔드가 별도로 구현한다. AI가 쓰는 서비스 토큰은 서버 환경변수/비밀 저장소에서 읽는다. 실제 공공 API 키와 서비스 토큰은 서로 다른 자격정보다.

| 방향 | 계약 | 설명 |
| --- | --- | --- |
| AI → 데이터 백엔드 | `GET /internal/ai-data/v1/stations` | 대여소 ID·좌표·이력 |
| AI → 데이터 백엔드 | `GET /internal/ai-data/v1/observations` | 재고·기상 실황 등 수신 이력 |
| AI → 데이터 백엔드 | `GET /internal/ai-data/v1/forecasts` | 발표본·목표 시각별 예보 |
| 앱 백엔드 → AI | `/v1/status`, `/v1/predictions`, `/v1/recommendations` | [AI 계약](API_REFERENCE.md) |

## 2. 페이지 계약

세 경로 모두 Bearer 인증, 200의 body는 Page다. [데이터 OpenAPI](../cycleon_backend_openapi.json)가 타입의 기준이다.

| 쿼리 | 범위·기본 | 적용 |
| --- | --- | --- |
| `cursor` | 정수 ≥0, 기본 0 | 세 API |
| `limit` | 정수 1~10000, 기본 1000 | 세 API |
| `watermark` | 정수 ≥0, 처음 생략 | 세 API |
| `source` | 문자열, 생략 가능 | observations/forecasts |
| `start`, `end` | timezone 있는 ISO 8601, 생략 가능 | observations/forecasts의 **received_at** 필터, 양 끝 포함 |

```json
{
  "items":[{"cursor":1,"conflict":false,"capture_id":null,"record":{"...":"Station / Observation / Forecast"}}],
  "next_cursor":1,"watermark":2,"has_more":true,"captures":[]
}
```

이 예시는 봉투 구조만 나타낸다. 검증 가능한 전체 record는 [합성 예시](mock_examples.json)의 `backend_*_page`를 사용한다.

1. 처음에는 cursor=0으로 요청하고 첫 응답 watermark를 고정한다.
2. has_more=true이면 **같은 필터·watermark**와 next_cursor로 이어 읽는다.
3. 끝까지 읽었으면 next_cursor를 체크포인트로 저장한다. 새 동기화는 그 cursor에서 watermark를 생략해 새 경계를 받는다.
4. source/start/end를 바꾸면 별도 스트림·커서로 관리한다.
5. 끝 페이지가 비어 있어도 next_cursor는 watermark까지 진행할 수 있다. 필터 밖 수신 순서를 다시 읽을 필요는 없다.

cursor는 관측 시각/record ID가 아니라 테이블별 **커밋된 수신·변경 순번**이다. 뒤늦게 받은 과거 관측과 충돌 수정도 새 cursor를 갖는다. 동일 revision·동일 값을 다시 받아도 최초 received_at을 덮지 않는다. source 쪽은 DB 트랜잭션 커밋 순서대로 단조 증가하도록 보장한다.

현재 `HTTPBackend.sync()`의 초기 기본 범위는 최근 170시간이며 `start`를 한 번 저장한 후 고정한다. 늦게 온 자료를 놓치지 않도록 매 poll마다 start를 현재 시각으로 옮기지 않는다. 더 긴 초기 일괄 이력 제공과 보존 정책은 팀에서 정해야 한다. 전체 2022~2025 연구 원본은 사용자 요청당 전송할 자료가 아니다.

## 3. Station

| 필드 | 타입·조건 | 설명 |
| --- | --- | --- |
| `station_id`, `source_station_id` | string 길이 1~80, 필수 | 내부 공통 ID·공급자 ID |
| `name` | string, 필수 | 표시 이름 |
| `latitude`, `longitude` | number 또는 null, 필수 키 | 범위 33~39 / 124~132 |
| `valid_from`, `received_at` | datetime, 필수 | 현재 자료의 적용 시작·수신 시각 |
| `valid_to` | datetime 또는 null, 기본 null | 종료가 있으면 valid_from보다 뒤 |
| `source` | string, 기본 seoul_bike | 출처 |
| `revision` | string, 필수 | 공급자 또는 수신 기반 버전 |
| `identity_status` | verified/unverified, 기본 verified | verified는 좌표 필수 |
| `validity_basis` | first_seen_receipt / source_effective_interval | 기본 first_seen_receipt |
| `source_valid_from`, `source_valid_to` | datetime 또는 null | 공급자가 제공한 실제 적용 기간만 저장 |

`station_id`는 클라이언트·관측·모델 연결에 쓰는 문자열이다. 예: 현재 API의 `ST-4`가 과거 번호 `102`에 연결되는 것은 검수된 매핑 결과이지 일반 변환 규칙이 아니다. 명칭의 숫자만 추출해 만들지 않는다.

공급자 개소일을 모르면 valid_from은 최초 수신 기반 보수적 적용 구간으로 두고 validity_basis로 구분한다. 좌표 이전·폐소·ID 재사용은 새 revision/적용 구간으로 보존한다. 검증되지 않은 ID·좌표는 unverified로 남겨 예측/추천 대상에서 제외할 수 있어야 한다.

## 4. Observation

| 필드 | 타입·기본 | 설명 |
| --- | --- | --- |
| `source`, `entity_id`, `event_key`, `variable`, `unit` | string, 필수 | 출처·대상·사건키·변수·단위 |
| `value` | number 또는 null, 필수 키 | valid이면 null 불가 |
| `received_at` | datetime, 필수 | 실제 수신 시각 |
| `observed_at`, `issued_at`, `valid_at` | datetime 또는 null | 미제공 시 null. 생성하지 않음 |
| `revision` | string, 기본 0 | 수정 버전 |
| `quality` | valid/missing/invalid, 기본 valid | 누락·비정상·정상 0 구분 |
| `timing_basis` | received / observed_and_received / issued_and_received / historical_proxy | 기본 received |
| `raw_value` | string 또는 null, 길이 ≤160 | 제한된 원래 값. 인증 URL/토큰 금지 |

재고는 `source=seoul_bike`, `variable=inventory`, `unit=bikes`, value는 0 이상 정수다. entity_id는 Station.station_id와 같아야 한다. bikeList에는 실제 관측 시각이 없어 observed_at=null로 둔다. 시각 없는 원본을 received_at으로 관측했다고 위장하지 않는다.

현재 source 예시는 `kma_current`(실황)이며 기상 변수·단위는 T1H/degC, RN1/mm, REH/%, WSD/m/s, PTY/code다. 그리드 entity_id 형식은 `60,127`처럼 `nx,ny`다. 실황의 baseDate/baseTime은 issued_at으로 보존하고 별도 observed_at은 null이다. historical_proxy 자료는 실제 운영 특징에서 사용하지 않는다.

## 5. Forecast

| 필드 | 타입·기본 | 설명 |
| --- | --- | --- |
| `source`, `grid_id`, `variable`, `unit` | string, 필수 | 현재 source=kma_forecast, 격자 nx,ny |
| `value` | number 또는 null, 필수 키 | valid이면 null 불가 |
| `issued_at`, `valid_at`, `received_at` | datetime, 필수 | 발표·목표·수신 시각을 별도 보존 |
| `revision`, `quality`, `raw_value` | Observation과 동일 | 수정 버전·품질 |

같은 목표 시각의 여러 발표본을 모두 보존한다. 특징 생성은 기준 t 이전의 발표·수신 조건과 목표 정각을 만족하는 자료만 선택한다. 나중에 조회한 과거 API 응답의 received_at은 현재 시각이며 과거 발표 때 수신한 것으로 바꾸지 않는다.

## 6. Capture·충돌·누락

captures의 각 항목은 `capture_id`, `source`, `started_at`, `completed_at`, `expected_rows`, `actual_rows`, `state`, `summary_json`을 갖는다. summary_json은 JSON 객체가 아니라 **문자열**이다. Observation 페이지의 item.capture_id로 연결한다.

서울 전체 재고 조회가 여러 페이지면 모두 모은 뒤 원자적으로 complete 캡처를 공개한다. 한 페이지만 성공한 자료로 직전 완전 캡처를 덮지 않는다. 한 complete 캡처가 API 응답 여러 페이지에 나뉘어도 expected_rows/actual_rows는 **전체 캡처 수량**이다. AI 캐시는 모든 행이 들어올 때까지 importing으로 둔다.

중복 충돌은 item.conflict=true로 전달하고 정상 0대로 바꾸지 않는다. 동일 revision의 서로 다른 값은 충돌로 보존하고 새 변경 cursor를 발행한다. 누락은 value=null/quality=missing이며 실제 0대는 value=0/quality=valid다. 필수 자료 누락으로 모델이 null을 반환하는 것은 정상 업무 응답이다.

## 7. 서버에서 AI 호출 예시

아래 TypeScript는 Node 서버 측 예시다. 프론트에 import하지 않는다. AI/데이터 백엔드의 서로 다른 base URL을 혼동하지 않는다.

```typescript
async function predictStations(stationIds: string[]) {
  const base = process.env.CYCLEON_AI_BASE_URL;
  const token = process.env.CYCLEON_SERVICE_TOKEN;
  if (!base || !token) throw new Error('CycleOn server settings missing');
  const response = await fetch(`${base.replace(/\/$/, '')}/v1/predictions`, {
    method: 'POST',
    headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'},
    body: JSON.stringify({station_ids: stationIds, horizons_hours: [1, 2, 3]}),
    signal: AbortSignal.timeout(10000), // 애플리케이션 예시값. 서비스 SLA 아님
  });
  if (!response.ok) throw new Error(`CycleOn HTTP ${response.status}`);
  return await response.json(); // 운영 코드는 OpenAPI 기반 타입/런타임 검증 권장
}
```

응답의 null·보류 사유·시각·모델 정보를 프론트로 전달한다. 200+null을 500으로 바꾸거나 확률 누락을 안전으로 변환하지 않는다. 원시 토큰·인증 헤더·외부 공급자 인증 URL은 로그에 남기지 않는다.

## 8. 인계 시 합의할 항목

- 프론트 공개 중계 경로와 사용자 인증, 대여소 목록/좌표 API.
- 실서버 실행 주체·SSH 접속·서비스 토큰 전달·장애 담당자. 현재 listener 부재는 [현황](README.md#현재-사용할-수-있는-것) 참조.
- 팀 데이터 DB와 소스별 수집 주기/계정 한도·재시도·보존 기간.
- 추가 응답 필드나 계약 변경은 PR로 합의하고 코드·OpenAPI·예시를 함께 수정.

자료 계약 구현·모의 호출은 지금 진행할 수 있다. 최종 모델·추천정책·독립평가 결과는 모델 담당이 후속 번들로 전달한다.
