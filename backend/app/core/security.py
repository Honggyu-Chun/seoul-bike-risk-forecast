"""내부 데이터 API의 서비스 토큰 인증 (AI → 백엔드, Authorization: Bearer <CYCLEON_SERVICE_TOKEN>).

토큰이 설정되지 않았거나 32자보다 짧으면 모든 요청을 거부한다. 빈 토큰으로 내부 API가 열리는 일을 막기 위해서다.
"""
import logging
import secrets

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import AppError

logger = logging.getLogger(__name__)
MIN_TOKEN_LENGTH = 32

_bearer = HTTPBearer(auto_error=False)


class ServiceAuthRequired(AppError):
    def __init__(self):
        super().__init__(401, "service_authentication_required", "서비스 토큰이 필요합니다.")


def require_service_token(request: Request,
                          credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    """Bearer 토큰을 설정값과 비교한다. 비교는 시간 차로 토큰이 새지 않도록 compare_digest로 한다."""
    expected = request.app.state.settings.service_token.get_secret_value()
    if len(expected) < MIN_TOKEN_LENGTH:
        logger.error("internal API rejected: CYCLEON_SERVICE_TOKEN is missing or shorter than %d", MIN_TOKEN_LENGTH)
        raise ServiceAuthRequired()
    if credentials is None or not secrets.compare_digest(credentials.credentials.encode(), expected.encode()):
        raise ServiceAuthRequired()
