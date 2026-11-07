from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import debug_flag, get_ai, get_app_settings, get_now, get_session
from app.clients.ai import AIClient
from app.core.config import Settings
from app.schemas.public import NearbyResponse, StationsResponse
from app.services import stations as service

router = APIRouter(tags=["stations"])

TARGET_AT_HELP = "GET /api/status의 supported_targets 중 하나(정각). 다른 값이면 409"


@router.get("/stations", response_model=StationsResponse)
async def get_stations(lat: float | None = Query(None, ge=33, le=39), lon: float | None = Query(None, ge=124, le=132),
                       radius_m: float | None = Query(None, ge=1, le=1500),
                       session: AsyncSession = Depends(get_session)):
    """지도에 표시할 현재 운영 대여소. 예측은 없고 AI 상태와 관계없이 동작한다."""
    return await service.list_stations(session, lat, lon, radius_m)


@router.get("/stations/nearby", response_model=NearbyResponse, response_model_exclude_unset=True)
async def get_nearby(lat: float = Query(ge=33, le=39), lon: float = Query(ge=124, le=132),
                     target_at: AwareDatetime = Query(description=TARGET_AT_HELP),
                     radius_m: float = Query(500, ge=1, le=1500),
                     session: AsyncSession = Depends(get_session), ai: AIClient = Depends(get_ai),
                     settings: Settings = Depends(get_app_settings), now: datetime = Depends(get_now),
                     debug: bool = Depends(debug_flag)):
    """반경 안 대여소를 가까운 순으로, 선택한 이용 시각의 0대 확률과 현재 수량을 함께."""
    return await service.nearby(session, ai, settings, now, lat=lat, lon=lon, target_at=target_at,
                                radius_m=radius_m, debug=debug)
