"""프로필 사진 (/api/me/avatar). 본인만 볼 수 있다(공개 프로필 화면이 없다).

- 업로드한 사진은 256×256 WebP로 새로 만들어 저장하고 원본은 버린다(app/core/images.py).
- 프로필 응답의 avatar_url은 "/api/me/avatar?v=<바꾼 시각(ms)>". 사진을 바꾸면 v가 바뀌어 브라우저가 새로 받는다.
- 탈퇴하면 외래 키(ON DELETE CASCADE)로 사진도 함께 지워진다.
"""
from datetime import datetime

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core import images
from app.core.errors import AvatarNotFound, ImageTooLarge
from app.db.models import User
from app.repositories import avatars as repo
from app.schemas.account import ProfileResponse
from app.services.accounts import Account

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def avatar_url(updated_at: datetime | None) -> str | None:
    return f"/api/me/avatar?v={int(updated_at.timestamp() * 1000)}" if updated_at else None


async def profile(session: AsyncSession, user: User) -> ProfileResponse:
    """프로필 응답. 사진이 있으면 avatar_url을 채운다(사진 본문은 읽지 않는다)."""
    return ProfileResponse.of(user, avatar_url(await repo.updated_at(session, user.id)))


async def read_upload(request: Request) -> bytes:
    """요청 본문을 5MB까지 읽는다. Content-Length가 5MB를 넘으면 읽지 않고 413, 헤더가 없거나 틀려도 읽으면서 멈춘다."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
        raise ImageTooLarge()
    body, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise ImageTooLarge()
        body.append(chunk)
    return b"".join(body)


async def upload(session: AsyncSession, account: Account, data: bytes, now: datetime) -> ProfileResponse:
    """사진을 변환해 넣거나 바꾼다. 변환(디코딩·축소·인코딩)은 CPU 작업이라 스레드풀에서 돌린다."""
    avatar = await run_in_threadpool(images.make_avatar, data)
    await repo.save(session, account.user.id, avatar, now)
    await session.commit()
    return ProfileResponse.of(account.user, avatar_url(now))


async def image(session: AsyncSession, account: Account) -> bytes:
    data = await repo.image(session, account.user.id)
    if data is None:
        raise AvatarNotFound()
    return data


async def delete(session: AsyncSession, account: Account) -> None:
    await repo.remove(session, account.user.id)
    await session.commit()
