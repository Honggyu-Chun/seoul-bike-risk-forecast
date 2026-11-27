from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import debug_flag, get_ai, get_app_settings, get_now, get_session
from app.clients.ai import AIClient
from app.core.config import Settings
from app.schemas.public import PredictionRequest, PredictionsResponse
from app.services import stations as service

router = APIRouter(tags=["predictions"])


@router.post("/predictions", response_model=PredictionsResponse, response_model_exclude_unset=True)
async def post_predictions(request: PredictionRequest, session: AsyncSession = Depends(get_session),
                           ai: AIClient = Depends(get_ai), settings: Settings = Depends(get_app_settings),
                           now: datetime = Depends(get_now), debug: bool = Depends(debug_flag)):
    """선택한 대여소·즐겨찾기 예측. 상세 화면에서 세 이용 시각을 한 번에 보려면 all_targets=true."""
    return await service.predict(session, ai, settings, now, request, debug)
