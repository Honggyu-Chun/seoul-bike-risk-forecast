"""공개 API 공통 의존성. 테스트에서는 app.dependency_overrides로 바꾼다."""
from datetime import datetime

from fastapi import Depends, Query, Request

from app.clients.ai import AIClient
from app.core.config import Settings
from app.core.time import utc_now


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_ai(request: Request) -> AIClient:
    return request.app.state.ai


def get_now() -> datetime:
    return utc_now()


def debug_flag(debug: bool = Query(False, description="진단 정보 포함 (개발·QA용)"),
               settings: Settings = Depends(get_app_settings)) -> bool:
    """?debug=true이고 설정에서 허용했을 때만 true"""
    return debug and settings.debug_enabled
