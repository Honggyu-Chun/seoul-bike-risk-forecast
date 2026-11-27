"""가입·로그인·로그아웃 (/api/auth/...). 로그인하지 않아도 부를 수 있다.

변경 요청이라 모두 Origin 확인(CSRF 방지)을 거친다. 로그인 CSRF(남의 계정으로 몰래 로그인시키기)도 막기 위해서다.
"""
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (client_ip, get_app_settings, get_mailer, get_now, get_session, require_trusted_origin,
                          session_token)
from app.clients.mailer import Mailer
from app.core.config import Settings
from app.schemas.account import (LoginRequest, PasswordResetConfirm, PasswordResetRequest, ProfileResponse,
                                 SignupRequest, TokenRequest)
from app.services import accounts as service
from app.services import email_recovery

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


@router.post("/email/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(request: TokenRequest, session: AsyncSession = Depends(get_session),
                       now: datetime = Depends(get_now)):
    """인증 메일 링크의 토큰으로 이메일 인증을 끝낸다. 로그인하지 않아도 된다(다른 기기에서 메일을 열 수 있으므로).

    만료·사용됨·그 뒤 이메일이 바뀜이면 400 invalid_token, 다른 계정이 먼저 인증한 주소면 409 email_taken.
    """
    await email_recovery.confirm_verification(session, request.token, now)


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED, response_class=Response)
async def request_password_reset(request: PasswordResetRequest, background: BackgroundTasks,
                                 session: AsyncSession = Depends(get_session),
                                 settings: Settings = Depends(get_app_settings), mailer: Mailer = Depends(get_mailer),
                                 now: datetime = Depends(get_now)):
    """비밀번호 재설정 메일 요청(아이디 또는 인증된 이메일). 계정이 있든 없든 항상 202.

    인증된 이메일이 있는 계정에만 메일을 보내고, 메일은 응답을 보낸 뒤에 보낸다.
    """
    mail = await email_recovery.request_reset(session, settings, request, now)
    if mail is not None:
        background.add_task(email_recovery.send_reset_mail, mailer, *mail)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_password_reset(request: PasswordResetConfirm, session: AsyncSession = Depends(get_session),
                                 settings: Settings = Depends(get_app_settings), now: datetime = Depends(get_now)):
    """메일 링크의 토큰과 새 비밀번호로 바꾼다. 그 계정의 모든 기기가 로그아웃된다.

    토큰이 무효면 400 invalid_token, 새 비밀번호가 규칙에 맞지 않으면 422 fields: ["new_password"].
    """
    await email_recovery.confirm_reset(session, settings, request, now)
