"""가입·로그인·세션·회원 API 테스트 (/api/auth/..., /api/me...).

테스트 DB를 쓰고, bcrypt 비용은 4로 낮춘다(운영 12). 현재 시각은 get_now를 바꿔 고정·이동한다.
TestClient는 테스트마다 하나만 연다. 여러 기기는 쿠키를 바꿔 끼워 흉내 낸다(클라이언트를 둘 열면
이벤트 루프가 둘이 되어 DB 연결 풀이 깨진다).
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.deps import get_now
from app.core.config import Settings
from app.db.session import make_engine
from app.main import create_app
from app.services.accounts import COOKIE_NAME

NOW = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
ORIGIN = {"Origin": "http://localhost:5173"}
EVIL = {"Origin": "http://evil.example"}
PASSWORD = "bike-ride-2026"


def run_sql(url: str, *statements: str) -> list:
    """SQL을 차례로 실행하고, 행을 돌려주는 문장은 그 행 목록을(아니면 None) 모아 돌려준다."""
    async def go():
        engine = make_engine(url)
        results = []
        try:
            async with engine.begin() as conn:
                for statement in statements:
                    result = await conn.execute(text(statement))
                    results.append(result.all() if result.returns_rows else None)
        finally:
            await engine.dispose()
        return results
    return asyncio.run(go())


class Clock:
    def __init__(self):
        self.now = NOW

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def make_client(test_database_url, clock):
    """앱을 만들어 TestClient를 연다. trust_proxy=True면 uvicorn처럼 프록시 헤더 미들웨어를 씌운다."""
    run_sql(test_database_url, "TRUNCATE users, sessions, login_attempts RESTART IDENTITY CASCADE")
    opened = []

    def build(trust_proxy: bool | None = None):
        settings = Settings(_env_file=None, DATABASE_URL=test_database_url, CYCLEON_BCRYPT_ROUNDS=4)
        app = create_app(settings)
        app.dependency_overrides[get_now] = lambda: clock.now
        asgi = app if trust_proxy is None else ProxyHeadersMiddleware(
            app, trusted_hosts=["testclient"] if trust_proxy else ["10.0.0.1"])
        client = TestClient(asgi, raise_server_exceptions=False)
        opened.append(client.__enter__())
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def signup(client, login_id="rider_01", password=PASSWORD, **extra):
    return client.post("/api/auth/signup", json={"login_id": login_id, "password": password, **extra}, headers=ORIGIN)


def login(client, login_id="rider_01", password=PASSWORD, headers=None):
    return client.post("/api/auth/login", json={"login_id": login_id, "password": password},
                       headers={**ORIGIN, **(headers or {})})


def use_token(client, token):
    """이 클라이언트를 token을 가진 기기로 바꾼다"""
    client.cookies.clear()
    if token:
        client.cookies.set(COOKIE_NAME, token)


def token_of(response):
    return response.cookies.get(COOKIE_NAME)


# 가입

def test_signup_logs_in_and_stores_only_hashes(client, test_database_url):
    response = signup(client, login_id="  Rider_01 ", email=" Me@Example.COM ")
    assert response.status_code == 201
    assert response.json() == {"login_id": "rider_01", "nickname": None, "bio": None, "email": "me@example.com",
                               "email_verified": False, "created_at": "2026-11-09T05:00:00Z"}
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/api" in cookie and "Max-Age=2592000" in cookie
    assert client.get("/api/me").json()["login_id"] == "rider_01"

    token = token_of(response)
    (users, sessions) = run_sql(test_database_url, "SELECT password_hash FROM users",
                                "SELECT token_hash FROM sessions")
    assert users[0][0].startswith("$2b$04$") and PASSWORD not in users[0][0]
    assert len(sessions) == 1 and sessions[0][0] != token and len(sessions[0][0]) == 64


@pytest.mark.parametrize("body, field", [
    ({"login_id": "abc"}, "login_id"),
    ({"login_id": "rider-01"}, "login_id"),
    ({"login_id": "라이더라이더"}, "login_id"),
    ({"login_id": "a" * 21}, "login_id"),
    ({"password": "short12"}, "password"),
    ({"password": "password123"}, "password"),           # 흔한 비밀번호
    ({"password": "RIDER_01"}, "password"),              # 아이디와 같음(대소문자 무시)
    ({"password": "가" * 25}, "password"),                # 75바이트 > 72
    ({"email": "not-an-email"}, "email"),
])
def test_signup_rejects_invalid_values(client, body, field):
    response = client.post("/api/auth/signup", json={"login_id": "rider_01", "password": PASSWORD, **body},
                           headers=ORIGIN)
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == [field]


def test_signup_accepts_long_korean_password_within_72_bytes(client):
    assert signup(client, password="가" * 24).status_code == 201   # 72바이트


def test_signup_duplicate_login_id_case_insensitive(client):
    assert signup(client).status_code == 201
    response = signup(client, login_id="RIDER_01")
    assert response.status_code == 409 and response.json()["error"]["code"] == "login_id_taken"


# 로그인·로그아웃·세션

def test_login_failures_look_the_same(client):
    signup(client)
    client.cookies.clear()
    wrong = login(client, password="wrong-password")
    unknown = login(client, login_id="nobody_99")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "invalid_credentials"
    assert login(client, login_id="RIDER_01").status_code == 200


def test_login_issues_new_session_and_drops_old_one(client):
    old = token_of(signup(client))
    new = token_of(login(client))
    assert new and new != old
    use_token(client, old)
    assert client.get("/api/me").status_code == 401
    use_token(client, new)
    assert client.get("/api/me").status_code == 200


def test_logout(client):
    signup(client)
    assert client.post("/api/auth/logout", headers=ORIGIN).status_code == 204
    assert client.get("/api/me").status_code == 401
    client.cookies.clear()
    assert client.post("/api/auth/logout", headers=ORIGIN).status_code == 204  # 로그인 안 해도 204


def test_me_requires_valid_session(client):
    response = client.get("/api/me")
    assert response.status_code == 401 and response.json()["error"]["code"] == "not_authenticated"
    assert "set-cookie" not in response.headers
    use_token(client, "made-up-token")
    response = client.get("/api/me")
    assert response.status_code == 401
    assert f'{COOKIE_NAME}=""' in response.headers["set-cookie"]  # 무효한 쿠키는 지우게 한다


def test_session_idle_and_absolute_expiry(client, clock):
    signup(client)
    clock.advance(days=13)
    assert client.get("/api/me").status_code == 200       # 사용 → 마지막 사용 시각 갱신
    clock.advance(days=13)
    assert client.get("/api/me").status_code == 200       # 마지막 사용 후 13일
    clock.advance(days=3, seconds=1)
    assert client.get("/api/me").status_code == 200       # 로그인 후 29일
    clock.advance(days=1)
    assert client.get("/api/me").status_code == 401       # 로그인 후 30일 넘음

    login(client)
    clock.advance(days=14)
    assert client.get("/api/me").status_code == 401       # 14일 동안 쓰지 않음


def test_login_cleans_up_stale_rows(client, clock, test_database_url):
    signup(client)
    login(client, password="wrong-password")
    clock.advance(days=31)
    login(client)
    attempts, sessions = run_sql(test_database_url, "SELECT kind, succeeded FROM login_attempts",
                                 "SELECT count(*) FROM sessions")
    assert attempts == [("login", True)]   # 하루 지난 가입·실패 기록은 지워짐
    assert sessions[0][0] == 1              # 만료된 가입 때 세션도 지워짐


# 횟수 제한

def test_login_limit_per_login_id_and_ip(client, clock):
    signup(client)
    client.cookies.clear()
    for _ in range(5):
        assert login(client, password="wrong-password").status_code == 401
    blocked = login(client)  # 맞는 비밀번호도 막힘
    assert blocked.status_code == 429
    assert blocked.json()["error"]["retry_after_seconds"] == 900
    assert blocked.headers["retry-after"] == "900"
    clock.advance(minutes=15, seconds=1)
    assert login(client).status_code == 200


def test_login_limit_per_ip(client):
    for i in range(30):
        login(client, login_id=f"user_{i:02d}")
    response = login(client, login_id="user_99")
    assert response.status_code == 429


def test_signup_limit_per_ip(client, clock):
    for i in range(10):
        assert signup(client, login_id=f"rider_{i:02d}").status_code == 201
    blocked = signup(client, login_id="rider_10")
    assert blocked.status_code == 429 and blocked.json()["error"]["retry_after_seconds"] == 3600
    clock.advance(hours=1, seconds=1)
    assert signup(client, login_id="rider_10").status_code == 201


def test_signup_validation_errors_do_not_count(client):
    for _ in range(12):
        signup(client, password="short")
    assert signup(client).status_code == 201


def test_trusted_proxy_forwarded_ip_is_used(make_client):
    client = make_client(trust_proxy=True)
    signup(client)
    client.cookies.clear()
    for _ in range(5):
        login(client, password="wrong-password", headers={"X-Forwarded-For": "203.0.113.1"})
    assert login(client, headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 429
    assert login(client, headers={"X-Forwarded-For": "203.0.113.2"}).status_code == 200  # 다른 사용자


def test_untrusted_forwarded_header_is_ignored(make_client):
    client = make_client(trust_proxy=False)
    signup(client)
    client.cookies.clear()
    for i in range(5):
        login(client, password="wrong-password", headers={"X-Forwarded-For": f"203.0.113.{i}"})
    assert login(client, headers={"X-Forwarded-For": "203.0.113.99"}).status_code == 429


# Origin 확인(CSRF 방지)

@pytest.mark.parametrize("method, path, body", [
    ("POST", "/api/auth/signup", {"login_id": "rider_02", "password": PASSWORD}),
    ("POST", "/api/auth/login", {"login_id": "rider_01", "password": PASSWORD}),
    ("POST", "/api/auth/logout", None),
    ("PATCH", "/api/me", {"nickname": "해킹"}),
    ("PUT", "/api/me/password", {"current_password": PASSWORD, "new_password": "new-bike-2026"}),
    ("POST", "/api/me/logout-all", None),
    ("DELETE", "/api/me", {"password": PASSWORD}),
])
def test_other_origin_is_rejected(client, method, path, body):
    signup(client)
    response = client.request(method, path, json=body, headers=EVIL)
    assert response.status_code == 403 and response.json()["error"]["code"] == "origin_not_allowed"
    assert client.get("/api/me").json()["nickname"] is None   # 아무것도 바뀌지 않음


def test_form_body_is_rejected(client):
    response = client.post("/api/auth/login", data={"login_id": "rider_01", "password": PASSWORD})
    assert response.status_code == 415 and response.json()["error"]["code"] == "unsupported_media_type"


def test_cors_allows_credentials_for_allowed_origin(client):
    response = client.options("/api/me", headers={**ORIGIN, "Access-Control-Request-Method": "PATCH"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert response.headers.get("access-control-allow-credentials") == "true"


# 프로필·비밀번호·탈퇴

def test_update_profile(client):
    signup(client)
    body = client.patch("/api/me", json={"nickname": " 라이더 ", "bio": "출퇴근은 따릉이로"}, headers=ORIGIN).json()
    assert (body["nickname"], body["bio"]) == ("라이더", "출퇴근은 따릉이로")
    body = client.patch("/api/me", json={"bio": ""}, headers=ORIGIN).json()
    assert (body["nickname"], body["bio"]) == ("라이더", None)   # 보낸 필드만 바뀜
    response = client.patch("/api/me", json={"nickname": "a"}, headers=ORIGIN)
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["nickname"]


def test_change_password_keeps_this_device_only(client):
    other = token_of(signup(client))
    this = token_of(login(client))
    url = "/api/me/password"
    wrong = client.put(url, json={"current_password": "nope-nope", "new_password": "new-bike-2026"}, headers=ORIGIN)
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "invalid_password"
    weak = client.put(url, json={"current_password": PASSWORD, "new_password": "12345678"}, headers=ORIGIN)
    assert weak.status_code == 422 and weak.json()["error"]["fields"] == ["new_password"]
    assert client.put(url, json={"current_password": PASSWORD, "new_password": "new-bike-2026"},
                      headers=ORIGIN).status_code == 204

    assert client.get("/api/me").status_code == 200
    use_token(client, other)
    assert client.get("/api/me").status_code == 401
    use_token(client, this)
    assert login(client).status_code == 401
    assert login(client, password="new-bike-2026").status_code == 200


def test_logout_all(client):
    first = token_of(signup(client))
    second = token_of(login(client))
    login(client)  # 세 번째 기기
    assert client.post("/api/me/logout-all", headers=ORIGIN).status_code == 204
    for token in (first, second):
        use_token(client, token)
        assert client.get("/api/me").status_code == 401


def test_delete_account(client, test_database_url):
    signup(client)
    wrong = client.request("DELETE", "/api/me", json={"password": "nope-nope"}, headers=ORIGIN)
    assert wrong.status_code == 403
    response = client.request("DELETE", "/api/me", json={"password": PASSWORD}, headers=ORIGIN)
    assert response.status_code == 204 and f'{COOKIE_NAME}=""' in response.headers["set-cookie"]
    assert client.get("/api/me").status_code == 401
    users, sessions = run_sql(test_database_url, "SELECT count(*) FROM users", "SELECT count(*) FROM sessions")
    assert users[0][0] == 0 and sessions[0][0] == 0
    assert signup(client).status_code == 201   # 같은 아이디로 다시 가입 가능
