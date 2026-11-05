"""AI 서비스 응답 중 백엔드가 읽는 필드만 정의한다. 모르는 필드는 무시한다(AI가 필드를 추가해도 깨지지 않게)."""
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class AIModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AIStatus(AIModel):
    status: Literal["ready", "not_ready"]
    base_at: AwareDatetime
    model_bundle_id: str | None = None
    model_stage: str | None = None
    last_precompute_at: AwareDatetime | None = None


class AIPrediction(AIModel):
    station_id: str
    horizon_hours: int
    target_at: AwareDatetime
    risk_probability: float | None = Field(None, ge=0, le=1)
    is_risk: bool | None = None
    model_id: str | None = None
    input_group: str | None = None
    reason: str | None = None
    latest_inventory: int | None = None
    latest_inventory_received_at: AwareDatetime | None = None
    latest_inventory_age_seconds: float | None = None


class AIPredictions(AIModel):
    base_at: AwareDatetime
    items: list[AIPrediction]


class AIRankedPrediction(AIPrediction):
    name: str
    latitude: float
    longitude: float
    distance_m: float


class AIRecommendation(AIModel):
    base_at: AwareDatetime
    horizon_hours: int
    radius_m: float
    recommended: AIRankedPrediction | None
    alternatives: list[AIRankedPrediction] = []
    abstained: bool
    reason: str | None = None
    nearby_count: int
    eligible_count: int
