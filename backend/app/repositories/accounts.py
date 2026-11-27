"""회원·세션·로그인 시도 테이블 조회와 변경. 커밋은 하지 않는다(서비스가 한 요청의 끝에서 커밋)."""
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuthSession, LoginAttempt, User


async def user_by_login_id(session: AsyncSession, login_id: str) -> User | None:
    return await session.scalar(select(User).where(User.login_id == login_id))


async def login_id_exists(session: AsyncSession, login_id: str) -> bool:
    return await session.scalar(select(func.count()).select_from(User).where(User.login_id == login_id)) > 0


# 로그인·가입 시도

def record_attempt(session: AsyncSession, kind: str, login_id: str | None, ip: str, at: datetime,
                   succeeded: bool) -> None:
    session.add(LoginAttempt(kind=kind, login_id=login_id, ip=ip, attempted_at=at, succeeded=succeeded))


async def recent_attempt_times(session: AsyncSession, *, kind: str, ip: str, since: datetime,
                               login_id: str | None = None, failed_only: bool = False) -> list[datetime]:
    """since 이후의 시도 시각(오래된 순). 횟수와 '언제 다시 시도할 수 있는지' 계산에 쓴다."""
    query = select(LoginAttempt.attempted_at).where(
        LoginAttempt.kind == kind, LoginAttempt.ip == ip, LoginAttempt.attempted_at > since)
    if login_id is not None:
        query = query.where(LoginAttempt.login_id == login_id)
    if failed_only:
        query = query.where(LoginAttempt.succeeded.is_(False))
    return list(await session.scalars(query.order_by(LoginAttempt.attempted_at)))


# 세션

def add_session(session: AsyncSession, user_id: int, token_hash: str, now: datetime, lifetime: timedelta) -> None:
    session.add(AuthSession(user_id=user_id, token_hash=token_hash, created_at=now, last_seen_at=now,
                            expires_at=now + lifetime))


async def session_with_user(session: AsyncSession, token_hash: str) -> tuple[AuthSession, User] | None:
    row = (await session.execute(
        select(AuthSession, User).join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == token_hash))).first()
    return (row[0], row[1]) if row else None


async def delete_session(session: AsyncSession, token_hash: str) -> None:
    await session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash))


async def delete_user_sessions(session: AsyncSession, user_id: int, keep_id: int | None = None) -> None:
    """한 사용자의 세션을 모두 지운다. keep_id를 주면 그 세션(지금 쓰는 기기)은 남긴다."""
    query = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep_id is not None:
        query = query.where(AuthSession.id != keep_id)
    await session.execute(query)


async def delete_stale(session: AsyncSession, now: datetime, idle: timedelta, attempts_kept: timedelta) -> None:
    """만료된 세션과 하루 지난 시도 기록을 지운다. 로그인할 때마다 부른다(테이블이 계속 커지지 않게)."""
    await session.execute(delete(AuthSession).where(
        (AuthSession.expires_at <= now) | (AuthSession.last_seen_at <= now - idle)))
    await session.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at <= now - attempts_kept))
