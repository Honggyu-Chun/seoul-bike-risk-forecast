"""내부 데이터 API 테스트. 실제 PostgreSQL(cycleon_test)에 수집분을 저장해 두고 페이지 규칙을 확인한다."""
import asyncio
import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.collectors.seoul_bike import BikeRow, CaptureData
from app.core.config import Settings
from app.db.session import make_engine, make_sessionmaker
from app.main import create_app
from app.repositories.inventory import save_capture

TOKEN = "s" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
T0 = datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc)

# 모델개발/cycleon/contracts.py의 record 필드. AI는 이 밖의 필드가 있으면 거부한다.
STATION_FIELDS = {"station_id", "source_station_id", "name", "latitude", "longitude", "valid_from", "valid_to",
                  "received_at", "source", "revision", "identity_status"}
OBSERVATION_FIELDS = {"source", "entity_id", "event_key", "variable", "value", "unit", "raw_value", "received_at",
                      "observed_at", "issued_at", "valid_at", "revision", "quality", "timing_basis"}


def capture(station_ids, at=T0):
    rows = [BikeRow(station_id=s, name=f"{s} 대여소", latitude=37.5665, longitude=126.978, verified=True,
                    value=i, quality="valid", raw_value=str(i), received_at=at) for i, s in enumerate(station_ids)]
    return CaptureData(capture_id=str(uuid.uuid4()), started_at=at - timedelta(seconds=1),
                       completed_at=at + timedelta(seconds=1), rows=rows)


def seed(url, *captures):
    """테스트 DB를 비우고 수집분들을 차례로 저장한다."""
    async def go():
        engine = make_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("TRUNCATE observations, stations, captures"))
            sessions = make_sessionmaker(engine)
            for c in captures:
                async with sessions() as session:
                    await save_capture(session, c)
        finally:
            await engine.dispose()
    asyncio.run(go())


@pytest.fixture
def client(test_database_url):
    settings = Settings(_env_file=None, DATABASE_URL=test_database_url, CYCLEON_SERVICE_TOKEN=TOKEN)
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        yield c


def get(client, table, **params):
    response = client.get(f"/internal/ai-data/v1/{table}", params=params, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


# 인증

@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}])
def test_requires_service_token(client, headers):
    response = client.get("/internal/ai-data/v1/stations", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "service_authentication_required"


def test_short_configured_token_rejects_everything(test_database_url):
    app = create_app(Settings(_env_file=None, DATABASE_URL=test_database_url, CYCLEON_SERVICE_TOKEN="short"))
    response = TestClient(app).get("/internal/ai-data/v1/stations", headers={"Authorization": "Bearer short"})
    assert response.status_code == 401


# 페이지 규칙

def test_empty_table_returns_zero_watermark(client, test_database_url):
    seed(test_database_url)
    assert get(client, "stations") == {"items": [], "next_cursor": 0, "watermark": 0, "has_more": False,
                                       "captures": []}


def test_station_pages_follow_cursor_and_watermark(client, test_database_url):
    seed(test_database_url, capture(["ST-1", "ST-2", "ST-3"]))
    first = get(client, "stations", cursor=0, limit=2)
    assert [i["cursor"] for i in first["items"]] == [1, 2]
    assert (first["next_cursor"], first["watermark"], first["has_more"]) == (2, 3, True)
    second = get(client, "stations", cursor=2, limit=2, watermark=3)
    assert [i["cursor"] for i in second["items"]] == [3]
    assert (second["next_cursor"], second["has_more"]) == (3, False)
    assert set(first["items"][0]["record"]) == STATION_FIELDS
    assert first["items"][0]["capture_id"] is None


def test_watermark_pins_snapshot(client, test_database_url):
    # 첫 페이지를 받은 뒤 새 수집분이 커밋돼도, 같은 watermark로 이어 받으면 이번 회차에는 나오지 않는다
    seed(test_database_url, capture(["ST-1", "ST-2"]))
    first = get(client, "observations", cursor=0, limit=1)
    seed_more = capture(["ST-1", "ST-2"], at=T0 + timedelta(minutes=2))

    async def add():
        engine = make_engine(client.app.state.settings.database_url)
        try:
            async with make_sessionmaker(engine)() as session:
                await save_capture(session, seed_more)
        finally:
            await engine.dispose()
    asyncio.run(add())

    rest = get(client, "observations", cursor=first["next_cursor"], limit=10, watermark=first["watermark"])
    assert [i["cursor"] for i in rest["items"]] == [2] and rest["next_cursor"] == 2
    latest = get(client, "observations", cursor=2, limit=10)
    assert [i["cursor"] for i in latest["items"]] == [3, 4] and latest["watermark"] == 4


def test_observation_page_carries_its_captures(client, test_database_url):
    a, b = capture(["ST-1", "ST-2"]), capture(["ST-1", "ST-2"], at=T0 + timedelta(minutes=2))
    seed(test_database_url, a, b)
    page = get(client, "observations", cursor=0, limit=2)
    assert {i["capture_id"] for i in page["items"]} == {a.capture_id}
    assert [c["capture_id"] for c in page["captures"]] == [a.capture_id]  # 이 페이지에 있는 capture만
    meta = page["captures"][0]
    assert (meta["expected_rows"], meta["actual_rows"], meta["state"]) == (2, 2, "complete")
    record = page["items"][0]["record"]
    assert set(record) == OBSERVATION_FIELDS
    assert record["received_at"] == "2026-11-06T05:00:00Z" and record["value"] == 0


def test_observation_start_filter(client, test_database_url):
    seed(test_database_url, capture(["ST-1"]), capture(["ST-1"], at=T0 + timedelta(hours=2)))
    page = get(client, "observations", start="2026-11-06T06:00:00Z")
    assert [i["cursor"] for i in page["items"]] == [2] and page["watermark"] == 2


@pytest.mark.parametrize("params, status", [
    ({"cursor": 5, "watermark": 3}, 422),
    ({"limit": 0}, 422),
    ({"limit": 10001}, 422),
    ({"cursor": -1}, 422),
])
def test_invalid_page_requests(client, test_database_url, params, status):
    seed(test_database_url)
    response = client.get("/internal/ai-data/v1/observations", params=params, headers=AUTH)
    assert response.status_code == status


def test_forecasts_is_empty_page(client):
    assert get(client, "forecasts", cursor=0, limit=10000) == {
        "items": [], "next_cursor": 0, "watermark": 0, "has_more": False, "captures": []}
