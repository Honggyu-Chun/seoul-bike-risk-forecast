# CycleOn 백엔드

따릉이 대여소의 **1~3시간 뒤 0대 위험 예측** 서비스의 앱 백엔드(FastAPI)다. 하는 일은 다섯 가지다.

| 역할 | 호출 방향 | 경로 |
| --- | --- | --- |
| 공개 API | 프론트 → 백엔드 | `/api/...` (5개) |
| 로그인·회원 API | 프론트 → 백엔드 | `/api/auth/...`, `/api/me...` (세션 쿠키) |
| AI 중계 | 백엔드 → AI 서비스 | AI의 `/v1/status`, `/v1/predictions`, `/v1/recommendations` |
| 내부 데이터 API | AI 서비스 → 백엔드 | `/internal/ai-data/v1/...` (3개, 서비스 토큰 필요) |
| 재고 수집기 | 백엔드 → 서울시 API | 별도 프로세스, 2분마다 |
| 장소 검색 중계 | 백엔드 → 카카오 로컬 API | `GET /api/places`가 부를 때만 |

```text
[프론트] ──/api──> [백엔드] ──/v1──> [AI 서비스]
                     │  ▲              │
                     │  └──── 20초마다 /internal/ai-data/v1 (AI가 재고를 가져감)
                     ▼
               [PostgreSQL] <── 2분마다 [수집기] <── 서울시 따릉이 API
```

- 프론트는 **공개 API만** 부른다. 요청·응답 필드는 「CycleOn 공개 API 명세 (프론트엔드용)」 문서가 기준이다.
- AI 예측은 **정각마다 한 번** 계산된다. 수집(2분), AI 동기화(20초), 예측(정각)은 서로 독립적으로 돈다.

## 폴더 구조

```text
backend/
├─ app/
│  ├─ main.py               앱 생성: 설정·DB·AI 클라이언트·CORS·오류 처리·라우터
│  ├─ core/
│  │  ├─ config.py          환경변수 (pydantic-settings, backend/.env)
│  │  ├─ time.py            기준 정각(KST), target_at → 간격(1·2·3) 변환
│  │  ├─ errors.py          오류 응답 {"error": {"code", "message", ...}}
│  │  ├─ security.py        내부 API 서비스 토큰 확인
│  │  ├─ passwords.py       비밀번호 규칙, bcrypt 해시(스레드풀)
│  │  ├─ log_filters.py     접속 로그에서 쿼리(위치·검색어) 지우기
│  │  └─ geo.py             직선거리 계산
│  ├─ api/
│  │  ├─ deps.py            공통 의존성 (세션, AI, 현재 시각, debug, IP, Origin 확인)
│  │  ├─ public/            공개 API 라우터
│  │  ├─ account/           로그인·회원 API 라우터 (auth, me, favorites)
│  │  └─ internal/          내부 데이터 API 라우터
│  ├─ clients/              AI 호출(ai.py), 카카오 로컬 API 호출(kakao.py)과 오류 변환
│  ├─ services/             판단 로직 (AI·DB 합치기, 장애 대체, 정각 경계)
│  ├─ repositories/         DB 읽기·쓰기 (수집분 저장 규칙, 커서 페이지)
│  ├─ schemas/              요청·응답 모델 (public: 프론트, ai: AI 응답, ai_data: 내부 API)
│  ├─ db/                   엔진·세션, 테이블 정의
│  └─ collectors/           따릉이 재고 수집기
├─ migrations/              Alembic 마이그레이션
├─ docker/                  DB 초기화 스크립트 (테스트 DB 생성)
├─ docker-compose.yml       개발용 PostgreSQL 17
└─ tests/
```

## 처음 설정 (Windows PowerShell)

**Python 3.12 이상**이 필요하다(코드가 3.12 문법을 쓴다). PC에 여러 버전이 있으면 `python`이 다른 버전일 수 있으니 `python --version`으로 확인하고, 아니면 3.12 실행 파일 경로를 직접 쓴다.

```powershell
cd backend
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv   # 또는 py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env               # 아래 표를 보고 값 채우기. .env는 커밋하지 않는다
docker compose up -d                 # PostgreSQL 17 (127.0.0.1:5432), 개발 DB cycleon + 테스트 DB cycleon_test
.\.venv\Scripts\alembic upgrade head # 테이블 만들기
```

PyCharm은 인터프리터를 `backend\.venv\Scripts\python.exe`로, 소스 루트를 `backend`로 잡는다.

### `.env` 항목

