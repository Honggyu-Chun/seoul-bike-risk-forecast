# 로컬 PC로 작업 옮기기

이 문서는 2026년 10월 20일 클라우드 작업 폴더 `/workspace/P_Project`를 기준으로 작성했습니다. 압축 파일을 다운로드해 로컬에서 푼 뒤, Codex 또는 편집기에서 **풀린 `P_Project` 폴더**를 여세요. 기존에 같은 이름의 프로젝트가 있다면 새 폴더에 먼저 풉니다.

## 어떤 파일을 받으면 되나요?

|파일|용도|포함 범위|
|---|---|---|
|`따릉이_데모_실행패키지.zip`|화면 시연과 일반적인 코드 수정|앱 코드, 데모용 모델·예측·데이터, 최종 보고서, 주요 실험 기록|
|`따릉이_전체작업_로컬이전_20261020.zip`|클라우드에서 한 연구 작업까지 이어가기|현재 프로젝트의 소스·원본·처리 데이터, 1차/후속 연구 모델, 최종 예측·보정기, 로그, 문서, 실험 기록|

전체 백업은 클라우드 가상환경 `.venv`, 캐시, Git 내부 정보, 비밀 환경설정 파일을 제외합니다. 필요한 Python 패키지는 로컬에서 다시 설치합니다. 실행 중인 서버 프로세스, 이 채팅의 전체 대화, 계정 사용 한도는 옮겨지지 않습니다. 원래 업로드한 첨부물은 현재 프로젝트 안에 저장된 파일만 포함합니다.

## 가장 빠른 확인: 설치 없이 화면 보기

`deliverables/따릉이_시민용_서비스데모_v2.html`을 브라우저에서 엽니다. 이 HTML은 유한한 12개 과거 시각과 결과표를 담은 오프라인 시연본입니다. 실시간 조회와 서버용 Word 다운로드는 아래 서버 실행이 필요합니다. 클라우드 검수에서는 동일 HTML을 네트워크 차단 상태로 확인했으며, 운영체제별 파일 직접 열기는 별도로 확인해야 합니다.

## Windows에서 서버 실행하기

