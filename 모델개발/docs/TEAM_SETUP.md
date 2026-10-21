# 팀원 첫 실행 안내

**Windows에서는 저장소를 클론한 뒤 `SETUP_P_PROJECT.cmd` → `START_P_PROJECT.cmd` 순서로 실행한다.** 준비 도구가 Python 환경과 실제 실행 데이터·모델을 마련하므로 처음부터 학습할 필요는 없다. 화면·백엔드 담당자는 기본 준비로 시작하고 모델 담당자는 `research` 준비를 선택한다.

## 1. 처음 한 번 설치한다

Git과 [uv](https://docs.astral.sh/uv/getting-started/installation/)를 설치한다. 준비 도구는 검수한 Python **3.12.15**를 찾거나 내려받아 프로젝트 전용 환경을 구성한다. Windows에서 uv를 설치할 때 PowerShell에 다음 명령을 입력한다.

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

설치 뒤 터미널을 새로 열고 `git --version`, `uv --version`이 각각 버전을 출력하는지 확인한다. 시스템의 다른 Python 환경을 바꿀 필요는 없다.

## 2. main을 클론한다

```powershell
git clone --single-branch --branch main https://github.com/Honggyu-Chun/seoul-bike-risk-forecast.git
cd seoul-bike-risk-forecast
```

이미 `main`을 클론했다면 새로 클론하지 않고 로컬 변경을 보존한 상태에서 최신 내용을 받는다. 이전 대용량 전달 브랜치는 첫 실행에 사용하지 않는다.

## 3. 역할에 맞춰 준비한다

### 화면·백엔드·일반 시연

저장소 최상위 폴더에서 다음을 실행한다. 탐색기에서 `SETUP_P_PROJECT.cmd`를 두 번 클릭해도 같은 기본 준비를 수행한다.

```powershell
.\SETUP_P_PROJECT.cmd
```

의존성 설치 → 실행 묶음 약 **28.6MB** 다운로드 → 12개 데이터·모델 파일 해시 확인 → 로컬 API 확인 순으로 수행한다. `.env`가 없으면 빈 키 예시로 생성하며 기존 `.env`는 유지한다. 기존 과거 시제품을 실행하는 데 서울·KOPIS 키를 발급받을 필요는 없다.

### 모델·평가 담당

```powershell
.\SETUP_P_PROJECT.cmd research
```

기본 실행 준비에 CatBoost·XGBoost·CPU PyTorch 의존성과 약 **36.1MB**의 연구 기초 묶음을 추가한다. 2024년·2025년 상반기 패널, 검수 기록, A 구간의 기본·공간 LightGBM과 보정기 8개 파일을 준비한다. 기본 프로필과 연구 프로필의 다운로드 합계는 약 64.7MB이며, Python 의존성 용량은 별도로 필요하다.

전체 53개 모델·모든 원시 확률·2022~2025년 전체 원본은 이 묶음에 포함하지 않는다. 현재 기초 자료는 기존 1시간 연구를 확인하고 새 실험을 시작하는 용도로 제공한다. 최신 제안서의 1·2·3시간 서비스 구현 완료와 구분한다.

## 4. 실행하고 확인한다

```powershell
.\START_P_PROJECT.cmd
```

`Ready` 안내 뒤 `http://127.0.0.1:8000` 화면을 연다. 대여소 검색·과거 시각 선택·주변 후보 비교를 확인한다. 첫 모델 로딩에는 시간이 걸릴 수 있어 시작 도구가 최대 300초 동안 준비 상태를 확인한다.

종료할 때는 저장소 최상위의 `STOP_P_PROJECT.cmd`를 실행한다. 화면은 기존 2025년 3월의 1시간 예측 재생이며, 1·2·3시간 시각 선택 기능은 앞으로 개발할 범위로 구분한다.

설치 상태만 다시 확인할 때는 다음을 실행한다. 이미 일치하는 데이터가 있으면 다시 내려받지 않는다.

```powershell
cd 모델개발
uv run --frozen --no-sync python scripts/setup_runtime.py --profile demo --check
uv run --frozen --no-sync python scripts/check_runtime.py
```

`research`로 준비한 팀원은 상태 확인에도 `--profile research`를 사용한다. 준비를 다시 실행해도 일치하는 기존 데이터·모델·키는 유지한다. 다른 버전의 파일을 발견하면 덮어쓰지 않고 경로를 안내한다.

### API 키가 필요한 작업만 따로 설정한다

기본 화면·과거 재생에는 키를 입력하지 않는다. 추가 연동이 필요할 때 `모델개발/.env`의 해당 항목만 입력한다. `SEOUL_API_KEY`는 서울 실시간 수량, `KOPIS_API_KEY`는 공연 자료 조사 등에 사용한다. 기상청·공공데이터포털은 기관별 활용신청과 키 권한을 따로 확인한다. `.env`는 커밋하지 않는다.

기존 Windows 시작 도구는 `.env`를 자동으로 읽지 않는다. 키를 사용하는 서버는 기존 서버를 종료한 뒤 아래처럼 **uv의 `--env-file` 옵션**으로 실행한다. 키 값을 명령줄에 직접 적지 않는다.

```powershell
cd 모델개발
uv run --env-file .env --frozen --no-sync uvicorn bike_demo.api:app --host 127.0.0.1 --port 8000
```

키를 입력해도 기관의 승인·호출 한도·자료 제공 여부를 자동 해결하지는 않는다. KOPIS의 기존 차단 상태는 재호출로 우회하지 않으며, 이번 설치 검수에서는 실시간 API를 호출하지 않는다.

## 5. 작업을 시작한다

Python 코드 실행 위치는 항상 `모델개발`로 한다.

| 역할 | 먼저 확인할 위치 |
| --- | --- |
| 화면 | `bike_demo/static/`에서 기존 지도·목록·시각 입력을 확인한다. |
| 데이터·백엔드 | `bike_demo/api.py`, `data/`, `scripts/`에서 API·자료 계약을 확인한다. |
| 모델·평가 | `bike_demo/followup_v4/`, `config/`, `experiments/`에서 현재 기준과 평가 구간을 확인한다. |

기본 검사 명령은 다음과 같이 실행한다.

```powershell
uv run --frozen --no-sync pytest tests/test_runtime_setup.py tests/test_followup_v4.py tests/test_public_feature_join.py tests/test_rider_demo.py -q
```

모델 작업에서 전체 특징 행렬이 필요하면 `research` 준비 뒤 다음 명령을 한 번 수행한다. 약 5.5GB의 행렬을 생성하므로 가상환경·캐시까지 고려해 여유 공간 15GB와 RAM 16GB를 권장한다. 화면 작업에는 이 단계가 필요하지 않다.

```powershell
uv run --frozen --no-sync python -m bike_demo.followup_v4.features
```

이후 실험은 새 계획·이름·출력 폴더로 작성한다. 기존 완료 JSON이 있는 실행기를 그대로 돌리면 완료 후보를 건너뛸 수 있으므로 이를 새 학습의 증거로 사용하지 않는다. 기존 고정 시험을 덮어쓰지 않는다.

브랜치는 `feature/작업명`, `model/작업명`, `data/작업명`, `fix/작업명`, `docs/작업명`, `chore/작업명`으로 나누고 커밋 메시지는 `실행 데이터 준비 명령 추가`처럼 짧은 한국어로 작성한다. 자세한 예시는 [협업 안내](../../CONTRIBUTING.md)를 따른다.

## 명령으로 직접 준비하는 방법

Linux 등에서는 다음 명령으로 기본 환경과 데이터를 준비한다. 모델 역할은 `uv sync`에 `--group research`, 준비 명령에 `--profile research`를 추가한다. Windows에서 전체 절차를 검수하며 다른 운영체제의 연구용 PyTorch 휠 호환성은 별도 확인한다.

```bash
cd 모델개발
uv sync --frozen --inexact --python 3.12.15
uv run --frozen --no-sync python scripts/setup_runtime.py --profile demo
uv run --frozen --no-sync python scripts/check_runtime.py
uv run --frozen --no-sync uvicorn bike_demo.api:app --host 127.0.0.1 --port 8000
```

## 자주 막히는 경우

| 증상 | 확인 방법 |
| --- | --- |
| `uv`를 찾지 못한다. | 설치 후 새 터미널을 열고 `uv --version`을 확인한다. |
| Python 설치 중 `Missing expected target directory`가 나타난다. | Windows의 uv Python 연결 경로 문제로 확인한 사례를 [공식 이슈](https://github.com/astral-sh/uv/issues/19622)에서 참고한다. `uv sync --frozen --inexact --python 3.12.15`처럼 패치 버전까지 지정한다. Windows 준비 도구에는 이 지정을 적용한다. |
| ZIP 다운로드가 실패한다. | [실행 자료 릴리스](https://github.com/Honggyu-Chun/seoul-bike-risk-forecast/releases/tag/runtime-20261021)에서 해당 ZIP을 직접 내려받는다. 아래 직접 지정 명령으로 같은 해시 검사를 수행한다. |
| 파일 버전이 다르다는 안내가 나타난다. | 기존 자료를 보존한다. 새 클론 폴더에서 준비하거나 모델 담당자와 버전을 확인한다. 임의 삭제·덮어쓰기를 하지 않는다. |
| 서버가 준비되지 않는다. | `scripts/check_runtime.py`의 결과와 `reports/local_runs`의 로그를 확인한다. 포트 8000을 다른 프로그램이 쓰는지도 확인한다. |
| 실시간 API가 실패한다. | 기본 과거 재생이 먼저 되는지 확인한다. 실시간 연동은 개인별 키·기관 호출 제한·새 기간 검증이 필요한 별도 작업으로 구분한다. |

```powershell
uv run --frozen --no-sync python scripts/setup_runtime.py --profile demo --archive "C:\다운로드경로\demo-runtime-20261021.zip"
```

직접 지정하는 ZIP도 크기·SHA-256·내부 경로·각 파일 해시가 모두 일치해야 설치한다. 실행 자료의 정확한 목록과 원본 재확보 경로는 [데이터·모델 안내](DATA_AND_ARTIFACTS.md)를 따른다.
