"""대여소·최신 재고 조회 (공개 API용, 읽기 전용)."""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Capture, Observation, Station


async def latest_stations(session: AsyncSession) -> dict[str, Station]:
    """대여소별 가장 최근 revision (cursor_seq가 가장 큰 행). 상태와 관계없이 모두."""
    rows = await session.scalars(
        select(Station).ext(distinct_on(Station.station_id)).order_by(Station.station_id, Station.cursor_seq.desc()))
    return {row.station_id: row for row in rows}


async def current_stations(session: AsyncSession) -> list[Station]:
    """지금 운영 중이고 좌표가 확인된 대여소. 지도·반경 검색에 쓴다.

    최신 revision이 verified이고, 끝난 구간(valid_to)이 아니고, 충돌 표시가 없는 것만 남긴다.
    """
    return [s for s in (await latest_stations(session)).values() if is_current(s)]


def is_current(station: Station) -> bool:
    """지금 운영 중이고 좌표가 확인된 대여소인지 (최신 revision 기준)"""
    return station.identity_status == "verified" and station.valid_to is None and not station.conflict


async def stations_by_ids(session: AsyncSession, station_ids: list[str]) -> dict[str, Station]:
    """요청한 ID의 최신 revision. 모르는 ID는 결과에 없다."""
    if not station_ids:
        return {}
    rows = await session.scalars(
        select(Station).where(Station.station_id.in_(station_ids))
        .ext(distinct_on(Station.station_id)).order_by(Station.station_id, Station.cursor_seq.desc()))
    return {row.station_id: row for row in rows}


@dataclass(frozen=True)
class Inventory:
    value: int | None          # 비정상 값이면 None
    received_at: datetime


async def latest_inventory(session: AsyncSession, station_ids: list[str]) -> dict[str, Inventory]:
    """가장 최근 완료 수집분에서 요청한 대여소들의 재고. AI가 꺼져 있을 때 수량 표시에 쓴다.

    수집분이 하나도 없거나, 그 수집분에 없는 대여소는 결과에 없다.
    """
    if not station_ids:
        return {}
    capture_id = await session.scalar(
        select(Capture.capture_id).where(Capture.source == "seoul_bike", Capture.state == "complete")
        .order_by(Capture.completed_at.desc()).limit(1))
    if capture_id is None:
        return {}
    rows = await session.execute(
        select(Observation.entity_id, Observation.value, Observation.received_at)
        .where(Observation.capture_id == capture_id, Observation.entity_id.in_(station_ids),
               Observation.variable == "inventory"))
    return {sid: Inventory(int(value) if value is not None else None, received_at)
            for sid, value, received_at in rows}
