"""가입·로그인·세션 흐름.

세션: 무작위 토큰을 HttpOnly 쿠키로 주고, DB에는 SHA-256 해시만 둔다. 서버 세션이라 로그아웃이 바로 적용된다.
횟수 제한: login_attempts에 남긴 시도 수로 판단한다. IP는 request.client.host이고, 리버스 프록시 뒤에서는
uvicorn이 믿는 프록시(--forwarded-allow-ips)가 보낸 X-Forwarded-For로 바꿔 준 값이다.
"""
import hashlib
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.core.config import Settings
from app.core.errors import (EmailTaken, InvalidCredentials, InvalidPassword, InvalidRequest, LoginIdTaken,
                             NotAuthenticated, TooManyAttempts)
from app.core.passwords import hash_password, password_is_acceptable, spend_verify_time, verify_password
from app.db.models import AuthSession, User
from app.repositories import accounts as repo
from app.repositories import email_tokens as email_repo
from app.schemas.account import (LoginRequest, PasswordChange, ProfileUpdate, SignupRequest,
                                 normalize_login_id)

COOKIE_NAME = "cycleon_session"
COOKIE_PATH = "/api"
SESSION_IDLE = timedelta(days=14)       # 마지막 사용 후 이만큼 쓰지 않으면 끝
SESSION_LIFETIME = timedelta(days=30)   # 로그인 후 최대 유지 기간
TOUCH_INTERVAL = timedelta(hours=1)     # 마지막 사용 시각은 이 간격보다 자주 쓰지 않는다(요청마다 쓰기 방지)

LOGIN_WINDOW = timedelta(minutes=15)
LOGIN_FAILS_PER_ID_IP = 5    # 같은 아이디+IP 실패
LOGIN_FAILS_PER_IP = 30      # 같은 IP 실패(여러 아이디를 돌려 가며 시도하는 경우)
SIGNUP_WINDOW = timedelta(hours=1)
SIGNUPS_PER_IP = 10          # 해시 계산으로 서버를 묶는 대량 가입 방지
ATTEMPTS_KEPT = timedelta(days=1)


@dataclass
class Account:
    """로그인한 요청의 사용자와 지금 쓰는 세션"""
    user: User
    auth_session: AuthSession


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# 쿠키

def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(COOKIE_NAME, token, max_age=int(SESSION_LIFETIME.total_seconds()), path=COOKIE_PATH,
                        httponly=True, samesite="lax", secure=settings.cookie_secure)


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(COOKIE_NAME, path=COOKIE_PATH, httponly=True, samesite="lax",
                           secure=settings.cookie_secure)


def clear_cookie_header(settings: Settings) -> str:
    """오류 응답(401)에 붙일 쿠키 삭제 헤더 값"""
    response = Response()
    clear_session_cookie(response, settings)
    return response.headers["set-cookie"]


async def _new_session(session: AsyncSession, user_id: int, now: datetime) -> str:
    token = secrets.token_urlsafe(32)  # 256비트
    repo.add_session(session, user_id, token_hash(token), now, SESSION_LIFETIME)
    return token


# 횟수 제한

def _retry_after(times: list[datetime], limit: int, window: timedelta, now: datetime) -> int | None:
    """window 안의 시도가 limit 이상이면 다시 시도할 수 있을 때까지 남은 초, 아니면 None"""
    if len(times) < limit:
        return None
    frees_at = times[len(times) - limit] + window   # 이 시도가 window 밖으로 나가면 한 번 더 할 수 있다
    return max(1, math.ceil((frees_at - now).total_seconds()))


async def _check_login_limit(session: AsyncSession, login_id: str, ip: str, now: datetime) -> None:
    since = now - LOGIN_WINDOW
    per_pair = await repo.recent_attempt_times(session, kind="login", ip=ip, since=since, login_id=login_id,
                                               failed_only=True)
    per_ip = await repo.recent_attempt_times(session, kind="login", ip=ip, since=since, failed_only=True)
    waits = [w for w in (_retry_after(per_pair, LOGIN_FAILS_PER_ID_IP, LOGIN_WINDOW, now),
                         _retry_after(per_ip, LOGIN_FAILS_PER_IP, LOGIN_WINDOW, now)) if w is not None]
    if waits:
        raise TooManyAttempts(max(waits))


async def _check_signup_limit(session: AsyncSession, ip: str, now: datetime) -> None:
    times = await repo.recent_attempt_times(session, kind="signup", ip=ip, since=now - SIGNUP_WINDOW)
    wait = _retry_after(times, SIGNUPS_PER_IP, SIGNUP_WINDOW, now)
    if wait is not None:
        raise TooManyAttempts(wait)


# 가입·로그인·로그아웃

