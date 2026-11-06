"""AI용 내부 데이터 API. AI 서비스가 20초마다 새 기록을 커서로 이어 받는다.

모든 경로에 서비스 토큰(Bearer)이 필요하다. 프론트는 이 경로를 부르지 않는다.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.security import require_service_token
from app.repositories import ai_data
from app.schemas.ai_data import ObservationRecord, Page, StationRecord

router = APIRouter(prefix="/internal/ai-data/v1", tags=["ai-data"], dependencies=[Depends(require_service_token)])

Cursor = Query(0, ge=0, description="마지막으로 받은 cursor. 처음이면 0")
Limit = Query(1000, ge=1, le=10000)
Watermark = Query(None, ge=0, description="첫 페이지 응답의 watermark를 그대로 보낸다")


@router.get("/stations", response_model=Page[StationRecord])
async def stations(cursor: int = Cursor, limit: int = Limit, watermark: int | None = Watermark,
                   session: AsyncSession = Depends(get_session)):
    """대여소 revision을 커밋 순서대로."""
    return await ai_data.station_page(session, cursor=cursor, limit=limit, watermark=watermark)


@router.get("/observations", response_model=Page[ObservationRecord])
async def observations(cursor: int = Cursor, limit: int = Limit, watermark: int | None = Watermark,
                       start: AwareDatetime | None = Query(None, description="이 시각 이후에 받은 기록만"),
                       end: AwareDatetime | None = None, source: str | None = None,
                       session: AsyncSession = Depends(get_session)):
    """재고 관측을 커밋 순서대로. 페이지의 재고가 속한 capture 정보를 captures에 함께 준다."""
    return await ai_data.observation_page(session, cursor=cursor, limit=limit, watermark=watermark,
                                          start=start, end=end, source=source)


@router.get("/forecasts")
async def forecasts(cursor: int = Cursor, limit: int = Limit, watermark: int | None = Watermark,
                    start: datetime | None = None, end: datetime | None = None, source: str | None = None):
    """기상 예보. 기상을 쓰는 모델 번들이 나올 때까지 수집하지 않으므로 항상 빈 페이지다.

    AI는 이 경로도 동기화하므로 404 대신 규칙에 맞는 빈 페이지를 돌려준다.
    """
    if watermark is not None and watermark < cursor:
        raise ai_data.InvalidCursor()
    end_cursor = watermark if watermark is not None else cursor
    return {"items": [], "next_cursor": end_cursor, "watermark": end_cursor, "has_more": False, "captures": []}
