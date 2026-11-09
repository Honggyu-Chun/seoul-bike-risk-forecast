"""즐겨찾기 흐름.

- 목록 + 예측: 기존 POST /api/predictions 로직(services.stations.predict)을 그대로 쓴다. 그래서 409 규칙,
  AI가 꺼졌을 때의 대체 수량(ai_available=false), 모르는 대여소 처리가 /api/predictions와 같다.
- 추가: 지금 운영 중인 대여소만(아니면 404 station_not_found). 이미 있으면 그대로 두고 200.
- 20개 한도는 사용자 행을 잠근 상태에서 센다(동시 추가로 넘지 않게).
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai import AIClient
from app.core.config import Settings
from app.core.errors import FavoriteNotFound, FavoritesLimit, InvalidRequest, StationNotFound
from app.core.time import horizon_for
from app.db.models import Favorite, Station
from app.repositories import favorites as repo
from app.repositories import stations as station_repo
from app.schemas.favorites import (FAVORITES_LIMIT, FavoriteAliasUpdate, FavoriteCreate, FavoriteImport,
                                   FavoriteImportResponse, FavoriteItem, FavoriteOrder, FavoritesResponse)
from app.schemas.public import PredictionRequest
from app.services import stations as station_service


def _item(favorite: Favorite, station: Station | None) -> FavoriteItem:
    return FavoriteItem(station_id=favorite.station_id, alias=favorite.alias, position=favorite.position,
                        name=station.name if station else None,
                        latitude=station.latitude if station else None,
                        longitude=station.longitude if station else None)


async def list_with_predictions(session: AsyncSession, ai: AIClient, settings: Settings, now: datetime,
                                user_id: int, target_at: datetime | None, debug: bool) -> FavoritesResponse:
    """GET /api/me/favorites. target_at을 주면 그 이용 시각의 예측·현재 수량을 붙인다."""
    favorites = await repo.list_favorites(session, user_id)
    ids = [f.station_id for f in favorites]
    if target_at is None or not ids:
        if target_at is not None:  # 빈 목록이어도 지원하지 않는 시각이면 똑같이 409
            horizon_for(target_at, now)
        known = await station_repo.stations_by_ids(session, ids)
        return FavoritesResponse(limit=FAVORITES_LIMIT, count=len(favorites),
                                 items=[_item(f, known.get(f.station_id)) for f in favorites])

    predictions = await station_service.predict(session, ai, settings, now,
                                                PredictionRequest(station_ids=ids, target_at=target_at), debug)
    by_id = {p.station_id: p for p in predictions.items}
    items = []
    for favorite in favorites:
        prediction = by_id[favorite.station_id]
        fields = prediction.model_dump(exclude={"station_id"}, exclude_unset=True)
        items.append(FavoriteItem(station_id=favorite.station_id, alias=favorite.alias, position=favorite.position,
                                  **fields))
    return FavoritesResponse(base_at=predictions.base_at, ai_available=predictions.ai_available,
                             limit=FAVORITES_LIMIT, count=len(items), items=items)


async def _next_position(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """(지금 개수, 다음 position)"""
    favorites = await repo.list_favorites(session, user_id)
    return len(favorites), max((f.position for f in favorites), default=-1) + 1


async def add(session: AsyncSession, user_id: int, request: FavoriteCreate,
              now: datetime) -> tuple[FavoriteItem, bool]:
    """추가. (항목, 새로 만들었는지)를 돌려준다. 이미 있으면 별칭도 바꾸지 않고 그대로 돌려준다."""
    await repo.lock_user(session, user_id)
    existing = await repo.get_favorite(session, user_id, request.station_id)
    station = (await station_repo.stations_by_ids(session, [request.station_id])).get(request.station_id)
    if existing is not None:
        item = _item(existing, station)
        await session.rollback()  # 잠금만 풀고 아무것도 바꾸지 않는다(롤백 뒤에는 객체를 읽지 않는다)
        return item, False
    if station is None or not station_repo.is_current(station):
        raise StationNotFound()
    count, position = await _next_position(session, user_id)
    if count >= FAVORITES_LIMIT:
        raise FavoritesLimit(FAVORITES_LIMIT)
    favorite = Favorite(user_id=user_id, station_id=request.station_id, alias=request.alias, position=position,
                        created_at=now)
    session.add(favorite)
    await session.commit()
    return _item(favorite, station), True


async def rename(session: AsyncSession, user_id: int, station_id: str, request: FavoriteAliasUpdate) -> FavoriteItem:
    favorite = await repo.get_favorite(session, user_id, station_id)
    if favorite is None:
        raise FavoriteNotFound()
    favorite.alias = request.alias
    await session.commit()
    station = (await station_repo.stations_by_ids(session, [station_id])).get(station_id)
    return _item(favorite, station)


async def reorder(session: AsyncSession, user_id: int, request: FavoriteOrder) -> FavoritesResponse:
    """보낸 순서대로 position을 0부터 다시 매긴다. 지금 목록과 ID 집합이 정확히 같아야 한다."""
    await repo.lock_user(session, user_id)
    favorites = {f.station_id: f for f in await repo.list_favorites(session, user_id)}
    if len(request.station_ids) != len(set(request.station_ids)) or set(request.station_ids) != set(favorites):
        raise InvalidRequest(["station_ids"])
    for position, station_id in enumerate(request.station_ids):
        favorites[station_id].position = position
    await session.commit()
    ordered = [favorites[sid] for sid in request.station_ids]
    known = await station_repo.stations_by_ids(session, request.station_ids)
    return FavoritesResponse(limit=FAVORITES_LIMIT, count=len(ordered),
                             items=[_item(f, known.get(f.station_id)) for f in ordered])


async def remove(session: AsyncSession, user_id: int, station_id: str) -> None:
    """삭제. 없는 대여소여도 204(이미 지워진 상태와 같으므로)."""
    await repo.delete_favorite(session, user_id, station_id)
    await session.commit()


async def import_from_guest(session: AsyncSession, user_id: int, request: FavoriteImport,
                            now: datetime) -> FavoriteImportResponse:
    """게스트 즐겨찾기 옮기기. 앞에서부터 넣고, 이미 있음·운영 중 아님·20개 초과는 건너뛰고 알려 준다."""
    await repo.lock_user(session, user_id)
    ids = list(dict.fromkeys(request.station_ids))
    existing = {f.station_id for f in await repo.list_favorites(session, user_id)}
    known = await station_repo.stations_by_ids(session, ids)
    count, position = await _next_position(session, user_id)
    result = FavoriteImportResponse(added=[], skipped_existing=[], skipped_unknown=[], skipped_over_limit=[],
                                    count=count)
    for station_id in ids:
        station = known.get(station_id)
        if station_id in existing:
            result.skipped_existing.append(station_id)
        elif station is None or not station_repo.is_current(station):
            result.skipped_unknown.append(station_id)
        elif result.count >= FAVORITES_LIMIT:
            result.skipped_over_limit.append(station_id)
        else:
            session.add(Favorite(user_id=user_id, station_id=station_id, alias=None, position=position,
                                 created_at=now))
            result.added.append(station_id)
            result.count += 1
            position += 1
    await session.commit()
    return result
