# AI API 레퍼런스 · v1

> Draft v0.1 · 기계 명세: [OpenAPI](../cycleon_ai_openapi.json). 아래 필드명은 `contracts.py`와 실제 라우터 기준이다.

## 공통 규칙

- JSON 요청/응답, `Content-Type: application/json`, Bearer 인증.
- 서버 간 요청: `Authorization: Bearer <CYCLEON_SERVICE_TOKEN>`. 브라우저에 비밀 토큰을 배포하지 않는다.
- 입력에 정의되지 않은 필드는 422. `NaN`/무한대는 허용하지 않는다.
- 시각은 UTC 오프셋이 있는 ISO 8601이며 `Z` 또는 `+00:00`을 UTC로 읽는다. UI에서 KST로 변환한다.
- 현재 AI 요청은 `horizon_hours` 또는 `horizons_hours`를 받는다. 임의 `target_at`/과거 날짜는 입력할 수 없다.
- 상태·확률·판정·최신 재고는 의미가 다르다. `null`을 0 또는 안전으로 바꾸지 않는다.

### 시간 의미

14:40 KST 요청에서 기준 `t=14:00`, 목표는 15:00/16:00/17:00 KST다. **조회 순간부터 60/120/180분 뒤가 아니다.** `base_at`과 `target_at`을 그대로 사용한다. 정각을 넘으면 이전 캐시의 목표와 새 요청 목표가 달라질 수 있다.

모델 입력은 `t`까지 이용 가능한 정보다. 14:40의 최신 수량은 14:00 모델 입력으로 소급하지 않는다. 최신 재고는 별도 표시 및 추천 자격 판단에 쓴다.

## GET /v1/status

요청 body 없음. 200은 HTTP 처리 성공이며 `status`가 준비 상태다.

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `service` | `"CycleOn"` | 서비스 식별 |
| `status` | `ready / not_ready` | 현재 기준 정각에 적어도 한 건의 유효 확률이 있으면 ready |
| `server_at`, `base_at` | datetime | 서버 시각·현재 기준 정각 |
| `supported_targets` | datetime[] | 현재 h1/h2/h3 목표 정각 |
| `model_bundle_id`, `model_stage` | string 또는 null | 현재 번들과 개발/선정/모의 상태 |
| `last_precompute_at` | datetime 또는 null | 마지막 사전 계산 시각 |
| `precompute_error` | string 또는 null | 사전 계산 실패 종류 |
| `data` | object | 자료 요약. 상세 내부 키를 UI 필수 계약으로 가정하지 않음 |
| `latest_inventory_age_limit_seconds` | integer | 현재 300초 |
| `time_policy` | string | 기준 정각과 최신 재고 분리 정책 |

`ready`는 모든 대여소 예측 가능·현재 재고 신선·운영 승인 완료를 뜻하지 않는다. 대여소별 응답과 `model_stage`를 함께 본다. 서버가 내려간 경우 이 응답 자체가 없으므로 네트워크 오류 화면이 필요하다.

## POST /v1/predictions

### 요청

```json
{"station_ids":["ST-MOCK-1","ST-UNKNOWN"],"horizons_hours":[1,2,3]}
```

| 필드 | 타입·제약 | 필수/기본 |
| --- | --- | --- |
| `station_ids` | string[] 1~500건, 각 길이 1~80 | 필수. 내부 데이터 API의 station_id 사용 |
| `horizons_hours` | 1/2/3 정수 배열, 1~3건 | 생략 시 `[1,2,3]` |

중복 ID·간격은 최초 등장 순서로 중복 제거한다. 응답 개수는 고유 ID×고유 간격이며 최대 1,500건이다. `102` 같은 역사상 번호와 `ST-4` 같은 API ID를 임의 변환하지 않는다.

### 200 응답 예시: 합성·일부 누락

