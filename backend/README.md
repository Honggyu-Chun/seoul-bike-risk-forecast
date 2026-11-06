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
├─ migrations/             Alembic 마이그레이션
├─ docker-compose.yml      개발용 PostgreSQL
└─ tests/
```

## 실행

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env                       # 값 채우기 (.env는 커밋하지 않음)
docker compose up -d                         # PostgreSQL 17 (127.0.0.1:5432)
.\.venv\Scripts\alembic upgrade head          # 테이블 생성·변경 적용
.\.venv\Scripts\uvicorn app.main:app --reload --port 8100
.\.venv\Scripts\pytest                        # DB 테스트는 TEST_DATABASE_URL이 있을 때만
```

테이블을 바꿀 때는 `app/db/models.py`를 고친 뒤 `alembic revision --autogenerate -m "설명"`으로 마이그레이션 파일을 만들고, 내용을 확인한 다음 `alembic upgrade head`로 적용한다.

AI는 목업(`RUN_CYCLEON_MOCK.cmd`, 18011)으로 개발한다. 계약은 `모델개발/docs/cycleon/BACKEND_HANDOFF.md`와 `모델개발/docs/cycleon_*_openapi.json`을 따른다.
