"""복구용 이메일: 등록·변경, 인증 메일, 비밀번호 재설정.

- 링크 토큰은 무작위 256비트이고 DB에는 SHA-256 해시만 둔다. 30분 유효, 한 번만 쓴다.
- 한 회원에게 보내는 메일은 15분에 3통까지(인증·재설정 합쳐서).
- 재설정 요청은 계정이 있든 없든, 메일을 보냈든 안 보냈든 같은 202를 준다. 메일은 응답 뒤에 보낸다
  (발송 시간 차이로 계정 존재 여부를 알아낼 수 없게).
- 토큰을 만든 뒤 회원이 이메일을 바꿨거나(인증·재설정), 비밀번호를 바꿨으면(재설정) 그 토큰은 무효다.
"""
import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.mailer import Mailer
from app.core.config import Settings
from app.core.errors import (EmailTaken, InvalidPassword, InvalidRequest, InvalidToken, MailUnavailable,
                             TooManyAttempts)
from app.core.passwords import hash_password, password_is_acceptable, verify_password
from app.db.models import User
from app.repositories import accounts as account_repo
from app.repositories import email_tokens as repo
from app.schemas.account import EmailUpdate, PasswordResetConfirm, PasswordResetRequest, normalize_login_id
from app.services.accounts import Account, token_hash

logger = logging.getLogger(__name__)

TOKEN_LIFETIME = timedelta(minutes=30)
MAIL_WINDOW = timedelta(minutes=15)
MAILS_PER_WINDOW = 3
TOKENS_KEPT = timedelta(days=1)   # 만료 후 이만큼 지나면 지운다


