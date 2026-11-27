"""수집분 저장 규칙 테스트. 실제 PostgreSQL(cycleon_test)에서 확인한다."""
import asyncio
import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app.collectors.seoul_bike import BikeRow, CaptureData
from app.db.models import Capture, Observation, Station
from app.db.session import make_engine, make_sessionmaker
from app.repositories.inventory import prune, save_capture
from app.repositories.stations import latest_stations

T0 = datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc)


def row(station_id="ST-1", name="1. 대여소", lat=37.5665, lon=126.978, value=3, verified=True):
    return BikeRow(station_id=station_id, name=name, latitude=lat, longitude=lon, verified=verified,
                   value=value, quality="valid" if value is not None else "invalid",
                   raw_value=None if value is None else str(value), received_at=T0)


def capture(*rows, at=T0):
    """at 시각에 받은 수집분. 각 행의 received_at도 at으로 맞춘다."""
    return CaptureData(capture_id=str(uuid.uuid4()), started_at=at - timedelta(seconds=1),
                       completed_at=at + timedelta(seconds=1),
                       rows=[dataclasses.replace(r, received_at=at) for r in rows])


@pytest.fixture
def db(test_database_url):
    """async 함수 fn(sessionmaker)을 테스트 DB에서 실행한다. 매번 세 테이블을 비우고 시작한다."""
    def run(fn):
        async def go():
            engine = make_engine(test_database_url)
            try:
                async with engine.begin() as conn:
                    await conn.execute(text("TRUNCATE observations, stations, captures"))
                return await fn(make_sessionmaker(engine))
            finally:
                await engine.dispose()
        return asyncio.run(go())
    return run


async def save(sessions, data):
    async with sessions() as session:
        return await save_capture(session, data)


async def stations_of(sessions, station_id):
    async with sessions() as session:
        return list(await session.scalars(
            select(Station).where(Station.station_id == station_id).order_by(Station.cursor_seq)))


def test_first_capture_saves_everything_with_consecutive_cursors(db):
    async def scenario(sessions):
        result = await save(sessions, capture(row("ST-1"), row("ST-2", value=0), row("ST-3", value=None)))
        async with sessions() as session:
            obs = list(await session.scalars(select(Observation).order_by(Observation.cursor_seq)))
            cap = await session.scalar(select(Capture))
            seqs = list(await session.scalars(select(Station.cursor_seq).order_by(Station.cursor_seq)))
        return result, obs, cap, seqs

    result, obs, cap, seqs = db(scenario)
    assert result.station_revisions == 3 and result.observations == 3
    assert [o.cursor_seq for o in obs] == [1, 2, 3] and seqs == [1, 2, 3]
    assert [o.value for o in obs] == [3, 0, None] and obs[2].quality == "invalid"
    assert cap.expected_rows == cap.actual_rows == 3 and cap.state == "complete"
    assert all(o.event_key == cap.capture_id for o in obs)


def test_unchanged_station_adds_no_revision_but_cursors_continue(db):
    async def scenario(sessions):
        await save(sessions, capture(row("ST-1"), row("ST-2")))
        second = await save(sessions, capture(row("ST-1", value=5), row("ST-2", value=1), at=T0 + timedelta(minutes=2)))
        async with sessions() as session:
            obs_seq = await session.scalar(select(func.max(Observation.cursor_seq)))
            station_count = await session.scalar(select(func.count()).select_from(Station))
        return second, obs_seq, station_count

    second, obs_seq, station_count = db(scenario)
    assert second.station_revisions == 0 and obs_seq == 4 and station_count == 2


def test_name_change_keeps_valid_from(db):
    async def scenario(sessions):
        await save(sessions, capture(row(name="1. 대여소")))
        await save(sessions, capture(row(name="1. 대여소(이름 수정)"), at=T0 + timedelta(days=3)))
        return await stations_of(sessions, "ST-1")

    first, second = db(scenario)
    assert second.name == "1. 대여소(이름 수정)" and second.cursor_seq > first.cursor_seq
    assert second.valid_from == first.valid_from == T0  # 이력이 끊기지 않음


def test_small_coordinate_fix_keeps_valid_from_but_move_resets(db):
    later = T0 + timedelta(days=1)

    async def scenario(sessions):
        await save(sessions, capture(row()))
        await save(sessions, capture(row(lat=37.5666), at=later))                     # 약 11m
        await save(sessions, capture(row(lat=37.5700), at=later + timedelta(days=1)))  # 약 380m
        return await stations_of(sessions, "ST-1")

    original, nudged, moved = db(scenario)
    assert nudged.valid_from == T0
    assert moved.valid_from == later + timedelta(days=1)


def test_coordinates_lost_then_restored(db):
    async def scenario(sessions):
        await save(sessions, capture(row()))
        await save(sessions, capture(row(lat=None, lon=None, verified=False), at=T0 + timedelta(hours=1)))
        return await stations_of(sessions, "ST-1")

    _, lost = db(scenario)
    assert lost.identity_status == "unverified" and lost.latitude is None and lost.valid_from == T0


def test_failed_save_leaves_nothing(db):
    async def scenario(sessions):
        bad = capture(row("ST-1"), row("ST-2", value=-1))  # 음수 재고는 DB 제약에서 거부
        with pytest.raises(Exception):
            await save(sessions, bad)
        async with sessions() as session:
            return [await session.scalar(select(func.count()).select_from(m)) for m in (Capture, Station, Observation)]

    assert db(scenario) == [0, 0, 0]


def test_concurrent_saves_get_cursors_in_commit_order(db):
    async def scenario(sessions):
        captures = [capture(*(row(f"ST-{i}") for i in range(50)), at=T0 + timedelta(minutes=k)) for k in range(4)]
        await asyncio.gather(*(save(sessions, c) for c in captures))
        async with sessions() as session:
            rows = (await session.execute(select(Observation.cursor_seq, Observation.capture_id)
                                          .order_by(Observation.cursor_seq))).all()
        return rows

    rows = db(scenario)
    assert [seq for seq, _ in rows] == list(range(1, 201))  # 빈 번호·중복 없음
    blocks = [rows[i][1] for i in range(0, 200, 50)]
    assert all(rows[i + j][1] == blocks[i // 50] for i in range(0, 200, 50) for j in range(50))  # 수집분끼리 섞이지 않음


def test_latest_stations_returns_newest_revision(db):
    async def scenario(sessions):
        await save(sessions, capture(row(name="a")))
        await save(sessions, capture(row(name="b"), at=T0 + timedelta(hours=1)))
        async with sessions() as session:
            return await latest_stations(session)

    assert db(scenario)["ST-1"].name == "b"


def test_prune_removes_old_inventory_and_captures(db):
    async def scenario(sessions):
        await save(sessions, capture(row(), at=T0))
        await save(sessions, capture(row(value=7), at=T0 + timedelta(days=9)))
        async with sessions() as session:
            removed = await prune(session, T0 + timedelta(days=1))
            values = list(await session.scalars(select(Observation.value)))
            captures = await session.scalar(select(func.count()).select_from(Capture))
            stations = await session.scalar(select(func.count()).select_from(Station))
        return removed, values, captures, stations

    assert db(scenario) == (1, [7], 1, 1)
