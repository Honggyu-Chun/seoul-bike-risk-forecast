"""마이 리포트 테스트: 판정(이용 시각 직전 5분 수집분과 비교)과 GET /api/me/report.

현재 2026-11-04(수) 14:40 KST. 이번 주는 11-02(월)부터, 기본 보관 2주라 고를 수 있는 주는 11-02와 10-26.
이용 시각 H1 = 15:00 KST(06:00Z), H2 = 16:00 KST(07:00Z). 판정은 이용 시각 5분 뒤부터.
"""
import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from app.api.deps import get_now
from app.collectors.seoul_bike import BikeRow, CaptureData
from app.db.session import make_engine, make_sessionmaker
from app.repositories.inventory import save_capture
from app.services.views import judge_pending, selectable_weeks
from tests.test_favorites_api import ORIGIN, client, make_client, signup  # noqa: F401 (픽스처)
from tests.test_stations_api import H1, H2, NOW, STATIONS, FakeAI, row
from tests.test_views_api import record

T1 = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)
METERS = {sid: meters for sid, meters, _ in STATIONS}


class RiskyAI(FakeAI):
    """ST-10만 위험(is_risk=true)으로 예측한다."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(super().__call__(request).content)
        for item in body["items"]:
            if item["station_id"] == "ST-10":
                item.update(risk_probability=0.6, is_risk=True)
        return httpx.Response(200, json=body)


def save(url: str, completed_at: datetime, values: dict[str, int | None]) -> None:
    """completed_at에 끝난 수집분 하나를 저장한다. 값이 None이면 비정상 값(quality=invalid)."""
    at = completed_at - timedelta(seconds=1)
    rows = []
    for sid, value in values.items():
        base = row(sid, METERS[sid], value if value is not None else 0, at)
        if value is None:
            base = BikeRow(**{**base.__dict__, "value": None, "quality": "invalid", "raw_value": "x"})
        rows.append(base)
    data = CaptureData(capture_id=str(uuid.uuid4()), started_at=at - timedelta(seconds=1), completed_at=completed_at,
                       rows=rows)

    async def go():
        engine = make_engine(url)
        try:
            async with make_sessionmaker(engine)() as session:
                await save_capture(session, data)
        finally:
            await engine.dispose()
    asyncio.run(go())


def execute(url: str, sql: str, **params) -> None:
    async def go():
        engine = make_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql), params)
        finally:
            await engine.dispose()
    asyncio.run(go())


def at(client, moment: datetime) -> None:
    client.app.dependency_overrides[get_now] = lambda: moment


def report(client, **params):
    return client.get("/api/me/report", params=params)


def cards(body):
    return [(i["station_id"], i["target_at"], i["outcome"], i["actual_inventory"]) for i in body["items"]]


def test_requires_login(make_client):
    assert report(make_client(login=False)).status_code == 401


def test_empty_report(client):
    body = report(client).json()
    assert body == {"week": "2026-11-02", "weeks": ["2026-11-02", "2026-10-26"],
                    "summary": {"viewed": 0, "judged": 0, "hits": 0, "rented": 0}, "items": []}


def test_judges_against_capture_just_before_target(make_client, test_database_url):
    client = make_client(RiskyAI())
    for sid in ("ST-2", "ST-3", "ST-10", "ST-4"):
        assert record(client, sid).status_code == 201
    assert record(client, "ST-4", H2).status_code == 201
    # 06:00Z 직전 5분 안의 수집분(ST-4는 비정상 값). 그보다 이른 것·늦은 것은 쓰지 않는다
    save(test_database_url, T1 - timedelta(minutes=6), {"ST-2": 9, "ST-3": 0, "ST-10": 9, "ST-4": 9})
    save(test_database_url, T1 - timedelta(minutes=2), {"ST-2": 0, "ST-3": 5, "ST-10": 0, "ST-4": None})
    save(test_database_url, T1 + timedelta(minutes=1), {"ST-2": 9, "ST-3": 0, "ST-10": 9, "ST-4": 9})

    at(client, T1 + timedelta(minutes=4))   # 이용 시각은 지났지만 5분이 안 됨 → 판정 전
    body = report(client).json()
    assert {i["outcome"] for i in body["items"]} == {None}
    assert body["summary"] == {"viewed": 5, "judged": 0, "hits": 0, "rented": 0}

    at(client, T1 + timedelta(hours=1, minutes=10))
    body = report(client).json()
    assert cards(body) == [
        ("ST-4", H2, "unknown", None),     # 07:00Z 직전 5분 안에 수집분 없음
        ("ST-4", H1, "unknown", None),     # 값이 비정상
        ("ST-10", H1, "hit", 0),           # 위험 예측 + 실제 0대
        ("ST-3", H1, "hit", 5),            # 위험 아님 + 1대 이상
        ("ST-2", H1, "miss", 0),           # 위험 아님 + 실제 0대
    ]
    assert body["summary"] == {"viewed": 5, "judged": 3, "hits": 2, "rented": 0}
    assert body["items"][2]["is_risk"] is True and body["items"][2]["horizon_hours"] == 1


def test_judgment_is_kept(make_client, test_database_url):
    """한 번 판정하면 나중에 수집분이 지워지거나 바뀌어도 결과가 그대로다."""
    client = make_client()
    record(client, "ST-3")
    save(test_database_url, T1 - timedelta(minutes=1), {"ST-3": 4})
    at(client, T1 + timedelta(minutes=10))
    assert cards(report(client).json()) == [("ST-3", H1, "hit", 4)]
    execute(test_database_url, "DELETE FROM observations")
    assert cards(report(client).json()) == [("ST-3", H1, "hit", 4)]


def test_rented_is_counted(client, test_database_url):
    record(client, "ST-3")
    record(client, "ST-2")
    execute(test_database_url, "UPDATE prediction_views SET rented = true WHERE station_id = 'ST-3'")
    assert report(client).json()["summary"]["rented"] == 1


def test_week_boundary_is_monday_midnight_kst(client, test_database_url):
    record(client, "ST-3")  # 회원 id를 얻기 위한 기록(이번 주)
    sunday_23, monday_00 = datetime(2026, 11, 1, 14, tzinfo=timezone.utc), datetime(2026, 11, 1, 15, tzinfo=timezone.utc)
    for target in (sunday_23, monday_00):
        execute(test_database_url,
                "INSERT INTO prediction_views (user_id, station_id, station_name, base_at, target_at, viewed_at, "
                "risk_probability, is_risk) SELECT user_id, 'ST-2', 'ST-2 대여소', :base, :target, :base, 0.1, false "
                "FROM prediction_views LIMIT 1", base=target - timedelta(hours=1), target=target)
    this_week = report(client).json()
    assert [(i["station_id"], i["target_at"]) for i in this_week["items"]] == [
        ("ST-3", H1), ("ST-2", "2026-11-01T15:00:00Z")]
    last_week = report(client, week="2026-10-26").json()
    assert [i["target_at"] for i in last_week["items"]] == ["2026-11-01T14:00:00Z"]
    assert last_week["items"][0]["outcome"] == "unknown"   # 그때 수집분이 없음


@pytest.mark.parametrize("week", ["2026-10-19", "2026-11-03", "2026-11-09", "not-a-date"])
def test_week_outside_retention_or_not_monday_is_422(client, week):
    response = report(client, week=week)
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["week"]


def test_other_members_records_are_not_shown(client):
    record(client, "ST-3")
    client.cookies.clear()
    signup(client, "rider_02")
    assert report(client).json()["summary"]["viewed"] == 0


def test_selectable_weeks():
    assert selectable_weeks(NOW, 2) == [date(2026, 11, 2), date(2026, 10, 26)]
    assert selectable_weeks(NOW, 1) == [date(2026, 11, 2)]
    sunday_late = datetime(2026, 11, 8, 14, 59, tzinfo=timezone.utc)    # 일요일 23:59 KST
    monday_early = datetime(2026, 11, 8, 15, 0, tzinfo=timezone.utc)    # 월요일 00:00 KST
    assert selectable_weeks(sunday_late, 1) == [date(2026, 11, 2)]
    assert selectable_weeks(monday_early, 1) == [date(2026, 11, 9)]


def test_collector_judges_every_member(client, test_database_url):
    """수집기는 회원 구분 없이 대기 기록을 판정한다(오래된 재고를 지우기 전에)."""
    record(client, "ST-3")
    client.cookies.clear()
    signup(client, "rider_02")
    record(client, "ST-3")
    save(test_database_url, T1 - timedelta(minutes=1), {"ST-3": 0})

    async def go():
        engine = make_engine(test_database_url)
        try:
            async with make_sessionmaker(engine)() as session:
                early = await judge_pending(session, T1 + timedelta(minutes=4))
                later = await judge_pending(session, T1 + timedelta(minutes=5))
                again = await judge_pending(session, T1 + timedelta(minutes=6))
                return early, later, again
        finally:
            await engine.dispose()
    assert asyncio.run(go()) == (0, 2, 0)
    assert cards(report(client).json()) == [("ST-3", H1, "miss", 0)]
