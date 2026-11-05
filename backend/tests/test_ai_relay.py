"""AI 중계 테스트. 가짜 AI(httpx.MockTransport)로 실제 네트워크 없이 확인한다.

현재 시각은 2026-11-04 14:40 KST로 고정. 기준 정각 05:00Z, 이용 시각 06:00Z·07:00Z·08:00Z.
"""
import asyncio
import json
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_now
from app.clients.ai import AIClient
from app.core.config import Settings
from app.core.time import KST
from app.main import create_app

TOKEN = "t" * 32
NOW = datetime(2026, 11, 4, 14, 40, tzinfo=KST)
AI_BASE = "2026-11-04T05:00:00.000000+00:00"
TARGET_H2 = "2026-11-04T07:00:00Z"


def ai_status(base=AI_BASE):
    return {"service": "CycleOn", "status": "ready", "server_at": "2026-11-04T05:40:00.000000+00:00",
            "base_at": base, "supported_targets": [], "model_bundle_id": "bundle-1",
            "model_stage": "development", "last_precompute_at": "2026-11-04T05:00:20.000000+00:00",
            "precompute_error": None, "data": {}, "latest_inventory_age_limit_seconds": 300,
            "time_policy": "x"}


def ranked(station_id, probability, distance):
    return {"station_id": station_id, "horizon_hours": 2, "target_at": "2026-11-04T07:00:00.000000+00:00",
            "risk_probability": probability, "is_risk": probability >= .5, "model_id": "m", "input_group": "C",
            "reason": None, "latest_inventory": 3, "latest_inventory_received_at": "2026-11-04T05:39:30.000000+00:00",
            "latest_inventory_age_seconds": 30, "latest_inventory_observed_at": None,
            "name": f"대여소 {station_id}", "latitude": 37.5665, "longitude": 126.978,
            "distance_m": distance, "score": probability + .1}


def ai_recommendation(body, base=AI_BASE, picks=()):
    return {"base_at": base, "requested_at": "2026-11-04T05:40:00.000000+00:00",
            "horizon_hours": body["horizon_hours"], "radius_m": body["radius_m"],
            "distance_type": "straight_line", "automatic_radius_expansion": False,
            "recommended": picks[0] if picks else None, "alternatives": list(picks[1:3]),
            "abstained": not picks, "reason": None if picks else "recommendation_policy_not_selected",
            "nearby_count": 4, "eligible_count": len(picks)}


class FakeAI:
    """경로별 응답을 정해 두고, 받은 요청을 기록한다."""

    def __init__(self, status=None, recommendation=None, error=None):
        self.requests: list[httpx.Request] = []
        self.status = status or (lambda: httpx.Response(200, json=ai_status()))
        self.recommendation = recommendation or (lambda body: httpx.Response(200, json=ai_recommendation(body)))
        self.error = error

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        if request.url.path == "/v1/status":
            return self.status()
        if request.url.path == "/v1/recommendations":
            return self.recommendation(json.loads(request.content))
        if request.url.path == "/v1/predictions":
            body = json.loads(request.content)
            return httpx.Response(200, json={"base_at": AI_BASE, "requested_at": AI_BASE, "items": [
                {"station_id": sid, "horizon_hours": h, "target_at": AI_BASE}
                for sid in body["station_ids"] for h in body["horizons_hours"]]})
        return httpx.Response(404)


def make_settings(**env):
    return Settings(_env_file=None, CYCLEON_SERVICE_TOKEN=TOKEN, **env)


def make_client(fake: FakeAI, **env) -> TestClient:
    settings = make_settings(**env)
    app = create_app(settings, AIClient(settings, transport=httpx.MockTransport(fake)))
    app.dependency_overrides[get_now] = lambda: NOW
    return TestClient(app, raise_server_exceptions=False)


def recommend(client, **overrides):
    body = {"latitude": 37.5665, "longitude": 126.978, "target_at": TARGET_H2, **overrides}
    return client.post("/api/recommendations", json=body)


# GET /api/status

