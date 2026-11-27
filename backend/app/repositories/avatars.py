"""프로필 사진 저장·조회. 커밋은 하지 않는다(서비스가 한 요청의 끝에서 커밋).

사진은 지금 DB 테이블(user_avatars)에 둔다. 저장 위치를 객체 저장소(S3 등)로 옮기게 되면 이 모듈만 바꾼다.
"""
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserAvatar


async def updated_at(session: AsyncSession, user_id: int) -> datetime | None:
    """사진을 마지막으로 바꾼 시각. 사진이 없으면 None. 사진 본문은 읽지 않는다."""
    return await session.scalar(select(UserAvatar.updated_at).where(UserAvatar.user_id == user_id))


async def image(session: AsyncSession, user_id: int) -> bytes | None:
    return await session.scalar(select(UserAvatar.image).where(UserAvatar.user_id == user_id))


async def save(session: AsyncSession, user_id: int, data: bytes, now: datetime) -> None:
    """있으면 바꾸고 없으면 넣는다(upsert)."""
    statement = insert(UserAvatar).values(user_id=user_id, image=data, updated_at=now)
    await session.execute(statement.on_conflict_do_update(
        index_elements=[UserAvatar.user_id], set_={"image": statement.excluded.image, "updated_at": now}))


async def remove(session: AsyncSession, user_id: int) -> None:
    await session.execute(delete(UserAvatar).where(UserAvatar.user_id == user_id))
