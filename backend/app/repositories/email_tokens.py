"""메일 토큰(이메일 인증·비밀번호 재설정)과 이메일로 회원 찾기. 커밋은 서비스가 한다."""
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EmailToken, User


async def verified_email_owner(session: AsyncSession, email: str) -> int | None:
    """그 이메일을 인증한 회원의 id. 없으면 None"""
    return await session.scalar(select(User.id).where(User.email == email, User.email_verified_at.is_not(None)))


async def user_by_verified_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email, User.email_verified_at.is_not(None)))


async def recent_times(session: AsyncSession, user_id: int, since: datetime) -> list[datetime]:
    """since 이후 이 회원에게 만든 토큰(=보낸 메일)의 생성 시각, 오래된 순"""
    return list(await session.scalars(
        select(EmailToken.created_at).where(EmailToken.user_id == user_id, EmailToken.created_at > since)
        .order_by(EmailToken.created_at)))


def add_token(session: AsyncSession, user_id: int, purpose: str, email: str, token_hash: str, now: datetime,
              lifetime: timedelta) -> None:
    session.add(EmailToken(user_id=user_id, purpose=purpose, email=email, token_hash=token_hash, created_at=now,
                           expires_at=now + lifetime))


async def usable_token(session: AsyncSession, token_hash: str, purpose: str, now: datetime) -> EmailToken | None:
    """아직 쓰지 않았고 만료되지 않은 토큰"""
    return await session.scalar(select(EmailToken).where(
        EmailToken.token_hash == token_hash, EmailToken.purpose == purpose, EmailToken.used_at.is_(None),
        EmailToken.expires_at > now))


async def use_all(session: AsyncSession, user_id: int, purpose: str, now: datetime) -> None:
    """이 회원의 남은 토큰을 모두 쓴 것으로 표시한다(하나를 쓰면 같은 용도의 다른 링크도 막는다)."""
    await session.execute(update(EmailToken).where(
        EmailToken.user_id == user_id, EmailToken.purpose == purpose, EmailToken.used_at.is_(None))
        .values(used_at=now))


async def delete_expired(session: AsyncSession, before: datetime) -> None:
    await session.execute(delete(EmailToken).where(EmailToken.expires_at <= before))
