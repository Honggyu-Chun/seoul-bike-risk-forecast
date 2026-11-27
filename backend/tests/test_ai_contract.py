"""백엔드가 읽는 AI 응답 필드가 모델 명세(모델개발/docs/cycleon/team_model_openapi.json)에 있는지 확인한다.

응답 스키마(schemas/ai.py)는 읽는 필드만 정의하고 나머지는 무시한다. 모델이 필드 이름을 바꾸거나 지우면
실제 호출에서 502가 나기 전에 여기서 먼저 실패한다.
"""
import json

import pytest

from app.core.config import BACKEND_DIR
from app.schemas.ai import (AIPrediction, AIPredictions, AIRankedPrediction, AIRecommendation, AIServiceData,
                            AIStatus)

OPENAPI = BACKEND_DIR.parent / "모델개발" / "docs" / "cycleon" / "team_model_openapi.json"


@pytest.fixture(scope="module")
def model_schemas() -> dict:
    if not OPENAPI.exists():
        pytest.skip("model OpenAPI document is not available")
    return json.loads(OPENAPI.read_text(encoding="utf-8"))["components"]["schemas"]


@pytest.mark.parametrize("ours, theirs", [
    (AIStatus, "ServiceStatus"), (AIServiceData, "ServiceData"),
    (AIPredictions, "PredictionResponse"), (AIPrediction, "Prediction"),
    (AIRecommendation, "RecommendationResponse"), (AIRankedPrediction, "RankedPrediction"),
])
def test_read_fields_exist_in_model_response(model_schemas, ours, theirs):
    assert set(ours.model_fields) <= set(model_schemas[theirs]["properties"])
