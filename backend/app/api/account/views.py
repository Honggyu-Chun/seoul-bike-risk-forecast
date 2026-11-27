"""예측 확인 기록과 마이 리포트 (/api/me/views..., /api/me/report). 로그인 필요, 변경 요청은 Origin 확인(CSRF 방지)을 거친다.

게스트의 확인은 기록하지 않는다.
"""
from datetime import date, datetime

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.account.me import current_account
from app.api.deps import get_ai, get_app_settings, get_now, get_session, require_trusted_origin
from app.clients.ai import AIClient
from app.core.config import Settings
from app.schemas.views import ReportResponse, ViewCreate, ViewItem, ViewRecordResponse, ViewRentedUpdate
from app.services import views as service
from app.services.accounts import Account

router = APIRouter(prefix="/me", tags=["my report"])
guarded = [Depends(require_trusted_origin)]
ViewIdPath = Path(ge=1, le=2**63 - 1)   # DB bigint 범위 밖은 422


@router.post("/views", response_model=ViewRecordResponse, status_code=status.HTTP_201_CREATED, dependencies=guarded,
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


@router.get("/report", response_model=ReportResponse)
async def my_report(week: date | None = Query(None, description="그 주의 월요일(KST, 예: 2026-11-02). 빼면 이번 주. "
                                                                  "응답의 weeks 중 하나만 받고 그 밖은 422"),
                    session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_app_settings),
                    now: datetime = Depends(get_now), account: Account = Depends(current_account)):
    """마이 리포트: 한 주의 요약(확인한 예측·판정 끝남·맞음·대여 표시 수)과 카드(이용 시각이 늦은 순).

    열 때 이용 시각이 5분 이상 지난 기록을 먼저 판정한다(직전 5분 안 수집분의 실제 수량과 비교).
    """
    return await service.report(session, settings, now, account.user.id, week)


@router.patch("/views/{view_id}", response_model=ViewItem, dependencies=guarded)
async def mark_rented(request: ViewRentedUpdate, view_id: int = ViewIdPath, session: AsyncSession = Depends(get_session),
                      now: datetime = Depends(get_now), account: Account = Depends(current_account)):
    """"대여함" 표시(true·false)·지우기(null). 이용 시각 전에는 표시할 수 없어 409 target_not_passed.

    없거나 다른 회원의 기록이면 404 view_not_found.
    """
    return await service.set_rented(session, now, account.user.id, view_id, request)


@router.delete("/views/{view_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=guarded)
async def delete_view(view_id: int = ViewIdPath, session: AsyncSession = Depends(get_session),
                      account: Account = Depends(current_account)):
    """기록 하나 지우기. 없는 기록이어도 204."""
    await service.remove(session, account.user.id, view_id)


@router.delete("/views", status_code=status.HTTP_204_NO_CONTENT, dependencies=guarded)
async def delete_all_views(session: AsyncSession = Depends(get_session), account: Account = Depends(current_account)):
    """내 기록 전체 지우기."""
    await service.remove_all(session, account.user.id)
