from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import TargetNotSupported
from app.core.time import KST, base_at
from app.main import create_app
from datetime import datetime


def make_client():
    app = create_app(Settings(_env_file=None))

    @app.get("/_test/number")
    def number(lat: float):
        return {"lat": lat}

    @app.get("/_test/expired")
    def expired():
        raise TargetNotSupported([base_at(datetime(2026, 11, 4, 14, 40, tzinfo=KST))])

    @app.get("/_test/boom")
    def boom():
        raise RuntimeError("secret detail")

    return TestClient(app, raise_server_exceptions=False)


def test_validation_error_envelope():
    body = make_client().get("/_test/number", params={"lat": "abc"}).json()
    assert body == {"error": {"code": "invalid_request", "message": body["error"]["message"], "fields": ["lat"]}}


def test_target_not_supported_envelope():
    response = make_client().get("/_test/expired")
    assert response.status_code == 409
    assert response.json()["error"]["supported_targets"] == ["2026-11-04T05:00:00Z"]


def test_not_found_envelope():
    response = make_client().get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unexpected_error_hides_detail():
    response = make_client().get("/_test/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret" not in response.text


def test_cors_allows_dev_frontend_only():
    client = make_client()
    preflight = {"Access-Control-Request-Method": "GET"}
    allowed = client.options("/health", headers={"Origin": "http://localhost:5173", **preflight})
    denied = client.options("/health", headers={"Origin": "http://evil.example", **preflight})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers
