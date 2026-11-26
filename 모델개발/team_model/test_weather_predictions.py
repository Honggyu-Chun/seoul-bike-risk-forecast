"""Serving contract for primary and fallback routes. No provider calls."""
import csv
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from .service import create_app
from .test_service import BASE, NOW, TOKEN, fixture
from .weather_predictions import BUNDLE_SHA, LABELS, PRIMARY


class FakeRuntime:
    allowed_codes = {102}
    manifest = {"id": "fixture"}
    routes = {name: {"threshold": 0.4, "model_sha256": str(i) * 64}
              for i, name in enumerate(PRIMARY, 1)}

    def predict_c(self, matrix, horizon):
        return [0.2] * len(matrix), 0.4


class TestWeatherServing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.folder = self.root / "slots" / BASE.strftime("%Y%m%dT%HZ")
        self.folder.mkdir(parents=True)
        self.now = NOW
        self.headers = {"Authorization": "Bearer " + TOKEN}
        self.app = create_app(TOKEN, FakeRuntime(), lambda: self.now, self.root)
        self.client = TestClient(self.app)
        self.sid = "ST-DEMO-102"

    def publish(self, *, wet=(), identity="fixture-v1", inventory=2, corrupt=False):
        p = self.folder / "paired_predictions.csv"
        with p.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow("station_id identity inventory_at_base horizon model p_primary decision_primary base_utc target_utc forecast_hourly_precip_raw".split())
            for h, model in enumerate(PRIMARY, 1):
                writer.writerow([self.sid, identity, inventory, h, model, 0.75, 1,
                                 BASE.isoformat(), (BASE+timedelta(hours=h)).isoformat(),
                                 1 if h in wet else 0])
        receipt = {"base": BASE.isoformat(), "issued_at": (BASE+timedelta(seconds=8)).isoformat(),
                   "bundle": BUNDLE_SHA, "paired_counts": {str(h):1 for h in (1,2,3)},
                   "inputs": {str(h): {"primary":FakeRuntime.routes[m]["model_sha256"],
                                       "weather": dict.fromkeys(("RN1","REH","T1H","PTY","WSD"),"abc")}
                              for h,m in enumerate(PRIMARY,1)},
                   "predictions_sha256": "wrong" if corrupt else hashlib.sha256(p.read_bytes()).hexdigest()}
        (self.folder / "issue.json").write_text(json.dumps(receipt))

    def predict(self):
        snapshot=fixture()
        reply=self.client.post("/internal/ai-data/v1/snapshot",json=snapshot,headers=self.headers)
        self.assertEqual(reply.status_code,200,reply.text)
        return self.client.post("/v1/predictions",headers=self.headers,json={"station_ids":[self.sid]}).json()["items"]

    def test_primary_predictions_and_recommendation(self):
        self.publish()
        rows=self.predict()
        self.assertEqual([r["model_id"] for r in rows], list(PRIMARY))
        self.assertEqual([r["input_group"] for r in rows], list(LABELS))
        self.assertTrue(all(r["available_probability"]==0.25 for r in rows))
        reply=self.client.post("/v1/recommendations",headers=self.headers,json={
            "latitude":37.5665,"longitude":126.978,"horizon_hours":2}).json()
        self.assertEqual(reply["recommended"]["model_id"],PRIMARY[1])
        self.now += timedelta(hours=1)
        reply=self.client.post("/v1/predictions",headers=self.headers,json={"station_ids":[self.sid]}).json()
        self.assertTrue(all(r["risk_probability"] is None for r in reply["items"]))

    def test_wet_row_falls_back_without_disabling_other_horizons(self):
        self.publish(wet=(2,))
        self.assertEqual([r["model_id"] for r in self.predict()],[PRIMARY[0],"C_h2",PRIMARY[2]])

    def test_missing_or_corrupt_receipt(self):
        for mode in ("missing","corrupt"):
            with self.subTest(mode=mode):
                if mode=="corrupt":
                    self.publish(corrupt=True)
                    self.now += timedelta(seconds=2)
                data=fixture()
                data["materialized_at"]=self.now.isoformat()
                self.assertEqual(self.client.post("/internal/ai-data/v1/snapshot",headers=self.headers,json=data).status_code,200)
                rows=self.app.state.service.predict([self.sid],[1,2,3])["items"]
                self.assertEqual([r["model_id"] for r in rows],["C_h1","C_h2","C_h3"])
                self.assertIsNotNone(self.app.state.service.status()["precompute_error"])

    def test_identity_revision_and_base_quantity_must_match(self):
        self.publish(identity="different")
        self.assertTrue(all(r["input_group"]=="C" for r in self.predict()))
