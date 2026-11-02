# CycleOn 백엔드

FastAPI 애플리케이션 백엔드. 프론트 공개 API, AI 중계, AI용 내부 데이터 API, 수집 작업을 담당한다.

```text
backend/
├─ app/
│  ├─ main.py              앱 생성·라우터 등록
│  ├─ core/                설정(config)·인증(security)
│  ├─ api/
│  │  ├─ public/           프론트용: 대여소 목록, 예측·추천 중계
│  │  └─ internal/         AI용: /internal/ai-data/v1 (stations, observations, forecasts)
│  ├─ clients/ai.py        AI 서비스 호출
│  ├─ schemas/             요청·응답 모델
│  ├─ db/                  DB 연결·테이블
│  ├─ repositories/        DB 조회·저장
│  ├─ services/            업무 로직
│  └─ collectors/          따릉이·기상 수집 (별도 프로세스)
└─ tests/
```

## 실행

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\uvicorn app.main:app --reload --port 8100
.\.venv\Scripts\pytest
```

AI는 목업(`RUN_CYCLEON_MOCK.cmd`, 18011)으로 개발한다. 계약은 `모델개발/docs/cycleon/BACKEND_HANDOFF.md`와 `모델개발/docs/cycleon_*_openapi.json`을 따른다.
