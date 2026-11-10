# 백엔드 모델 연결 매뉴얼

팀 백엔드는 원본 저장·신원 연결·수신 이력을, 모델 서비스는 C 특징 생성·3간격 추론·추천을 담당합니다. 기본 HTTP 경로는 C 실제 모델이며 고급 기상 모델은 활성화하지 않습니다. 이번 snapshot은 팀 연동용 입력이며 기존 내부 조회 API의 저장 책임을 대체하지 않습니다.

## API 계약

| API | 호출자와 용도 |
| --- | --- |
| POST /internal/ai-data/v1/snapshot | 신뢰하는 백엔드만, 도시 입력 완전 snapshot |
| GET /v1/status | 현재 기준 정각 준비 상태 |
| POST /v1/predictions | station_ids, horizons_hours [1,2,3] |
| POST /v1/recommendations | latitude, longitude, horizon_hours, radius_m |

모든 업무 API는 Authorization: Bearer 토큰을 사용합니다. 브라우저에 비밀 토큰과 snapshot 권한을 주지 않습니다. [OpenAPI](team_model_openapi.json)를 사용합니다. snapshot은 **증분이 아닙니다**. 생략한 대여소를 자동으로 이전 입력과 합치지 않습니다. 원천 증분 조회·저장은 백엔드가 처리합니다.

## snapshot 필드

| 필드 | 의미와 제약 |
| --- | --- |
| capture_complete / source_capture_id | 전체 페이지·입력 검수 완료 true / 실제 캡처 식별자. 일부 페이지 실패면 전송 금지 |
| base_at | KST 최근 정각, offset 있는 ISO8601 |
| materialized_at | 실제 snapshot 작성 시각. 기준 이후, 서버 현재 이전 |
| stations | 최대 5,000개. ID와 model_station_code 중복 불가 |
| station_id / model_station_code | 서비스 ID / 학습의 역사상 정수 번호. ST 접미사·행 번호로 추정 금지 |
| name / latitude / longitude | 기준 정각에 적용되는 이름·좌표 |
| identity_verified / identity_verified_at | 실제 신원 검증 여부·시각. 기준 이후에 알게 된 신원 소급 금지 |
| identity_evidence / identity_revision | 근거 식별자·수정 버전. 모델 서버는 근거의 진위를 자동 인증하지 않음 |
| inventory_at_base | 기준까지 수신한 유효 재고. 결측 null과 실제 0대 구별 |
| base_inventory_received_at | 그 재고의 실제 수신 시각. 기준 이후 금지. 300초 이상이면 해당 역 예측 보류 |
| latest_inventory / latest_inventory_received_at | 현재 표시·추천용 최신 실측 재고와 실제 수신 시각 |
| latest_inventory_observed_at | 공급자가 주는 실제 관측 시각. 없으면 null |

재고와 대응 수신 시각은 함께 null 또는 함께 존재해야 합니다. 미래 수신·중복·시간대 누락·알 수 없는 필드는 422입니다. 미검증 신원·기준 재고 결측·기준 재고 stale·미지원 학습 번호는 개별 예측 null입니다. 같은 정각에서 기준 재고·좌표·신원 메타데이터를 바꾸면 422입니다. 새 materialized_at은 이전보다 커야 합니다.

지원 번호 목록은 설치된 manifest의 trained_station_codes입니다. 이는 과거 모델 번호 목록이며 현재 대여소와 동일하다는 증명이 아닙니다. 이전·신설·번호 재사용을 확인하고 identity_verified=true를 일괄 채우지 않습니다. 수신·수정 journal과 신원 증빙은 백엔드가 보존합니다.

## 데이터와 시간

C는 station_code, inventory, log_inventory, hour, weekday, month, weekend, horizon의 8열입니다. 서버가 원래 학습과 같은 float32 규칙으로 만듭니다. 백엔드가 8개 특징을 직접 계산할 필요는 없습니다.

14:40 조회에서 기준 14:00, 목표 15·16·17시입니다. 14:40 최신 재고를 14:00 inventory_at_base로 소급하지 않습니다. 원본에 없는 관측 시각·수신 시각을 만들지 않습니다.

서울 재고·공식 대여소 신원/좌표가 필요합니다. 기상청 과거 포털 예보는 고급 모델의 연구 입력이지만 이번 C HTTP 경로는 사용하지 않습니다. RN1·단위·발표/수신·전체 소속 검증 전 live API를 연구 예보와 교환하지 않습니다. 대기질·버스·열차·관광·NEIS·SGIS·DEM을 C 입력으로 보낼 필요는 없습니다.

## Python 예시

~~~python
import os, httpx
headers = {"Authorization": "Bearer " + os.environ["CYCLEON_SERVICE_TOKEN"]}
with httpx.Client(base_url=os.environ["CYCLEON_AI_BASE_URL"],
                  headers=headers, timeout=10) as client:
    # verified_snapshot은 실제 저장 원본으로 만든 값
    client.post("/internal/ai-data/v1/snapshot", json=verified_snapshot).raise_for_status()
    r = client.post("/v1/predictions", json={
        "station_ids": ["<실제 전송한 ID>"], "horizons_hours": [1, 2, 3]
    })
    r.raise_for_status()
    result = r.json()
~~~

team_model/examples/snapshot.json은 **고정 시각의 합성 구조 예시**입니다. 현재 서버에 그대로 보내 실데이터라고 쓰지 않습니다. 시각을 임의 바꾸어 과거 신원 증빙을 유효하게 만들지 않습니다. 실제 모델 계산 확인은 python -m team_model check 또는 합성 테스트를 실행합니다.

## 장애와 재시작

| 결과 | 처리 |
| --- | --- |
| 401 | 서버 토큰 확인. 사용자 로그인과 분리 |
| 422 | 입력·시각·중복·수정 규칙 수정. 무한 재시도 금지 |
| 200 + 확률 null | reason 보존. 0%로 바꾸지 않음 |
| 200 + abstained | 추천 없음. 반경 자동 확대 금지 |
| 연결 실패 / 5xx | 제한된 재시도·장애 로그·오류 UI, 비밀값 로그 금지 |

snapshot은 메모리에 보관합니다. 재시작 후 백엔드가 현재 정각 입력과 최신 재고를 재전송해야 합니다. 멀티 worker는 메모리를 공유하지 않으므로 worker 1개로 실행합니다. TLS·배포 프로세스·모니터링·사용자 인증은 백엔드 배포 범위입니다.

## 통합 확인

실제 검증 대여소에서 snapshot → 세 간격 확률 → 현재 양수·수신 후 5분 미만 후보 추천을 확인합니다. 0대·stale·unknown ID·새 정각·재시작·잘못된 토큰도 확인합니다. 합성 검사 통과와 실제 공급자 입력 검수를 구분해 기록합니다.

원천 중복 충돌·부정확한 재고는 유효값으로 보내지 말고 재고와 대응 수신 시각을 함께 null로 전달합니다. 페이지나 출처 일부 실패 시 성공 snapshot을 덮어쓰지 않습니다. capture_complete는 신뢰하는 백엔드의 확인이며 모델 서버가 공급자 원본을 독립 확인한 증거는 아닙니다.
