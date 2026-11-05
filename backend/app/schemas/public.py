"""프론트 공개 API 요청·응답. 필드는 'CycleOn 공개 API 명세 (프론트엔드용)'와 같게 유지한다.

debug 필드는 ?debug=true일 때만 채운다. 응답은 exclude_unset으로 내보내므로,
나머지 필드는 값이 null이어도 항상 명시적으로 넣어야 한다.
"""
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, PlainSerializer

from app.core.time import iso_utc

# 응답 시각은 모두 UTC "...Z"
UtcDatetime = Annotated[AwareDatetime, PlainSerializer(iso_utc, return_type=str)]


class StatusDebug(BaseModel):
    model_stage: str | None
    model_bundle_id: str | None
    last_precompute_at: UtcDatetime | None


class StatusResponse(BaseModel):
    ai_available: bool
    status: Literal["ready", "not_ready"] | None
    server_at: UtcDatetime
    base_at: UtcDatetime
    supported_targets: list[UtcDatetime]
    debug: StatusDebug | None = None


class RecommendationRequest(BaseModel):
    latitude: float = Field(ge=33, le=39)
    longitude: float = Field(ge=124, le=132)
    target_at: AwareDatetime
    radius_m: float = Field(500, ge=1, le=1500)


class RecommendedStation(BaseModel):
    station_id: str
    name: str
    latitude: float
    longitude: float
    distance_m: float
    latest_inventory: int | None
    latest_inventory_received_at: UtcDatetime | None
    risk_probability: float | None
    is_risk: bool | None


class RecommendationDebug(BaseModel):
    nearby_count: int
    eligible_count: int


class RecommendationResponse(BaseModel):
    base_at: UtcDatetime
    target_at: UtcDatetime
    radius_m: float
    abstained: bool
    reason: str | None
    recommended: RecommendedStation | None
    alternatives: list[RecommendedStation]
    debug: RecommendationDebug | None = None
