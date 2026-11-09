"""로그인한 회원 본인 (/api/me...). 세션 쿠키가 없거나 끝났으면 401 not_authenticated.

조회(GET)를 뺀 변경 요청은 Origin 확인(CSRF 방지)을 거친다.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_app_settings, get_mailer, get_now, get_session, require_trusted_origin, session_token
from app.clients.mailer import Mailer
from app.core.config import Settings
from app.schemas.account import EmailUpdate, PasswordChange, PasswordConfirm, ProfileResponse, ProfileUpdate
from app.services import accounts as service
from app.services import email_recovery
from app.services.accounts import Account

router = APIRouter(prefix="/me", tags=["me"])
guarded = [Depends(require_trusted_origin)]


async def current_account(session: AsyncSession = Depends(get_session),
                          settings: Settings = Depends(get_app_settings), token: str | None = Depends(session_token),
                          now: datetime = Depends(get_now)) -> Account:
    return await service.current_account(session, settings, token, now)


@router.get("", response_model=ProfileResponse)
async def get_me(account: Account = Depends(current_account)):
    """로그인 여부와 프로필. 앱을 열 때 한 번 부른다(401이면 게스트)."""
    return ProfileResponse.of(account.user)


@router.patch("", response_model=ProfileResponse, dependencies=guarded)
async def update_me(request: ProfileUpdate, session: AsyncSession = Depends(get_session),
                    account: Account = Depends(current_account)):
    """닉네임·한 줄 소개 변경. 보낸 필드만 바꾸고, 빈 문자열·null은 지운다."""
    return ProfileResponse.of(await service.update_profile(session, account, request))


@router.put("/email", response_model=ProfileResponse, dependencies=guarded)
async def change_email(request: EmailUpdate, session: AsyncSession = Depends(get_session),
                       account: Account = Depends(current_account)):
    """복구용 이메일 등록·변경·삭제(null). 비밀번호 확인(틀리면 403 invalid_password). 바꾸면 인증 전 상태.

    다른 계정이 이미 인증한 주소면 409 email_taken.
    """
    return ProfileResponse.of(await email_recovery.change_email(session, account, request))


@router.post("/email/verification", status_code=status.HTTP_204_NO_CONTENT, dependencies=guarded)
async def send_email_verification(session: AsyncSession = Depends(get_session),
                                  settings: Settings = Depends(get_app_settings), mailer: Mailer = Depends(get_mailer),
                                  account: Account = Depends(current_account), now: datetime = Depends(get_now)):
    """인증 메일 보내기(링크 30분 유효). 이메일이 없으면 422, 이미 인증됐으면 보내지 않고 204.

    15분에 3통을 넘으면 429, 메일을 보내지 못하면 503 mail_unavailable.
    """
    await email_recovery.send_verification(session, settings, mailer, account, now)


@router.put("/password", status_code=status.HTTP_204_NO_CONTENT, dependencies=guarded)
async def change_password(request: PasswordChange, session: AsyncSession = Depends(get_session),
                          settings: Settings = Depends(get_app_settings), account: Account = Depends(current_account),
                          now: datetime = Depends(get_now)):
    """비밀번호 변경. 현재 비밀번호가 틀리면 403 invalid_password. 다른 기기의 로그인은 모두 끊는다."""
    await service.change_password(session, settings, account, request, now)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT, dependencies=guarded)
async def logout_all(response: Response, session: AsyncSession = Depends(get_session),
                     settings: Settings = Depends(get_app_settings), account: Account = Depends(current_account)):
    """이 기기를 포함한 모든 기기에서 로그아웃"""
    await service.logout_all(session, account)
    service.clear_session_cookie(response, settings)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, dependencies=guarded)
async def delete_me(request: PasswordConfirm, response: Response, session: AsyncSession = Depends(get_session),
                    settings: Settings = Depends(get_app_settings), account: Account = Depends(current_account)):
    """탈퇴. 비밀번호를 확인하고 계정과 세션을 한 번에 지운다."""
    await service.delete_account(session, account, request.password)
    service.clear_session_cookie(response, settings)
