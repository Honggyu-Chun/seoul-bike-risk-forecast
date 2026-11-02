"""Verify running loopback mock APIs over HTTP, without models or provider calls."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from cycleon.contracts import StatusResponse, PredictionResponse, RecommendationResponse, Page, Station, Observation, Forecast


def local_url(value):
    try:
        p = urlsplit(value)
        if p.scheme != "http" or p.hostname not in {"127.0.0.1", "localhost", "::1"} or p.username or p.password or p.query or p.fragment or p.path not in {"", "/"}:
            raise ValueError
        if p.port is None or p.port < 1024 or p.port in {8000, 8001, 8002}:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError("Use an explicit loopback HTTP mock port outside 8000/8001/8002") from None
    return value.rstrip("/")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ai-url", type=local_url, default="http://127.0.0.1:18011")
    parser.add_argument("--backend-url", type=local_url)
    args = parser.parse_args()
    token = os.environ.get("CYCLEON_MOCK_TOKEN", "")
    if len(token) < 32:
        parser.error("Set CYCLEON_MOCK_TOKEN to the same local mock token (32+ characters)")
    checks = 0
    with httpx.Client(timeout=10, trust_env=False) as client:
        assert client.get(args.ai_url + "/v1/status").status_code == 401
        checks += 1
        headers = {"Authorization": "Bearer " + token}
        def get(url):
            response = client.get(url, headers=headers)
            response.raise_for_status()
            return response.json()
        status = StatusResponse.model_validate(get(args.ai_url + "/v1/status"))
        assert status.model_stage == "mock_no_model_no_live_data", "Only the synthetic mock is accepted"
        assert len(status.supported_targets) == 3
        checks += 2
        response = client.post(args.ai_url + "/v1/predictions", headers=headers,
                               json={"station_ids": ["ST-MOCK-1", "ST-UNKNOWN"], "horizons_hours": [1, 2, 3]})
        response.raise_for_status()
        prediction = PredictionResponse.model_validate(response.json())
        assert len(prediction.items) == 6
        assert all(x.risk_probability is None and x.reason for x in prediction.items if x.station_id == "ST-UNKNOWN")
        checks += 2
        response = client.post(args.ai_url + "/v1/recommendations", headers=headers,
                               json={"latitude": 37.5665, "longitude": 126.978, "horizon_hours": 1, "radius_m": 500})
        response.raise_for_status()
        rec = RecommendationResponse.model_validate(response.json())
        assert rec.abstained or rec.recommended is not None
        checks += 1
        assert client.post(args.ai_url + "/v1/predictions", headers=headers,
                           json={"station_ids": ["x"], "horizons_hours": [4]}).status_code == 422
        checks += 1
        if args.backend_url:
            for table, schema in [("stations", Station), ("observations", Observation), ("forecasts", Forecast)]:
                page = Page[schema].model_validate(get(args.backend_url + "/internal/ai-data/v1/" + table + "?limit=1"))
                assert page.items
                checks += 1
    print(json.dumps({"http_checks_passed": checks, "model_stage": status.model_stage,
                      "recommendation_abstained": rec.abstained, "real_model_loaded": False,
                      "real_provider_calls": 0}))


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, AssertionError, ValueError) as error:
        # Do not print request headers, credential-bearing URLs or server bodies.
        raise SystemExit("Mock HTTP check failed: " + type(error).__name__ + ". Check server, scenario and token.") from None