| 항목 | 설명 |
| --- | --- |
| `CYCLEON_AI_BASE_URL` | AI 서비스 주소. 목업 `http://127.0.0.1:18011`, 실제 AI `http://127.0.0.1:8001` |
| `CYCLEON_SERVICE_TOKEN` | 백엔드와 AI가 **같은 값**으로 쓰는 32자 이상 비밀 문자열. JWT가 아니다. 만들기: `.\.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `CYCLEON_AI_TIMEOUT_SECONDS` | AI 응답 대기 시간(초) |
| `CYCLEON_AI_CLOCK_CHECK` | AI 응답의 기준 정각이 백엔드와 같은지 확인. **실제 AI는 `true`, 목업은 `false`** (목업 시계가 고정이라) |
| `CORS_ORIGINS` | 브라우저 호출을 허용할 프론트 주소 (JSON 배열). 로그인·회원 API의 Origin 확인도 이 목록을 쓴다. 리버스 프록시로 한 사이트에서 서비스하면 그 사이트 주소(`https://...`)를 넣는다 |
| `CYCLEON_COOKIE_SECURE` | 세션 쿠키에 `Secure`를 붙일지. **https 운영은 `true`**, http 로컬 개발은 `false` |
| `CYCLEON_BCRYPT_ROUNDS` | bcrypt 비용. 기본 12(1회 약 0.2~0.3초). 테스트만 4로 낮춘다 |
| `KAKAO_REST_API_KEY` | 장소 검색용 카카오 **REST API 키**(developers.kakao.com의 앱 → 앱 키). 서버에만 둔다. 지도 표시용 JavaScript 키는 프론트에서 쓰고, 앱 설정에 사이트 도메인을 등록한다. 비어 있으면 `/api/places`만 503 |
| `CYCLEON_DEBUG_ENABLED` | `?debug=true` 허용 여부. 운영에서는 `false` |
| `DATABASE_URL` | 개발 DB. `postgresql+asyncpg://...` 형식 (드라이버는 asyncpg) |
| `TEST_DATABASE_URL` | pytest 전용 DB. 테스트가 테이블을 비우므로 **개발 DB와 다른 DB**여야 한다 |
| `SEOUL_COLLECTION_API_KEY` | 서울 열린데이터광장 인증키 (data.seoul.go.kr) |
| `CYCLEON_COLLECTION_INTERVAL_SECONDS` | 수집 간격(초). 기본 120 |
| `CYCLEON_RETENTION_HOURS` | 재고 보관 시간. 기본 192(8일). AI 첫 동기화가 170시간을 요청하므로 그보다 길어야 한다 |

## 실행

터미널을 나눠 띄운다. 모두 `backend` 폴더 기준이다.

```powershell
# 1. DB (한 번 띄워 두면 됨)
docker compose start                 # 처음이면 docker compose up -d

# 2. 수집기 (2분마다 서울 전체 재고 저장, Ctrl+C로 종료)
.\.venv\Scripts\python -m app.collectors.seoul_bike
.\.venv\Scripts\python -m app.collectors.seoul_bike --once   # 한 번만

# 3. 백엔드 (개발 주소 8100, 코드를 고치면 자동 재시작)
.\.venv\Scripts\uvicorn app.main:app --port 8100 --reload
```

API 문서는 `http://127.0.0.1:8100/docs`에서 볼 수 있다.

### AI 서비스 (둘 중 하나)

**목업** (모델 없이 고정된 합성 값, 시계 고정)

```powershell
cd ..
.\SETUP_CYCLEON_MOCK.cmd             # 처음 한 번
.\RUN_CYCLEON_MOCK.cmd               # 18011
```

`.env`: `CYCLEON_AI_BASE_URL=http://127.0.0.1:18011`, `CYCLEON_SERVICE_TOKEN=`(RUN_CYCLEON_MOCK.cmd의 값), `CYCLEON_AI_CLOCK_CHECK=false`

**실제 AI 서비스** (우리 DB를 동기화해서 예측. 모델 번들이 없으면 확률은 `null`)

```powershell
cd ..\모델개발
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv   # 처음 한 번 (3.12)
.\.venv\Scripts\pip install -r requirements-serving.txt
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:CYCLEON_SERVICE_TOKEN = "<backend\.env와 같은 값>"
.\.venv\Scripts\python scripts\cycleon_service.py ai --backend-url http://127.0.0.1:8100 --port 8001
# 모델 번들을 받았으면 --bundle <번들 폴더> 추가
```

`.env`: `CYCLEON_AI_BASE_URL=http://127.0.0.1:8001`, `CYCLEON_AI_CLOCK_CHECK=true`

