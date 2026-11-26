"""Regression checks for the public, raw-snapshot primary-model path."""
from datetime import datetime, timezone, timedelta
import unittest
from fastapi.testclient import TestClient
from .demo import snapshot
from .runtime import Runtime
from .service import create_app
from .weather_predictions import PRIMARY


class TestPortableFinal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = Runtime()

    def setUp(self):
        self.now = datetime(2026, 11, 25, 23, 5, tzinfo=timezone.utc)
        self.data = snapshot(self.now)
        token = "synthetic-test-secret-for-local-regression-only"
        self.client = TestClient(create_app(token,self.runtime,clock=lambda:self.now))
        self.client.headers["Authorization"] = "Bearer " + token

    def upload(self):
        return self.client.post("/internal/ai-data/v1/snapshot",json=self.data)

    def items(self):
        r=self.client.post("/v1/predictions",json={"station_ids":["DEMO-102"]})
        r.raise_for_status()
        return r.json()["items"]

    def test_routes_and_wet_fallback(self):
        self.data["final_inputs"]["forecasts"][1]["by_station"]["DEMO-102"]["RN1"]=3
        self.assertEqual(self.upload().status_code,200)
        items=self.items()
        self.assertEqual([x["model_id"] for x in items],[PRIMARY[0],"C_h2",PRIMARY[2]])
        self.assertTrue(all(x["risk_probability"]+x["available_probability"]==1 for x in items))
        self.assertEqual(self.client.get("/v1/status").json()["data"]["primary_prediction_count"],17)

    def test_null_history_skips_primary(self):
        self.data["final_inputs"]["histories"]["DEMO-102"]["prior_inventory"][-168]=None
        self.assertEqual(self.upload().status_code,200)
        self.assertEqual([x["input_group"] for x in self.items()],["C"]*3)

    def test_invalid_future_receipt_keeps_previous_snapshot(self):
        self.assertEqual(self.upload().status_code,200)
        self.data["final_inputs"]["forecasts"][0]["received_at"]=self.now.isoformat()
        self.assertEqual(self.upload().status_code,422)
        self.assertEqual(self.items()[0]["model_id"],PRIMARY[0])

    def test_input_locked_at_base(self):
        self.assertEqual(self.upload().status_code,200)
        self.now+=timedelta(seconds=1)
        self.data["materialized_at"]=self.now.isoformat()
        self.data["final_inputs"]["forecasts"][0]["by_station"]["DEMO-102"]["T1H"]=55
        self.assertEqual(self.upload().status_code,422)

    def test_stale_base_and_missing_forecasts(self):
        self.data["final_inputs"]["forecasts"].pop()
        self.assertEqual(self.upload().status_code,200)
        self.assertEqual(self.items()[2]["model_id"],"C_h3")
        self.now+=timedelta(hours=1)
        self.assertTrue(all(x["risk_probability"] is None for x in self.items()))

    def test_latest_stock_refresh_reuses_frozen_forecast(self):
        self.assertEqual(self.upload().status_code, 200)
        original = self.items()
        self.now += timedelta(seconds=5)
        self.data["materialized_at"] = self.now.isoformat()
        station = self.data["stations"][0]
        station.update(latest_inventory=0, latest_inventory_received_at=self.now.isoformat())
        self.assertEqual(self.upload().status_code, 200)
        current = self.items()
        self.assertEqual([i["risk_probability"] for i in current],
                         [i["risk_probability"] for i in original])
        self.assertTrue(all(i["latest_inventory"] == 0 for i in current))
        self.now += timedelta(seconds=5)
        self.data["materialized_at"] = self.now.isoformat()
        added = {**station, "station_id": "ADDED", "model_station_code": 109}
        self.data["stations"].append(added)
        self.assertEqual(self.upload().status_code, 422)