Python 패키지 관리 도구 [uv](https://docs.astral.sh/uv/getting-started/installation/)를 설치한 뒤 PowerShell에서 실행합니다. 경로는 실제로 압축을 푼 위치로 바꾸세요.

```powershell
cd "C:\Users\사용자\Downloads\P_Project"
uv python install 3.12
uv sync --frozen
uv run --frozen --no-sync uvicorn bike_demo.api:app --host 127.0.0.1 --port 8000
```

## macOS / Linux에서 서버 실행하기

uv 설치 후 터미널에서 실행합니다.

```bash
cd "/압축을/푼/위치/P_Project"
uv python install 3.12
uv sync --frozen
uv run --frozen --no-sync uvicorn bike_demo.api:app --host 127.0.0.1 --port 8000
```

macOS에서 LightGBM이 OpenMP 라이브러리를 찾지 못하면 Homebrew의 `libomp`가 필요할 수 있습니다. Linux도 해당 배포판의 OpenMP 런타임이 필요합니다. 로컬 운영체제에서의 신규 설치는 아직 검증하지 않았습니다.

서버 실행 후 **내 PC의 브라우저 주소창**에 `http://127.0.0.1:8000`을 입력합니다. 준비 상태는 같은 주소의 `/api/health`에서 확인합니다. 이 주소는 로컬 실행 안내이며 클라우드 데모 접속 주소가 아닙니다. 종료는 터미널에서 `Ctrl+C`를 누릅니다. 포트가 사용 중이면 명령의 `8000`을 `8001`로 바꾸고 브라우저에도 같은 포트를 입력합니다.

처음 설치에는 인터넷이 필요하지만 과거 재생에는 API 키나 재학습이 필요하지 않습니다. 앱 실행 명령은 선택 의존성을 지우지 않도록 `--no-sync`를 사용합니다. README에 남아 있는 `/workspace/...` 경로는 클라우드 경로이므로 로컬에서 그대로 쓰지 않습니다.

## 전체 연구를 이어가려면

전체 백업을 사용하세요. 잠금 파일의 연구 환경은 **Python 3.12 / Linux CPU**를 기준으로 구성했습니다. macOS용 PyTorch 등으로 변경하려면 별도 호환성 조정이 필요할 수 있으므로 기존 환경과 같다고 간주하지 마세요.

```bash
uv sync --frozen --group research
uv run --frozen --no-sync python -m pytest -q
```

현재 클라우드에서 전체 테스트 40개와 브라우저 시나리오 13개를 통과했습니다. 이는 새 PC의 설치 성공을 보증하는 결과는 아닙니다. 브라우저 자동 검사는 Playwright와 Chromium이 별도로 필요하며 현재 검사 스크립트의 Chromium 경로는 Linux 기준입니다. Word 재작성과 PDF 변환도 별도 작업으로, PDF 변환에는 LibreOffice와 한글 글꼴이 필요합니다.

이미 완료된 시험을 다시 선택 자료로 사용하지 않도록 아래를 보존하세요.

- 고정 규칙: `config/improvement_v3.json`
- 고정 선택·파일 해시: `artifacts/improvement/final/selection_frozen_before_test.json`
- 최종 실제 결과: `artifacts/improvement/final/results.json`
- 시험 예측과 보정기: `artifacts/improvement/final/`
- 1차 후보 45개·후속 후보 15개의 모델과 기록: `artifacts/improvement/screen/`, `artifacts/improvement/forward/`

새 시험은 2025년 4월 15일~5월 14일이며 **이미 한 번 평가한 자료**입니다. 이 결과를 보고 튜닝한 다음 같은 기간을 새 독립 시험이라고 부르면 안 됩니다. 추가 모델 선택에는 진짜 미사용 기간과 새 검증 계획이 필요합니다. 단순 화면 수정 때문에 전체 학습 파이프라인을 다시 실행할 필요는 없습니다.

## 새 로컬 Codex 대화에 전달할 내용

아래 내용을 첫 메시지에 붙여 넣으면 작업 맥락을 이어가기 쉽습니다.

> 이 폴더는 서울 따릉이 대여자를 위한 대여소 비교·예측 데모입니다. 먼저 README.md, docs/local_transfer_guide.md, docs/demo_v2_guide.md를 읽으세요. 지도는 기존 3월 M1 예측 재생이고 새 M2는 결과 탭에서 별도로 표시합니다. 새 시험의 실제 전체 F1은 B1 72.9593% → M2 74.0702%, 추천 유효율은 R0 94.0822% → R2 98.1040%, 평균 추가 직선거리는 32.18m입니다. 두 +5%p 목표는 미달입니다. 실제 시민의 대여 성공과 전역 실시간 정확도는 검증하지 않았고 공식 실시간 샘플은 5건입니다. 최종 results.json과 selection_frozen_before_test.json, 모델·보정기·프로토콜 해시를 보존하고, 이미 열린 시험을 다시 튜닝에 사용하지 마세요. 소스·데이터·모델은 현재 폴더를 재사용하고 불필요한 재학습이나 다운로드는 피하세요. 앞으로 요청하는 수정부터 진행해 주세요.

## 백업 파일 확인

전체 백업 안의 `reports/local_transfer_manifest.json`에는 포함 파일의 크기와 SHA-256이 기록됩니다. 별도 `.zip.sha256` 파일은 다운로드한 압축 파일 자체를 확인하기 위한 것입니다. 압축 내부 경로는 모두 `P_Project/` 아래에 있습니다. 전체 백업에 Git 내부 정보는 없으므로 기존 GitHub 저장소의 커밋 내역을 대신하지 않습니다. 현재 클라우드 저장소의 작업 파일은 아직 커밋되지 않은 상태입니다.
