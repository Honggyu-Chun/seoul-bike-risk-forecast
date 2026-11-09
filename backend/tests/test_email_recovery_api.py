"""복구용 이메일·인증 메일·비밀번호 재설정 테스트.

메일은 가짜 발송기(FakeMailer)가 받는다. 본문의 링크에서 토큰을 꺼내 실제 흐름대로 확인한다.
SMTP 발송기 자체는 맨 아래에서 smtplib를 가짜로 바꿔 확인한다.
"""
import asyncio
import re
import smtplib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_now
from app.clients.mailer import SmtpMailer
from app.core.config import Settings
from app.core.errors import MailUnavailable
from app.db.session import make_engine
from app.main import create_app
from app.services.accounts import COOKIE_NAME

NOW = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
ORIGIN = {"Origin": "http://localhost:5173"}
PASSWORD = "bike-ride-2026"
BASE = "https://cycleon.example"


class FakeMailer:
    def __init__(self):
        self.sent, self.fail = [], False

    async def send(self, to, subject, body):
        if self.fail:
            raise MailUnavailable()
        self.sent.append({"to": to, "subject": subject, "body": body})

    def token(self, index=-1):
        return re.search(r"token=([A-Za-z0-9_-]+)", self.sent[index]["body"]).group(1)


class Clock:
    now = NOW

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


def sql(url, statement):
    async def go():
        engine = make_engine(url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement))
                return result.all() if result.returns_rows else None
        finally:
            await engine.dispose()
    return asyncio.run(go())


@pytest.fixture
def env(test_database_url):
    """(TestClient, FakeMailer, Clock). rider_01로 가입·로그인한 상태로 시작한다."""
    sql(test_database_url, "TRUNCATE users, sessions, login_attempts, favorites, email_tokens RESTART IDENTITY CASCADE")
    mailer, clock = FakeMailer(), Clock()
    settings = Settings(_env_file=None, DATABASE_URL=test_database_url, CYCLEON_BCRYPT_ROUNDS=4,
                        CYCLEON_PUBLIC_BASE_URL=BASE + "/")
    app = create_app(settings, mailer=mailer)
    app.dependency_overrides[get_now] = lambda: clock.now
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    assert signup(client, "rider_01").status_code == 201
    yield client, mailer, clock
    client.__exit__(None, None, None)


def signup(client, login_id, **extra):
    return client.post("/api/auth/signup", json={"login_id": login_id, "password": PASSWORD, **extra}, headers=ORIGIN)


def set_email(client, email, password=PASSWORD):
    return client.put("/api/me/email", json={"email": email, "password": password}, headers=ORIGIN)


def send_verification(client):
    return client.post("/api/me/email/verification", headers=ORIGIN)


def verify(client, token):
    return client.post("/api/auth/email/verify", json={"token": token}, headers=ORIGIN)


def verified_user(client, mailer, email="me@example.com"):
    set_email(client, email)
    send_verification(client)
    assert verify(client, mailer.token()).status_code == 204


def request_reset(client, identifier):
    return client.post("/api/auth/password-reset", json={"login_id_or_email": identifier}, headers=ORIGIN)


def confirm_reset(client, token, new_password="new-bike-2026"):
    return client.post("/api/auth/password-reset/confirm", json={"token": token, "new_password": new_password},
                       headers=ORIGIN)


def login(client, password):
    return client.post("/api/auth/login", json={"login_id": "rider_01", "password": password}, headers=ORIGIN)


# 이메일 등록·변경

def test_change_email(env):
    client, _, _ = env
    assert set_email(client, "me@example.com", password="wrong-pass").status_code == 403
    body = set_email(client, " Me@Example.com ").json()
    assert (body["email"], body["email_verified"]) == ("me@example.com", False)
    assert set_email(client, None).json()["email"] is None
    response = set_email(client, "not-an-email")
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["email"]


# 인증 메일

