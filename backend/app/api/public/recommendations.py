from datetime import datetime

from fastapi import APIRouter, Depends

from app.api.deps import debug_flag, get_ai, get_app_settings, get_now
from app.clients.ai import AIClient
from app.core.config import Settings
from app.schemas.public import RecommendationRequest, RecommendationResponse
from app.services import ai_relay

router = APIRouter(tags=["recommendations"])


@router.post("/recommendations", response_model=RecommendationResponse, response_model_exclude_unset=True)
async def post_recommendations(request: RecommendationRequest, ai: AIClient = Depends(get_ai),
                               now: datetime = Depends(get_now), settings: Settings = Depends(get_app_settings),
                               debug: bool = Depends(debug_flag)):
    """반경 안 추천 1곳과 대안 최대 2곳. AI가 죽어 있으면 503 ai_unavailable."""
    return await ai_relay.recommend(ai, request, now, settings, debug)
