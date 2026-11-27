"""시간대별 평균 수량 API 테스트 (GET /api/stations/{station_id}/hourly-average).

테스트 DB에 수집분을 저장해 둔다. 현재 시각은 2026-11-27(금) 14:00 KST(05:00Z)로 고정.
기간은 오늘 포함 KST 7일 = 11-21(토) 00:00 KST(11-20 15:00Z)부터 지금까지. 평일 5일, 주말 2일.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_now
from app.collectors.seoul_bike import BikeRow, CaptureData
from app.core.config import Settings
from app.core.time import KST
from app.db.session import make_engine, make_sessionmaker
from app.main import create_app
from app.repositories.inventory import save_capture

NOW = datetime(2026, 11, 27, 5, 0, tzinfo=timezone.utc)
PERIOD_START = "2026-11-20T15:00:00Z"
SITE = (37.5665, 126.978)


def kst(day, hour, minute=0):
    """2026-11-<day> <hour>:<minute> KST"""
    return datetime(2026, 11, day, hour, minute, tzinfo=KST)


def bike(sid, value, at, *, quality="valid", name=None, north_m=0, verified=True):
    lat = SITE[0] + north_m / 111_195
    return BikeRow(station_id=sid, name=name or f"{sid} 대여소", latitude=lat if verified else None,
                   longitude=SITE[1] if verified else None, verified=verified, value=value, quality=quality,
                   raw_value=str(value), received_at=at)


def seed(url, captures, incomplete=()):
    """captures: [(받은 시각, [BikeRow...])]. 순서대로 저장한다. incomplete의 순번은 완료되지 않은 수집분으로 바꾼다."""
    async def go():
        engine = make_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("TRUNCATE observations, stations, captures"))
            ids = []
            for at, rows in captures:
                data = CaptureData(capture_id=str(uuid.uuid4()), started_at=at - timedelta(seconds=1),
                                   completed_at=at + timedelta(seconds=1), rows=rows)
                ids.append(data.capture_id)
                async with make_sessionmaker(engine)() as session:
                    await save_capture(session, data)
            async with engine.begin() as conn:
                for i in incomplete:
                    await conn.execute(text("UPDATE captures SET state = 'partial' WHERE capture_id = :id"),
                                       {"id": ids[i]})
        finally:
            await engine.dispose()
    asyncio.run(go())


def capture(at, *values, **kwargs):
    """ST-1 하나(값 values[0])와 ST-2(값 values[1], 있으면)를 담은 수집분"""
    rows = [bike("ST-1", values[0], at, **kwargs)]
    if len(values) > 1:
        rows.append(bike("ST-2", values[1], at))
    return at, rows


def long_ago():
    """기간 전 수집분. 대여소를 이 시각부터 있던 곳으로 만든다(valid_from이 기간 시작보다 앞)."""
    return capture(kst(14, 6), 0, 0)


@pytest.fixture
def client(test_database_url):
    settings = Settings(_env_file=None, DATABASE_URL=test_database_url)
    app = create_app(settings)
    app.dependency_overrides[get_now] = lambda: NOW
    with TestClient(app, raise_server_exceptions=False) as opened:
        yield opened


def get(client, sid="ST-1", **params):
    return client.get(f"/api/stations/{sid}/hourly-average", params=params)


def hour(body, h):
    return body["hours"][h]


def test_hours_are_grouped_in_kst(client, test_database_url):
    # 11-25 14:50Z = 11-25 23:50 KST(수), 11-25 15:10Z = 11-26 00:10 KST(목)
    seed(test_database_url, [long_ago(), capture(datetime(2026, 11, 25, 14, 50, tzinfo=timezone.utc), 8, 1),
                             capture(datetime(2026, 11, 25, 15, 10, tzinfo=timezone.utc), 4, 1)])
    response = get(client)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "max-age=600"
    body = response.json()
    assert body["station_id"] == "ST-1" and body["name"] == "ST-1 대여소" and body["day_type"] == "weekday"
    assert body["period_start"] == PERIOD_START and body["period_end"] == "2026-11-27T05:00:00Z"
    assert body["days"] == 2 and len(body["hours"]) == 24
    assert [h["hour"] for h in body["hours"]] == list(range(24))
    assert hour(body, 0) == {"hour": 0, "avg_inventory": 4.0, "samples": 1}
    assert hour(body, 23) == {"hour": 23, "avg_inventory": 8.0, "samples": 1}
    assert hour(body, 12) == {"hour": 12, "avg_inventory": None, "samples": 0}


def test_each_day_has_the_same_weight(client, test_database_url):
    # 월요일 8시에는 수집분 3개(0대), 화요일 8시에는 1개(6대). 단순 평균이면 1.5, 날마다 같은 무게면 3.0
    seed(test_database_url, [capture(kst(23, 8, m), 0) for m in (0, 2, 4)] + [capture(kst(24, 8, 30), 6)])
    body = get(client).json()
    assert hour(body, 8) == {"hour": 8, "avg_inventory": 3.0, "samples": 4} and body["days"] == 2


def test_average_is_rounded_to_one_decimal(client, test_database_url):
    seed(test_database_url, [capture(kst(23, 9, m), v) for m, v in ((0, 1), (2, 1), (4, 2))])
    assert hour(get(client).json(), 9)["avg_inventory"] == 1.3


def test_weekday_weekend_split_and_default(client, test_database_url):
    seed(test_database_url, [capture(kst(21, 10), 10), capture(kst(22, 10), 8),      # 토·일
                             capture(kst(23, 10), 2), capture(kst(27, 10), 4)])     # 월·금(오늘)
    default = get(client).json()                                                     # 오늘은 금요일 → 평일
    assert default["day_type"] == "weekday" and default["days"] == 2 and hour(default, 10)["avg_inventory"] == 3.0
    weekend = get(client, day_type="weekend").json()
    assert weekend["day_type"] == "weekend" and weekend["days"] == 2 and hour(weekend, 10)["avg_inventory"] == 9.0
    every = get(client, day_type="all").json()
    assert every["days"] == 4 and hour(every, 10) == {"hour": 10, "avg_inventory": 6.0, "samples": 4}


def test_default_day_type_on_weekend(client, test_database_url):
    seed(test_database_url, [capture(kst(21, 10), 10), capture(kst(23, 10), 2)])
    client.app.dependency_overrides[get_now] = lambda: kst(28, 9).astimezone(timezone.utc)   # 토요일
    body = get(client).json()
    assert body["day_type"] == "weekend" and body["period_start"] == "2026-11-21T15:00:00Z"   # 11-22 00:00 KST
    assert body["days"] == 0 and hour(body, 10)["avg_inventory"] is None   # 11-21은 기간 밖


def test_invalid_missing_and_incomplete_captures_are_excluded(client, test_database_url):
    seed(test_database_url, [
        capture(kst(24, 7, 0), 5),
        capture(kst(24, 7, 2), None, quality="invalid"),
        capture(kst(24, 7, 4), None, quality="missing"),
        capture(kst(24, 7, 6), 0),           # 실제 0대는 넣는다
        capture(kst(24, 7, 8), 40),          # 완료되지 않은 수집분
    ], incomplete=[4])
    assert hour(get(client).json(), 7) == {"hour": 7, "avg_inventory": 2.5, "samples": 2}


def test_data_before_the_period_is_excluded(client, test_database_url):
    seed(test_database_url, [capture(kst(20, 23, 59), 30), capture(kst(21, 23, 0), 2)])
    body = get(client, day_type="all").json()
    assert hour(body, 23) == {"hour": 23, "avg_inventory": 2.0, "samples": 1} and body["days"] == 1
    assert body["period_start"] == PERIOD_START


def test_new_station_period_starts_when_first_seen(client, test_database_url):
    seed(test_database_url, [capture(kst(25, 8, 30), 3)])
    assert get(client).json()["period_start"] == "2026-11-24T23:30:00Z"


def test_moved_station_uses_history_since_the_move(client, test_database_url):
    seed(test_database_url, [capture(kst(23, 6), 9),                         # 예전 위치
                             capture(kst(24, 6), 9),
                             capture(kst(25, 6, 30), 1, north_m=500)])         # 500m 옮겨짐
    body = get(client).json()
    assert body["period_start"] == "2026-11-24T21:30:00Z"                    # 옮긴 뒤 처음 받은 시각
    assert hour(body, 6) == {"hour": 6, "avg_inventory": 1.0, "samples": 1} and body["days"] == 1


def test_rename_keeps_history(client, test_database_url):
    seed(test_database_url, [long_ago(), capture(kst(23, 6), 9), capture(kst(25, 6), 1, name="새 이름", north_m=10)])
    body = get(client).json()
    assert body["name"] == "새 이름" and body["period_start"] == PERIOD_START
    assert hour(body, 6) == {"hour": 6, "avg_inventory": 5.0, "samples": 2}


def test_other_stations_are_not_mixed_in(client, test_database_url):
    seed(test_database_url, [capture(kst(23, 6), 2, 20)])
    assert hour(get(client).json(), 6)["avg_inventory"] == 2.0
    assert hour(get(client, "ST-2").json(), 6)["avg_inventory"] == 20.0


def test_station_without_data_has_24_null_hours(client, test_database_url):
    seed(test_database_url, [capture(kst(20, 6), 2)])   # 대여소는 있지만 기간 안 수집분은 없음
    body = get(client).json()
    assert body["days"] == 0 and all(h["avg_inventory"] is None and h["samples"] == 0 for h in body["hours"])


@pytest.mark.parametrize("sid", ["ST-404", "ST-9"])
def test_unknown_or_unverified_station_is_404(client, test_database_url, sid):
    seed(test_database_url, [(kst(23, 6), [bike("ST-1", 3, kst(23, 6)), bike("ST-9", 3, kst(23, 6), verified=False)])])
    response = get(client, sid)
    assert response.status_code == 404 and response.json()["error"]["code"] == "station_not_found"


def test_invalid_day_type_is_422(client, test_database_url):
    response = get(client, day_type="holiday")
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["day_type"]
