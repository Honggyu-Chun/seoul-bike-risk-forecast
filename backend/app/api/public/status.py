from datetime import datetime

from fastapi import APIRouter, Depends

from app.api.deps import debug_flag, get_ai, get_now
from app.clients.ai import AIClient
from app.schemas.public import StatusResponse
from app.services import ai_relay

router = APIRouter(tags=["status"])


@router.get("/status", response_model=StatusResponse, response_model_exclude_unset=True)
async def get_status(ai: AIClient = Depends(get_ai), now: datetime = Depends(get_now),
                     debug: bool = Depends(debug_flag)):
    """예측 기준 정각과 선택 가능한 이용 시각 3개. AI가 죽어 있어도 200."""
    return await ai_relay.build_status(ai, now, debug)