AI 동기화 캐시는 `모델개발/data/runtime_cache/`에 생긴다(Git 제외). 오래된 자료를 지우지 않아 켜 둔 만큼 커진다.

### 끄기

수집기 → 백엔드·AI(Ctrl+C) → `docker compose stop` 순서. `docker compose down`은 컨테이너만 지우고 데이터는 남긴다. **`down -v`는 데이터 볼륨까지 지우므로 쓰지 않는다.**

## API

| 메서드·경로 | 용도 | AI가 꺼졌을 때 |
| --- | --- | --- |
| `GET /api/status` | 기준 정각, 선택 가능한 이용 시각 3개 | 200, `ai_available=false` |
| `GET /api/stations` | 지도용 대여소 목록과 지금 수량(마커 색, DB 최신 수집분). 위치를 주면 거리순. 캐시 없이 매번, gzip 압축 | 영향 없음 |
| `GET /api/stations/nearby` | 반경 안 대여소 + 0대 확률 + 현재 수량 | 200, 확률 `null`, 수량은 DB 최신 수집분 |
| `POST /api/predictions` | 선택·즐겨찾기 대여소 예측 (`all_targets`로 3개 시각) | 위와 같음 |
| `POST /api/recommendations` | 추천 1곳 + 대안 최대 2곳 | 503 `ai_unavailable` |
| `GET /api/places?query=` | 장소·주소 검색 → 서울 안 후보 최대 5개(이름·주소·좌표). 카카오 장애·키 없음은 503 `place_search_unavailable` | 영향 없음 |
| `POST /api/auth/signup`·`login`·`logout` | 가입(바로 로그인)·로그인·로그아웃. 세션 쿠키 발급·삭제 | 영향 없음 |
| `GET`·`PATCH`·`DELETE /api/me` | 프로필 조회·닉네임/소개 변경·탈퇴(비밀번호 확인) | 영향 없음 |
| `PUT /api/me/password`, `POST /api/me/logout-all` | 비밀번호 변경(다른 기기 로그아웃), 모든 기기 로그아웃 | 영향 없음 |
| `GET /api/me/favorites` | 즐겨찾기 목록. `target_at`을 주면 예측·현재 수량을 붙임(`/api/predictions`와 같은 항목 + `alias`·`position`) | 200, 확률 `null`, 수량은 DB 최신 수집분 |
| `POST`·`PATCH`·`DELETE /api/me/favorites...` | 추가(20개까지, 운영 중인 대여소만)·별칭 변경·삭제, `PUT .../order` 순서 변경, `POST .../import` 게스트 즐겨찾기 옮기기 | 영향 없음 |
| `GET /internal/ai-data/v1/stations`·`observations`·`forecasts` | AI 동기화 (커서 페이지) | — |

- `target_at`은 `GET /api/status`의 `supported_targets` 중 하나를 **그대로** 보낸다. 지난 정각이나 정각이 아닌 시각은 409 `target_not_supported`.
- 오류는 모두 `{"error": {"code", "message", ...}}`. AI 401은 502 `ai_auth_failed`, AI 422·5xx는 502 `ai_error`.

### 로그인과 세션

- 로그인하면 `cycleon_session` 쿠키(HttpOnly, SameSite=Lax, Path=/api)를 준다. DB에는 토큰의 SHA-256 해시만 둔다. 마지막 사용 후 14일, 로그인 후 30일 중 먼저 오는 때 끝난다.
- 프론트는 백엔드와 **같은 사이트**에서 불러야 쿠키가 실린다. 개발은 Vite 프록시(`/api` → `http://127.0.0.1:8100`), 운영은 리버스 프록시(nginx·Caddy)가 프론트 파일과 `/api`를 함께 서비스하는 구성을 권장한다.
- 로그인·가입·회원 변경 요청은 `Origin`이 `CORS_ORIGINS`에 있어야 하고(403 `origin_not_allowed`), 본문은 JSON만 받는다(415).
- 횟수 제한: 같은 아이디+IP 로그인 실패 15분 5회, 같은 IP 실패 15분 30회, 같은 IP 가입 1시간 10회. 넘으면 429 `too_many_attempts`(`retry_after_seconds`, `Retry-After`).
- **리버스 프록시 뒤에서는 실제 IP를 받아야 한다.** 프록시가 `X-Forwarded-For`를 붙이고, uvicorn은 믿는 프록시 주소만 지정한다: `uvicorn app.main:app --forwarded-allow-ips <프록시 IP>`(같은 서버의 nginx면 기본값 `127.0.0.1`로 충분). `*`는 쓰지 않는다. 아무나 보낸 헤더를 믿게 되어 횟수 제한을 피할 수 있다.

