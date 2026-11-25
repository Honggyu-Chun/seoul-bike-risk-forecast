> **최신 팀 연결 기준(2026-11-10):** 실제 C 모델 호출·입력 방식은 [팀 모델 실행 안내](TEAM_MODEL_GUIDE.md), [백엔드](BACKEND_MODEL_INTEGRATION.md), [프론트](FRONTEND_MODEL_INTEGRATION.md), [현재 OpenAPI](team_model_openapi.json)를 사용합니다. 아래는 이전 참조·모의 API 기록입니다. 새 최종 집계는 [11월 10일 결과](RESULTS_20261110.md)를 보세요.

# 모델 없이 개발 시작하기

Git과 **Python 3.12**가 필요하다. 모델·과거 데이터·공공 API 키 없이 AI/데이터 목업과 호출 검사를 실행한다. Windows PowerShell과 Linux/macOS 터미널에서 아래 중 해당 경로를 따른다.

## 1 저장소와 설치

```text
git clone --depth 1 https://github.com/Honggyu-Chun/seoul-bike-risk-forecast.git
cd seoul-bike-risk-forecast
git switch -c feature/my-integration
cd 모델개발
```

Windows:

```powershell
python scripts/cycleon_setup.py
```

Linux/macOS:

```bash
python3.12 scripts/cycleon_setup.py
```

설치기는 이 폴더의 `.venv-contract`만 만들고 고정 의존성을 설치한 뒤 계약 검사를 실행한다. 정상 결과는 `checks_passed: 33`, `real_model_loaded: false`, `real_provider_calls: 0`이다. `.env`나 실제 API를 읽지 않으며 수집·학습을 시작하지 않는다. 이미 다른 Python 버전으로 만든 가상환경은 설치기가 사용을 거부한다. Python 3.12 미설치 시 먼저 준비한다. 인증서/네트워크로 pip 설치가 실패하면 오류를 해결하고 같은 명령을 다시 실행한다.

Windows의 `python`이3.12가 아니고 Python Launcher가 있으면 `py -3.12 scripts/cycleon_setup.py`를 사용한다. 루트의 `SETUP_CYCLEON_MOCK.cmd`는 Launcher→PATH Python 순서로 찾는다. 직접 경로가 필요하면 PowerShell에서 `$env:CYCLEON_PYTHON='설치한 Python 3.12의 python.exe 절대경로'`를 지정한 뒤 루트 설치 CMD를 실행한다. 실행 정책 때문에 가상환경 activate가 막혀도 위 직접 실행은 사용할 수 있다.

기존 클론은 작업을 보존한 뒤 `git fetch origin`, `git pull --ff-only origin main`을 사용한다. 기존 작업 브랜치에 적용할 때는 팀의 merge 흐름을 따른다. 이전 초안 `docs/model-api-handoff`가 아니라 반영된 `main`에서 새 작업을 시작한다.

## 2 AI 목업 실행

아래는 공개된 로컬 목업 토큰 예시다. 실제 서비스 토큰으로 쓰지 않는다.

Windows:

```powershell
$env:CYCLEON_MOCK_TOKEN = 'local-mock-only-change-this-token-123456'
.\.venv-contract\Scripts\python.exe scripts/cycleon_mock_service.py ai
```

Linux/macOS:

```bash
export CYCLEON_MOCK_TOKEN='local-mock-only-change-this-token-123456'
.venv-contract/bin/python scripts/cycleon_mock_service.py ai
```

<http://127.0.0.1:18011/docs>에서 Authorize에 토큰을 입력한다. JSON 명세는 `/openapi.json`이다. `127.0.0.1`은 실행한 본인 PC다. 서비스는 Ctrl+C로 종료한다. 포트가 사용 중이면 `--port 18111`처럼 다른 목업 포트를 지정한다. 기존 8000/8001/8002는 사용하지 않는다.

## 3 실제 HTTP 호출 확인

두 번째 터미널에서 `모델개발`로 이동하고 같은 목업 토큰을 지정한다.

Windows:

```powershell
$env:CYCLEON_MOCK_TOKEN = 'local-mock-only-change-this-token-123456'
.\.venv-contract\Scripts\python.exe scripts/cycleon_http_check.py
```

Linux/macOS:

```bash
export CYCLEON_MOCK_TOKEN='local-mock-only-change-this-token-123456'
.venv-contract/bin/python scripts/cycleon_http_check.py
```

