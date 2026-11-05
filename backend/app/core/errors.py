"""오류 응답을 {"error": {"code", "message", ...}} 한 가지 모양으로 통일한다."""
import logging
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **extra):
        super().__init__(code)
        self.status_code, self.code, self.message, self.extra = status_code, code, message, extra


class TargetNotSupported(AppError):
    def __init__(self, targets: list[datetime]):
        from app.core.time import iso_utc
        super().__init__(409, "target_not_supported", "선택한 이용 시각을 지금은 조회할 수 없습니다.",
                         supported_targets=[iso_utc(t) for t in targets])


class AIUnavailable(AppError):
    """AI 연결 실패·시간 초과. 상태·예측 API는 잡아서 ai_available=false로 바꾸고, 추천 API는 그대로 503."""

    def __init__(self):
        super().__init__(503, "ai_unavailable", "추천 기능을 잠시 사용할 수 없습니다.")


class AIAuthFailed(AppError):
    """AI가 401. 프론트가 로그아웃으로 오해하지 않도록 502로 내린다."""

    def __init__(self):
        super().__init__(502, "ai_auth_failed", "잠시 조회할 수 없습니다.")


class AIError(AppError):
    """AI가 401 외의 오류를 주거나, 응답 모양·기준 시각이 맞지 않음."""

    def __init__(self):
        super().__init__(502, "ai_error", "잠시 조회할 수 없습니다.")


def error_response(status_code: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message, **extra}})


def _field_names(error: RequestValidationError) -> list[str]:
    names = []
    for item in error.errors():
        loc = [part for part in item.get("loc", ()) if part not in ("body", "query", "path", "header")]
        name = next((str(part) for part in loc if isinstance(part, str)), None)
        if name and name not in names:
            names.append(name)
    return names


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return error_response(exc.status_code, exc.code, exc.message, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        return error_response(422, "invalid_request", "요청 값을 확인해 주세요.", fields=_field_names(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        # 요청 본문·쿼리(사용자 위치)는 로그에 남기지 않는다.
        logger.error("unhandled error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
        return error_response(500, "internal_error", "잠시 후 다시 시도해 주세요.")
