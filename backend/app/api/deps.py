"""공개 API 공통 의존성. 테스트에서는 app.dependency_overrides로 바꾼다."""
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai import AIClient
from app.clients.mailer import Mailer
from app.core.config import Settings
from app.core.errors import OriginNotAllowed, UnsupportedMediaType
from app.core.time import utc_now
from app.services.accounts import COOKIE_NAME


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_ai(request: Request) -> AIClient:
    return request.app.state.ai


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """요청 하나에 세션 하나. 요청이 끝나면 닫힌다(커밋하지 않은 변경은 버려짐).

    DATABASE_URL이 없으면 500. DB를 쓰지 않는 API(status, recommendations)는 이 의존성을 쓰지 않으므로 영향이 없다.
    """
    factory = request.app.state.db
    if factory is None:
        raise RuntimeError("DATABASE_URL is not set")
    async with factory() as session:
        yield session


def get_now() -> datetime:
    return utc_now()


def client_ip(request: Request) -> str:
    """요청한 사용자의 IP. 리버스 프록시 뒤에서는 uvicorn이 믿는 프록시의 X-Forwarded-For로 바꿔 준 값이다
    (uvicorn --forwarded-allow-ips). 다른 곳에서 온 X-Forwarded-For는 uvicorn이 무시한다."""
    return request.client.host if request.client else "unknown"


def session_token(request: Request) -> str | None:
    """세션 쿠키 값(토큰 원문). 로그에 남기지 않는다."""
    return request.cookies.get(COOKIE_NAME) or None


def require_trusted_origin(request: Request, settings: Settings = Depends(get_app_settings)) -> None:
    """로그인·회원 변경 요청의 CSRF 방지.

    - Origin 헤더가 있으면 허용 출처(CORS_ORIGINS) 중 하나여야 한다. 브라우저는 다른 사이트에서 보낸 요청에
      항상 Origin을 붙이므로, 다른 사이트가 몰래 보낸 요청은 여기서 막힌다. Origin이 없는 요청은 브라우저가 아닌
      도구(curl 등)라 허용한다.
    - 본문이 있으면 JSON이어야 한다. HTML 폼(form-urlencoded 등)으로는 보낼 수 없게.
    """
    origin = request.headers.get("origin")
    if origin is not None and origin not in settings.cors_origins:
        raise OriginNotAllowed()
    has_body = request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers
    if has_body and request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise UnsupportedMediaType()


def debug_flag(debug: bool = Query(False, description="진단 정보 포함 (개발·QA용)"),
               settings: Settings = Depends(get_app_settings)) -> bool:
    """?debug=true이고 설정에서 허용했을 때만 true"""
    return debug and settings.debug_enabled