```json
{
  "base_at":"2026-10-29T05:00:00Z",
  "requested_at":"2026-10-29T05:40:00Z",
  "items":[
    {
      "station_id":"ST-MOCK-1","horizon_hours":1,"target_at":"2026-10-29T06:00:00Z",
      "risk_probability":0.2,"is_risk":false,"model_id":"mock-fixture","reason":null,"input_group":"C",
      "latest_inventory":2,"latest_inventory_received_at":"2026-10-29T05:39:30Z",
      "latest_inventory_age_seconds":30,"latest_inventory_observed_at":null
    },
    {
      "station_id":"ST-UNKNOWN","horizon_hours":1,"target_at":"2026-10-29T06:00:00Z",
      "risk_probability":null,"is_risk":null,"model_id":null,"reason":"station_unknown_or_ineligible","input_group":null,
      "latest_inventory":null,"latest_inventory_received_at":null,
      "latest_inventory_age_seconds":null,"latest_inventory_observed_at":null
    }
  ]
}
```

위 응답은 요청의 h1만 보여주는 축약 예시다. 전체 3간격 예시는 [mock_examples.json](mock_examples.json)의 `prediction_response`에 있다.

| item 필드 | 타입 | 의미 |
| --- | --- | --- |
| `station_id` | string | 요청 대여소 |
| `horizon_hours` | 1/2/3 | 기준 정각으로부터 시간 간격 |
| `target_at` | datetime | 이 확률의 목표 정각 |
| `risk_probability` | number 0~1 또는 null | 목표 정각 수량이 **0대**일 확률. 표시 시 ×100 |
| `is_risk` | boolean 또는 null | 모델별·간격별 저장 임계값을 적용한 판정. 0.5 고정 아님 |
| `model_id` | string 또는 null | 실제 사용한 모델 경로 ID. UI에서 하드코딩하지 않음 |
| `input_group` | T/S/W/F/E/C 또는 null | 적용된 입력 경로 |
| `reason` | string 또는 null | 확률을 반환할 수 없는 사유. 신규 코드에 대한 일반 처리 필요 |
| `latest_inventory` | integer 또는 null | 가장 최근 완전 캡처의 실제 재고. 미래 수량 예측값 아님 |
| `latest_inventory_received_at` | datetime 또는 null | 원본 수신 시각 |
| `latest_inventory_age_seconds` | number 또는 null | 요청 시각 기준 수신 후 경과 시간 |
| `latest_inventory_observed_at` | datetime 또는 null | 공급자가 관측 시각을 주지 않으면 null |

확률은 목표 정각에 0대인 사건을 예측하며, 그 사이 한 번이라도 고갈되거나 사용자가 실제 대여에 성공할 확률을 직접 뜻하지 않는다. 현재 API는 미래 재고 수량, 도보 거리/시간, 이동 경로를 반환하지 않는다.

### 예측 보류 사유

| `reason` | 의미 | 클라이언트 처리 |
| --- | --- | --- |
| `base_not_precomputed` | 새 기준 정각 사전 계산 전 | 갱신 대기. 제한된 재시도 |
| `station_unknown_or_ineligible` | 알 수 없거나 예측 자격 없는 ID | 목록/ID 확인, 해당 항목만 예측 불가 |
| `model_not_ready` | 사용할 모델 경로 없음 | 모델 준비 중 |
| `station_identity_not_verified_for_model` | 학습·운영 대여소 ID 연결 미검증 | 해당 대여소 보류 |
| `history_not_ready` | 필요한 이력 부족 | 초기 C 경로 가능 여부는 서비스가 결정 |
| `base_inventory_unavailable` | 기준 정각 재고 없음 | 확률 미표시. 최신 재고와 분리 |
| 그 밖의 값 | 후속 구현에서 추가 가능 | 일반 예측 불가와 원문 코드 보존 |

현재 `reason`은 자유 문자열이며 입력별·기상별 실패를 모두 세분화하지는 않는다. 클라이언트가 사유만 보고 누락된 입력을 재구성하거나 T 대신 C를 강제하지 않는다.

## POST /v1/recommendations

### 요청

```json
{"latitude":37.5665,"longitude":126.978,"horizon_hours":1,"radius_m":500}
```

| 필드 | 타입·제약 | 필수/기본 |
| --- | --- | --- |
| `latitude` | number 33~39 | 필수 |
| `longitude` | number 124~132 | 필수 |
| `horizon_hours` | 정수 1/2/3 | 필수 |
| `radius_m` | number, 0 초과~1500 이하 | 기본 500 |

