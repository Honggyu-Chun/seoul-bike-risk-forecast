"""예측 확인 기록(마이 리포트의 재료).

기록 (POST /api/me/views)
- 프론트는 회원이 대여소 상세 화면을 열거나 추천 결과에서 대여소를 골랐을 때만 부른다(지도 마커 카드·목록은 제외).
- 순서: 이용 시각 확인(409) → 이미 있으면 그대로(AI를 부르지 않음) → 운영 중인 대여소인지(404) → AI 예측.
- 예측은 기존 POST /api/predictions 로직(services.stations.predict)으로 백엔드가 직접 받는다.
  그래서 정각 경계 409, AI가 꺼졌을 때의 처리가 /api/predictions와 같다.
- 확률이나 위험 판정이 null이면(AI 꺼짐, 대상 아님) 기록하지 않고 recorded=false와 이유만 돌려준다.
- 사용자 위치는 받지도 저장하지도 않는다.

판정
- 이용 시각 직전 5분 안에 완료된 수집분의 실제 수량과 비교한다(모델이 기준 재고를 잡는 방식과 같다).
  위험 예측 + 0대, 위험 아님 + 1대 이상이면 hit, 반대면 miss. 수집분이 없거나 값이 비정상이면 unknown.
- 이용 시각 5분 뒤부터 판정한다. 정각 직전에 끝난 수집분이 DB에 들어오기 전에 unknown으로 굳지 않게.
- 리포트를 열 때 그 회원의 대기 기록을, 수집기가 회차마다(오래된 재고를 지우기 전) 모든 회원의 대기 기록을 판정한다.
  재고는 8일만 두므로, 리포트를 오래 열지 않아도 결과가 남는다.

리포트 (GET /api/me/report)
- 월~일 한 주(KST, 이용 시각 기준). 이번 주 포함 CYCLEON_VIEW_RETENTION_WEEKS주(기본 2)만 고를 수 있고 그 밖은 422.

대여 표시와 지우기 (PATCH·DELETE /api/me/views...)
- "대여함"은 이용 시각이 지난 뒤에만 표시할 수 있다(409). 표시 지우기(null)는 언제든 된다.
- 하나 지우기·전체 지우기. 없는 기록을 지워도 204(즐겨찾기 삭제와 같다).

보관 기간 정리
- 고를 수 있는 가장 오래된 주보다 이용 시각이 이른 기록은 수집기가 회차마다 지운다.
  기준이 월요일 0시(KST)에만 바뀌므로 매일 지우는 것과 같고, 주가 바뀌면 바로 반영된다.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai import AIClient
from app.core.config import Settings
from app.core.errors import InvalidRequest, StationNotFound, TargetNotPassed, ViewNotFound
from app.core.time import KST, horizon_for
from app.db.models import PredictionView
from app.repositories import stations as station_repo
from app.repositories import views as repo
from app.schemas.public import PredictionRequest
from app.schemas.views import (ReportResponse, ReportSummary, ViewCreate, ViewItem, ViewRecordResponse,
                               ViewRentedUpdate)
from app.services import stations as station_service

CAPTURE_WINDOW = timedelta(minutes=5)   # 이용 시각 직전 이 안에 완료된 수집분으로 판정
JUDGE_DELAY = timedelta(minutes=5)      # 이용 시각 뒤 이만큼 지나야 판정


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


def _judge(view: PredictionView, value: int | None, now: datetime) -> None:
    view.actual_inventory = value
    if value is None:
        view.outcome = "unknown"
    else:
        view.outcome = "hit" if view.is_risk == (value == 0) else "miss"
    view.judged_at = now


async def judge_pending(session: AsyncSession, now: datetime, user_id: int | None = None,
                        limit: int | None = None) -> int:
    """판정할 때가 된 기록을 판정하고 커밋한다. 판정한 수를 돌려준다. user_id가 None이면 모든 회원."""
    views = await repo.pending_views(session, now - JUDGE_DELAY, user_id, limit)
    if not views:
        return 0
    by_target: dict[datetime, list[PredictionView]] = defaultdict(list)
    for view in views:
        by_target[view.target_at].append(view)
    for target_at, group in by_target.items():
        capture_id = await repo.capture_before(session, target_at, CAPTURE_WINDOW)
        values = (await repo.inventory_in_capture(session, capture_id, [v.station_id for v in group])
                  if capture_id else {})
        for view in group:
            _judge(view, values.get(view.station_id), now)
    await session.commit()
    return len(views)


def selectable_weeks(now: datetime, count: int) -> list[date]:
    """고를 수 있는 주의 월요일(KST), 이번 주부터 최신 순."""
    today = now.astimezone(KST).date()
    monday = today - timedelta(days=today.weekday())
    return [monday - timedelta(weeks=i) for i in range(count)]


def week_start(week: date) -> datetime:
    """그 주 월요일 0시(KST)."""
    return datetime.combine(week, time(0), KST)


async def report(session: AsyncSession, settings: Settings, now: datetime, user_id: int,
                 week: date | None) -> ReportResponse:
    """GET /api/me/report. 그 회원의 대기 기록을 먼저 판정하고 한 주의 요약과 카드를 돌려준다."""
    weeks = selectable_weeks(now, settings.view_retention_weeks)
    week = weeks[0] if week is None else week
    if week not in weeks:  # 월요일이 아니거나 보관 기간 밖·미래
        raise InvalidRequest(["week"])
    await judge_pending(session, now, user_id)
    start = week_start(week)
    views = await repo.week_views(session, user_id, start, start + timedelta(weeks=1))
    summary = ReportSummary(viewed=len(views), judged=sum(v.outcome in ("hit", "miss") for v in views),
                            hits=sum(v.outcome == "hit" for v in views), rented=sum(v.rented is True for v in views))
    return ReportResponse(week=week, weeks=weeks, summary=summary, items=[view_item(v) for v in views])


async def set_rented(session: AsyncSession, now: datetime, user_id: int, view_id: int,
                     request: ViewRentedUpdate) -> ViewItem:
    view = await repo.own_view(session, user_id, view_id)
    if view is None:
        raise ViewNotFound()
    if request.rented is not None and now < view.target_at:
        raise TargetNotPassed()
    view.rented = request.rented
    item = view_item(view)
    await session.commit()
    return item


async def remove(session: AsyncSession, user_id: int, view_id: int) -> None:
    await repo.delete_view(session, user_id, view_id)
    await session.commit()


async def remove_all(session: AsyncSession, user_id: int) -> None:
    await repo.delete_all(session, user_id)
    await session.commit()


async def prune_views(session: AsyncSession, now: datetime, weeks: int) -> int:
    """보관 기간(이번 주 포함 weeks주)보다 오래된 기록을 모든 회원에서 지우고 커밋한다. 지운 수를 돌려준다."""
    removed = await repo.delete_before(session, week_start(selectable_weeks(now, weeks)[-1]))
    await session.commit()
    return removed
