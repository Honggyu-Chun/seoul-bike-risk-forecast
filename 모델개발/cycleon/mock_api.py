"""Offline contract fixtures. No trained model, provider key, database or network."""
from __future__ import annotations

from datetime import timedelta
import math

from .api import ai_app, backend_app
from .common import hour_base, instant, stamp
from .contracts import Station, Observation, Forecast

AT = instant("2026-10-29T14:40:00+09:00")
BASE = hour_base(AT)
SCENARIOS = ("policy-pending", "recommendation-demo", "not-ready")


def station_records():
    return [Station(station_id=f"ST-MOCK-{i}", source_station_id=f"ST-MOCK-{i}",
                    name=f"모의 대여소 {i}", latitude=37.5665 + (i - 1) * .001,
                    longitude=126.978, valid_from=BASE - timedelta(days=1),
                    received_at=BASE - timedelta(days=1), revision="mock-v1").model_dump(mode="json")
            for i in (1, 2)]


class MockEngine:
    def __init__(self, scenario="policy-pending"):
        if scenario not in SCENARIOS:
            raise ValueError("Unknown mock scenario")
        self.scenario = scenario
        self.compute_error = None

    def precompute(self):
        pass

    def status(self):
        return {"service": "CycleOn", "status": "not_ready" if self.scenario == "not-ready" else "ready",
                "server_at": stamp(AT), "base_at": stamp(BASE),
                "supported_targets": [stamp(BASE + timedelta(hours=h)) for h in (1, 2, 3)],
                "model_bundle_id": "mock-contract-v1", "model_stage": "mock_no_model_no_live_data",
                "last_precompute_at": stamp(AT), "precompute_error": None,
                "data": {"mode": "synthetic_fixture", "fixed_clock": True, "scenario": self.scenario},
                "latest_inventory_age_limit_seconds": 300,
                "time_policy": "latest_exact_hour_base; latest_inventory_separate"}

    def predict(self, station_ids, horizons):
        items = []
        for sid in dict.fromkeys(station_ids):
            known = sid in {"ST-MOCK-1", "ST-MOCK-2"}
            for h in dict.fromkeys(horizons):
                ready = known and self.scenario != "not-ready"
                p = (.2 if sid == "ST-MOCK-1" else .8) if ready else None
                items.append({"station_id": sid, "horizon_hours": h,
                              "target_at": stamp(BASE + timedelta(hours=h)),
                              "risk_probability": p, "is_risk": p >= .5 if ready else None,
                              "model_id": "mock-fixture" if ready else None,
                              "input_group": "C" if ready else None,
                              "reason": None if ready else "base_not_precomputed" if self.scenario == "not-ready"
                              else "station_unknown_or_ineligible",
                              "latest_inventory": (2 if sid == "ST-MOCK-1" else 0) if known else None,
                              "latest_inventory_received_at": stamp(AT - timedelta(seconds=30)) if known else None,
                              "latest_inventory_age_seconds": 30 if known else None,
                              "latest_inventory_observed_at": None})
        return {"base_at": stamp(BASE), "requested_at": stamp(AT), "items": items}

    def recommend(self, latitude, longitude, horizon, radius):
        nearby, eligible = [], []
        predictions = {p["station_id"]: p for p in self.predict([s["station_id"] for s in station_records()], [horizon])["items"]}
        for station in station_records():
            a, b = math.radians(latitude), math.radians(station["latitude"])
            d = 6371008.8 * 2 * math.asin(min(1, math.sqrt(math.sin((b-a)/2)**2 +
                math.cos(a) * math.cos(b) * math.sin(math.radians(station["longitude"]-longitude)/2)**2)))
            if d > radius:
                continue
            nearby.append(station)
            p = predictions[station["station_id"]]
            if self.scenario == "recommendation-demo" and p["latest_inventory"] > 0:
                eligible.append(p | {k: station[k] for k in ("name", "latitude", "longitude")} |
                                {"distance_m": d, "score": p["risk_probability"] + .1*d/radius})
        eligible.sort(key=lambda p: (p["score"], p["distance_m"], p["station_id"]))
        reason = (None if eligible else "no_station_within_radius" if not nearby else
                  "recommendation_policy_not_selected" if self.scenario == "policy-pending" else
                  "no_fresh_positive_eligible_prediction")
        return {"base_at": stamp(BASE), "requested_at": stamp(AT), "horizon_hours": horizon,
                "radius_m": radius, "distance_type": "straight_line", "automatic_radius_expansion": False,
                "recommended": eligible[0] if eligible else None, "alternatives": eligible[1:3],
                "abstained": not bool(eligible), "reason": reason,
                "nearby_count": len(nearby), "eligible_count": len(eligible)}


class MockStore:
    def page(self, table, *, cursor=0, limit=1000, source=None, start=None, end=None, watermark=None):
        stations = station_records()
        observations = [Observation(source="seoul_bike", entity_id=s["station_id"], event_key="mock-capture-v1",
            variable="inventory", value=2 if i == 0 else 0, unit="bikes", received_at=AT-timedelta(seconds=30),
            revision="mock-v1").model_dump(mode="json") for i, s in enumerate(stations)]
        forecasts = [Forecast(source="kma_forecast", grid_id="60,127", variable="T1H", value=15,
            unit="degC", issued_at=BASE-timedelta(minutes=30), valid_at=BASE+timedelta(hours=1),
            received_at=BASE-timedelta(minutes=20)).model_dump(mode="json")]
        records = {"stations": stations, "observations": observations, "forecasts": forecasts}[table]
        watermark = len(records) if watermark is None else watermark
        if watermark < cursor:
            raise ValueError("Watermark precedes cursor")
        rows = [{"cursor": i, "conflict": False, "record": record,
                 "capture_id": "mock-capture-v1" if table == "observations" else None}
                for i, record in enumerate(records, 1)
                if cursor < i <= watermark and (not source or record["source"] == source)
                and (not start or instant(record["received_at"]) >= start)
                and (not end or instant(record["received_at"]) <= end)]
        more = len(rows) > limit
        items = rows[:limit]
        captures = [{"capture_id": "mock-capture-v1", "source": "seoul_bike",
                     "started_at": stamp(AT-timedelta(seconds=32)), "completed_at": stamp(AT-timedelta(seconds=29)),
                     "expected_rows": 2, "actual_rows": 2, "state": "complete", "summary_json": "{}"}]
        return {"items": items, "next_cursor": items[-1]["cursor"] if more else watermark,
                "watermark": watermark, "has_more": more,
                "captures": captures if table == "observations" and items else []}


def mock_app(mode, token, scenario="policy-pending"):
    return ai_app(MockEngine(scenario), token) if mode == "ai" else backend_app(MockStore(), token)
