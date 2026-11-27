"""회원 즐겨찾기 (/api/me/favorites...). 로그인 필요. 변경 요청은 Origin 확인(CSRF 방지)을 거친다.

게스트 즐겨찾기는 서버에 저장하지 않는다(브라우저에 두고 POST /api/predictions로 예측을 받는다).
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.account.me import current_account
from app.api.deps import debug_flag, get_ai, get_app_settings, get_now, get_session, require_trusted_origin
from app.clients.ai import AIClient
from app.core.config import Settings
from app.schemas.favorites import (FavoriteAliasUpdate, FavoriteCreate, FavoriteImport, FavoriteImportResponse,
                                   FavoriteItem, FavoriteOrder, FavoritesResponse)
from app.services import favorites as service
from app.services.accounts import Account

router = APIRouter(prefix="/me/favorites", tags=["favorites"])
guarded = [Depends(require_trusted_origin)]
StationPath = Path(min_length=1, max_length=80)


@router.get("", response_model=FavoritesResponse, response_model_exclude_unset=True)
async def list_favorites(target_at: AwareDatetime | None = Query(
                             None, description="GET /api/status의 supported_targets 중 하나. 빼면 예측 없이 목록만"),
                         session: AsyncSession = Depends(get_session), ai: AIClient = Depends(get_ai),
                         settings: Settings = Depends(get_app_settings), now: datetime = Depends(get_now),
                         debug: bool = Depends(debug_flag), account: Account = Depends(current_account)):
    """즐겨찾기 목록(순서대로). target_at을 주면 그 이용 시각의 0대 확률과 현재 수량을 함께."""
    return await service.list_with_predictions(session, ai, settings, now, account.user.id, target_at, debug)


@router.post("", response_model=FavoriteItem, response_model_exclude_unset=True, status_code=status.HTTP_201_CREATED,
             dependencies=guarded, responses={200: {"description": "이미 즐겨찾기에 있음(바꾸지 않음)"}})
async def add_favorite(request: FavoriteCreate, response: Response, session: AsyncSession = Depends(get_session),
                       now: datetime = Depends(get_now), account: Account = Depends(current_account)):
    """추가(맨 뒤). 운영 중이 아닌 대여소는 404 station_not_found, 20개가 차 있으면 409 favorites_limit."""
    item, created = await service.add(session, account.user.id, request, now)
    if not created:
        response.status_code = status.HTTP_200_OK
    return item


@router.patch("/{station_id}", response_model=FavoriteItem, response_model_exclude_unset=True, dependencies=guarded)
async def rename_favorite(request: FavoriteAliasUpdate, station_id: str = StationPath,
                          session: AsyncSession = Depends(get_session), account: Account = Depends(current_account)):
    """별칭 변경. 빈 문자열·null이면 별칭을 지운다."""
    return await service.rename(session, account.user.id, station_id, request)


@router.put("/order", response_model=FavoritesResponse, response_model_exclude_unset=True, dependencies=guarded)
async def reorder_favorites(request: FavoriteOrder, session: AsyncSession = Depends(get_session),
                            account: Account = Depends(current_account)):
    """순서 변경. 지금 즐겨찾기 ID 전체를 원하는 순서대로 보낸다."""
    return await service.reorder(session, account.user.id, request)


@router.delete("/{station_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=guarded)
async def remove_favorite(station_id: str = StationPath, session: AsyncSession = Depends(get_session),
                          account: Account = Depends(current_account)):
    """삭제. 없는 대여소여도 204."""
    await service.remove(session, account.user.id, station_id)


@router.post("/import", response_model=FavoriteImportResponse, dependencies=guarded)
async def import_favorites(request: FavoriteImport, session: AsyncSession = Depends(get_session),
                           now: datetime = Depends(get_now), account: Account = Depends(current_account)):
    """게스트(브라우저) 즐겨찾기를 계정으로 옮긴다. 사용자가 동의했을 때만 프론트가 부른다."""
    return await service.import_from_guest(session, account.user.id, request, now)
