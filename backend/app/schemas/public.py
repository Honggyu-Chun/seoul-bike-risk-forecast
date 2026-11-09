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


# GET /api/stations

class StationItem(BaseModel):
    station_id: str
    name: str
    latitude: float
    longitude: float
    distance_m: float | None   # lat·lon을 주지 않으면 null
    # 지도 마커 색용 지금 수량(가장 최근 완료 수집분). 그 수집분에 없거나 비정상 값이면 null
    latest_inventory: int | None
    latest_inventory_received_at: UtcDatetime | None
    inventory_fresh: bool          # 수량이 300초 안의 값이면 true


class StationsResponse(BaseModel):
    count: int
    items: list[StationItem]


# GET /api/stations/nearby, POST /api/predictions 공통

class PredictionDebug(BaseModel):
    model_id: str | None
    input_group: str | None
    latest_inventory_age_seconds: float | None


class NearbyItem(BaseModel):
    station_id: str
    name: str
    latitude: float
    longitude: float
    distance_m: float
    latest_inventory: int | None
    latest_inventory_received_at: UtcDatetime | None
    inventory_fresh: bool          # 수량이 300초 안의 값이면 true
    risk_probability: float | None
    is_risk: bool | None
    reason: str | None             # 확률이 null인 이유. AI 원문 그대로, AI 접속 실패만 ai_unavailable
    debug: PredictionDebug | None = None


class NearbyResponse(BaseModel):
    base_at: UtcDatetime
    target_at: UtcDatetime
    radius_m: float
    ai_available: bool
    count: int
    items: list[NearbyItem]


class PredictionRequest(BaseModel):
    station_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(min_length=1, max_length=100)
    target_at: AwareDatetime | None = None
    all_targets: bool = False  # target_at과 all_targets=true 중 정확히 하나 (서비스에서 확인, fields를 알려주기 위해)


class PredictionItem(BaseModel):
    station_id: str
    target_at: UtcDatetime
    name: str | None               # DB에 없는 ID면 null
    latitude: float | None
    longitude: float | None
    latest_inventory: int | None
    latest_inventory_received_at: UtcDatetime | None
    inventory_fresh: bool
    risk_probability: float | None
    is_risk: bool | None
    reason: str | None
    debug: PredictionDebug | None = None


class PredictionsResponse(BaseModel):
    base_at: UtcDatetime
    ai_available: bool
    items: list[PredictionItem]
