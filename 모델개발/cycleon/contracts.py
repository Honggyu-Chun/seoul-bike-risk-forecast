"""Wire schemas: UTC on output; all supplied datetimes must carry an offset."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Literal, Generic, TypeVar, Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import instant


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Station(WireModel):
    station_id: str = Field(min_length=1, max_length=80)
    source_station_id: str = Field(min_length=1, max_length=80)
    name: str
    latitude: float | None = Field(ge=33, le=39)
    longitude: float | None = Field(ge=124, le=132)
    valid_from: datetime
    valid_to: datetime | None = None
    received_at: datetime
    source: str = "seoul_bike"
    revision: str
    identity_status: Literal["verified", "unverified"] = "verified"
    validity_basis: Literal["first_seen_receipt", "source_effective_interval"] = "first_seen_receipt"
    source_valid_from: datetime | None = None
    source_valid_to: datetime | None = None

    @field_validator("valid_from", "valid_to", "received_at", "source_valid_from", "source_valid_to")
    @classmethod
    def aware(cls, value):
        return instant(value) if value is not None else None

    @model_validator(mode="after")
    def interval(self):
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("Invalid validity interval")
        if self.identity_status == "verified" and (self.latitude is None or self.longitude is None):
            raise ValueError("Verified stations require valid coordinates")
        if self.source_valid_to is not None and (self.source_valid_from is None or self.source_valid_to <= self.source_valid_from):
            raise ValueError("Invalid source validity interval")
        if self.validity_basis == "source_effective_interval" and self.source_valid_from is None:
            raise ValueError("Source effective interval requires a supplied start")
        return self


class Observation(WireModel):
    source: str
    entity_id: str
    event_key: str
    variable: str
    value: float | None
    unit: str
    raw_value: str | None = Field(default=None, max_length=160)
    received_at: datetime
    observed_at: datetime | None = None
    issued_at: datetime | None = None
    valid_at: datetime | None = None
    revision: str = "0"
    quality: Literal["valid", "missing", "invalid"] = "valid"
    timing_basis: Literal["received", "observed_and_received", "issued_and_received", "historical_proxy"] = "received"

    @field_validator("received_at", "observed_at", "issued_at", "valid_at")
    @classmethod
    def aware(cls, value):
        return instant(value) if value is not None else None

    @model_validator(mode="after")
    def check_value(self):
        if self.quality == "valid" and self.value is None:
            raise ValueError("A valid observation requires a value")
        if self.variable == "inventory" and self.value is not None:
            if self.value < 0 or self.value != math.floor(self.value):
                raise ValueError("Inventory must be a nonnegative integer")
        if self.variable == "inventory" and self.unit != "bikes":
            raise ValueError("Inventory unit must be bikes")
        return self


class Forecast(WireModel):
    source: str
    grid_id: str
    variable: str
    value: float | None
    unit: str
    raw_value: str | None = Field(default=None, max_length=160)
    issued_at: datetime
    valid_at: datetime
    received_at: datetime
    revision: str = "0"
    quality: Literal["valid", "missing", "invalid"] = "valid"

    @field_validator("issued_at", "valid_at", "received_at")
    @classmethod
    def aware(cls, value):
        return instant(value)

    @model_validator(mode="after")
    def check_value(self):
        if self.quality == "valid" and self.value is None:
            raise ValueError("A valid forecast requires a value")
        return self


class PredictionRequest(WireModel):
    station_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(min_length=1, max_length=500)
    horizons_hours: list[Literal[1, 2, 3]] = Field(default_factory=lambda: [1, 2, 3], min_length=1, max_length=3)


class RecommendationRequest(WireModel):
    latitude: float = Field(ge=33, le=39)
    longitude: float = Field(ge=124, le=132)
    horizon_hours: Literal[1, 2, 3]
    radius_m: float = Field(default=500, gt=0, le=1500)


class Prediction(WireModel):
    station_id: str
    horizon_hours: Literal[1, 2, 3]
    target_at: datetime
    risk_probability: float | None = Field(default=None, ge=0, le=1)
    is_risk: bool | None = None
    model_id: str | None = None
    reason: str | None = None
    input_group: Literal["T", "S", "W", "F", "E", "C"] | None = None
    latest_inventory: int | None = None
    latest_inventory_received_at: datetime | None = None
    latest_inventory_age_seconds: float | None = None
    latest_inventory_observed_at: datetime | None = None


class PredictionResponse(WireModel):
    base_at: datetime
    requested_at: datetime
    items: list[Prediction]


class RankedPrediction(Prediction):
    name: str
    latitude: float
    longitude: float
    distance_m: float = Field(ge=0)
    score: float


class RecommendationResponse(WireModel):
    base_at: datetime
    requested_at: datetime
    horizon_hours: Literal[1, 2, 3]
    radius_m: float
    distance_type: Literal["straight_line"]
    automatic_radius_expansion: Literal[False]
    recommended: RankedPrediction | None
    alternatives: list[RankedPrediction] = Field(max_length=2)
    abstained: bool
    reason: Literal[
        "no_station_within_radius",
        "recommendation_policy_not_selected",
        "no_fresh_positive_eligible_prediction",
    ] | None
    nearby_count: int
    eligible_count: int


class StatusResponse(WireModel):
    service: Literal["CycleOn"]
    status: Literal["ready", "not_ready"]
    server_at: datetime
    base_at: datetime
    supported_targets: list[datetime]
    model_bundle_id: str | None
    model_stage: str | None
    last_precompute_at: datetime | None
    precompute_error: str | None
    data: dict
    latest_inventory_age_limit_seconds: int
    time_policy: str


class Capture(WireModel):
    capture_id: str
    source: str
    started_at: datetime
    completed_at: datetime
    expected_rows: int
    actual_rows: int
    state: str
    summary_json: str


Record = TypeVar("Record")


class PageItem(WireModel, Generic[Record]):
    cursor: int
    conflict: bool
    record: Record
    capture_id: str | None = None


class Page(WireModel, Generic[Record]):
    items: list[PageItem[Record]]
    next_cursor: int
    watermark: int
    has_more: bool
    captures: list[Capture]
