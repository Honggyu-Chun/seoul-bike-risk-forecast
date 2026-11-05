"""AI 서비스(/v1/status, /v1/predictions, /v1/recommendations) 호출.

이 모듈은 'HTTP로 AI를 부르고, 결과를 파이썬 객체나 공개 API 오류로 바꾸는 일'만 한다.
기준 정각 비교나 응답 필드 줄이기 같은 업무 판단은 services/ai_relay.py가 한다.

HTTP 결과 → 이 모듈이 내는 것
- 200 + 올바른 JSON         → schemas/ai.py의 모델 객체
- 연결 실패·시간 초과       → AIUnavailable(503 ai_unavailable)
- 401(토큰 불일치)          → AIAuthFailed(502 ai_auth_failed)
- 그 밖의 상태(422, 5xx 등)  → AIError(502 ai_error)
- 200인데 JSON 모양이 다름   → AIError(502 ai_error)

로그에는 메서드·경로·상태 코드만 남긴다. 요청 본문(사용자 위치)과 토큰은 남기지 않는다.
"""
import logging
from collections.abc import Iterable, Sequence

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.core.errors import AIAuthFailed, AIError, AIUnavailable
from app.schemas.ai import AIPrediction, AIPredictions, AIRecommendation, AIStatus

logger = logging.getLogger(__name__)

PREDICTION_CHUNK = 500  # AI /v1/predictions가 한 번에 받는 최대 대여소 수


class AIClient:
    """AI 서비스 HTTP 클라이언트. 앱이 뜰 때 하나 만들어 app.state.ai에 두고 모든 요청이 같이 쓴다."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        """설정의 주소·토큰·시간 제한으로 httpx 클라이언트를 만든다.

        연결을 재사용하려고 요청마다 새로 만들지 않는다.
        transport는 테스트에서 가짜 AI(httpx.MockTransport)를 넣을 때만 쓴다.
        """
        self._client = httpx.AsyncClient(
            base_url=settings.ai_base_url,
            timeout=settings.ai_timeout_seconds,  # 연결·읽기 각각 이 시간을 넘으면 AIUnavailable
            headers={"Authorization": f"Bearer {settings.service_token.get_secret_value()}"},
            transport=transport,
        )

    async def aclose(self) -> None:
        """열린 연결을 닫는다. 앱 종료 시(main.py lifespan) 한 번 호출된다."""
        await self._client.aclose()

    async def _call[M: BaseModel](self, method: str, path: str, model: type[M], body: dict | None = None) -> M:
        """AI를 한 번 호출하고 응답 JSON을 model로 검증해 돌려준다. 세 공개 메서드가 공통으로 쓴다.

        실패는 모두 공개 API 오류(AppError)로 바꿔서 던진다. 이 오류를 잡지 않으면
        errors.py의 처리기가 그대로 {"error": {...}} 응답으로 만든다.
        """
        try:
            response = await self._client.request(method, path, json=body)
        except httpx.TransportError as exc:  # 연결 거부, DNS 실패, 시간 초과(TimeoutException)가 모두 여기에 속함
            logger.warning("AI %s %s unreachable: %s", method, path, type(exc).__name__)
            raise AIUnavailable() from None
        if response.status_code == 401:
            # 사용자 인증 문제가 아니라 백엔드↔AI 토큰 설정 문제. 운영자가 봐야 하므로 error로 남긴다.
            logger.error("AI %s %s rejected the service token", method, path)
            raise AIAuthFailed()
        if response.status_code != 200:
            logger.error("AI %s %s returned %s", method, path, response.status_code)
            raise AIError()
        try:
            # 모르는 필드는 무시하고(AIModel extra="ignore"), 필요한 필드가 없거나 타입이 틀리면 실패
            return model.model_validate_json(response.content)
        except ValidationError:
            logger.error("AI %s %s returned an unexpected body", method, path)
            raise AIError() from None

    async def status(self) -> AIStatus:
        """GET /v1/status. AI의 준비 상태와 진단 값(모델 번들, 마지막 사전 계산 시각)을 가져온다."""
        return await self._call("GET", "/v1/status", AIStatus)

    async def predict(self, station_ids: Iterable[str], horizons: Sequence[int]) -> list[AIPrediction]:
        """POST /v1/predictions. 대여소별·간격별 0대 확률과 최신 재고를 가져온다.

        - 중복 ID는 처음 나온 순서를 유지한 채 지운다.
        - AI 한도(500개)를 넘으면 500개씩 나눠 차례로 호출하고 결과를 이어 붙인다.
        - 결과 순서는 요청한 대여소 순서 × horizons 순서다.
        - 모르는 ID도 오류가 아니라 reason이 붙은 항목으로 온다.
        - 빈 목록이면 AI를 부르지 않고 빈 리스트를 돌려준다.
        각 항목의 target_at으로 어느 정각 예측인지 확인할 수 있다(4단계 /api/predictions에서 사용).
        """
        ids = list(dict.fromkeys(station_ids))
        items: list[AIPrediction] = []
        for start in range(0, len(ids), PREDICTION_CHUNK):
            body = {"station_ids": ids[start:start + PREDICTION_CHUNK], "horizons_hours": list(horizons)}
            items.extend((await self._call("POST", "/v1/predictions", AIPredictions, body)).items)
        return items

    async def recommend(self, latitude: float, longitude: float, horizon: int, radius_m: float) -> AIRecommendation:
        """POST /v1/recommendations. 반경 안 추천 1곳·대안 최대 2곳 또는 보류 이유를 가져온다.

        horizon은 1·2·3 중 하나여야 한다. 공개 API의 target_at을 바꾸는 일은 호출하는 쪽(ai_relay)이 한다.
        """
        body = {"latitude": latitude, "longitude": longitude, "horizon_hours": horizon, "radius_m": radius_m}
        return await self._call("POST", "/v1/recommendations", AIRecommendation, body)
