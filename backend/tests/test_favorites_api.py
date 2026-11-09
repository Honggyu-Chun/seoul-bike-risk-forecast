"""회원 즐겨찾기 API 테스트 (/api/me/favorites...).

대여소 수집분과 가짜 AI는 test_stations_api의 것을 쓴다(현재 2026-11-04 14:40 KST, 대여소 ST-2·3·4·10은
운영 중, ST-99는 좌표 미확인, ST-404는 DB에 없음). 한도 확인용으로 ST-100번대 대여소를 더 넣는다.
"""
import asyncio
import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_now
from app.clients.ai import AIClient
from app.collectors.seoul_bike import CaptureData
from app.core.config import Settings
from app.db.session import make_engine, make_sessionmaker
from app.main import create_app
from app.repositories.inventory import save_capture
from app.services.accounts import COOKIE_NAME
from tests.test_stations_api import CENTER, H1, NOW, STATIONS, FakeAI, row

ORIGIN = {"Origin": "http://localhost:5173"}
PASSWORD = "bike-ride-2026"
EXTRA = [f"ST-{100 + i}" for i in range(22)]   # 한도(20) 확인용 대여소


def seed(url: str) -> None:
    """운영 대여소 4곳 + 좌표 미확인 1곳 + 한도 확인용 22곳을 한 수집분으로 저장하고, 회원 관련 테이블을 비운다."""
    at = NOW - timedelta(seconds=60)
    rows = [row(*s, at) for s in STATIONS] + [row("ST-99", 0, 5, at, verified=False)]
    rows += [row(sid, 3000 + i, 3, at) for i, sid in enumerate(EXTRA)]
    data = CaptureData(capture_id=str(uuid.uuid4()), started_at=at - timedelta(seconds=1),
                       completed_at=at + timedelta(seconds=1), rows=rows)

    async def go():
        engine = make_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("TRUNCATE observations, stations, captures"))
                await conn.execute(text("TRUNCATE users, sessions, login_attempts, favorites RESTART IDENTITY CASCADE"))
            async with make_sessionmaker(engine)() as session:
                await save_capture(session, data)
        finally:
            await engine.dispose()
    asyncio.run(go())


def count_favorites(url: str) -> int:
    async def go():
        engine = make_engine(url)
        try:
            async with engine.connect() as conn:
                return await conn.scalar(text("SELECT count(*) FROM favorites"))
        finally:
            await engine.dispose()
    return asyncio.run(go())


@pytest.fixture
def make_client(test_database_url):
    """대여소를 심고 앱을 연 뒤, 회원 한 명(rider_01)으로 로그인한 TestClient를 돌려준다."""
    seed(test_database_url)
    opened = []

    def build(fake=None, login=True):
        settings = Settings(_env_file=None, DATABASE_URL=test_database_url, CYCLEON_SERVICE_TOKEN="t" * 32,
                            CYCLEON_BCRYPT_ROUNDS=4)
        app = create_app(settings, AIClient(settings, transport=httpx.MockTransport(fake or FakeAI())))
        app.dependency_overrides[get_now] = lambda: NOW
        client = TestClient(app, raise_server_exceptions=False)
        opened.append(client.__enter__())
        if login:
            assert signup(client, "rider_01").status_code == 201
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


def signup(client, login_id):
    return client.post("/api/auth/signup", json={"login_id": login_id, "password": PASSWORD}, headers=ORIGIN)


def add(client, station_id, alias=None):
    return client.post("/api/me/favorites", json={"station_id": station_id, "alias": alias}, headers=ORIGIN)


def favorites(client, **params):
    return client.get("/api/me/favorites", params=params)


def ids(body):
    return [item["station_id"] for item in body["items"]]


# 목록·추가

def test_requires_login(make_client):
    client = make_client(login=False)
    assert favorites(client).status_code == 401
    assert add(client, "ST-3").status_code == 401


