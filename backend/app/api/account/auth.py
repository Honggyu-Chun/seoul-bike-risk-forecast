"""가입·로그인·로그아웃 (/api/auth/...). 로그인하지 않아도 부를 수 있다.

변경 요청이라 모두 Origin 확인(CSRF 방지)을 거친다. 로그인 CSRF(남의 계정으로 몰래 로그인시키기)도 막기 위해서다.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_app_settings, get_now, get_session, require_trusted_origin, session_token
from app.core.config import Settings
from app.schemas.account import LoginRequest, ProfileResponse, SignupRequest
from app.services import accounts as service

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_trusted_origin)])


@router.post("/signup", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
async def signup(request: SignupRequest, response: Response, session: AsyncSession = Depends(get_session),
                 settings: Settings = Depends(get_app_settings), ip: str = Depends(client_ip),
                 now: datetime = Depends(get_now)):
    """가입. 성공하면 바로 로그인 상태(세션 쿠키 발급)."""
    user, token = await service.signup(session, settings, request, ip, now)
    service.set_session_cookie(response, token, settings)
    return ProfileResponse.of(user)


@router.post("/login", response_model=ProfileResponse)
async def login(request: LoginRequest, response: Response, session: AsyncSession = Depends(get_session),
                settings: Settings = Depends(get_app_settings), ip: str = Depends(client_ip),
                now: datetime = Depends(get_now), old_token: str | None = Depends(session_token)):
    """로그인. 항상 새 세션 쿠키를 발급한다."""
    user, token = await service.login(session, settings, request, ip, now, old_token)
    service.set_session_cookie(response, token, settings)
    return ProfileResponse.of(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response, session: AsyncSession = Depends(get_session),
                 settings: Settings = Depends(get_app_settings), token: str | None = Depends(session_token)):
    """이 기기의 세션을 지운다. 로그인하지 않았어도 204."""
    await service.logout(session, token)
    service.clear_session_cookie(response, settings)