포트를 바꿨다면 `--ai-url http://127.0.0.1:18111`을 추가한다. 검사는 loopback 목업만 허용하고 3간격·unknown ID·인증·추천·스키마를 확인한다. 서버가 꺼졌거나 토큰이 다르면 실패한다. HTTP 통과는 실모델 품질·성능 검증이 아니다.

PowerShell에서 직접 호출하려면:

```powershell
$headers = @{Authorization = "Bearer $env:CYCLEON_MOCK_TOKEN"}
Invoke-RestMethod 'http://127.0.0.1:18011/v1/status' -Headers $headers
$body = @{station_ids=@('ST-MOCK-1','ST-UNKNOWN'); horizons_hours=@(1,2,3)} | ConvertTo-Json
Invoke-RestMethod 'http://127.0.0.1:18011/v1/predictions' -Method Post -Headers $headers -ContentType 'application/json' -Body $body
```

Linux/macOS:

```bash
curl -H "Authorization: Bearer $CYCLEON_MOCK_TOKEN" http://127.0.0.1:18011/v1/status
curl -H "Authorization: Bearer $CYCLEON_MOCK_TOKEN" -H 'Content-Type: application/json' --data '{"station_ids":["ST-MOCK-1","ST-UNKNOWN"],"horizons_hours":[1,2,3]}' http://127.0.0.1:18011/v1/predictions
```

`ST-MOCK-1`은 확률 0.2, `ST-UNKNOWN`은 null과 사유를 반환한다. 목업은 2026-10-29 14:40 KST 고정 시각이다. 시간 경과나 확률을 실제 최신 모델로 해석하지 않는다.

## 4 화면 성공과 예외 시나리오

기존 AI 목업을 Ctrl+C로 종료하고 같은 실행 명령 끝에 다음 옵션을 붙인다. 세 시나리오를 같은 포트에서 동시에 실행하지 않는다.

| 옵션 | 확인할 화면 |
| --- | --- |
| 생략 또는 `--scenario policy-pending` | 확률은 있으나 추천 정책 미선정으로 보류 |
| `--scenario recommendation-demo` | 합성 추천 성공과 대안 영역 |
| `--scenario not-ready` | 상태 미준비, 예측 null과 사유 |

기본 성공 예: `.\.venv-contract\Scripts\python.exe scripts/cycleon_mock_service.py ai --scenario recommendation-demo`.
모든 시나리오의 `model_stage`는 `mock_no_model_no_live_data`다. 입력 확률·최신 수량·위험 false·null을 구분한다.

## 5 데이터 백엔드 목업

세 번째 터미널에서 같은 토큰을 설정하고 위 AI 실행 명령의 `ai`를 `backend`로 바꾼다. 기본 주소는 <http://127.0.0.1:18012/docs>다. 내부 stations/observations/forecasts 페이지·cursor/watermark·capture 구조를 구현할 때 사용한다.

HTTP 검사에 `--backend-url http://127.0.0.1:18012`를 더하면 세 데이터 경로도 확인한다. 원본 타입과 상세 커서 규칙은 [백엔드 명세](BACKEND_HANDOFF.md)를 따른다. 합성 기상은 봉투·타입 예시이며 W/F/E 실제 제품 검수 자료가 아니다.

## 6 팀 코드와 연결

[Node 서버 호출 예제](../../examples/cycleon_backend_client.mjs)는 Node 18 이상에서 별도 npm 패키지 없이 실행한다. `CYCLEON_AI_BASE_URL`, `CYCLEON_SERVICE_TOKEN`은 서버 환경변수이며 브라우저용이 아니다. 목업에 연결할 때만 서비스 토큰 변수에 위 목업 토큰을 설정한다. 실제 키를 프론트 빌드 환경에 복사하지 않는다.

프론트는 앱 백엔드의 대여소 목록과 AI 예측을 공통 station_id로 연결한다. 앱 공개 경로가 준비 전이면 [합성 JSON](mock_examples.json)을 테스트 fixture로 사용할 수 있다. [파트 간 계약](INTEGRATION_CONTRACT.md)의 미합의 항목을 확인한다.

[Postman collection](CycleOn.postman_collection.json)을 import한 뒤 개인 로컬 token 값을 지정해도 된다. 공유 collection의 token은 비워 둔다.

## 7 실제 모델로 전환

목업에서 실모델로 base URL을 바꾸기 전에 [실모델 인수](REAL_MODEL_HANDOFF.md)의 주소·번들/해시·실제 ID·준비 상태·데이터 공급·추천 정책을 확인한다. Git 클론만으로 실제 모델이 설치되지는 않는다. 실서버가 꺼져 있으면 SSH 터널만 열어도 연결되지 않는다.