def test_add_and_list_without_predictions(client):
    created = add(client, "ST-3", alias="  집 앞 ")
    assert created.status_code == 201
    assert created.json() == {"station_id": "ST-3", "alias": "집 앞", "position": 0, "name": "ST-3 대여소",
                              "latitude": pytest.approx(CENTER[0] + 900 / 111_195), "longitude": CENTER[1]}
    assert add(client, "ST-2").json()["position"] == 1

    body = favorites(client).json()
    assert body == {"count": 2, "limit": 20, "items": body["items"]}   # 예측 없이 부르면 base_at 등이 없음
    assert ids(body) == ["ST-3", "ST-2"]
    assert "target_at" not in body["items"][0] and "latest_inventory" not in body["items"][0]


def test_add_existing_returns_200_unchanged(client):
    add(client, "ST-3", alias="집 앞")
    again = add(client, "ST-3", alias="다른 이름")
    assert again.status_code == 200 and again.json()["alias"] == "집 앞"
    assert favorites(client).json()["count"] == 1


@pytest.mark.parametrize("station_id", ["ST-404", "ST-99"])
def test_add_requires_operating_station(client, station_id):
    response = add(client, station_id)
    assert response.status_code == 404 and response.json()["error"]["code"] == "station_not_found"


def test_limit_20(client):
    for sid in EXTRA[:20]:
        assert add(client, sid).status_code == 201
    response = add(client, EXTRA[20])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "favorites_limit" and response.json()["error"]["limit"] == 20
    assert add(client, EXTRA[0]).status_code == 200   # 이미 있는 것은 한도와 상관없이 200


@pytest.mark.parametrize("alias", ["a" * 21])
def test_alias_too_long(client, alias):
    response = add(client, "ST-3", alias=alias)
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["alias"]


# 예측 붙이기

def test_list_with_predictions(client):
    add(client, "ST-3", alias="집 앞")
    add(client, "ST-2")
    body = favorites(client, target_at=H1).json()
    assert (body["base_at"], body["ai_available"], body["count"]) == ("2026-11-04T05:00:00Z", True, 2)
    first = body["items"][0]
    assert first == {"station_id": "ST-3", "alias": "집 앞", "position": 0, "name": "ST-3 대여소",
                     "latitude": first["latitude"], "longitude": CENTER[1], "target_at": H1, "latest_inventory": 4,
                     "latest_inventory_received_at": "2026-11-04T05:39:30Z", "inventory_fresh": True,
                     "risk_probability": pytest.approx(0.1), "is_risk": False, "reason": None}
    assert ids(body) == ["ST-3", "ST-2"]


def test_list_when_ai_is_down_uses_db_inventory(make_client):
    client = make_client(FakeAI(down=True))
    add(client, "ST-2")
    add(client, "ST-3")
    body = favorites(client, target_at=H1).json()
    assert body["ai_available"] is False
    assert [(i["latest_inventory"], i["reason"], i["risk_probability"]) for i in body["items"]] == [
        (0, "ai_unavailable", None), (7, "ai_unavailable", None)]   # 실제 0대는 0으로


def test_list_with_expired_target_is_409(client):
    assert favorites(client, target_at="2026-11-04T05:00:00Z").status_code == 409   # 빈 목록이어도
    add(client, "ST-3")
    response = favorites(client, target_at="2026-11-04T05:00:00Z")
    assert response.status_code == 409 and "supported_targets" in response.json()["error"]


def test_empty_list_does_not_call_ai(make_client):
    fake = FakeAI()
    client = make_client(fake)
    body = favorites(client, target_at=H1).json()
    assert body == {"count": 0, "limit": 20, "items": []} and fake.requests == []


# 별칭·순서·삭제

def test_rename(client):
    add(client, "ST-3", alias="집 앞")
    url = "/api/me/favorites/ST-3"
    assert client.patch(url, json={"alias": "회사"}, headers=ORIGIN).json()["alias"] == "회사"
    assert client.patch(url, json={"alias": ""}, headers=ORIGIN).json()["alias"] is None
    missing = client.patch("/api/me/favorites/ST-2", json={"alias": "x"}, headers=ORIGIN)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "favorite_not_found"


