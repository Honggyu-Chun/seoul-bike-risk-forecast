"""Start an isolated temporary local server, test real C HTTP inference, then stop that child."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import httpx

def main():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    token = secrets.token_urlsafe(40)
    env = {**os.environ, "CYCLEON_SERVICE_TOKEN": token}
    child = subprocess.Popen(
        [sys.executable, "-m", "team_model", "serve", "--port", str(port)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5,
                          headers={"Authorization": "Bearer " + token}) as client:
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError("Temporary inference server exited")
                try:
                    state = client.get("/v1/status")
                    if state.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("Temporary inference server did not start")
            assert state.json()["status"] == "not_ready"
            assert client.get("/v1/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
            now = datetime.now(timezone.utc)
            from .service import hour
            base = hour(now)
            sample = json.loads((Path(__file__).parent/"examples/snapshot.json").read_text(encoding="utf-8"))
            sample.update(base_at=base.isoformat(), materialized_at=now.isoformat(),
                          capture_complete=True, source_capture_id="synthetic-http-smoke-only")
            station = sample["stations"][0]
            station.update(identity_verified_at=(base-timedelta(days=1)).isoformat(),
                           base_inventory_received_at=(base-timedelta(seconds=30)).isoformat(),
                           latest_inventory_received_at=now.isoformat())
            response = client.post("/internal/ai-data/v1/snapshot", json=sample)
            response.raise_for_status()
            result = client.post("/v1/predictions", json={"station_ids": [station["station_id"]], "horizons_hours": [1, 2, 3]})
            result.raise_for_status()
            items = result.json()["items"]
            assert len(items) == 3
            assert all(x["model_id"] == "C_h" + str(x["horizon_hours"]) and x["available_probability"] == 1-x["risk_probability"] for x in items)
            rec = client.post("/v1/recommendations", json={"latitude": station["latitude"], "longitude": station["longitude"], "horizon_hours": 1})
            rec.raise_for_status()
            assert rec.json()["recommended"] is not None
            print(json.dumps({"status":"passed_real_HTTP_synthetic_inputs","saved_C_models":3,"auth_verified":True,
                              "snapshot_prediction_recommendation":True,"new_fit":0,"provider_calls":0,
                              "primary_live_routes_enabled":False,"temporary_server_stopped_on_exit":True}))
    finally:
        # Only the child created above is stopped. Existing services are untouched.
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

if __name__ == "__main__":
    main()
