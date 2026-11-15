"""예측 확인 기록 (/api/me/views...). 마이 리포트의 재료. 로그인 필요, 변경 요청은 Origin 확인(CSRF 방지)을 거친다.

게스트의 확인은 기록하지 않는다.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.account.me import current_account
from app.api.deps import get_ai, get_app_settings, get_now, get_session, require_trusted_origin
from app.clients.ai import AIClient
from app.core.config import Settings
from app.schemas.views import ViewCreate, ViewRecordResponse
from app.services import views as service
from app.services.accounts import Account

router = APIRouter(prefix="/me/views", tags=["my report"])
guarded = [Depends(require_trusted_origin)]


@router.post("", response_model=ViewRecordResponse, status_code=status.HTTP_201_CREATED, dependencies=guarded,
             responses={200: {"description": "이미 기록돼 있음(처음 기록 그대로), 또는 기록하지 않음(recorded=false)"}})
async def record_view(request: ViewCreate, response: Response, session: AsyncSession = Depends(get_session),
                      ai: AIClient = Depends(get_ai), settings: Settings = Depends(get_app_settings),
                      now: datetime = Depends(get_now), account: Account = Depends(current_account)):
    """회원이 대여소 상세를 열거나 추천에서 대여소를 골랐을 때 부른다. 백엔드가 예측을 직접 받아 저장한다.

    - 새로 기록하면 201, 같은 대여소·이용 시각이 이미 있으면 200(처음 기록 그대로).
    - AI가 꺼졌거나 확률이 없으면 기록하지 않고 200 + recorded=false, reason.
    - 지원하지 않는 이용 시각은 409 target_not_supported, 운영 중이 아닌 대여소는 404 station_not_found.
    """
    body, created = await service.record(session, ai, settings, now, account.user.id, request)
    if not created:
        response.status_code = status.HTTP_200_OK
    return body