def test_reorder(client):
    for sid in ("ST-3", "ST-2", "ST-10"):
        add(client, sid)
    url = "/api/me/favorites/order"
    body = client.put(url, json={"station_ids": ["ST-10", "ST-3", "ST-2"]}, headers=ORIGIN).json()
    assert ids(body) == ["ST-10", "ST-3", "ST-2"] and [i["position"] for i in body["items"]] == [0, 1, 2]
    assert ids(favorites(client).json()) == ["ST-10", "ST-3", "ST-2"]
    for bad in (["ST-10", "ST-3"], ["ST-10", "ST-3", "ST-2", "ST-4"], ["ST-10", "ST-3", "ST-3"]):
        response = client.put(url, json={"station_ids": bad}, headers=ORIGIN)
        assert response.status_code == 422 and response.json()["error"]["fields"] == ["station_ids"]


def test_remove_then_add_goes_last(client):
    for sid in ("ST-3", "ST-2"):
        add(client, sid)
    assert client.delete("/api/me/favorites/ST-3", headers=ORIGIN).status_code == 204
    assert client.delete("/api/me/favorites/ST-3", headers=ORIGIN).status_code == 204   # 이미 없어도 204
    add(client, "ST-3")
    assert ids(favorites(client).json()) == ["ST-2", "ST-3"]


# 게스트 즐겨찾기 옮기기

def test_import(client):
    add(client, "ST-3")
    response = client.post("/api/me/favorites/import",
                           json={"station_ids": ["ST-3", "ST-2", "ST-404", "ST-2", "ST-99", "ST-10"]}, headers=ORIGIN)
    assert response.json() == {"added": ["ST-2", "ST-10"], "skipped_existing": ["ST-3"],
                               "skipped_unknown": ["ST-404", "ST-99"], "skipped_over_limit": [], "count": 3}
    assert ids(favorites(client).json()) == ["ST-3", "ST-2", "ST-10"]


def test_import_over_limit(client):
    for sid in EXTRA[:19]:
        add(client, sid)
    body = client.post("/api/me/favorites/import", json={"station_ids": ["ST-2", "ST-3", "ST-4"]},
                       headers=ORIGIN).json()
    assert (body["added"], body["skipped_over_limit"], body["count"]) == (["ST-2"], ["ST-3", "ST-4"], 20)


# 다른 사용자·보안

def test_other_users_favorites_are_invisible(client, test_database_url):
    add(client, "ST-3", alias="집 앞")
    first = client.cookies.get(COOKIE_NAME)
    client.cookies.clear()
    signup(client, "rider_02")
    assert favorites(client).json()["count"] == 0
    assert client.patch("/api/me/favorites/ST-3", json={"alias": "x"}, headers=ORIGIN).status_code == 404
    assert client.delete("/api/me/favorites/ST-3", headers=ORIGIN).status_code == 204
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, first)
    assert favorites(client).json()["items"][0]["alias"] == "집 앞"   # 다른 사람의 삭제에 영향 없음
    assert count_favorites(test_database_url) == 1


def test_account_deletion_removes_favorites(client, test_database_url):
    add(client, "ST-3")
    assert client.request("DELETE", "/api/me", json={"password": PASSWORD}, headers=ORIGIN).status_code == 204
    assert count_favorites(test_database_url) == 0


def test_changes_require_trusted_origin(client):
    evil = {"Origin": "http://evil.example"}
    assert client.post("/api/me/favorites", json={"station_id": "ST-3"}, headers=evil).status_code == 403
    assert client.post("/api/me/favorites/import", json={"station_ids": ["ST-3"]}, headers=evil).status_code == 403
    assert favorites(client).json()["count"] == 0
