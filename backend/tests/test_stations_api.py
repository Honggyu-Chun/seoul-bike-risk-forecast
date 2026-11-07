"""대여소 공개 API 테스트 (/api/stations, /api/stations/nearby, /api/predictions).

테스트 DB에 수집분을 저장해 두고 가짜 AI(httpx.MockTransport)를 붙인다.
현재 시각은 2026-11-04 14:40 KST(05:40Z)로 고정. 기준 정각 05:00Z, 이용 시각 06·07·08Z.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_now
from app.clients.ai import AIClient
from app.collectors.seoul_bike import BikeRow, CaptureData
from app.core.config import Settings
from app.db.session import make_engine, make_sessionmaker
from app.main import create_app
from app.repositories.inventory import save_capture

NOW = datetime(2026, 11, 4, 5, 40, tzinfo=timezone.utc)
BASE = datetime(2026, 11, 4, 5, 0, tzinfo=timezone.utc)
H1, H2 = "2026-11-04T06:00:00Z", "2026-11-04T07:00:00Z"
CENTER = (37.5665, 126.978)

# (ID, 북쪽으로 몇 m, 재고). 위도 0.001도 ≈ 111m
STATIONS = [("ST-10", 300, 2), ("ST-2", 100, 0), ("ST-3", 900, 7), ("ST-4", 2000, 1)]


def row(sid, meters, value, at, verified=True):
    lat = CENTER[0] + meters / 111_195
    return BikeRow(station_id=sid, name=f"{sid} 대여소", latitude=lat if verified else None,
                   longitude=CENTER[1] if verified else None, verified=verified, value=value, quality="valid",
                   raw_value=str(value), received_at=at)


def seed(url, received_at=NOW - timedelta(seconds=60)):
    """대여소 4곳(검증됨) + 좌표 없는 1곳을 한 수집분으로 저장한다."""
    rows = [row(*s, received_at) for s in STATIONS] + [row("ST-99", 0, 5, received_at, verified=False)]
    data = CaptureData(capture_id=str(uuid.uuid4()), started_at=received_at - timedelta(seconds=1),
                       completed_at=received_at + timedelta(seconds=1), rows=rows)

    async def go():
        engine = make_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("TRUNCATE observations, stations, captures"))
            async with make_sessionmaker(engine)() as session:
                await save_capture(session, data)
        finally:
            await engine.dispose()
    asyncio.run(go())


class FakeAI:
    """/v1/predictions에 정해진 확률을 준다. base로 AI의 기준 정각을 바꿀 수 있다."""

    def __init__(self, base=BASE, down=False):
        self.base, self.down, self.requests = base, down, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused")
        body = json.loads(request.content)
        self.requests.append(body)
        stamp = lambda t: t.isoformat()
        items = []
        for sid in body["station_ids"]:
            for h in body["horizons_hours"]:
                known = sid.startswith("ST-") and sid != "ST-404"
                items.append({
                    "station_id": sid, "horizon_hours": h, "target_at": stamp(self.base + timedelta(hours=h)),
                    "risk_probability": 0.1 * h if known else None, "is_risk": False if known else None,
                    "model_id": "m-1" if known else None, "input_group": "C" if known else None,
                    "reason": None if known else "station_unknown_or_ineligible",
                    "latest_inventory": 4 if known else None,
                    "latest_inventory_received_at": stamp(NOW - timedelta(seconds=30)) if known else None,
                    "latest_inventory_age_seconds": 30 if known else None, "latest_inventory_observed_at": None})
        return httpx.Response(200, json={"base_at": stamp(self.base), "requested_at": stamp(NOW), "items": items})


@pytest.fixture
def make_client(test_database_url):
    """앱을 만들어 TestClient를 연다. with로 열어야 요청들이 한 이벤트 루프를 공유한다
    (DB 연결 풀이 루프에 묶여 있어서, with 없이 요청마다 새 루프를 쓰면 두 번째 요청이 실패한다)."""
    opened = []

    def build(fake=None, **env):
        settings = Settings(_env_file=None, DATABASE_URL=test_database_url, CYCLEON_SERVICE_TOKEN="t" * 32, **env)
        app = create_app(settings, AIClient(settings, transport=httpx.MockTransport(fake or FakeAI())))
        app.dependency_overrides[get_now] = lambda: NOW
        client = TestClient(app, raise_server_exceptions=False)
        opened.append(client.__enter__())
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


# GET /api/stations

def test_stations_lists_verified_only_by_id(make_client, test_database_url):
    seed(test_database_url)
    body = make_client().get("/api/stations").json()
    assert body["count"] == 4
    assert [s["station_id"] for s in body["items"]] == ["ST-2", "ST-3", "ST-4", "ST-10"]  # ST-99(좌표 없음) 제외
    assert body["items"][0]["distance_m"] is None


def test_stations_with_location_sorted_and_radius(make_client, test_database_url):
    seed(test_database_url)
    client = make_client()
    near = client.get("/api/stations", params={"lat": CENTER[0], "lon": CENTER[1]}).json()
    assert [s["station_id"] for s in near["items"]] == ["ST-2", "ST-10", "ST-3", "ST-4"]
    assert near["items"][0]["distance_m"] == pytest.approx(100, abs=1)
    within = client.get("/api/stations", params={"lat": CENTER[0], "lon": CENTER[1], "radius_m": 500}).json()
    assert within["count"] == 2


@pytest.mark.parametrize("params, fields", [
    ({"lat": 37.5}, ["lon"]),
    ({"lon": 126.9}, ["lat"]),
    ({"radius_m": 500}, ["lat", "lon"]),
    ({"lat": 40, "lon": 126.9}, ["lat"]),
])
def test_stations_invalid_params(make_client, test_database_url, params, fields):
    seed(test_database_url)
    response = make_client().get("/api/stations", params=params)
    assert response.status_code == 422 and response.json()["error"]["fields"] == fields


# GET /api/stations/nearby

def nearby(client, **params):
    return client.get("/api/stations/nearby",
                      params={"lat": CENTER[0], "lon": CENTER[1], "target_at": H2, **params})


def test_nearby_merges_db_and_ai(make_client, test_database_url):
    seed(test_database_url)
    fake = FakeAI()
    body = nearby(make_client(fake), radius_m=1000).json()
    assert (body["base_at"], body["target_at"], body["radius_m"], body["ai_available"], body["count"]) == \
        ("2026-11-04T05:00:00Z", H2, 1000, True, 3)
    assert [i["station_id"] for i in body["items"]] == ["ST-2", "ST-10", "ST-3"]  # 거리순
    first = body["items"][0]
    assert first == {"station_id": "ST-2", "name": "ST-2 대여소", "latitude": first["latitude"],
                     "longitude": CENTER[1], "distance_m": first["distance_m"], "latest_inventory": 4,
                     "latest_inventory_received_at": "2026-11-04T05:39:30Z", "inventory_fresh": True,
                     "risk_probability": pytest.approx(0.2), "is_risk": False, "reason": None}
    assert fake.requests == [{"station_ids": ["ST-2", "ST-10", "ST-3"], "horizons_hours": [2]}]


def test_nearby_debug(make_client, test_database_url):
    seed(test_database_url)
    item = nearby(make_client(), debug="true").json()["items"][0]
    assert item["debug"] == {"model_id": "m-1", "input_group": "C", "latest_inventory_age_seconds": 30}


def test_nearby_when_ai_is_down_uses_db_inventory(make_client, test_database_url):
    seed(test_database_url, received_at=NOW - timedelta(minutes=10))  # 600초 전 → 신선하지 않음
    response = nearby(make_client(FakeAI(down=True)))
    assert response.status_code == 200
    body = response.json()
    assert body["ai_available"] is False and body["count"] == 2
    first = body["items"][0]
    assert (first["latest_inventory"], first["inventory_fresh"], first["risk_probability"], first["reason"]) == \
        (0, False, None, "ai_unavailable")  # 실제 0대는 0으로 유지
    assert first["latest_inventory_received_at"] == "2026-11-04T05:30:00Z"


def test_nearby_empty_radius_does_not_call_ai(make_client, test_database_url):
    seed(test_database_url)
    fake = FakeAI()
    body = nearby(make_client(fake), radius_m=50).json()
    assert body["count"] == 0 and body["items"] == [] and fake.requests == []


def test_nearby_expired_target_is_409(make_client, test_database_url):
    seed(test_database_url)
    response = nearby(make_client(), target_at="2026-11-04T05:00:00Z")
    assert response.status_code == 409
    assert response.json()["error"]["supported_targets"] == [H1, H2, "2026-11-04T08:00:00Z"]


def test_nearby_ai_on_next_hour_is_409(make_client, test_database_url):
    seed(test_database_url)
    response = nearby(make_client(FakeAI(base=BASE + timedelta(hours=1))))
    assert response.status_code == 409


def test_nearby_requires_location_and_target(make_client, test_database_url):
    seed(test_database_url)
    response = make_client().get("/api/stations/nearby", params={"lat": CENTER[0]})
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == ["lon", "target_at"]


# POST /api/predictions

def predictions(client, body, debug=False):
    return client.post("/api/predictions" + ("?debug=true" if debug else ""), json=body)


def test_predictions_keep_request_order_and_unknown_ids(make_client, test_database_url):
    seed(test_database_url)
    body = predictions(make_client(), {"station_ids": ["ST-3", "ST-404", "ST-3", "ST-2"], "target_at": H1}).json()
    assert body["base_at"] == "2026-11-04T05:00:00Z" and body["ai_available"] is True
    assert [i["station_id"] for i in body["items"]] == ["ST-3", "ST-404", "ST-2"]  # 중복 제거, 순서 유지
    unknown = body["items"][1]
    assert (unknown["name"], unknown["latitude"], unknown["reason"], unknown["inventory_fresh"]) == \
        (None, None, "station_unknown_or_ineligible", False)
    assert body["items"][0]["target_at"] == H1 and "distance_m" not in body["items"][0]


def test_predictions_all_targets(make_client, test_database_url):
    seed(test_database_url)
    body = predictions(make_client(), {"station_ids": ["ST-2", "ST-3"], "all_targets": True}).json()
    assert [(i["station_id"], i["target_at"]) for i in body["items"]] == [
        ("ST-2", H1), ("ST-2", H2), ("ST-2", "2026-11-04T08:00:00Z"),
        ("ST-3", H1), ("ST-3", H2), ("ST-3", "2026-11-04T08:00:00Z")]
    assert [i["risk_probability"] for i in body["items"][:3]] == pytest.approx([0.1, 0.2, 0.3])


def test_predictions_when_ai_is_down(make_client, test_database_url):
    seed(test_database_url)
    body = predictions(make_client(FakeAI(down=True)), {"station_ids": ["ST-2", "ST-404"], "target_at": H1}).json()
    assert body["ai_available"] is False
    assert [(i["latest_inventory"], i["reason"]) for i in body["items"]] == [(0, "ai_unavailable"),
                                                                          (None, "ai_unavailable")]


@pytest.mark.parametrize("body, fields", [
    ({"station_ids": ["ST-2"]}, ["target_at", "all_targets"]),
    ({"station_ids": ["ST-2"], "target_at": H1, "all_targets": True}, ["target_at", "all_targets"]),
    ({"station_ids": [], "target_at": H1}, ["station_ids"]),
    ({"station_ids": [f"ST-{i}" for i in range(101)], "target_at": H1}, ["station_ids"]),
    ({"station_ids": ["ST-2"], "target_at": "2026-11-04T15:00:00"}, ["target_at"]),
])
def test_predictions_invalid_body(make_client, test_database_url, body, fields):
    seed(test_database_url)
    response = predictions(make_client(), body)
    assert response.status_code == 422 and response.json()["error"]["fields"] == fields
