"""테이블 제약 조건 테스트. 잘못된 값이 DB 단계에서 막히는지 실제 PostgreSQL(cycleon_test)로 확인한다."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.db.models import Capture, Observation, Station

T0 = datetime(2026, 11, 4, 5, 0, tzinfo=timezone.utc)


def capture(capture_id="cap-1"):
    return Capture(capture_id=capture_id, source="seoul_bike", started_at=T0, completed_at=T0 + timedelta(seconds=20),
                   expected_rows=1, actual_rows=1, state="complete")


def station(**overrides):
    values = dict(cursor_seq=1, station_id="ST-1", source_station_id="ST-1", name="대여소 1", latitude=37.5665,
                  longitude=126.978, identity_status="verified", valid_from=T0, received_at=T0, revision="cap-1")
    return Station(**(values | overrides))


def inventory(**overrides):
    values = dict(cursor_seq=1, capture_id="cap-1", source="seoul_bike", entity_id="ST-1", event_key="cap-1",
                  variable="inventory", value=3, unit="bikes", quality="valid", received_at=T0 + timedelta(seconds=5))
    return Observation(**(values | overrides))


@pytest.fixture
def save(test_database_url):
    """객체들을 한 트랜잭션으로 저장하는 함수를 돌려준다. 테스트마다 세 테이블을 비우고 시작한다."""
    def run(*rows):
        async def go():
            engine = create_async_engine(test_database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("TRUNCATE observations, stations, captures"))
                async with AsyncSession(engine, expire_on_commit=False) as session:
                    session.add_all(rows)
                    await session.commit()
                    for row in rows:  # DB가 채운 기본값(conflict, source 등)을 다시 읽는다
                        await session.refresh(row)
                    return list(rows)
            finally:
                await engine.dispose()
        return asyncio.run(go())
    return run


def test_valid_capture_station_and_inventory(save):
    _, saved_station, zero, missing = save(
        capture(), station(),
        inventory(value=0),                                                     # 실제 0대
        inventory(cursor_seq=2, entity_id="ST-2", value=None, quality="invalid"),  # 값 이상은 null
    )
    assert saved_station.conflict is False and saved_station.source == "seoul_bike"
    assert zero.value == 0 and missing.value is None
    assert zero.received_at == T0 + timedelta(seconds=5)  # 시간대 포함으로 저장·복원


@pytest.mark.parametrize("rows", [
    pytest.param([station(latitude=None, longitude=None)], id="verified-without-coordinates"),
    pytest.param([station(latitude=40.0)], id="latitude-out-of-range"),
    pytest.param([station(identity_status="maybe")], id="unknown-identity-status"),
    pytest.param([station(valid_to=T0)], id="empty-validity-interval"),
    pytest.param([station(), station(cursor_seq=2)], id="duplicate-station-revision"),
    pytest.param([station(), station(cursor_seq=1, revision="cap-2")], id="duplicate-cursor"),
    pytest.param([capture(), inventory(value=None)], id="valid-without-value"),
    pytest.param([capture(), inventory(value=-1)], id="negative-inventory"),
    pytest.param([capture(), inventory(value=1.5)], id="fractional-inventory"),
    pytest.param([capture(), inventory(unit="count")], id="wrong-inventory-unit"),
    pytest.param([capture(), inventory(), inventory(cursor_seq=2)], id="duplicate-observation-identity"),
    pytest.param([inventory(capture_id="missing")], id="unknown-capture"),
])
def test_rejected_rows(save, rows):
    with pytest.raises(IntegrityError):
        save(*rows)


def test_unverified_station_may_lack_coordinates(save):
    (saved,) = save(station(identity_status="unverified", latitude=None, longitude=None))
    assert saved.latitude is None