def test_verification_flow(env):
    client, mailer, _ = env
    assert send_verification(client).status_code == 422   # 이메일이 없음
    set_email(client, "me@example.com")
    assert send_verification(client).status_code == 204
    mail = mailer.sent[0]
    assert mail["to"] == "me@example.com" and mail["subject"] == "[CycleOn] 이메일 인증"
    assert f"{BASE}/verify-email?token=" in mail["body"] and "rider_01" in mail["body"]

    client.cookies.clear()   # 로그인하지 않은 다른 기기에서 링크를 열어도 된다
    assert verify(client, mailer.token()).status_code == 204
    assert verify(client, mailer.token()).status_code == 400   # 한 번만
    login(client, PASSWORD)
    assert client.get("/api/me").json()["email_verified"] is True
    assert send_verification(client).status_code == 204 and len(mailer.sent) == 1   # 이미 인증됨: 보내지 않음


def test_verification_token_expires_and_dies_when_email_changes(env):
    client, mailer, clock = env
    set_email(client, "me@example.com")
    send_verification(client)
    clock.advance(minutes=31)
    response = verify(client, mailer.token())
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_token"

    send_verification(client)
    set_email(client, "other@example.com")
    assert verify(client, mailer.token()).status_code == 400
    assert verify(client, "made-up").status_code == 400


def test_changing_email_resets_verification(env):
    client, mailer, _ = env
    verified_user(client, mailer)
    assert set_email(client, "me@example.com").json()["email_verified"] is True   # 같은 주소면 그대로
    assert set_email(client, "new@example.com").json()["email_verified"] is False


def test_mail_limit_per_account(env):
    client, mailer, clock = env
    set_email(client, "me@example.com")
    for _ in range(3):
        assert send_verification(client).status_code == 204
    blocked = send_verification(client)
    assert blocked.status_code == 429 and blocked.json()["error"]["retry_after_seconds"] == 900
    clock.advance(minutes=15, seconds=1)
    assert send_verification(client).status_code == 204


def test_mail_failure_is_503_and_not_counted(env, test_database_url):
    client, mailer, _ = env
    set_email(client, "me@example.com")
    mailer.fail = True
    for _ in range(4):
        response = send_verification(client)
        assert response.status_code == 503 and response.json()["error"]["code"] == "mail_unavailable"
    assert sql(test_database_url, "SELECT count(*) FROM email_tokens")[0][0] == 0
    mailer.fail = False
    assert send_verification(client).status_code == 204


def test_email_taken(env):
    client, mailer, _ = env
    verified_user(client, mailer, "shared@example.com")
    first = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()

    assert signup(client, "rider_02", email="shared@example.com").json()["error"]["code"] == "email_taken"
    signup(client, "rider_03")
    assert set_email(client, "shared@example.com").status_code == 409

    # rider_04는 rider_01이 인증하기 전에 같은 주소를 넣어 둠 → rider_01이 먼저 인증했으므로 rider_04의 인증은 409
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, first)
    set_email(client, "late@example.com")
    client.cookies.clear()
    signup(client, "rider_04", email="race@example.com")
    send_verification(client)
    race_token = mailer.token()
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, first)
    set_email(client, "race@example.com")
    send_verification(client)
    assert verify(client, mailer.token()).status_code == 204
    response = verify(client, race_token)
    assert response.status_code == 409 and response.json()["error"]["code"] == "email_taken"


# 비밀번호 재설정

def test_reset_responses_look_the_same(env):
    client, mailer, _ = env
    no_account = request_reset(client, "nobody_99")
    no_verified_email = request_reset(client, "rider_01")
    assert no_account.status_code == no_verified_email.status_code == 202
    assert no_account.content == no_verified_email.content == b""
    assert mailer.sent == []


