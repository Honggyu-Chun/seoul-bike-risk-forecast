# CycleOn 최종 모델을 내 컴퓨터에서 호출하기

CycleOn은 서울 따릉이 대여소에 **목표 정각에 자전거가 한 대 이상 있을 확률**을 반환합니다. 14시 40분에 조회하면 14시까지 받은 입력으로 15·16·17시를 예측합니다. 최신 재고는 별도 필드입니다.

## 1. 처음 실행하기

Git과 Python 3.12가 있는 컴퓨터에서 아래 코드로 시작합니다. 저장소에 학습된 모델과 보정값이 들어 있습니다. 데모에는 공공데이터 키나 연구실 접속 권한이 필요하지 않습니다.

~~~shell
git clone https://github.com/Honggyu-Chun/seoul-bike-risk-forecast.git
cd seoul-bike-risk-forecast/모델개발
~~~

**Windows PowerShell**

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-team-model.txt
.\.venv\Scripts\python.exe -m team_model demo
~~~

**Linux 또는 macOS**

~~~sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-team-model.txt
python -m team_model demo
~~~

데모는 임시 로컬 서버에 예제 대여소 6곳의 모의 재고·예보를 전송하고, 실제 모델로 예측과 추천을 호출한 뒤 서버를 닫습니다. 출력의 `status`가 `passed_real_HTTP_synthetic_inputs`이고 다음 세 모델이 보이면 성공입니다. 확률 숫자는 실행한 기준 정각에 따라 바뀝니다. Linux에서 LightGBM 로딩 중 libgomp 오류가 발생하면 배포판의 OpenMP 런타임을 설치합니다. macOS에서 libomp 오류가 발생하면 Homebrew 환경에서는 `brew install libomp`로 설치합니다.

| 목표 정각 | input_group | model_id |
| --- | --- | --- |
| 1시간 뒤 | P53 | older_weekly_complete3_h1 |
| 2시간 뒤 | P51 | profile51_h2 |
| 3시간 뒤 | P48 | targetlag48_h3 |

## 2. 백엔드가 계속 호출할 서버 띄우기

이후 예시의 `python`은 위 가상환경의 Python입니다. Windows에서는 `.\.venv\Scripts\python.exe`를 그대로 쓸 수 있습니다. 서버 전용 환경변수 `CYCLEON_SERVICE_TOKEN`에 직접 발급한 비밀값을 설정합니다. 호출 프로그램에도 같은 값을 비공개로 설정합니다. 두 터미널에서 서로 다른 값을 생성하면 401이 반환됩니다.

~~~powershell
# Windows PowerShell, 서버 터미널
$env:CYCLEON_SERVICE_TOKEN = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python.exe -m team_model serve --host 127.0.0.1 --port 18101
~~~

~~~sh
# Linux 또는 macOS, 서버 터미널
export CYCLEON_SERVICE_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m team_model serve --host 127.0.0.1 --port 18101
~~~

다른 방법으로 팀의 비밀 관리 도구를 통해 서버와 호출자에 같은 환경변수를 주입할 수 있습니다. 로컬 API 명세는 `http://127.0.0.1:18101/docs`에 있습니다. 시작 직후에는 현 정각 입력을 기다리므로 `not_ready`가 정상입니다.

**백엔드 호출 예시.** 같은 호스트의 호출 터미널에서 예제 입력을 만들고 아래 Python 코드를 실행합니다. 예제 입력 전송은 직접 띄운 개발 서버에서만 수행하세요.

~~~shell
python -m team_model demo --write-snapshot final-example.json
~~~

~~~python
import json
import os
from pathlib import Path
import httpx

headers = {"Authorization": "Bearer " + os.environ["CYCLEON_SERVICE_TOKEN"]}
with httpx.Client(base_url="http://127.0.0.1:18101", headers=headers, timeout=60) as api:
    snapshot = json.loads(Path("final-example.json").read_text(encoding="utf-8"))
    api.post("/internal/ai-data/v1/snapshot", json=snapshot).raise_for_status()
    state = api.get("/v1/status").raise_for_status().json()
    print("최종 모델 예측 건수", state["data"]["primary_prediction_count"])
    response = api.post("/v1/predictions", json={
        "station_ids": ["DEMO-102"], "horizons_hours": [1, 2, 3]
    }).raise_for_status().json()
    for item in response["items"]:
        print(item["target_at"], item["model_id"], item["available_probability"])
    recommendation = api.post("/v1/recommendations", json={
        "latitude": 37.56, "longitude": 126.98, "radius_m": 500, "horizon_hours": 1
    }).raise_for_status().json()
    print(recommendation["recommended"])
~~~