반경은 직선거리이며 자동 확대하지 않는다. 현재 0대인 곳도 예측하지만 추천 후보는 최신 양수 재고이고 `0 ≤ age < 300`초인 정상 대여소로 제한한다. 등록된 정책은 `위험 확률 + 거리 비중 × 거리/반경`으로 정렬하고 최대 위험 제한을 적용한다. 비중·제한값은 최종 미선정이다.

### 200: 현재 개발 모델의 정상 보류 형식

```json
{
  "base_at":"2026-10-29T05:00:00Z","requested_at":"2026-10-29T05:40:00Z",
  "horizon_hours":1,"radius_m":500,"distance_type":"straight_line","automatic_radius_expansion":false,
  "recommended":null,"alternatives":[],"abstained":true,
  "reason":"recommendation_policy_not_selected","nearby_count":2,"eligible_count":0
}
```

위 수치·시각은 합성 예시다. 실모델도 정책이 없으면 같은 보류 코드가 온다.

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `base_at`, `requested_at` | datetime | 예측 기준·요청 시각 |
| `horizon_hours`, `radius_m` | integer, number | 적용 간격·반경 |
| `distance_type` | `straight_line` | 직선거리 |
| `automatic_radius_expansion` | false | 반경 자동 확대 안 함 |
| `recommended` | RankedPrediction 또는 null | 추천 1곳 |
| `alternatives` | RankedPrediction[] 최대 2 | 대안 |
| `abstained` | boolean | 추천 보류 여부. 실제 필드명은 `abstained` |
| `reason` | string 또는 null | 아래 세 코드 중 하나 |
| `nearby_count` | integer | 반경 안 좌표 검증 대여소 수 |
| `eligible_count` | integer | 모든 추천 자격 통과 후보 수 |

RankedPrediction은 예측 item 전체에 `name`, `latitude`, `longitude`, `distance_m`, `score`를 더한다. `score`는 정렬 점수이며 확률이 아니므로 퍼센트로 표시하지 않는다. 합성 성공 응답 전체는 JSON의 `recommendation_demo_only`를 본다.

| 추천 `reason` | 의미 |
| --- | --- |
| `no_station_within_radius` | 반경 안 좌표 검증 대여소 없음 |
| `recommendation_policy_not_selected` | 후보는 있지만 정책이 없음 |
| `no_fresh_positive_eligible_prediction` | 정책이 있어도 신선도·재고·예측·위험 제한 등을 통과한 후보 없음 |

## HTTP 오류와 업무상 보류

| 결과 | 응답/상황 | 처리 |
| --- | --- | --- |
| 200 + 확률 | 모델 예측 성공 | 목표 시각·확률·최신 수량 표시 |
| 200 + 확률 null | 개별 예측 불가 | reason을 해석. 전체 HTTP 오류 아님 |
| 200 + abstained | 추천 보류 | 안내 후 대안 UI. 안전한 추천으로 위장하지 않음 |
| 401 | `{"detail":"service_authentication_required"}` | 백엔드의 토큰/연결 설정 확인. 사용자에게 토큰 요구 안 함 |
| 422 | `detail` 배열 또는 `{"detail":"invalid_time_or_cursor"}`(데이터 API) | 입력 수정. 원 요청 재시도 안 함 |
| 500 등 | 내부 오류, 형태가 JSON으로 보장되지 않음 | 오류 처리·담당자 확인 |
| 연결 실패/timeout | 서버·터널 미가동 등 | HTTP 응답과 별도 처리 |

서비스가 공통 `error_code`/`request_id` 봉투, 429 rate limiter, 503 장애 코드, 재시도 헤더를 보장하지는 않는다. OpenAPI 기본 자동 생성에는 모든 예외 응답이 열거되지 않으므로 위 오류 규칙도 확인한다. 클라이언트 timeout·조회 재시도 횟수는 팀 백엔드에서 정한다. 100회 p95 2초는 최종 검증 목표이며 아직 달성 판정하지 않았다.
