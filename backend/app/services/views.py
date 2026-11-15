"""예측 확인 기록(마이 리포트의 재료).

기록 (POST /api/me/views)
- 프론트는 회원이 대여소 상세 화면을 열거나 추천 결과에서 대여소를 골랐을 때만 부른다(지도 마커 카드·목록은 제외).
- 순서: 이용 시각 확인(409) → 이미 있으면 그대로(AI를 부르지 않음) → 운영 중인 대여소인지(404) → AI 예측.
- 예측은 기존 POST /api/predictions 로직(services.stations.predict)으로 백엔드가 직접 받는다.
  그래서 정각 경계 409, AI가 꺼졌을 때의 처리가 /api/predictions와 같다.
- 확률이나 위험 판정이 null이면(AI 꺼짐, 대상 아님) 기록하지 않고 recorded=false와 이유만 돌려준다.
- 사용자 위치는 받지도 저장하지도 않는다.
"""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai import AIClient
from app.core.config import Settings
from app.core.errors import StationNotFound
from app.core.time import horizon_for
from app.db.models import PredictionView
from app.repositories import stations as station_repo
from app.repositories import views as repo
from app.schemas.public import PredictionRequest
from app.schemas.views import ViewCreate, ViewItem, ViewRecordResponse
from app.services import stations as station_service


def view_item(view: PredictionView) -> ViewItem:
    return ViewItem(id=view.id, station_id=view.station_id, station_name=view.station_name, target_at=view.target_at,
                    horizon_hours=round((view.target_at - view.base_at).total_seconds() / 3600),
                    risk_probability=view.risk_probability, is_risk=view.is_risk, outcome=view.outcome,
                    actual_inventory=view.actual_inventory, rented=view.rented)


async def record(session: AsyncSession, ai: AIClient, settings: Settings, now: datetime, user_id: int,
                 request: ViewCreate) -> tuple[ViewRecordResponse, bool]:
    """(응답, 새로 만들었는지)를 돌려준다."""
    horizon_for(request.target_at, now)  # 지원하지 않는 이용 시각이면 DB·AI 조회 전에 409
    existing = await repo.get_view(session, user_id, request.station_id, request.target_at)
    if existing is not None:
        return ViewRecordResponse(recorded=True, item=view_item(existing)), False

    station = (await station_repo.stations_by_ids(session, [request.station_id])).get(request.station_id)
    if station is None or not station_repo.is_current(station):
        raise StationNotFound()

    predictions = await station_service.predict(
        session, ai, settings, now, PredictionRequest(station_ids=[request.station_id], target_at=request.target_at),
        debug=False)
    prediction = predictions.items[0]
    if prediction.risk_probability is None or prediction.is_risk is None:
        return ViewRecordResponse(recorded=False, reason=prediction.reason), False

    view = await repo.insert_view(session, {
        "user_id": user_id, "station_id": request.station_id, "station_name": station.name,
        "base_at": predictions.base_at, "target_at": prediction.target_at, "viewed_at": now,
        "risk_probability": prediction.risk_probability, "is_risk": prediction.is_risk})
    created = view is not None
    if view is None:  # 같은 기록을 동시에 요청해 다른 요청이 먼저 넣었다
        view = await repo.get_view(session, user_id, request.station_id, request.target_at)
    response = ViewRecordResponse(recorded=True, item=view_item(view))
    await session.commit()
    return response, created