실서비스에서는 프론트가 팀 백엔드를 호출하고 백엔드만 모델 토큰을 사용합니다. 화면에는 `available_probability × 100`을 “목표 정각에 한 대 이상 있을 확률”로 표시합니다. `risk_probability`는 그 시각에 0대일 확률입니다. `null`이면 확률 대신 `reason`을 사용합니다. 추천은 수신 후 5분 미만이고 현재 재고가 양수인 대여소가 대상입니다.

## 3. 실제 데이터를 연결할 때 바꿀 입력

데모 JSON을 **백엔드가 검수한 현 정각 전체 대여소 입력**으로 교체합니다. 백엔드가 원본의 수신 시각과 대여소 신원을 보관합니다. 모델 서버가 특징을 만들고 대여소·간격별로 사용할 모델을 고릅니다.

| 입력 | 보내는 내용 |
| --- | --- |
| `stations` | 서비스 ID, 검증한 학습 대여소 번호와 좌표, 기준 정각 재고와 당시 수신 시각, 별도 최신 재고와 수신 시각. 주변 대여소 특징을 위해 대상 도시의 전체 목록을 보냅니다. |
| `final_inputs.histories` | 대여소 ID별 `identity_revision`, 동일 신원이 연속 유효해진 `continuous_since`, `prior_inventory`. 기준 정각 t의 재고는 stations에 넣고, 배열은 t−1시간에서 끝나도록 오래된 정각부터 168~671개를 채웁니다. 671개를 주면 4주 전 같은 시간 특징까지 계산할 수 있습니다. 관측이 없으면 null, 실제 0대면 0입니다. |
| `final_inputs.history_received_at` | 포함한 이력을 실제로 수신한 최종 시각. 기준 정각까지 알려진 이력만 포함합니다. |
| `final_inputs.forecasts` | `product="kma_completed_hour_v2"`를 final_inputs에 지정합니다. 간격별 `issued_at=t−70분`, 실제 `received_at≤t`, `target_at=t+h`를 사용합니다. `by_station`에 각 대여소 격자의 T1H(°C), RN1(시간 강수량 mm), REH(%), WSD(m/s), PTY(강수 형태 코드)를 넣습니다. |
| 갱신·오류 | 정각이 바뀌면 새 snapshot을 전송합니다. 같은 정각의 확정 이력·예보는 고정하고 최신 재고만 갱신합니다. 잘못된 발표본·미래 수신 시각 등 계약 위반은 422입니다. 마지막 정상 입력은 보존됩니다. |

검증된 기준 재고가 있고 이력·주변·예보 조건을 충족하면 P53/P51/P48을 사용합니다. 현재는 시간 강수량 RN1이 0인 격자만 주 모델을 허용합니다. 강수 양수, 누락된 예보, 부족한 이력 등에서는 가능한 대여소에 C 모델을 적용합니다. 기준 재고나 신원도 유효하지 않으면 확률은 null입니다. 호출자는 각 항목의 `input_group`, `model_id`, `reason`을 확인할 수 있습니다.

## 4. 연구실의 실시간 결과를 조회하는 경우

연구실 SSH 접근 권한과 **연구실 모델 조회용 토큰**이 있는 팀원은 직접 서버를 띄우는 대신 SSH 터널을 연결한 뒤 예측·추천을 조회할 수 있습니다. `amsl-lab`은 권한을 받은 사람의 SSH 설정에 등록한 별칭입니다. 예를 들어 로컬 18102를 사용하면 내 개발 서버 18101과 함께 쓸 수 있습니다.

~~~shell
ssh -N -L 18102:127.0.0.1:18101 amsl-lab
~~~

호출 주소를 `http://127.0.0.1:18102`로 바꾸고, 연구실의 실제 대여소 ID를 사용합니다. 연구실 서버에는 예제 snapshot을 보내지 않습니다. 임의의 외부 사용자는 1절만으로 자기 컴퓨터에서 모델을 실행할 수 있습니다. GitHub가 실시간 서울 재고·예보를 호스팅하지는 않습니다.

## 검증 범위

2026-11-26 08시(KST) 실제 입력을 외부용 API에 재생해 연구실 기록과 대조했습니다. 2,536개 대여소 × 세 간격의 주 모델 예측 **7,608건**은 모델·판정·확률이 모두 일치했고 최대 확률 차이는 0이었습니다. 16개 대여소의 48건은 C 경로였습니다. 이 검사는 같은 입력에서 같은 추론이 나오는지 확인합니다. 예측과 실측 재고 간 성능은 [기존 평가 결과](RESULTS_20261110.md)에서 확인할 수 있습니다.

[전체 요청·응답 명세](team_model_openapi.json) · [백엔드 데이터 계약](BACKEND_MODEL_INTEGRATION.md) · [프론트 표시 규칙](FRONTEND_MODEL_INTEGRATION.md)
