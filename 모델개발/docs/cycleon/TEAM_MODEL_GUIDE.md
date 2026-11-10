# 팀 모델 실행 안내

2026년 11월 10일 main 기준 인계본입니다. 실제 저장 모델의 확률을 받아 개발할 수 있습니다. 기본 HTTP 서비스는 **C 대체 모델 1·2·3시간**을 사용합니다. P53/P51/P48와 S 모델도 함께 제공하지만 기상·대여소 소속·수신 journal 검증이 남아 기본 HTTP 경로에서 활성화하지 않습니다.

## 제공 내용

| 내용 | 모델개발 폴더 기준 경로 |
| --- | --- |
| 실제 모델 서비스 | team_model/service.py |
| 실제 모델 9개 압축 묶음, 약 35.9MB | releases/team-model-20261110/models.zip |
| ZIP·개별 파일 SHA-256 | releases/team-model-20261110/release.json |
| 현재 서비스 기계 명세 | docs/cycleon/team_model_openapi.json |
| 자동 검증 | python -m unittest team_model.test_service -v |

학습 데이터·계정·비밀 토큰·운영 DB는 포함하지 않습니다. 설치와 실행은 학습·공공 API 수집을 시작하지 않습니다.

## 설치

Python 3.12, Windows PowerShell 기준입니다. Linux/macOS는 python3.12와 .venv-team/bin/python을 사용합니다.

~~~powershell
git clone https://github.com/Honggyu-Chun/seoul-bike-risk-forecast.git
cd seoul-bike-risk-forecast
cd 모델개발
py -3.12 -m venv .venv-team
.\.venv-team\Scripts\python.exe -m pip install -r requirements-team-model.txt
.\.venv-team\Scripts\python.exe -m team_model install
.\.venv-team\Scripts\python.exe -m team_model check
.\.venv-team\Scripts\python.exe -m unittest team_model.test_service -v
~~~

install은 ZIP과 멤버 해시·경로를 확인해 artifacts/team_model 아래에 풉니다. check는 실제 C 모델 세 개를 읽어 합성 입력으로 계산합니다. Linux에서 LightGBM이 libgomp를 찾지 못하면 OS 패키지 libgomp1을 설치합니다.

## 실행

~~~powershell
$env:CYCLEON_SERVICE_TOKEN = '<32자 이상 서버 전용 비밀값>'
.\.venv-team\Scripts\python.exe -m team_model serve --host 127.0.0.1 --port 18101
~~~

주소는 **실행한 사람 PC의** http://127.0.0.1:18101 입니다. /docs에서 명세를 확인합니다. 기존 8000·8001·8002와 별도이며 기존 서비스를 건드리지 않습니다. 팀 공용 주소·TLS·접근 제어·프로세스 관리·비밀 전달은 서버 담당자가 정합니다. 연구실 서버가 이미 켜져 있다는 뜻은 아닙니다.

## 실제 사용 순서

1. 백엔드가 POST /internal/ai-data/v1/snapshot에 검증한 정각 입력과 별도 최신 재고를 보냅니다.
2. GET /v1/status로 준비 상태를 확인합니다.
3. POST /v1/predictions에 station_ids와 horizons_hours를 보냅니다.
4. POST /v1/recommendations에 위치·시간 간격·반경을 보냅니다.

시작 직후 입력이 없으면 not_ready와 null이 정상입니다. 새 정각으로 바뀌면 새 snapshot이 필요합니다. 최신 재고 갱신마다 새 materialized_at으로 snapshot을 보내되, 같은 기준 정각의 특징·신원·좌표를 사후 수정하지 않습니다. API 요청마다 재학습하지 않고 snapshot 갱신 때 실제 C 모델로 사전 계산합니다.

~~~powershell
$headers = @{Authorization = "Bearer $env:CYCLEON_SERVICE_TOKEN"}
$base = 'http://127.0.0.1:18101'
Invoke-RestMethod "$base/v1/status" -Headers $headers
$body = @{station_ids=@('<실제 전송한 ID>'); horizons_hours=@(1,2,3)} | ConvertTo-Json
Invoke-RestMethod "$base/v1/predictions" -Method Post -Headers $headers -ContentType 'application/json' -Body $body
$body = @{latitude=37.5665; longitude=126.978; horizon_hours=1; radius_m=500} | ConvertTo-Json
Invoke-RestMethod "$base/v1/recommendations" -Method Post -Headers $headers -ContentType 'application/json' -Body $body
~~~

available_probability는 목표 정각에 1대 이상일 확률, risk_probability는 0대 확률입니다. 현재 수량 latest_inventory와 다릅니다. HTTP 200이어도 개별 예측·추천은 보류일 수 있습니다.

## 역할과 사용 조건

- 백엔드는 [입력·인증·갱신 매뉴얼](BACKEND_MODEL_INTEGRATION.md)을 구현합니다.
- 프론트는 [확률·보류·오류 표시 매뉴얼](FRONTEND_MODEL_INTEGRATION.md)을 구현합니다.
- 모델 담당은 실제 공급자 입력과 신원 증빙을 검수합니다. 주 모델의 기상·전체 대여소 소속·immutable journal 검증 전 C 경로를 고급 모델로 표시하지 않습니다.

실제 저장 모델 3개·원래 C 특징 생성과 확률/판정 일치·3간격·자정·정각 변경·신원 누락·0대·300초 신선도·중복·미래 입력·인증·반경·재현을 자동 검사합니다. 이 결과는 실제 공급자 품질·전체 도시 지연·24시간 운영·TLS·사용자 인증까지 증명하지 않습니다. 과거 58.75% 감소도 이 새 HTTP 배포의 실운영 측정값이 아닙니다. [평가 결과](RESULTS_20261110.md)를 함께 읽습니다.

추가로 python -m team_model.smoke_http를 실행하면 임시 localhost 서버에서 인증→입력→세 간격 예측→추천을 검사하고 자신이 만든 임시 서버만 종료합니다. 합성 입력이므로 공급자 품질이나 실제 대여 효과를 검사하지 않습니다.