## 테스트

```powershell
docker compose start                 # DB 테스트에 필요
.\.venv\Scripts\pytest -rs           # -rs: 건너뛴 테스트와 이유 표시
```

- DB가 꺼져 있거나 `TEST_DATABASE_URL`이 없으면 DB 테스트는 **건너뛴다**(실패가 아님). 푸시 전에는 건너뛴 것이 없는지 확인한다.
- CI(`.github/workflows/backend.yml`)는 `backend/`가 바뀐 PR과 `main`·`feature/backend` 푸시에서 PostgreSQL을 띄워 `alembic check`와 전체 테스트를 돌린다. CI에서는 DB 테스트를 건너뛰면 실패로 처리한다.
- 서울시 API와 AI는 테스트에서 가짜(httpx.MockTransport)를 쓴다. 비밀값이 필요 없다.

## 테이블을 바꿀 때

1. `app/db/models.py`를 고친다.
2. `.\.venv\Scripts\alembic revision --autogenerate -m "설명"`으로 마이그레이션 파일을 만들고 **내용을 확인한다.**
3. `.\.venv\Scripts\alembic upgrade head`로 개발 DB에 적용한다. 테스트 DB는 pytest가 자동으로 적용한다.
4. `.\.venv\Scripts\alembic check`가 "No new upgrade operations detected"인지 확인한다(CI도 확인함).

`alembic.ini`는 **영어(ASCII)로만** 쓴다. Windows에서 cp949로 읽어 한글이 있으면 Alembic이 실행되지 않는다.

## 자주 겪는 문제

| 증상 | 원인과 해결 |
| --- | --- |
| 409 `target_not_supported` | `target_at`이 지금 선택지가 아님. `/docs`의 기본값(현재 시각, 분·초 포함)이나 지난 정각을 보낸 경우. 응답의 `supported_targets` 중 하나를 쓴다 |
| 확률 `null`, `reason: base_inventory_unavailable` | 기준 정각 직전 5분 안에 완료된 수집분이 없음. 수집기가 멈췄거나 PC가 절전 상태였던 경우. 수집기가 돌면 다음 정각부터 풀린다 |
| 확률 `null`, `reason: model_not_ready` / `history_not_ready` | 모델 번들이 없거나, 수집 이력이 부족함(T 경로는 수집 시작 후 7일) |
| 주변·예측·추천 API가 502 `ai_error` (목업 사용 중) | `CYCLEON_AI_CLOCK_CHECK=true`로 목업에 붙음. 목업은 `false`로 |
| 502 `ai_auth_failed` | 백엔드와 AI의 `CYCLEON_SERVICE_TOKEN`이 다름 |
| AI 동기화가 계속 422 | 개발 DB를 비웠는데 AI 캐시는 예전 커서를 기억함. AI를 끄고 `모델개발/data/runtime_cache/`를 지운 뒤 다시 켠다 |
| 수집기가 바로 종료 (`collector stopped: provider_INFO-100` 등) | 인증키 오류나 한도 초과. 키를 확인한다. 이때는 재시도하지 않고 멈추도록 되어 있다 |
| `alembic` 실행 시 `UnicodeDecodeError` | `alembic.ini`에 한글이 들어감 |
| import 단계에서 `SyntaxError` (`class Page[R]` 등) | venv가 Python 3.11 이하로 만들어짐. 3.12로 다시 만든다 |

## 남은 일

- uvicorn 접속 로그는 쿼리를 지우고 경로만 남긴다(`app/core/log_filters.py`). 위치(`lat`, `lon`)·검색어가 로그에 쌓이지 않는다. 앞단 리버스 프록시(nginx 등)의 접속 로그는 배포 때 따로 맞춘다.
- `/api/places`에는 호출 횟수 제한이 없다. 카카오 하루 호출 한도를 넘기면 그날은 503이 난다. 배포 때 IP별 제한을 검토한다.
- 폐소 감지(`valid_to`), 수집기 중복 실행 방지가 없다.
- 기상 자료(`/forecasts`)는 기상을 쓰는 모델 번들이 나올 때까지 빈 페이지다.
- 배포용 Dockerfile·운영 설정은 배포 서버가 정해지면 추가한다.
- 만료된 세션과 하루 지난 로그인 시도 기록은 로그인할 때 지운다. 로그인이 드문 기간에는 남아 있을 수 있다(정기 정리는 배포 때 추가).