def _link(settings: Settings, path: str, token: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/{path}?token={token}"


def _verify_mail(user: User, link: str) -> tuple[str, str]:
    return ("[CycleOn] 이메일 인증",
            f"CycleOn 계정({user.login_id})의 복구용 이메일을 인증하려면 아래 링크를 30분 안에 열어 주세요.\n\n"
            f"{link}\n\n직접 요청하지 않았다면 이 메일을 무시하세요.")


def _reset_mail(user: User, link: str) -> tuple[str, str]:
    return ("[CycleOn] 비밀번호 재설정",
            f"CycleOn 계정({user.login_id})의 비밀번호를 재설정하려면 아래 링크를 30분 안에 열어 주세요. "
            f"링크는 한 번만 쓸 수 있습니다.\n\n{link}\n\n"
            "직접 요청하지 않았다면 이 메일을 무시하세요. 비밀번호는 바뀌지 않습니다.")


async def _mail_wait_seconds(session: AsyncSession, user_id: int, now: datetime) -> int | None:
    """15분에 3통을 넘었으면 다시 보낼 수 있을 때까지 남은 초, 아니면 None"""
    times = await repo.recent_times(session, user_id, now - MAIL_WINDOW)
    if len(times) < MAILS_PER_WINDOW:
        return None
    frees_at = times[len(times) - MAILS_PER_WINDOW] + MAIL_WINDOW
    return max(1, int((frees_at - now).total_seconds() + 0.999))


async def _issue(session: AsyncSession, user: User, purpose: str, now: datetime) -> str:
    """새 토큰을 만든다(커밋 전). 지난 토큰 정리도 여기서 한다."""
    await repo.delete_expired(session, now - TOKENS_KEPT)
    token = secrets.token_urlsafe(32)
    repo.add_token(session, user.id, purpose, user.email, token_hash(token), now, TOKEN_LIFETIME)
    return token


# 로그인한 회원

async def change_email(session: AsyncSession, account: Account, request: EmailUpdate) -> User:
    """복구용 이메일 등록·변경·삭제. 바꾸면 인증 전 상태가 된다. 같은 주소면 아무것도 바꾸지 않는다."""
    user = account.user
    if not await verify_password(request.password, user.password_hash):
        raise InvalidPassword()
    if request.email == user.email:
        return user
    if request.email is not None and await repo.verified_email_owner(session, request.email) not in (None, user.id):
        raise EmailTaken()
    user.email, user.email_verified_at = request.email, None
    await session.commit()
    return user


async def send_verification(session: AsyncSession, settings: Settings, mailer: Mailer, account: Account,
                            now: datetime) -> None:
    """인증 메일 보내기. 이메일이 없으면 422, 이미 인증됐으면 보내지 않고 끝낸다. 보내지 못하면 503."""
    user = account.user
    if user.email is None:
        raise InvalidRequest(["email"])
    if user.email_verified_at is not None:
        return
    wait = await _mail_wait_seconds(session, user.id, now)
    if wait is not None:
        raise TooManyAttempts(wait)
    token = await _issue(session, user, "verify", now)
    subject, body = _verify_mail(user, _link(settings, "verify-email", token))
    try:
        await mailer.send(user.email, subject, body)
    except MailUnavailable:
        await session.rollback()  # 보내지 못한 토큰은 남기지 않는다(횟수에도 넣지 않음)
        raise
    await session.commit()


# 메일 링크 (로그인 없이)

async def confirm_verification(session: AsyncSession, token: str, now: datetime) -> None:
    found = await repo.usable_token(session, token_hash(token), "verify", now)
    user = await session.get(User, found.user_id) if found else None
    if found is None or user is None or user.email != found.email:
        raise InvalidToken()
    if user.email_verified_at is None:
        if await repo.verified_email_owner(session, user.email) not in (None, user.id):
            raise EmailTaken()
        user.email_verified_at = now
    await repo.use_all(session, user.id, "verify", now)
    try:
        await session.commit()
    except IntegrityError:  # 같은 순간 다른 계정이 같은 이메일을 인증함
        await session.rollback()
        raise EmailTaken() from None


async def request_reset(session: AsyncSession, settings: Settings, request: PasswordResetRequest,
                        now: datetime) -> tuple[str, str, str] | None:
    """재설정 메일 준비. 보낼 대상이면 (받는 주소, 제목, 본문), 아니면 None. 어느 쪽이든 응답은 같다.

    대상: 아이디 또는 인증된 이메일로 찾은 계정 중 인증된 이메일이 있고, 15분에 3통을 넘지 않은 경우.
    """
    identifier = request.login_id_or_email.strip().lower()
    if "@" in identifier:
        user = await repo.user_by_verified_email(session, identifier)
    else:
        user = await account_repo.user_by_login_id(session, normalize_login_id(identifier))
    if user is None or user.email is None or user.email_verified_at is None:
        return None
    if await _mail_wait_seconds(session, user.id, now) is not None:
        return None
    token = await _issue(session, user, "reset", now)
    await session.commit()
    subject, body = _reset_mail(user, _link(settings, "reset-password", token))
    return user.email, subject, body


async def send_reset_mail(mailer: Mailer, to: str, subject: str, body: str) -> None:
    """응답을 보낸 뒤 실행된다. 실패는 메일러가 로그로 남기고, 사용자는 다시 요청하면 된다."""
    try:
        await mailer.send(to, subject, body)
    except MailUnavailable:
        pass


async def confirm_reset(session: AsyncSession, settings: Settings, request: PasswordResetConfirm,
                        now: datetime) -> None:
    """새 비밀번호로 바꾸고, 그 계정의 모든 기기를 로그아웃시킨다."""
    found = await repo.usable_token(session, token_hash(request.token), "reset", now)
    user = await session.get(User, found.user_id) if found else None
    if (found is None or user is None or user.email != found.email or user.email_verified_at is None
            or found.created_at < user.password_changed_at):
        raise InvalidToken()
    if not password_is_acceptable(request.new_password, user.login_id):
        raise InvalidRequest(["new_password"])  # 토큰은 쓰지 않은 채로 둔다(다시 입력할 수 있게)
    user.password_hash = await hash_password(request.new_password, settings.bcrypt_rounds)
    user.password_changed_at = now
    await repo.use_all(session, user.id, "reset", now)
    await account_repo.delete_user_sessions(session, user.id)
    await session.commit()
