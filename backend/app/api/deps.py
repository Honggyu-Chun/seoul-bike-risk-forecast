"""공개 API 공통 의존성. 테스트에서는 app.dependency_overrides로 바꾼다."""
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai import AIClient
from app.core.config import Settings
from app.core.time import utc_now


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_ai(request: Request) -> AIClient:
    return request.app.state.ai


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


def debug_flag(debug: bool = Query(False, description="진단 정보 포함 (개발·QA용)"),
               settings: Settings = Depends(get_app_settings)) -> bool:
    """?debug=true이고 설정에서 허용했을 때만 true"""
    return debug and settings.debug_enabled
