"""One numerical feature kernel used by historical training and live inference.

Only inventory and as-of exogenous arrays enter the kernel; labels never do.
NaN represents unknown data. Zero inventory always remains an observed value.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
import warnings

import numpy as np

from .common import KST, instant, stamp
from .collectors import kma_grid

WEATHER = ("T1H", "RN1", "REH", "WSD", "PTY")
COLUMNS_C = ["station_code", "inventory", "log_inventory", "hour", "weekday", "month", "weekend", "horizon"]
COLUMNS_T = COLUMNS_C + [f"lag_{h}" for h in (1, 2, 3, 24, 168)] + ["delta_1", "delta_3", "delta_24", "past_24_mean", "past_24_std", "past_24_zero_rate", "past_24_known", "past_168_mean", "past_168_known"]
COLUMNS_S = COLUMNS_T + ["near5_inventory_mean", "near5_zero_rate", "near5_known", "radius500_inventory_mean", "radius500_zero_rate", "radius500_known"] + [
    f"{prefix}_delta_{h}_{stat}" for prefix in ("near5", "radius500") for h in (1, 3, 24) for stat in ("mean", "known")]
FEATURE_COLUMNS = {
    "C": COLUMNS_C, "T": COLUMNS_T, "S": COLUMNS_S,
    "W": COLUMNS_S + ["current_" + v for v in WEATHER] + ["current_weather_age_hours"],
    "F": COLUMNS_S + ["forecast_" + v for v in WEATHER] + ["forecast_issue_age_hours"],
    "E": COLUMNS_S + ["current_" + v for v in WEATHER] + ["current_weather_age_hours"] + ["forecast_" + v for v in WEATHER] + ["forecast_issue_age_hours", "air_pm10", "air_pm25", "air_o3", "air_no2", "air_age_hours"],
}


def distances(lat, lon, other_lat, other_lon):
    a, b = np.deg2rad(lat), np.deg2rad(other_lat)
    dlat, dlon = b - a, np.deg2rad(np.asarray(other_lon) - lon)
    return 6371008.8 * 2 * np.arcsin(np.sqrt(np.clip(np.sin(dlat / 2) ** 2 + np.cos(a) * np.cos(b) * np.sin(dlon / 2) ** 2, 0, 1)))


def neighborhoods(coords: np.ndarray):
    # Small station catalogue only; no time × station × station allocation.
    five, radius = [], []
    valid = np.isfinite(coords).all(axis=1)
    for i, (lat, lon) in enumerate(coords):
        if not valid[i]:
            five.append(np.array([], dtype=int)); radius.append(np.array([], dtype=int)); continue
        d = distances(lat, lon, coords[:, 0], coords[:, 1])
        d[~valid] = np.inf; d[i] = np.inf
        order = np.argsort(d, kind="stable")
        five.append(order[np.isfinite(d[order])][:5])
        radius.append(np.flatnonzero(d <= 500))
    return five, radius


def kernel(history: np.ndarray, base: datetime, station_codes: np.ndarray, horizon: int,
           group: str, neighbors=None, external: dict[str, np.ndarray] | None = None):
    """history[-1] is the inventory available at base; earlier rows are exact hours.

    Missing earlier hours MUST be padded with NaN, never shifted or backfilled.
    This function supports a station chunk as well as the whole station catalogue.
    """
    if group not in FEATURE_COLUMNS or horizon not in (1, 2, 3) or history.ndim != 2:
        raise ValueError("Unsupported feature request")
    t, n = history.shape
    q = np.asarray(history[-1], dtype=np.float32)
    local = instant(base).astimezone(KST)
    values = {"station_code": station_codes, "inventory": q, "log_inventory": np.log1p(q),
        "hour": np.full(n, local.hour), "weekday": np.full(n, local.weekday()), "month": np.full(n, local.month),
        "weekend": np.full(n, local.weekday() >= 5), "horizon": np.full(n, horizon)}
    if group != "C":
        for h in (1, 2, 3, 24, 168):
            values[f"lag_{h}"] = history[-1 - h] if t > h else np.full(n, np.nan)
        for h in (1, 3, 24):
            values[f"delta_{h}"] = q - values[f"lag_{h}"]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            for hours in (24, 168):
                a = history[max(0, t - hours):]
                known = np.isfinite(a).sum(axis=0)
                values[f"past_{hours}_mean"] = np.nanmean(a, axis=0)
                values[f"past_{hours}_known"] = known
                if hours == 24:
                    values["past_24_std"] = np.nanstd(a, axis=0)
                    values["past_24_zero_rate"] = np.divide((a == 0).sum(axis=0), known, out=np.full(n, np.nan), where=known > 0)
    if group in ("S", "W", "F", "E"):
        if neighbors is None:
            raise ValueError("Spatial features require fixed neighbors")
        for prefix, indices in zip(("near5", "radius500"), neighbors):
            means, zeros, knowns = [], [], []
            for idx in indices:
                observed = q[idx][np.isfinite(q[idx])]
                means.append(float(observed.mean()) if len(observed) else np.nan)
                zeros.append(float((observed == 0).mean()) if len(observed) else np.nan)
                knowns.append(len(observed))
            values[prefix + "_inventory_mean"] = means
            values[prefix + "_zero_rate"] = zeros
            values[prefix + "_known"] = knowns
            for h in (1, 3, 24):
                lag = history[-1 - h] if t > h else np.full(n, np.nan)
                # A change needs both endpoints from the SAME neighbor.
                # Subtracting independent means would hide missing endpoints.
                paired = q - lag
                changes, counts = [], []
                for idx in indices:
                    delta = paired[idx][np.isfinite(paired[idx])]
                    changes.append(float(delta.mean()) if len(delta) else np.nan)
                    counts.append(len(delta))
                values[f"{prefix}_delta_{h}_mean"] = changes
                values[f"{prefix}_delta_{h}_known"] = counts
    values.update(external or {})
    return np.column_stack([values.get(c, np.full(n, np.nan)) for c in FEATURE_COLUMNS[group]]).astype(np.float32)


def select_asof(records: list[dict], base: datetime, *, target: datetime | None = None,
                max_age_hours: float = 3, forecast=False):
    """Latest *available* revision. Equal-time conflicting values are unknown.

    A later receipt of an older publication does not supersede a newer issue.
    Historical proxy records are disallowed in an operational feature query.
    """
    selected = {}
    for r in records:
        if r.get("conflict") or r.get("quality") != "valid" or r.get("timing_basis") == "historical_proxy":
            continue
        received = instant(r["received_at"])
        if received > base:
            continue
        if forecast:
            issue = instant(r["issued_at"])
            if issue > base or instant(r["valid_at"]) != target:
                continue
            entity = r["grid_id"]
        else:
            # Ncst baseDate/baseTime are provider base/issue fields, not a
            # separately supplied source observation timestamp. Ignore legacy
            # captures which mislabeled that field; new revisions retain it as
            # issued_at and keep observed_at unknown.
            if r.get("source") == "kma_current" and not r.get("issued_at"):
                continue
            source_time = r.get("observed_at") or r.get("issued_at")
            if not source_time:
                continue
            issue = instant(source_time)
            if issue > base:
                continue
            entity = r["entity_id"]
        if (base - issue).total_seconds() > max_age_hours * 3600:
            continue
        key, rank = (entity, r["variable"]), (issue, received)
        if key not in selected or rank > selected[key][0]:
            selected[key] = (rank, r["value"])
        elif rank == selected[key][0] and r["value"] != selected[key][1]:
            selected[key] = (rank, None)
    return {k: v[1] for k, v in selected.items()}


class LiveFeatures:
    def __init__(self, store):
        self.store = store

    def view(self, base: datetime):
        stations = self.store.stations_at(base)
        stations = sorted((s for s in stations if s["identity_status"] == "verified"), key=lambda s: s["station_id"])
        ids = [s["station_id"] for s in stations]
        q = np.full((169, len(ids)), np.nan, np.float32)
        for i in range(169):
            at = base - timedelta(hours=168 - i)
            snapshot = self.store.inventory_snapshot(at, max_age_seconds=300)
            if snapshot:
                records = {r["entity_id"]: r for r in snapshot["rows"]}
                for j, s in enumerate(stations):
                    r = records.get(s["station_id"])
                    # Do not carry the present location/identity into its past.
                    if r and not r["conflict"] and r["quality"] == "valid" and instant(s["valid_from"]) <= at and 0 <= (at - instant(r["received_at"])).total_seconds() < 300:
                        q[i, j] = r["value"]
        coords = np.array([[s["latitude"], s["longitude"]] for s in stations], dtype=float).reshape(-1, 2)
        return {"stations": stations, "ids": ids, "history": q, "neighbors": neighborhoods(coords),
                "grid_ids": [",".join(map(str, kma_grid(s["latitude"], s["longitude"]))) for s in stations]}

    def external(self, view, base, horizon):
        with self.store.connection() as db:
            obs = [json.loads(r["payload"]) | {"conflict": bool(r["conflict"])} for r in db.execute(
                "SELECT payload,conflict FROM observations WHERE source!='seoul_bike' AND received_at>=? AND received_at<=?", (stamp(base - timedelta(hours=12)), stamp(base)))]
            fc = [json.loads(r["payload"]) | {"conflict": bool(r["conflict"])} for r in db.execute(
                "SELECT payload,conflict FROM forecasts WHERE received_at>=? AND received_at<=? AND valid_at=?", (stamp(base - timedelta(hours=12)), stamp(base), stamp(base + timedelta(hours=horizon))))]
        current = select_asof([r for r in obs if r["source"] == "kma_current"], base)
        forecast = select_asof(fc, base, target=base + timedelta(hours=horizon), forecast=True, max_age_hours=6)
        out = {}
        for prefix, selected in (("current", current), ("forecast", forecast)):
            for var in WEATHER:
                out[prefix + "_" + var] = np.array([selected.get((g, var), np.nan) for g in view["grid_ids"]], np.float32)
        # Age features use the same causal selection, not the current poll time.
        for prefix, records, is_fc in (("current_weather", obs, False), ("forecast_issue", fc, True)):
            ages = []
            for g in view["grid_ids"]:
                candidates = [r for r in records if r["source"] == ("kma_ultra_forecast" if is_fc else "kma_current")
                    and r.get("grid_id", r.get("entity_id")) == g and not r["conflict"] and r["quality"] == "valid"
                    and (is_fc or r.get("issued_at"))
                    and instant(r["received_at"]) <= base and instant(r["issued_at"] if is_fc else (r.get("observed_at") or r["issued_at"])) <= base]
                ages.append((base - max(instant(r["issued_at"] if is_fc else (r.get("observed_at") or r["issued_at"])) for r in candidates)).total_seconds() / 3600 if candidates else np.nan)
            out[prefix + "_age_hours"] = np.asarray(ages, np.float32)
        air = [r for r in obs if r["source"] == "airkorea"]
        selected = select_asof(air, base)
        n = len(view["ids"])
        for var, column in (("pm10Value", "air_pm10"), ("pm25Value", "air_pm25"), ("o3Value", "air_o3"), ("no2Value", "air_no2")):
            values = [v for (entity, variable), v in selected.items() if variable == var and v is not None]
            out[column] = np.full(n, np.mean(values) if values else np.nan, np.float32)
        # Citywide mean is explicitly represented, not called nearest-station air.
        valid_air = [r for r in air if not r["conflict"] and r["quality"] == "valid" and r["observed_at"] and instant(r["observed_at"]) <= base and instant(r["received_at"]) <= base]
        out["air_age_hours"] = np.full(n, (base - max(instant(r["observed_at"]) for r in valid_air)).total_seconds() / 3600 if valid_air else np.nan)
        return out