def test_reset_flow(env):
    client, mailer, _ = env
    verified_user(client, mailer)
    old_session = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()

    assert request_reset(client, "RIDER_01").status_code == 202
    mail = mailer.sent[-1]
    assert mail["to"] == "me@example.com" and f"{BASE}/reset-password?token=" in mail["body"]
    token = mailer.token()

    weak = confirm_reset(client, token, new_password="12345678")
    assert weak.status_code == 422 and weak.json()["error"]["fields"] == ["new_password"]
    assert confirm_reset(client, token).status_code == 204   # 규칙 오류 뒤에도 같은 토큰을 쓸 수 있음
    assert confirm_reset(client, token).status_code == 400   # 한 번만

    client.cookies.set(COOKIE_NAME, old_session)
    assert client.get("/api/me").status_code == 401           # 모든 기기 로그아웃
    assert login(client, PASSWORD).status_code == 401
    assert login(client, "new-bike-2026").status_code == 200


def test_reset_by_verified_email_and_other_links_die(env):
    client, mailer, _ = env
    verified_user(client, mailer)
    request_reset(client, "Me@Example.com")
    first = mailer.token()
    request_reset(client, "rider_01")
    second = mailer.token()
    assert confirm_reset(client, second).status_code == 204
    assert confirm_reset(client, first).status_code == 400   # 하나를 쓰면 나머지 링크도 무효


def test_reset_token_dies_after_password_change(env):
    client, mailer, clock = env
    verified_user(client, mailer)
    request_reset(client, "rider_01")
    clock.advance(minutes=1)
    client.put("/api/me/password", json={"current_password": PASSWORD, "new_password": "other-bike-2026"},
               headers=ORIGIN)
    assert confirm_reset(client, mailer.token()).status_code == 400


def test_reset_limit_and_failure_still_202(env):
    client, mailer, _ = env
    verified_user(client, mailer)                 # 인증 메일 1통
    for _ in range(3):
        assert request_reset(client, "rider_01").status_code == 202
    assert len(mailer.sent) == 3                  # 15분에 3통까지(인증 메일 포함)
    mailer.fail = True
    assert request_reset(client, "nobody_99").status_code == 202


def test_reset_requires_trusted_origin(env):
    client, _, _ = env
    response = client.post("/api/auth/password-reset", json={"login_id_or_email": "rider_01"},
                           headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_account_deletion_removes_tokens(env, test_database_url):
    client, mailer, _ = env
    set_email(client, "me@example.com")
    send_verification(client)
    client.request("DELETE", "/api/me", json={"password": PASSWORD}, headers=ORIGIN)
    assert sql(test_database_url, "SELECT count(*) FROM email_tokens")[0][0] == 0


# SMTP 발송기

class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout, self.calls = host, port, timeout, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send", message["From"], message["To"], message["Subject"], message.get_content()))


def smtp_settings(**env):
    return Settings(_env_file=None, SMTP_HOST="smtp.example.com", SMTP_USERNAME="bot@example.com",
                    SMTP_PASSWORD="app-password", MAIL_FROM="CycleOn <bot@example.com>", **env)


def test_smtp_mailer_sends_with_starttls_and_login(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    asyncio.run(SmtpMailer(smtp_settings()).send("me@example.com", "제목", "본문"))
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587)
    assert smtp.calls[:2] == ["starttls", ("login", "bot@example.com", "app-password")]
    assert smtp.calls[2][:4] == ("send", "CycleOn <bot@example.com>", "me@example.com", "제목")
    assert smtp.calls[2][4].strip() == "본문"


def test_smtp_mailer_for_mailpit_skips_tls_and_login(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    settings = Settings(_env_file=None, SMTP_HOST="127.0.0.1", SMTP_PORT=1025, SMTP_STARTTLS=False)
    asyncio.run(SmtpMailer(settings).send("me@example.com", "제목", "본문"))
    assert [c[0] for c in FakeSMTP.instances[0].calls] == ["send"]


def test_smtp_mailer_failures(monkeypatch, caplog):
    with pytest.raises(MailUnavailable):
        asyncio.run(SmtpMailer(Settings(_env_file=None)).send("me@example.com", "s", "b"))   # SMTP_HOST 없음

    def refuse(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    with pytest.raises(MailUnavailable):
        asyncio.run(SmtpMailer(smtp_settings()).send("me@example.com", "s", "token=secret-token"))
    assert "me@example.com" not in caplog.text and "secret-token" not in caplog.text
    assert "app-password" not in caplog.text
