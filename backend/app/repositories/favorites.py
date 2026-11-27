"""즐겨찾기 테이블 조회와 변경. 모든 조회는 user_id로 거른다(다른 사람의 즐겨찾기에 닿지 않게). 커밋은 서비스가 한다."""
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Favorite, User


async def lock_user(session: AsyncSession, user_id: int) -> None:
    """같은 사용자의 즐겨찾기 변경을 한 줄로 세운다(동시에 추가해 20개를 넘는 일을 막는다).

    트랜잭션이 끝날(커밋·롤백) 때까지 그 사용자 행을 잠근다.
    """
    await session.execute(select(User.id).where(User.id == user_id).with_for_update())


async def list_favorites(session: AsyncSession, user_id: int) -> list[Favorite]:
    return list(await session.scalars(
        select(Favorite).where(Favorite.user_id == user_id).order_by(Favorite.position, Favorite.created_at)))


async def get_favorite(session: AsyncSession, user_id: int, station_id: str) -> Favorite | None:
    return await session.get(Favorite, (user_id, station_id))


async def delete_favorite(session: AsyncSession, user_id: int, station_id: str) -> None:
    await session.execute(delete(Favorite).where(Favorite.user_id == user_id, Favorite.station_id == station_id))
