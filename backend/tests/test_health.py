from fastapi.testclient import TestClient

from app.main import app


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}


def test_removed_internal_data_api_is_404():
    """모델 서비스(연구실 API)는 재고·이력·예보를 직접 모으므로 내부 데이터 API(/internal/ai-data/v1/*)는 지웠다."""
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + "t" * 32}
    for path in ("stations", "observations", "forecasts"):
        assert client.get(f"/internal/ai-data/v1/{path}", headers=headers).status_code == 404
