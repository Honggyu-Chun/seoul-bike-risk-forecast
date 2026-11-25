# 프론트 모델 결과 사용 매뉴얼

팀 백엔드가 반환한 JSON을 표시합니다. 모델 토큰·snapshot 전송 권한을 브라우저에 넣지 않습니다. 기본 실제 모델 경로는 C이며 선정 고급 모델이 자동 실행된다고 표시하지 않습니다.

## 화면의 의미

- 위치·1/2/3시간·반경을 선택합니다. 기본 500m, 최대 1,500m이며 자동 확대하지 않습니다.
- 14:40 조회에서 기준 14:00, 목표 15·16·17시입니다. 조회 순간부터 정확히 60/120/180분 후가 아닙니다.
- 주 문구는 **“15시 정각에 자전거가 1대 이상 있을 확률 80%”**입니다. available_probability × 100을 표시합니다.
- latest_inventory는 최신 실측 수량입니다. 미래 예측 수량과 구별하고 수신 시각·경과 시간을 함께 표시합니다.
- 미래 수량·실제 대여 성공률·그 사이 계속 재고 유지로 설명하지 않습니다.

## 응답 필드

| 필드 | 화면 처리 |
| --- | --- |
| base_at / target_at | 기준 / 목표 정각. offset을 읽어 KST 표시 |
| available_probability | 1대 이상 확률. null이면 예측 자료 부족 |
| risk_probability | 목표 정각 0대 확률. available과 합 1 |
| latest_inventory | 최신 실측 수량. null은 알 수 없음, 0은 실제 0대 |
| latest_inventory_received_at / latest_inventory_age_seconds | 실제 수신과 경과 시간 |
| is_risk | 저장 임계값 판정. 임의 50% 판정선 금지 |
| input_group / model_id | 실제 적용 경로. 현재 C / C_h1 등 |
| reason | 개별 보류 사유. 새 코드에도 일반 보류 처리 |

예측 요청은 station_ids와 horizons_hours 배열입니다. 팀 백엔드가 모델 JSON을 그대로 중계할지 화면용 봉투로 감쌀지는 합의하되 필드 의미는 유지합니다.

## 추천과 예외

recommended는 1곳 또는 null, alternatives는 최대 2곳, abstained는 보류 여부입니다. distance_m는 직선거리이며 도보 경로·시간이 아닙니다. score는 위험과 거리를 합친 정렬값으로 퍼센트가 아닙니다.

| 상태 | 표시 |
| --- | --- |
| 준비 전 / 새 정각 계산 전 | 준비 중·갱신 대기 |
| 정상 확률 + 현재 양수 | 목표 정각 확률과 현재 실측 수량 |
| 정상 확률 + 현재 0대 | 현재 0대 표시. 추천에 강제 추가하지 않음 |
| 확률 null | 숫자 숨김, 사유 안내 |
| 오래된 재고 / 추천 보류 | 신선도 안내·추천 없음 |
| 연결 실패 / 서버 오류 | 다시 시도·장애 안내. 0%로 바꾸지 않음 |

no_station_within_radius와 no_fresh_positive_eligible_prediction을 구분합니다. 반경 확대는 사용자가 선택하도록 합니다. 기존 모의 API로 화면을 개발할 수 있으며 실제 C 모델 연결은 [팀 실행 안내](TEAM_MODEL_GUIDE.md)를 사용합니다.

## 타입과 연결

team_model/examples/cycleon.ts에 타입과 호출 예시가 있습니다. 함수의 base URL은 팀 백엔드 주소입니다. 모델 토큰은 포함하지 않습니다. 사용자 로그인·세션 전달 방식은 프론트와 백엔드가 합의합니다.
