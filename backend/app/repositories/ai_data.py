"""내부 데이터 API 페이지 조회. AI가 '마지막으로 받은 cursor 다음'부터 이어 받는다.

페이지 규칙 (모델개발 BACKEND_HANDOFF와 같음)
- 조회 조건: cursor < cursor_seq ≤ watermark (+ start ≤ received_at ≤ end, source), cursor_seq 오름차순, limit개
- watermark를 안 주면 현재 max(cursor_seq)를 넣어 돌려준다. AI는 다음 페이지에 그 값을 그대로 보낸다.
  덕분에 동기화 도중 새로 커밋된 기록은 이번 회차가 아니라 다음 회차(20초 뒤)에 받는다.
- has_more=true면 next_cursor = 이번 페이지 마지막 cursor, false면 next_cursor = watermark (빈 페이지여도)
- watermark < cursor면 422

watermark를 max(cursor_seq)로 잡아도 되는 이유: 저장 쪽(repositories/inventory.py)이 잠금 안에서 번호를 붙이고
커밋해서, 번호 순서 = 커밋 순서다. 그래서 max 이하의 번호는 모두 이미 커밋되어 보인다(중간에 빈 번호가 나중에 생기지 않음).
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Capture, Observation, Station
from app.schemas.ai_data import CaptureMeta, ObservationRecord, Page, PageItem, StationRecord


class InvalidCursor(AppError):
    def __init__(self):
        super().__init__(422, "invalid_time_or_cursor", "watermark가 cursor보다 작습니다.")


async def _resolve_watermark(session: AsyncSession, model, cursor: int, watermark: int | None) -> int:
    """watermark가 없으면 현재 최대 번호로 정한다. cursor보다 작으면 422."""
    if watermark is None:
        watermark = await session.scalar(select(func.coalesce(func.max(model.cursor_seq), 0)))
    if watermark < cursor:
        raise InvalidCursor()
    return watermark


def _paging(rows: list, limit: int, watermark: int) -> tuple[list, int, bool]:
    """limit+1개를 읽어 다음 페이지가 있는지 판단한다. (이번 페이지 행, next_cursor, has_more)"""
    has_more = len(rows) > limit
    rows = rows[:limit]
    return rows, rows[-1].cursor_seq if has_more else watermark, has_more


async def station_page(session: AsyncSession, *, cursor: int, limit: int, watermark: int | None) -> Page:
    """대여소 revision 페이지. AI는 대여소에는 start를 보내지 않는다(이력 전체를 받음)."""
    watermark = await _resolve_watermark(session, Station, cursor, watermark)
    rows = list(await session.scalars(
        select(Station).where(Station.cursor_seq > cursor, Station.cursor_seq <= watermark)
        .order_by(Station.cursor_seq).limit(limit + 1)))
    rows, next_cursor, has_more = _paging(rows, limit, watermark)
    items = [PageItem[StationRecord](cursor=r.cursor_seq, conflict=r.conflict,
                                     record=StationRecord.model_validate(r)) for r in rows]
    return Page[StationRecord](items=items, next_cursor=next_cursor, watermark=watermark,
                               has_more=has_more, captures=[])


async def observation_page(session: AsyncSession, *, cursor: int, limit: int, watermark: int | None,
                           start: datetime | None, end: datetime | None, source: str | None) -> Page:
    """재고(관측) 페이지와, 그 재고들이 속한 capture 정보."""
    watermark = await _resolve_watermark(session, Observation, cursor, watermark)
    query = select(Observation).where(Observation.cursor_seq > cursor, Observation.cursor_seq <= watermark)
    if start is not None:
        query = query.where(Observation.received_at >= start)
    if end is not None:
        query = query.where(Observation.received_at <= end)
    if source is not None:
        query = query.where(Observation.source == source)
    rows = list(await session.scalars(query.order_by(Observation.cursor_seq).limit(limit + 1)))
    rows, next_cursor, has_more = _paging(rows, limit, watermark)

    capture_ids = sorted({r.capture_id for r in rows if r.capture_id})
    captures = list(await session.scalars(select(Capture).where(Capture.capture_id.in_(capture_ids)))) \
        if capture_ids else []
    items = [PageItem[ObservationRecord](cursor=r.cursor_seq, conflict=r.conflict, capture_id=r.capture_id,
                                         record=ObservationRecord.model_validate(r)) for r in rows]
    return Page[ObservationRecord](items=items, next_cursor=next_cursor, watermark=watermark, has_more=has_more,
                                   captures=[CaptureMeta.model_validate(c) for c in captures])
