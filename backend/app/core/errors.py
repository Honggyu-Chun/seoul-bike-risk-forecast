"""오류 응답을 {"error": {"code", "message", ...}} 한 가지 모양으로 통일한다."""
import logging
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """headers: 응답에 붙일 헤더(Retry-After, 쿠키 삭제 등). 본문에는 나가지 않는다."""

    def __init__(self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None, **extra):
        super().__init__(code)
        self.status_code, self.code, self.message, self.extra = status_code, code, message, extra
        self.headers = headers or {}


class TargetNotSupported(AppError):
    def __init__(self, targets: list[datetime]):
        from app.core.time import iso_utc
        super().__init__(409, "target_not_supported", "선택한 이용 시각을 지금은 조회할 수 없습니다.",
                         supported_targets=[iso_utc(t) for t in targets])


class InvalidRequest(AppError):
    """여러 인자를 함께 봐야 하는 입력 오류. FastAPI 기본 검증과 같은 422 invalid_request 모양으로 낸다."""

    def __init__(self, fields: list[str]):
        super().__init__(422, "invalid_request", "요청 값을 확인해 주세요.", fields=fields)


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


class NotAuthenticated(AppError):
    """로그인하지 않았거나 세션이 끝남. 무효한 쿠키가 왔으면 지우는 Set-Cookie를 함께 보낸다."""

    def __init__(self, clear_cookie_header: str | None = None):
        super().__init__(401, "not_authenticated", "로그인이 필요합니다.",
                         headers={"set-cookie": clear_cookie_header} if clear_cookie_header else None)


class InvalidCredentials(AppError):
    """아이디가 없든 비밀번호가 틀리든 같은 응답. 가입된 아이디를 알아낼 수 없게 한다."""

    def __init__(self):
        super().__init__(401, "invalid_credentials", "아이디 또는 비밀번호가 맞지 않습니다.")


class InvalidPassword(AppError):
    """로그인한 상태에서 비밀번호 확인(변경·탈퇴)이 틀림. 401이면 프론트가 로그아웃으로 오해하므로 403."""

    def __init__(self):
        super().__init__(403, "invalid_password", "비밀번호가 맞지 않습니다.")


class LoginIdTaken(AppError):
    def __init__(self):
        super().__init__(409, "login_id_taken", "이미 사용 중인 아이디입니다.")


class TooManyAttempts(AppError):
    def __init__(self, retry_after_seconds: int):
        super().__init__(429, "too_many_attempts", "시도 횟수가 너무 많습니다. 잠시 후 다시 시도해 주세요.",
                         headers={"retry-after": str(retry_after_seconds)}, retry_after_seconds=retry_after_seconds)


class OriginNotAllowed(AppError):
    """로그인·회원 변경 요청이 허용하지 않은 사이트에서 옴(CSRF 방지)."""

    def __init__(self):
        super().__init__(403, "origin_not_allowed", "허용되지 않은 출처의 요청입니다.")


class UnsupportedMediaType(AppError):
    def __init__(self):
        super().__init__(415, "unsupported_media_type", "JSON 본문만 받습니다.")


class StationNotFound(AppError):
    """지금 운영 중이 아닌(모르는·폐소·좌표 미확인) 대여소를 즐겨찾기에 넣으려 함"""

    def __init__(self):
        super().__init__(404, "station_not_found", "운영 중인 대여소가 아닙니다.")


class FavoriteNotFound(AppError):
    def __init__(self):
        super().__init__(404, "favorite_not_found", "즐겨찾기에 없는 대여소입니다.")


class PlaceSearchUnavailable(AppError):
    """카카오 로컬 API 접속 실패·오류, 또는 REST API 키가 설정되지 않음"""

    def __init__(self):
        super().__init__(503, "place_search_unavailable", "장소 검색을 잠시 사용할 수 없습니다.")


class FavoritesLimit(AppError):
    def __init__(self, limit: int):
        super().__init__(409, "favorites_limit", f"즐겨찾기는 {limit}개까지 저장할 수 있습니다.", limit=limit)


def error_response(status_code: int, code: str, message: str, headers: dict[str, str] | None = None,
                   **extra) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message, **extra}},
                        headers=headers)


def _field_names(error: RequestValidationError) -> list[str]:
    names = []
    for item in error.errors():
        loc = list(item.get("loc", ()))
        if loc and loc[0] in ("body", "query", "path", "header", "cookie"):
            loc = loc[1:]  # 맨 앞의 위치 표시만 뺀다(이름이 query인 쿼리 인자도 있으므로)
        name = next((str(part) for part in loc if isinstance(part, str)), None)
        if name and name not in names:
            names.append(name)
    return names


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_: Request, exc: AppError):
        return error_response(exc.status_code, exc.code, exc.message, headers=exc.headers, **exc.extra)

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
