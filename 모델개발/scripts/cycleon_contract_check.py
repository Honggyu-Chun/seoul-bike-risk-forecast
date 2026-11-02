"""Check/export OpenAPI and synthetic examples without model/data imports."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from cycleon.api import ai_app, backend_app
from cycleon.common import ROOT, save_json
from cycleon.contracts import (StatusResponse, PredictionResponse, RecommendationResponse,
                              Station, Observation, Forecast, Page)
from cycleon.mock_api import MockEngine, MockStore, mock_app

TOKEN = "contract-test-only-" + "x" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN}
REQUEST = {"station_ids": ["ST-MOCK-1", "ST-MOCK-2", "ST-UNKNOWN"], "horizons_hours": [1, 2, 3]}
RECOMMEND = {"latitude": 37.5665, "longitude": 126.978, "horizon_hours": 1, "radius_m": 500}


def exportable_assets():
    engine = MockEngine()
    store = MockStore()
    examples = {"_meta": {"kind": "synthetic_fixture", "not_model_performance": True,
                          "fixed_clock_KST": "2026-10-29T14:40:00+09:00"},
                "status_response": engine.status(), "prediction_request": REQUEST,
                "prediction_response": engine.predict(REQUEST["station_ids"], REQUEST["horizons_hours"]),
                "recommendation_request": RECOMMEND,
                "recommendation_policy_pending": engine.recommend(37.5665, 126.978, 1, 500),
                "recommendation_demo_only": MockEngine("recommendation-demo").recommend(37.5665, 126.978, 1, 500),
                "status_not_ready": MockEngine("not-ready").status(),
                "prediction_not_ready": MockEngine("not-ready").predict(["ST-MOCK-1"], [1]),
                "backend_station_page": store.page("stations"),
                "backend_observation_page": store.page("observations"),
                "backend_forecast_page": store.page("forecasts")}
    operations = [
        ("AI status", "GET", "{{ai_base_url}}/v1/status", None),
        ("AI predictions", "POST", "{{ai_base_url}}/v1/predictions", REQUEST),
        ("AI recommendations", "POST", "{{ai_base_url}}/v1/recommendations", RECOMMEND),
        *[("Backend " + table, "GET", "{{backend_base_url}}/internal/ai-data/v1/" + table + "?cursor=0&limit=1", None)
          for table in ("stations", "observations", "forecasts")],
    ]
    collection = {"info": {"name": "CycleOn v1 collaboration (mock defaults)",
                           "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                           "description": "Draft. Defaults target local synthetic APIs. token is deliberately empty."},
                  "variable": [{"key": "ai_base_url", "value": "http://127.0.0.1:18011"},
                               {"key": "backend_base_url", "value": "http://127.0.0.1:18012"},
                               {"key": "token", "value": ""}],
                  "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]},
                  "item": []}
    for name, method, url, body in operations:
        request = {"method": method, "url": url, "header": []}
        if body is not None:
            request["header"] = [{"key": "Content-Type", "value": "application/json"}]
            request["body"] = {"mode": "raw", "raw": json.dumps(body, indent=2),
                               "options": {"raw": {"language": "json"}}}
        collection["item"].append({"name": name, "request": request})
    return {ROOT / "docs/cycleon_ai_openapi.json": ai_app(engine, TOKEN).openapi(),
            ROOT / "docs/cycleon_backend_openapi.json": backend_app(store, TOKEN).openapi(),
            ROOT / "docs/cycleon/mock_examples.json": examples,
            ROOT / "docs/cycleon/CycleOn.postman_collection.json": collection}


def check_calls():
    checks = 0
    for scenario in ("policy-pending", "recommendation-demo", "not-ready"):
        with TestClient(mock_app("ai", TOKEN, scenario)) as client:
            assert client.get("/v1/status").status_code == 401
            status = client.get("/v1/status", headers=HEADERS)
            assert status.status_code == 200
            StatusResponse.model_validate(status.json())
            response = client.post("/v1/predictions", headers=HEADERS, json=REQUEST)
            assert response.status_code == 200
            result = PredictionResponse.model_validate(response.json())
            assert len(result.items) == 9
            assert all(item.risk_probability is not None or item.reason for item in result.items)
            assert all(item.risk_probability is None for item in result.items if item.station_id == "ST-UNKNOWN")
            rec = client.post("/v1/recommendations", headers=HEADERS, json=RECOMMEND)
            assert rec.status_code == 200
            recommendation = RecommendationResponse.model_validate(rec.json())
            if scenario == "policy-pending":
                assert recommendation.abstained and recommendation.reason == "recommendation_policy_not_selected"
            if scenario == "recommendation-demo":
                assert recommendation.recommended and not recommendation.abstained
            if scenario == "not-ready":
                assert all(item.risk_probability is None for item in result.items)
            assert client.post("/v1/predictions", headers=HEADERS, json={"station_ids": ["x"], "horizons_hours": [4]}).status_code == 422
            assert client.post("/v1/recommendations", headers=HEADERS, json=RECOMMEND | {"radius_m": 1501}).status_code == 422
            assert client.post("/v1/predictions", headers=HEADERS, json=REQUEST | {"target_at": "2025-01-01T00:00:00Z"}).status_code == 422
            checks += 8
    with TestClient(mock_app("backend", TOKEN)) as client:
        for table, schema in (("stations", Station), ("observations", Observation), ("forecasts", Forecast)):
            url = "/internal/ai-data/v1/" + table
            assert client.get(url).status_code == 401
            page = client.get(url, headers=HEADERS, params={"limit": 1}).json()
            Page[schema].model_validate(page)
            cursors = [item["cursor"] for item in page["items"]]
            watermark = page["watermark"]
            while page["has_more"]:
                page = client.get(url, headers=HEADERS, params={"limit": 1, "cursor": page["next_cursor"], "watermark": watermark}).json()
                Page[schema].model_validate(page)
                assert page["watermark"] == watermark
                cursors.extend(item["cursor"] for item in page["items"])
            assert len(cursors) == len(set(cursors))
            assert page["next_cursor"] == watermark
            checks += 3
    assert "numpy" not in sys.modules and "lightgbm" not in sys.modules and "catboost" not in sys.modules
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true", help="Regenerate tracked contract assets")
    args = parser.parse_args()
    assets = exportable_assets()
    for path, expected in assets.items():
        if args.export:
            save_json(path, expected)
        else:
            if not path.exists() or json.loads(path.read_text(encoding="utf-8")) != expected:
                raise SystemExit(f"Contract asset drift: {path.name}; run --export and review the diff")
    count = check_calls()
    print(json.dumps({"checks_passed": count, "assets_checked": len(assets),
                      "real_model_loaded": False, "real_provider_calls": 0,
                      "performance_measured": False}))


if __name__ == "__main__":
    main()
