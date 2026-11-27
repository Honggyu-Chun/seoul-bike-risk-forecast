"""예측 확인 기록 테이블 조회와 변경. 회원 화면용 조회는 모두 user_id로 거른다(다른 사람의 기록에 닿지 않게).

판정 대기 조회(pending_views)와 보관 기간 정리(delete_before)만 user_id 없이 쓴다(수집기가 모든 회원 기록을 다룰 때).
커밋은 서비스가 한다.
"""
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Capture, Observation, PredictionView


async def get_view(session: AsyncSession, user_id: int, station_id: str, target_at: datetime) -> PredictionView | None:
    return await session.scalar(select(PredictionView).where(
        PredictionView.user_id == user_id, PredictionView.station_id == station_id,
        PredictionView.target_at == target_at))


async def own_view(session: AsyncSession, user_id: int, view_id: int) -> PredictionView | None:
    """그 회원의 기록 하나. 없거나 다른 회원의 것이면 None."""
    return await session.scalar(select(PredictionView).where(
        PredictionView.id == view_id, PredictionView.user_id == user_id))


async def delete_view(session: AsyncSession, user_id: int, view_id: int) -> None:
    await session.execute(delete(PredictionView).where(
        PredictionView.id == view_id, PredictionView.user_id == user_id))


async def delete_all(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(delete(PredictionView).where(PredictionView.user_id == user_id))
    return result.rowcount


async def delete_before(session: AsyncSession, before: datetime) -> int:
    """이용 시각이 before 이전인 기록을 모든 회원에서 지운다. 지운 수를 돌려준다."""
    result = await session.execute(delete(PredictionView).where(PredictionView.target_at < before))
    return result.rowcount


async def insert_view(session: AsyncSession, values: dict) -> PredictionView | None:
    """새 기록을 넣는다. 같은 (회원, 대여소, 이용 시각)이 이미 있으면(동시 요청) 아무것도 하지 않고 None."""
    stmt = (insert(PredictionView).values(**values)
            .on_conflict_do_nothing(index_elements=["user_id", "station_id", "target_at"])
            .returning(PredictionView))
    return await session.scalar(stmt)


async def week_views(session: AsyncSession, user_id: int, start: datetime, end: datetime) -> list[PredictionView]:
    """이용 시각이 [start, end)인 기록. 최신(이용 시각이 늦은) 순."""
    return list(await session.scalars(
        select(PredictionView)
        .where(PredictionView.user_id == user_id, PredictionView.target_at >= start, PredictionView.target_at < end)
        .order_by(PredictionView.target_at.desc(), PredictionView.id.desc())))


async def pending_views(session: AsyncSession, due_before: datetime, user_id: int | None = None,
                        limit: int | None = None) -> list[PredictionView]:
    """아직 판정하지 않았고 이용 시각이 due_before 이전인 기록. user_id가 None이면 모든 회원."""
    query = select(PredictionView).where(PredictionView.outcome.is_(None), PredictionView.target_at <= due_before)
    if user_id is not None:
        query = query.where(PredictionView.user_id == user_id)
    query = query.order_by(PredictionView.target_at, PredictionView.id)
    if limit is not None:
        query = query.limit(limit)
    return list(await session.scalars(query))


async def capture_before(session: AsyncSession, target_at: datetime, window: timedelta) -> str | None:
    """target_at 직전 window 안에 완료된 가장 늦은 수집분(모델이 기준 재고를 잡는 방식과 같다). 없으면 None."""
    return await session.scalar(
        select(Capture.capture_id)
        .where(Capture.source == "seoul_bike", Capture.state == "complete",
               Capture.completed_at <= target_at, Capture.completed_at >= target_at - window)
        .order_by(Capture.completed_at.desc()).limit(1))


async def inventory_in_capture(session: AsyncSession, capture_id: str, station_ids: list[str]) -> dict[str, int | None]:
    """그 수집분의 대여소별 재고. 값이 비정상·누락이면 None, 수집분에 없는 대여소는 결과에 없다."""
    rows = await session.execute(
        select(Observation.entity_id, Observation.value)
        .where(Observation.capture_id == capture_id, Observation.variable == "inventory",
               Observation.entity_id.in_(station_ids)))
    return {sid: int(value) if value is not None else None for sid, value in rows}
