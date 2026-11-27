"""예측 확인 기록 API 테스트 (POST /api/me/views).

대여소·가짜 AI·로그인 클라이언트는 test_favorites_api의 것을 쓴다(현재 2026-11-04 14:40 KST, 기준 정각 14:00 KST,
ST-2·3·4·10 운영 중, ST-99 좌표 미확인, ST-404 DB에 없음). 가짜 AI의 확률은 0.1 × 간격, is_risk=false.
"""
import asyncio
import json

import httpx
from sqlalchemy import text

from app.db.session import make_engine
from tests.test_favorites_api import ORIGIN, PASSWORD, client, make_client, signup  # noqa: F401 (픽스처)
from tests.test_stations_api import H1, H2, FakeAI

H1_KST = "2026-11-04T15:00:00+09:00"   # H1과 같은 시각, 다른 표기


def record(client, station_id="ST-3", target_at=H1, headers=ORIGIN):
    return client.post("/api/me/views", json={"station_id": station_id, "target_at": target_at}, headers=headers)


def db_scalar(url: str, sql: str):
    async def go():
        engine = make_engine(url)
        try:
            async with engine.connect() as conn:
                return await conn.scalar(text(sql))
        finally:
            await engine.dispose()
    return asyncio.run(go())


def count_views(url: str) -> int:
    return db_scalar(url, "SELECT count(*) FROM prediction_views")


class NullProbabilityAI(FakeAI):
    """운영 중인 대여소인데도 AI가 확률을 주지 않는 경우(모델 입력 부족 등)."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = super().__call__(request)
        body = json.loads(response.content)
        for item in body["items"]:
            item.update(risk_probability=None, is_risk=None, reason="insufficient_history")
        return httpx.Response(200, json=body)


def test_requires_login(make_client):
    assert record(make_client(login=False)).status_code == 401


def test_record_saves_prediction_from_ai(make_client, test_database_url):
    fake = FakeAI()
    client = make_client(fake)
    response = record(client)
    assert response.status_code == 201
    body = response.json()
    assert body == {"recorded": True, "reason": None, "item": {
        "id": body["item"]["id"], "station_id": "ST-3", "station_name": "ST-3 대여소", "target_at": H1,
        "horizon_hours": 1, "risk_probability": 0.1, "is_risk": False, "outcome": None, "actual_inventory": None,
        "rented": None}}
    assert len(fake.requests) == 1 and fake.requests[0]["station_ids"] == ["ST-3"]
    assert count_views(test_database_url) == 1


def test_same_station_and_target_is_recorded_once(make_client, test_database_url):
    fake = FakeAI()
    client = make_client(fake)
    first = record(client).json()["item"]
    again = record(client, target_at=H1_KST)     # 표기가 달라도 같은 시각
    assert again.status_code == 200 and again.json()["item"] == first
    assert len(fake.requests) == 1                # 이미 있으면 AI를 다시 부르지 않음
    later = record(client, target_at=H2)
    assert later.status_code == 201 and later.json()["item"]["horizon_hours"] == 2
    assert later.json()["item"]["risk_probability"] == 0.2
    assert count_views(test_database_url) == 2


def test_unsupported_target_is_409(make_client, test_database_url):
    fake = FakeAI()
    response = record(make_client(fake), target_at="2026-11-04T05:00:00Z")
    assert response.status_code == 409 and "supported_targets" in response.json()["error"]
    assert fake.requests == [] and count_views(test_database_url) == 0


def test_station_must_be_operating(make_client, test_database_url):
    fake = FakeAI()
    client = make_client(fake)
    for station_id in ("ST-404", "ST-99"):
        response = record(client, station_id)
        assert response.status_code == 404 and response.json()["error"]["code"] == "station_not_found"
    assert fake.requests == [] and count_views(test_database_url) == 0


def test_ai_down_records_nothing(make_client, test_database_url):
    response = record(make_client(FakeAI(down=True)))
    assert response.status_code == 200
    assert response.json() == {"recorded": False, "reason": "ai_unavailable", "item": None}
    assert count_views(test_database_url) == 0


def test_null_probability_records_nothing(make_client, test_database_url):
    response = record(make_client(NullProbabilityAI()))
    assert response.status_code == 200
    assert response.json() == {"recorded": False, "reason": "insufficient_history", "item": None}
    assert count_views(test_database_url) == 0


def test_each_member_has_own_records(client, test_database_url):
    record(client)
    client.cookies.clear()
    signup(client, "rider_02")
    response = record(client)
    assert response.status_code == 201   # 다른 회원의 같은 대여소·이용 시각은 따로 기록
    assert count_views(test_database_url) == 2


def test_account_deletion_removes_records(client, test_database_url):
    record(client)
    assert client.request("DELETE", "/api/me", json={"password": PASSWORD}, headers=ORIGIN).status_code == 204
    assert count_views(test_database_url) == 0


def test_requires_trusted_origin(client, test_database_url):
    assert record(client, headers={"Origin": "http://evil.example"}).status_code == 403
    assert count_views(test_database_url) == 0


def test_location_is_never_stored(client, test_database_url):
    """위치·거리 칸이 없고, 요청에 위치를 붙여 보내도 무시된다(저장할 곳이 없음)."""
    columns = db_scalar(test_database_url, "SELECT string_agg(column_name, ',') FROM information_schema.columns "
                                           "WHERE table_name = 'prediction_views'").split(",")
    assert not [c for c in columns if any(word in c for word in ("lat", "lon", "distance", "location"))]
    response = client.post("/api/me/views", json={"station_id": "ST-3", "target_at": H1, "lat": 37.5, "lon": 127.0},
                           headers=ORIGIN)
    assert response.status_code == 201
