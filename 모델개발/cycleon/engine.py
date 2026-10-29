from __future__ import annotations

from datetime import timedelta
import math
from threading import RLock

import numpy as np

from .common import hour_base, instant, now, stamp
from .features import LiveFeatures, distances, kernel


def history_eligible(history, group):
    eligible = np.isfinite(history[-1])
    if group == "C":
        return eligible
    # The proposal requires a genuine prior-week observation before exposing
    # any route that contains lag_168. Padding the history array is not history.
    if len(history) < 169:
        return np.zeros(history.shape[1], dtype=bool)
    return eligible & np.isfinite(history[-169]) & (np.isfinite(history[-24:]).sum(axis=0) >= 12)


class Engine:
    def __init__(self, cache, bundle=None, clock=now):
        self.cache, self.bundle, self.clock = cache, bundle, clock
        self.lock = RLock()
        self.base, self.predictions, self.last_compute = None, {}, None
        self.compute_error = None

    def precompute(self, force=False):
        at = self.clock(); base = hour_base(at)
        with self.lock:
            if base == self.base and not force:
                return
        features = LiveFeatures(self.cache)
        view = features.view(base)
        ids, stations = view["ids"], view["stations"]
        outputs = {(sid, h): {"station_id": sid, "horizon_hours": h, "target_at": stamp(base + timedelta(hours=h)),
            "risk_probability": None, "is_risk": None, "model_id": None, "reason": "model_not_ready"}
            for sid in ids for h in (1, 2, 3)}
        if self.bundle:
            codebook = self.bundle.manifest["station_codes"]
            codes = np.array([codebook.get(sid, 0) for sid in ids], np.float32)
            identity_ok = np.array([sid in codebook for sid in ids], dtype=bool)
            for j in np.flatnonzero(~identity_ok):
                for h in (1, 2, 3):
                    outputs[ids[j], h]["reason"] = "station_identity_not_verified_for_model"
            for h in (1, 2, 3):
                external = features.external(view, base, h)
                for route_index, (route, _) in enumerate(self.bundle.routes):
                    group = route["group"]
                    eligible = history_eligible(view["history"], group) & identity_ok
                    if group != "C":
                        for j in np.flatnonzero(np.isfinite(view["history"][-1]) & identity_ok & ~eligible):
                            if outputs[ids[j], h]["risk_probability"] is None:
                                outputs[ids[j], h]["reason"] = "history_not_ready"
                    if group in ("W", "E"):
                        eligible &= np.isfinite(external["current_T1H"]) & np.isfinite(external["current_RN1"])
                        eligible &= external["current_weather_age_hours"] <= 3
                    if group in ("F", "E"):
                        eligible &= np.isfinite(external["forecast_T1H"]) & np.isfinite(external["forecast_RN1"])
                        eligible &= external["forecast_issue_age_hours"] <= 6
                    eligible &= np.array([outputs[sid, h]["risk_probability"] is None for sid in ids], dtype=bool)
                    if not eligible.any():
                        continue
                    # Spatial kernel must see the whole catalogue before slicing.
                    matrix = kernel(view["history"], base, codes, h, group, view["neighbors"], external)
                    probabilities = self.bundle.predict(route_index, matrix[eligible], h)
                    for j, p in zip(np.flatnonzero(eligible), probabilities):
                        if not math.isfinite(float(p)) or not 0 <= p <= 1:
                            raise ValueError("Invalid model probability")
                        outputs[ids[j], h].update(risk_probability=float(p), is_risk=bool(p >= route["thresholds"][str(h)]),
                            model_id=route["id"], reason=None, input_group=group)
        for j, sid in enumerate(ids):
            if not np.isfinite(view["history"][-1, j]):
                for h in (1, 2, 3):
                    outputs[sid, h]["reason"] = "base_inventory_unavailable"
        with self.lock:
            self.base, self.predictions, self.last_compute, self.stations = base, outputs, stamp(self.clock()), {s["station_id"]: s for s in stations}
            self.compute_error = None

    def status(self):
        at = self.clock(); base = hour_base(at)
        with self.lock:
            ready = self.base == base and any(r["risk_probability"] is not None for r in self.predictions.values())
            return {"service": "CycleOn", "status": "ready" if ready else "not_ready", "server_at": stamp(at),
                "base_at": stamp(base), "supported_targets": [stamp(base + timedelta(hours=h)) for h in (1, 2, 3)],
                "model_bundle_id": self.bundle.manifest["id"] if self.bundle else None,
                "model_stage": self.bundle.manifest.get("stage") if self.bundle else None,
                "last_precompute_at": self.last_compute, "precompute_error": self.compute_error,
                "data": self.cache.summary(), "latest_inventory_age_limit_seconds": 300,
                "time_policy": "latest_exact_hour_base; latest_inventory_separate"}

    def predict(self, station_ids, horizons):
        at = self.clock(); base = hour_base(at)
        latest = self.cache.inventory_snapshot(at)
        current = {r["entity_id"]: r for r in latest["rows"]} if latest else {}
        with self.lock:
            items = []
            for sid in dict.fromkeys(station_ids):
                r = current.get(sid)
                for h in dict.fromkeys(horizons):
                    default = {"station_id": sid, "horizon_hours": h, "target_at": stamp(base + timedelta(hours=h)),
                        "risk_probability": None, "is_risk": None, "model_id": None,
                        "reason": "base_not_precomputed" if self.base != base else "station_unknown_or_ineligible"}
                    p = dict(self.predictions.get((sid, h), default)) if self.base == base else default
                    p.update(latest_inventory=r["value"] if r and not r["conflict"] and r["quality"] == "valid" else None,
                        latest_inventory_received_at=r["received_at"] if r else None,
                        latest_inventory_age_seconds=(at - instant(r["received_at"])).total_seconds() if r else None,
                        latest_inventory_observed_at=r["observed_at"] if r else None)
                    items.append(p)
            return {"base_at": stamp(base), "requested_at": stamp(at), "items": items}

    def recommend(self, latitude, longitude, horizon, radius):
        at = self.clock()
        current_stations = {s["station_id"]: s for s in self.cache.stations_at(at) if s["identity_status"] == "verified"}
        sid_list = sorted(current_stations)
        prediction = self.predict(sid_list, [horizon])
        nearby, eligible = [], []
        policy = self.bundle.manifest.get("recommendation_policy") if self.bundle else None
        for p in prediction["items"]:
            s = current_stations[p["station_id"]]
            distance = float(distances(latitude, longitude, np.array([s["latitude"]]), np.array([s["longitude"]]))[0])
            if distance > radius:
                continue
            nearby.append(p)
            q, age = p["latest_inventory"], p["latest_inventory_age_seconds"]
            if q is None or q <= 0 or age is None or not 0 <= age < 300 or p["risk_probability"] is None or not policy:
                continue
            prior = getattr(self, "stations", {}).get(p["station_id"])
            if not prior or prior["latitude"] != s["latitude"] or prior["longitude"] != s["longitude"]:
                continue
            if p["risk_probability"] > policy["maximum_risk"]:
                continue
            score = p["risk_probability"] + policy["distance_weight"] * distance / radius
            eligible.append(p | {"name": s["name"], "latitude": s["latitude"], "longitude": s["longitude"], "distance_m": distance, "score": score})
        eligible.sort(key=lambda p: (p["score"], p["distance_m"], p["station_id"]))
        if eligible:
            reason = None
        elif not nearby:
            reason = "no_station_within_radius"
        elif not policy:
            reason = "recommendation_policy_not_selected"
        else:
            reason = "no_fresh_positive_eligible_prediction"
        return {"base_at": prediction["base_at"], "requested_at": prediction["requested_at"], "horizon_hours": horizon,
            "radius_m": radius, "distance_type": "straight_line", "automatic_radius_expansion": False,
            "recommended": eligible[0] if eligible else None, "alternatives": eligible[1:3],
            "abstained": not bool(eligible), "reason": reason,
            "nearby_count": len(nearby), "eligible_count": len(eligible)}
