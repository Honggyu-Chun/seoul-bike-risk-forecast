"""따릉이 수집분 저장과 보관 기간 정리.

save_capture는 capture 1행, 바뀐 대여소 revision, 재고 전체를 '한 트랜잭션'으로 저장한다.
중간에 실패하면 아무것도 남지 않는다(AI가 반쪽짜리 수집분을 보지 않도록).

cursor_seq 붙이는 방법
- 테이블마다 트랜잭션 잠금(pg_advisory_xact_lock)을 잡고 현재 max(cursor_seq)+1부터 붙인다.
- 잠금은 커밋할 때 풀린다. 그래서 저장 작업들이 한 줄로 서고, 번호 순서 = 커밋 순서가 된다.
  (DB 시퀀스는 커밋 순서와 번호 순서가 어긋날 수 있어 쓰지 않는다. AI가 번호를 건너뛴 것으로 오해할 수 있음)
- 잠금은 항상 stations → observations 순서로 잡는다(서로 반대로 잡아 교착되는 일을 막음).

충돌(conflict) 재발행
- 같은 식별자에 다른 값이 들어오는 경우에 필요한 규칙이다. 이 수집기는 capture마다 새 capture_id를
  revision·event_key로 쓰므로 식별자가 겹치지 않아 충돌이 생기지 않는다. 그래서 conflict는 항상 false로 둔다.
  다른 출처(과거 자료 재적재 등)를 넣게 되면 그때 재발행 규칙을 추가한다.
"""
import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.seoul_bike import BikeRow, CaptureData
from app.core.geo import distance_m
from app.db.models import Capture, Observation, Station
from app.repositories.stations import latest_stations

SOURCE = "seoul_bike"
LOCK_STATIONS = 7_101       # pg_advisory_xact_lock 키 (테이블별 고정 번호)
LOCK_OBSERVATIONS = 7_102
MOVED_METERS = 100          # 좌표가 이보다 많이 옮겨지면 다른 위치로 보고 새 적용 구간을 연다


@dataclass(frozen=True)
class SaveResult:
    station_revisions: int   # 새로 추가된 대여소 revision 수 (신규 + 정보 변경)
    observations: int


async def _next_cursor(session: AsyncSession, model: type[Station] | type[Observation], lock_key: int) -> int:
    """테이블 잠금을 잡고 다음 cursor_seq를 돌려준다. 잠금은 이 트랜잭션이 끝날 때까지 유지된다."""
    await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
    return (await session.scalar(select(func.coalesce(func.max(model.cursor_seq), 0)))) + 1


def revision_valid_from(previous: Station | None, row: BikeRow) -> datetime | None:
    """이번 수집에서 새 revision이 필요하면 그 valid_from을, 필요 없으면 None을 돌려준다.

    - 처음 본 대여소: 이번에 받은 시각부터
    - 이름·좌표·식별 상태가 모두 같음: 새 revision 없음
    - 좌표가 100m 넘게 옮겨짐: 다른 위치로 보고 이번에 받은 시각부터 (이전 재고 이력과 끊음)
    - 그 밖의 변경(이름 오타 수정, 좌표 미세 보정, 좌표 누락↔복구): 새 revision을 내되 원래 valid_from 유지.
      AI는 valid_from 이전 재고를 버리므로, 이름 하나 바뀌었다고 7일 이력을 잃지 않게 하기 위함이다.
    """
    status = "verified" if row.verified else "unverified"
    if previous is None:
        return row.received_at
    same = (previous.name, previous.latitude, previous.longitude, previous.identity_status) == \
        (row.name, row.latitude, row.longitude, status)
    if same:
        return None
    if None not in (previous.latitude, previous.longitude, row.latitude, row.longitude) and \
            distance_m(previous.latitude, previous.longitude, row.latitude, row.longitude) > MOVED_METERS:
        return row.received_at
    return previous.valid_from


async def save_capture(session: AsyncSession, capture: CaptureData) -> SaveResult:
    """수집분 1회를 한 트랜잭션으로 저장하고 커밋한다."""
    station_seq = await _next_cursor(session, Station, LOCK_STATIONS)
    observation_seq = await _next_cursor(session, Observation, LOCK_OBSERVATIONS)
    previous = await latest_stations(session)

    await session.execute(insert(Capture).values(
        capture_id=capture.capture_id, source=SOURCE, started_at=capture.started_at,
        completed_at=capture.completed_at, expected_rows=len(capture.rows), actual_rows=len(capture.rows),
        state="complete", summary_json=json.dumps({"pages": capture.pages}, ensure_ascii=False)))

    revisions = []
    for row in capture.rows:
        valid_from = revision_valid_from(previous.get(row.station_id), row)
        if valid_from is None:
            continue
        revisions.append({
            "cursor_seq": station_seq + len(revisions), "station_id": row.station_id,
            "source_station_id": row.station_id, "source": SOURCE, "name": row.name,
            "latitude": row.latitude, "longitude": row.longitude,
            "identity_status": "verified" if row.verified else "unverified",
            "valid_from": valid_from, "received_at": row.received_at, "revision": capture.capture_id,
        })
    if revisions:
        await session.execute(insert(Station), revisions)

    await session.execute(insert(Observation), [{
        "cursor_seq": observation_seq + i, "capture_id": capture.capture_id, "source": SOURCE,
        "entity_id": row.station_id, "event_key": capture.capture_id, "variable": "inventory",
        "value": row.value, "unit": "bikes", "raw_value": row.raw_value, "quality": row.quality,
        "timing_basis": "received", "received_at": row.received_at,
    } for i, row in enumerate(capture.rows)])

    await session.commit()
    return SaveResult(station_revisions=len(revisions), observations=len(capture.rows))


async def prune(session: AsyncSession, before: datetime) -> int:
    """before보다 오래된 재고와 수집분을 지우고 커밋한다. 지운 재고 수를 돌려준다.

    대여소 이력(stations)은 지우지 않는다. 행 수가 적고, valid_from 판단에 필요하다.
    지워도 AI 커서에는 영향이 없다. AI는 이미 받은 번호 다음부터만 요청한다.
    """
    removed = await session.execute(delete(Observation).where(Observation.received_at < before))
    await session.execute(delete(Capture).where(Capture.completed_at < before,
                                                ~select(Observation.id).where(
                                                    Observation.capture_id == Capture.capture_id).exists()))
    await session.commit()
    return removed.rowcount
