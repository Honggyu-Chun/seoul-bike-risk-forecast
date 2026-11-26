"""Backend-supplied, as-of-hour primary model inputs. All times require UTC offsets."""
from datetime import datetime, timedelta
from typing import Annotated, Literal

import numpy as np
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt
from cycleon.features import kernel, neighborhoods
from .weather_predictions import PRIMARY, LABELS

Stock = Annotated[StrictInt, Field(ge=0)]


class Inputs(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StationHistory(Inputs):
    identity_revision: str = Field(min_length=1)
    continuous_since: AwareDatetime
    prior_inventory: list[Stock | None] = Field(min_length=168, max_length=671)


class GridValues(Inputs):
    T1H: float | None = Field(ge=-90, le=60)
    RN1: float | None = Field(ge=0, le=2000)
    REH: float | None = Field(ge=0, le=100)
    WSD: float | None = Field(ge=0, le=150)
    PTY: int | None = Field(ge=0, le=7, strict=True)


class Forecast(Inputs):
    horizon: Literal[1, 2, 3]
    issued_at: AwareDatetime
    received_at: AwareDatetime
    target_at: AwareDatetime
    by_station: dict[str, GridValues] = Field(max_length=5000)


class FinalInputs(Inputs):
    product: Literal["kma_completed_hour_v2"]
    history_received_at: AwareDatetime
    histories: dict[str, StationHistory] = Field(max_length=5000)
    forecasts: list[Forecast] = Field(max_length=3)

    def validate_at(self, base, ids):
        if self.history_received_at > base or self.histories.keys() - ids:
            raise ValueError("History must match the catalogue and be received by base_at")
        if any(v.continuous_since > base for v in self.histories.values()):
            raise ValueError("Station revision starts after base_at")
        if len({f.horizon for f in self.forecasts}) != len(self.forecasts):
            raise ValueError("Duplicate forecast horizon")
        for f in self.forecasts:
            if (f.issued_at != base - timedelta(minutes=70)
                    or not f.issued_at <= f.received_at <= base
                    or f.target_at != base + timedelta(hours=f.horizon)
                    or f.by_station.keys() - ids):
                raise ValueError("Forecast vintage, receipt, target or station mismatch")


def feature_matrix(history, base, codes, h, neighbors, external):
    # All sums use training-time float32 arithmetic.
    recent = history[-169:]
    parts = [kernel(recent, base, codes, h, "F", neighbors, external),
             recent[-1+h-24], recent[-1+h-168]]
    if h in (1, 2):
        samples = np.stack([recent[-1+h-24*k] for k in range(1, 8)])
        known = np.isfinite(samples).sum(axis=0)
        denominator = np.where(known > 0, known, np.nan).astype(np.float32)
        parts.extend([np.nansum(samples,axis=0,dtype=np.float32)/denominator,
                      (samples==0).sum(axis=0).astype(np.float32)/denominator,known])
    if h == 1:
        older = history[[-336,-504,-672]]
        known = np.isfinite(older).sum(axis=0)
        parts.extend([np.where(known==3,(older==0).sum(axis=0).astype(np.float32)/3,np.nan),known])
    return np.column_stack(parts).astype(np.float32, copy=False)


def calculate(runtime, snapshot, eligible):
    inputs = snapshot.final_inputs
    # Include verified neighbours even where their current quantity is missing.
    stations = sorted((s for s in snapshot.stations if s.identity_verified and
                       s.model_station_code in runtime.allowed_codes), key=lambda s:s.model_station_code)
    if not stations or not inputs.forecasts:
        return {}
    n = len(stations)
    history = np.full((672,n), np.nan, np.float32)
    stable = np.zeros(n, bool)
    for i,s in enumerate(stations):
        if s.station_id in eligible:
            history[-1,i] = s.inventory_at_base
        prior = inputs.histories.get(s.station_id)
        if prior is None or prior.identity_revision != s.identity_revision:
            continue
        age = int((snapshot.base_at-prior.continuous_since).total_seconds()//3600)
        hours = min(len(prior.prior_inventory), age)
        if hours > 0:
            history[-1-hours:-1,i] = prior.prior_inventory[-hours:]
        stable[i] = age >= 24
    ready = np.isfinite(history[-1]) & np.isfinite(history[-169]) & (np.isfinite(history[-24:]).sum(axis=0)>=12)
    neighbors = neighborhoods(np.asarray([(s.latitude,s.longitude) for s in stations], np.float64))
    spatial = np.asarray([stable[five].all() and stable[radius].all() for five,radius in zip(*neighbors)])
    codes = np.asarray([s.model_station_code for s in stations],np.float32)
    predictions = {}
    for forecast in inputs.forecasts:
        h = forecast.horizon
        rows = [forecast.by_station.get(s.station_id) for s in stations]
        external = {"forecast_"+key:np.asarray([getattr(row,key) if row else None for row in rows],np.float32)
                    for key in GridValues.model_fields}
        external["forecast_issue_age_hours"] = np.full(n, (snapshot.base_at-forecast.issued_at).total_seconds()/3600, np.float32)
        chosen = np.flatnonzero(ready & spatial & np.isfinite(external["forecast_T1H"]) & (external["forecast_RN1"]==0))
        if chosen.size:
            model = PRIMARY[h-1]
            matrix = feature_matrix(history,snapshot.base_at,codes,h,neighbors,external)
            risks,threshold = runtime.predict_route(model,matrix[chosen])
            for i,risk in zip(chosen,risks):
                predictions[stations[i].station_id,h] = (float(risk),bool(risk>=threshold),model,LABELS[h-1])
    return predictions