def test_status_uses_backend_clock():
    fake = FakeAI()
    body = make_client(fake).get("/api/status").json()
    assert body == {"ai_available": True, "status": "ready", "server_at": "2026-11-04T05:40:00Z",
                    "base_at": "2026-11-04T05:00:00Z",
                    "supported_targets": ["2026-11-04T06:00:00Z", "2026-11-04T07:00:00Z", "2026-11-04T08:00:00Z"]}
    assert fake.requests[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_status_debug_only_when_enabled():
    debug = make_client(FakeAI()).get("/api/status", params={"debug": "true"}).json()["debug"]
    assert debug == {"model_stage": "development", "model_bundle_id": "bundle-1",
                     "last_precompute_at": "2026-11-04T05:00:20Z"}
    disabled = make_client(FakeAI(), CYCLEON_DEBUG_ENABLED="false").get("/api/status", params={"debug": "true"})
    assert "debug" not in disabled.json()


def test_status_when_ai_is_down():
    response = make_client(FakeAI(error=httpx.ConnectError("refused"))).get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["ai_available"] is False and body["status"] is None
    assert body["supported_targets"][0] == "2026-11-04T06:00:00Z"


def test_status_ai_auth_failure_is_502():
    response = make_client(FakeAI(status=lambda: httpx.Response(401, json={"detail": "x"}))).get("/api/status")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "ai_auth_failed"


# POST /api/recommendations

def test_recommendation_abstained():
    fake = FakeAI()
    response = recommend(make_client(fake))
    assert response.status_code == 200
    assert response.json() == {"base_at": "2026-11-04T05:00:00Z", "target_at": TARGET_H2, "radius_m": 500.0,
                               "abstained": True, "reason": "recommendation_policy_not_selected",
                               "recommended": None, "alternatives": []}
    assert json.loads(fake.requests[0].content) == {"latitude": 37.5665, "longitude": 126.978,
                                                    "horizon_hours": 2, "radius_m": 500.0}


def test_recommendation_keeps_order_and_trims_fields():
    picks = (ranked("ST-2", .3, 120.0), ranked("ST-1", .1, 400.0))
    fake = FakeAI(recommendation=lambda body: httpx.Response(200, json=ai_recommendation(body, picks=picks)))
    body = recommend(make_client(fake), radius_m=800, target_at="2026-11-04T17:00:00+09:00").json()
    assert body["target_at"] == "2026-11-04T08:00:00Z"
    assert body["radius_m"] == 800
    assert body["recommended"] == {"station_id": "ST-2", "name": "대여소 ST-2", "latitude": 37.5665,
                                   "longitude": 126.978, "distance_m": 120.0, "latest_inventory": 3,
                                   "latest_inventory_received_at": "2026-11-04T05:39:30Z",
                                   "risk_probability": .3, "is_risk": False}
    assert [s["station_id"] for s in body["alternatives"]] == ["ST-1"]
    assert json.loads(fake.requests[0].content)["horizon_hours"] == 3


def test_recommendation_debug():
    body = make_client(FakeAI()).post("/api/recommendations?debug=true", json={
        "latitude": 37.5665, "longitude": 126.978, "target_at": TARGET_H2}).json()
    assert body["debug"] == {"nearby_count": 4, "eligible_count": 0}


def test_recommendation_expired_target_is_409_without_calling_ai():
    fake = FakeAI()
    response = recommend(make_client(fake), target_at="2026-11-04T05:00:00Z")
    assert response.status_code == 409
    assert response.json()["error"]["supported_targets"][0] == "2026-11-04T06:00:00Z"
    assert fake.requests == []


@pytest.mark.parametrize("overrides, field", [
    ({"radius_m": 2000}, "radius_m"),
    ({"latitude": 40}, "latitude"),
    ({"target_at": "2026-11-04T16:00:00"}, "target_at"),
])
def test_recommendation_invalid_input(overrides, field):
    response = recommend(make_client(FakeAI()), **overrides)
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == [field]


@pytest.mark.parametrize("fake, status, code", [
    (FakeAI(error=httpx.ConnectError("refused")), 503, "ai_unavailable"),
    (FakeAI(error=httpx.ReadTimeout("slow")), 503, "ai_unavailable"),
    (FakeAI(recommendation=lambda body: httpx.Response(401)), 502, "ai_auth_failed"),
    (FakeAI(recommendation=lambda body: httpx.Response(422, json={"detail": []})), 502, "ai_error"),
    (FakeAI(recommendation=lambda body: httpx.Response(500, text="oops")), 502, "ai_error"),
    (FakeAI(recommendation=lambda body: httpx.Response(200, json={"unexpected": True})), 502, "ai_error"),
])
def test_recommendation_ai_failures(fake, status, code):
    response = recommend(make_client(fake))
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_ai_answering_for_next_hour_is_409():
    # 백엔드는 14시 기준으로 h를 계산했는데 AI에는 15시 이후에 도착한 경우
    later = "2026-11-04T06:00:00.000000+00:00"
    fake = FakeAI(recommendation=lambda body: httpx.Response(200, json=ai_recommendation(body, base=later)))
    response = recommend(make_client(fake))
    assert response.status_code == 409
    assert response.json()["error"]["supported_targets"][0] == "2026-11-04T07:00:00Z"


def test_ai_clock_behind_is_502_unless_check_disabled():
    earlier = "2026-10-29T05:00:00.000000+00:00"  # 시계가 고정된 목업
    fake = FakeAI(recommendation=lambda body: httpx.Response(200, json=ai_recommendation(body, base=earlier)))
    assert recommend(make_client(fake)).status_code == 502
    relaxed = recommend(make_client(fake, CYCLEON_AI_CLOCK_CHECK="false"))
    assert relaxed.status_code == 200
    assert relaxed.json()["base_at"] == "2026-11-04T05:00:00Z"


# AIClient.predict

def test_predict_splits_into_chunks_of_500():
    fake = FakeAI()
    client = AIClient(make_settings(), transport=httpx.MockTransport(fake))
    ids = [f"ST-{i}" for i in range(1200)] + ["ST-0"]

    async def run():
        try:
            return await client.predict(ids, [1])
        finally:
            await client.aclose()

    items = asyncio.run(run())
    assert [len(json.loads(r.content)["station_ids"]) for r in fake.requests] == [500, 500, 200]
    assert [item.station_id for item in items] == [f"ST-{i}" for i in range(1200)]
