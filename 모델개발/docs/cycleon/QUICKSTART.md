# 빠른 시작

이 가이드는 **모델 파일·공공 API 키·학습 데이터 없이** 계약과 화면을 검증한다. 모의 API 시각은 2026-10-29 14:40 KST로 고정되어 있고 모든 수치는 합성값이다.

## 1. 협업 브랜치 받기

초안 검토 중에는 다음 브랜치를 사용한다. main 병합 후에는 main에서 작업 브랜치를 만든다.

```powershell
git clone --single-branch --branch docs/model-api-handoff https://github.com/Honggyu-Chun/seoul-bike-risk-forecast.git
cd seoul-bike-risk-forecast
git switch -c feature/my-integration
cd 모델개발
```

이미 저장소를 받았다면 미커밋 작업을 먼저 보존한 뒤 필요한 브랜치만 fetch한다.

```powershell
git fetch origin docs/model-api-handoff
git switch -c feature/my-integration origin/docs/model-api-handoff
```

기존 8000 시제품의 설치·실행은 [TEAM_SETUP](../TEAM_SETUP.md)에 있다. 그 릴리스 묶음에는 이번 다중 시간 간격 개발 모델이 포함되지 않는다.

## 2. 최소 환경과 계약 검사

Python 3.12가 필요하다. 아래는 Windows PowerShell 기준이며 Linux/macOS에서는 `python3.12`, 가상환경의 `bin/python`을 사용한다.

```powershell
py -3.12 -m venv .venv-contract
.\.venv-contract\Scripts\python.exe -m pip install -r requirements-contract.txt
.\.venv-contract\Scripts\python.exe scripts/cycleon_contract_check.py
```

정상 출력에는 `checks_passed: 33`, `assets_checked: 4`, `real_model_loaded: false`, `real_provider_calls: 0`이 포함된다. 이 검사는 동일 API 라우터로 인증·3간격·누락·유효성 오류·페이지 이어읽기와 저장 명세 일치를 확인한다. 모델 학습·실제 기상 API·서울 전체 수집은 시작하지 않는다.

## 3. 로컬 모의 AI 실행

각 터미널에서 같은 로컬 테스트 토큰을 환경변수로 지정한다. 아래 값은 모의 서버용 예시이며 실서비스에서 사용하지 않는다. `.env`에 들어 있는 실제 서비스 토큰은 모의 서버가 읽지 않는다.

```powershell
$env:CYCLEON_MOCK_TOKEN = 'local-mock-only-change-this-token-123456'
.\.venv-contract\Scripts\python.exe scripts/cycleon_mock_service.py ai
```

AI 모의 주소는 `http://127.0.0.1:18011`이다. 이 주소의 `127.0.0.1`은 실행한 개발자 본인 PC다. 확인용 UI는 `/docs`, JSON 스키마는 `/openapi.json`이다. 실제 데이터 API와 동일한 형식의 페이지도 필요하면 다른 터미널에서 실행한다.

```powershell
$env:CYCLEON_MOCK_TOKEN = 'local-mock-only-change-this-token-123456'
.\.venv-contract\Scripts\python.exe scripts/cycleon_mock_service.py backend
```

데이터 모의 주소는 `http://127.0.0.1:18012`다. 종료는 각 터미널에서 Ctrl+C. 8000/8001/8002 포트는 모의 서버에서 사용할 수 없다.

## 4. 처음 세 가지 호출

세 번째 터미널에서:

```powershell
$env:CYCLEON_MOCK_TOKEN = 'local-mock-only-change-this-token-123456'
$headers = @{Authorization = "Bearer $env:CYCLEON_MOCK_TOKEN"}
$baseUrl = 'http://127.0.0.1:18011'
Invoke-RestMethod "$baseUrl/v1/status" -Headers $headers

$body = @{station_ids=@('ST-MOCK-1','ST-UNKNOWN'); horizons_hours=@(1,2,3)} | ConvertTo-Json
Invoke-RestMethod "$baseUrl/v1/predictions" -Method Post -Headers $headers -ContentType 'application/json' -Body $body

$body = @{latitude=37.5665; longitude=126.978; horizon_hours=1; radius_m=500} | ConvertTo-Json
Invoke-RestMethod "$baseUrl/v1/recommendations" -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

`ST-MOCK-1`에는 합성 확률 0.2가, `ST-UNKNOWN`에는 `null`과 사유가 온다. 기본 추천 응답은 `abstained=true`, `reason=recommendation_policy_not_selected`다. 현재 실모델의 정책 미선정 흐름을 화면에서 처리하도록 만든 시나리오다.

## 5. 화면 예외·성공 시나리오

AI 모의를 Ctrl+C로 종료한 후 같은 포트에서 시나리오를 바꾼다.

```powershell
# 합성 추천 성공: 실제 선정된 추천 정책이 아님
.\.venv-contract\Scripts\python.exe scripts/cycleon_mock_service.py ai --scenario recommendation-demo
# 또는 준비 안 됨: status=not_ready, 예측은 null
.\.venv-contract\Scripts\python.exe scripts/cycleon_mock_service.py ai --scenario not-ready
```

모든 시나리오는 `model_stage=mock_no_model_no_live_data`로 표시된다. 확률·거리·점수의 품질이나 응답 지연 목표를 평가하는 용도로 사용하지 않는다.

## 6. Postman 사용

[collection JSON](CycleOn.postman_collection.json)을 import한다. 기본 `ai_base_url`과 `backend_base_url`은 모의 포트다. `token`은 비어 있으므로 Postman의 개인 로컬 값에 모의 토큰을 넣는다. 실제 토큰을 컬렉션 공유 값에 저장하거나 export하지 않는다.

실모델 확인 시에는 아래 접속 준비 후 base URL과 개인 토큰만 바꾼다. `ST-MOCK-*`는 실서비스에 존재하지 않으므로 대여소 페이지에서 실제 `record.station_id`를 받아 요청한다.

## 7. 연구실 실모델 접속

현재 상태는 [가이드의 현황표](README.md#현재-사용할-수-있는-것)를 확인한다. 8001/8002가 내려가 있으면 SSH 터널만 열어도 연결되지 않는다. 모델 담당자가 자원 확인 후 서비스를 올리고, 각 팀원의 SSH 접근과 서비스 토큰 전달을 확인해야 한다.

```powershell
# amsl-lab은 각 PC에 등록된 별칭이어야 한다. 없다면 담당자가 SSH 주소·계정을 안내한다.
ssh -N -L 18001:127.0.0.1:8001 -L 18002:127.0.0.1:8002 amsl-lab
```

호출 측 AI base URL은 `http://127.0.0.1:18001`, 데이터는 `http://127.0.0.1:18002`다. 브라우저에 서비스 토큰을 넣어 직접 연결하지 말고 팀 백엔드에서 중계한다. 실제 주소·토큰·서버 장애 대응 담당자 인계는 실연동 전 남은 작업이다.

## 8. 명세 변경 검사

```powershell
# 계약 코드를 수정한 경우만 생성 후 diff 검토
.\.venv-contract\Scripts\python.exe scripts/cycleon_contract_check.py --export
.\.venv-contract\Scripts\python.exe scripts/cycleon_contract_check.py
```

GitHub의 `CycleOn API contract` 작업도 같은 검사를 실행한다. 자료/가중치가 필요한 기존 연구 회귀검사와 독립된 검사다.