async def signup(session: AsyncSession, settings: Settings, request: SignupRequest, ip: str,
                 now: datetime) -> tuple[User, str]:
    """가입하고 바로 로그인 상태로 만든다. (사용자, 쿠키에 넣을 토큰)을 돌려준다.

    값만 틀린 요청(아이디 형식, 비밀번호 규칙, 중복 아이디)은 해시를 계산하지 않으므로 가입 횟수에 넣지 않는다.
    """
    await _check_signup_limit(session, ip, now)
    if not password_is_acceptable(request.password, request.login_id):
        raise InvalidRequest(["password"])
    if await repo.login_id_exists(session, request.login_id):
        raise LoginIdTaken()
    if request.email is not None and await email_repo.verified_email_owner(session, request.email) is not None:
        raise EmailTaken()

    repo.record_attempt(session, "signup", None, ip, now, succeeded=True)
    user = User(login_id=request.login_id, password_hash=await hash_password(request.password, settings.bcrypt_rounds),
                email=request.email, created_at=now, password_changed_at=now)
    session.add(user)
    try:
        await session.flush()  # 동시에 같은 아이디로 가입하면 여기서 유일 제약에 걸린다
    except IntegrityError:
        await session.rollback()
        raise LoginIdTaken() from None
    token = await _new_session(session, user.id, now)
    await session.commit()
    return user, token


async def login(session: AsyncSession, settings: Settings, request: LoginRequest, ip: str, now: datetime,
                old_token: str | None) -> tuple[User, str]:
    """아이디·비밀번호를 확인하고 새 세션을 만든다. 로그인 전에 갖고 있던 세션은 지운다(세션 고정 방지)."""
    login_id = normalize_login_id(request.login_id)
    await _check_login_limit(session, login_id, ip, now)

    user = await repo.user_by_login_id(session, login_id)
    if user is None:
        await spend_verify_time(request.password, settings.bcrypt_rounds)
        ok = False
    else:
        ok = await verify_password(request.password, user.password_hash)
    repo.record_attempt(session, "login", login_id, ip, now, succeeded=ok)
    if not ok:
        await session.commit()  # 실패 기록은 남겨야 횟수 제한이 동작한다
        raise InvalidCredentials()

    await repo.delete_stale(session, now, SESSION_IDLE, ATTEMPTS_KEPT)
    if old_token:
        await repo.delete_session(session, token_hash(old_token))
    token = await _new_session(session, user.id, now)
    await session.commit()
    return user, token


async def logout(session: AsyncSession, token: str | None) -> None:
    if token:
        await repo.delete_session(session, token_hash(token))
        await session.commit()


async def current_account(session: AsyncSession, settings: Settings, token: str | None, now: datetime) -> Account:
    """쿠키의 세션을 확인한다. 없거나 끝난 세션이면 401(무효한 쿠키는 지우게 한다)."""
    if not token:
        raise NotAuthenticated()
    found = await repo.session_with_user(session, token_hash(token))
    if found is None:
        raise NotAuthenticated(clear_cookie_header(settings))
    auth_session, user = found
    if auth_session.expires_at <= now or auth_session.last_seen_at <= now - SESSION_IDLE:
        await repo.delete_session(session, auth_session.token_hash)
        await session.commit()
        raise NotAuthenticated(clear_cookie_header(settings))
    if now - auth_session.last_seen_at >= TOUCH_INTERVAL:
        auth_session.last_seen_at = now
        await session.commit()
    return Account(user=user, auth_session=auth_session)


# 로그인한 회원

async def update_profile(session: AsyncSession, account: Account, request: ProfileUpdate) -> User:
    for field in request.model_fields_set & {"nickname", "bio"}:
        setattr(account.user, field, getattr(request, field))
    await session.commit()
    return account.user


async def change_password(session: AsyncSession, settings: Settings, account: Account, request: PasswordChange,
                          now: datetime) -> None:
    """현재 비밀번호를 확인하고 바꾼다. 지금 기기만 로그인 상태로 두고 다른 기기의 세션은 모두 끊는다."""
    user = account.user
    if not await verify_password(request.current_password, user.password_hash):
        raise InvalidPassword()
    if not password_is_acceptable(request.new_password, user.login_id):
        raise InvalidRequest(["new_password"])
    user.password_hash = await hash_password(request.new_password, settings.bcrypt_rounds)
    user.password_changed_at = now
    await repo.delete_user_sessions(session, user.id, keep_id=account.auth_session.id)
    await session.commit()


async def logout_all(session: AsyncSession, account: Account) -> None:
    await repo.delete_user_sessions(session, account.user.id)
    await session.commit()


async def delete_account(session: AsyncSession, account: Account, password: str) -> None:
    """비밀번호를 확인하고 회원을 지운다. 세션 등 회원에 달린 행은 ON DELETE CASCADE로 함께 지워진다."""
    if not await verify_password(password, account.user.password_hash):
        raise InvalidPassword()
    await session.execute(delete(User).where(User.id == account.user.id))
    await session.commit()
