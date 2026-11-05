"""AI 응답을 공개 API 응답으로 바꾸는 업무 로직.

라우터(api/public/*.py)는 입력 검증과 의존성 주입만 하고, 실제 처리는 여기서 한다.
AI 호출 자체(HTTP, 오류 변환)는 clients/ai.py가 맡는다.

지키는 규칙
- 기준 정각·선택지는 백엔드 시계로 계산한다(AI가 죽어 있어도 상태 API가 동작하도록).
- AI 값의 의미는 바꾸지 않는다. null을 0으로, reason 코드를 다른 말로, 추천 순서를 바꾸지 않는다.
- 프론트가 쓰지 않는 필드는 빼고, 진단 값은 debug일 때만 넣는다.
"""
import logging
from datetime import datetime

from app.clients.ai import AIClient
from app.core.config import Settings
from app.core.errors import AIError, AIUnavailable, TargetNotSupported
from app.core.time import base_at, horizon_for, supported_targets
from app.schemas.ai import AIRankedPrediction
from app.schemas.public import (RecommendationDebug, RecommendationRequest, RecommendationResponse,
                                RecommendedStation, StatusDebug, StatusResponse)

logger = logging.getLogger(__name__)


def check_same_base(ai_base: datetime, base: datetime, settings: Settings) -> None:
    """AI 응답의 기준 정각(ai_base)이 백엔드 기준 정각(base)과 같은지 확인한다. 다르면 예외를 던진다.

    왜 필요한가: 백엔드가 14:59:59에 "16:00 = h2"로 계산했는데 AI가 15:00:01에 요청을 받으면,
    AI는 15시 기준 h2 = 17:00 예측을 준다. 오류 없이 다른 시각의 확률이 나가는 셈이다.

    - 같음               → 통과
    - AI가 늦음(정각 변경) → 409 target_not_supported + AI 기준의 새 선택지
    - AI가 앞섬          → AI 시계가 뒤처진 설정 오류. 502 ai_error
    - CYCLEON_AI_CLOCK_CHECK=false → 확인하지 않음(시계가 고정된 목업 개발용)
    """
    if not settings.ai_clock_check or ai_base == base:
        return
    if ai_base > base:
        raise TargetNotSupported(supported_targets(ai_base))
    logger.error("AI base_at is behind the backend clock")
    raise AIError()


async def build_status(ai: AIClient, now: datetime, debug: bool) -> StatusResponse:
    """GET /api/status 응답을 만든다.

    server_at·base_at·supported_targets는 백엔드가 now로 직접 계산하고, AI에서는 준비 상태만 가져온다.
    - AI 접속 실패(AIUnavailable)는 여기서 잡아 ai_available=false, status=null로 200을 낸다.
    - AI 401·5xx(AIAuthFailed, AIError)는 잡지 않는다. 설정·장애 문제라 502로 드러나게 둔다.
    - debug면 모델 단계·번들·마지막 사전 계산 시각을 debug 객체로 붙인다(AI가 죽어 있으면 모두 null).
    """
    base = base_at(now)
    try:
        ai_status = await ai.status()
    except AIUnavailable:
        ai_status = None
    fields = {
        "ai_available": ai_status is not None,
        "status": ai_status.status if ai_status else None,
        "server_at": now,
        "base_at": base,
        "supported_targets": supported_targets(base),
    }
    if debug:  # debug 키 자체를 넣지 않아야 응답에서 빠진다(라우터의 exclude_unset)
        fields["debug"] = StatusDebug(
            model_stage=ai_status.model_stage if ai_status else None,
            model_bundle_id=ai_status.model_bundle_id if ai_status else None,
            last_precompute_at=ai_status.last_precompute_at if ai_status else None,
        )
    return StatusResponse(**fields)


def _station(item: AIRankedPrediction) -> RecommendedStation:
    """AI 추천 항목에서 공개 명세의 9개 필드만 남긴다.

    score(정렬 점수라 확률로 오해할 수 있음), model_id, input_group, horizon_hours 등은 버린다.
    남길 필드 목록은 RecommendedStation 정의를 그대로 따르므로, 필드를 늘릴 때는 스키마만 고치면 된다.
    """
    return RecommendedStation(**item.model_dump(include=set(RecommendedStation.model_fields)))


async def recommend(ai: AIClient, request: RecommendationRequest, now: datetime,
                    settings: Settings, debug: bool) -> RecommendationResponse:
    """POST /api/recommendations 응답을 만든다.

    1. target_at → 간격(h). 지원하지 않는 시각이면 AI를 부르지 않고 409.
    2. AI /v1/recommendations 호출. 접속 실패는 잡지 않아 503 ai_unavailable이 그대로 나간다.
    3. AI 기준 정각 확인(check_same_base).
    4. 필드를 줄여 응답. base_at은 백엔드 값, target_at은 요청값(UTC로 표기만 통일).
       radius_m·abstained·reason·추천 순서는 AI 값 그대로.
    5. debug면 nearby_count·eligible_count를 붙인다.
    """
    horizon = horizon_for(request.target_at, now)
    base = base_at(now)
    result = await ai.recommend(request.latitude, request.longitude, horizon, request.radius_m)
    check_same_base(result.base_at, base, settings)
    fields = {
        "base_at": base,
        "target_at": request.target_at,
        "radius_m": result.radius_m,
        "abstained": result.abstained,
        "reason": result.reason,
        "recommended": _station(result.recommended) if result.recommended else None,
        "alternatives": [_station(item) for item in result.alternatives],
    }
    if debug:
        fields["debug"] = RecommendationDebug(nearby_count=result.nearby_count,
                                              eligible_count=result.eligible_count)
    return RecommendationResponse(**fields)
