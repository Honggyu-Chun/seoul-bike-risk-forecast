"""Offline verification and power-loss recovery inventory."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = ROOT / "data/raw/followup_v4_extra"
READY = RAW / "model_ready"
obs = pd.read_parquet(READY / "air_2025h1_observations.parquet").set_index(["timestamp", "district"]).sort_index()
lags = pd.read_parquet(READY / "air_2025h1_input_lags2_3_24.parquet").set_index(["timestamp", "district"]).sort_index()
assert len(obs) == len(lags) == 108600
assert not obs.index.has_duplicates and not lags.index.has_duplicates
assert obs.index.equals(lags.index)
columns = ["pm10_ug_m3", "pm25_ug_m3", "o3_ppm", "no2_ppm", "co_ppm", "so2_ppm"]
checks = []
for col in columns:
    matrix = obs[col].unstack("district")
    for lag in (2, 3, 24):
        actual = lags[f"air_{col}_lag{lag}h"].unstack("district")
        np.testing.assert_array_equal(actual.to_numpy(), matrix.shift(lag).to_numpy())
        checks.append(f"all_rows_equal_exact_past_shift:{col}:{lag}")
        assert actual.head(lag).isna().all().all()
forecast = pd.read_parquet(READY / "gfs_day1_2025h1_input.parquet")
assert len(forecast) == 21720
assert not forecast.duplicated(["timestamp", "point_id"]).any()
assert ((forecast.valid_time.to_numpy() - forecast.timestamp.to_numpy()) == np.timedelta64(1, "h")).all()
assert forecast.filter(regex="^gfs_day1_").notna().all().all()
checks.extend(["forecast_time_keys_unique", "forecast_valid_time_equals_input_plus_1h", "forecast_no_nulls"])
files = []
for directory in (HERE, RAW):
    for path in directory.rglob("*"):
        if path.is_file() and path.name != "보존검수.json" and "__pycache__" not in path.parts:
            files.append(dict(path=str(path.relative_to(ROOT)), bytes=path.stat().st_size,
                              sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
report = dict(saved_at_utc=datetime.now(timezone.utc).isoformat(), status="saved_and_offline_verified",
              new_network_requests_stopped=True, model_training_started_by_this_subtask=False,
              files=len(files), bytes=sum(f["bytes"] for f in files), checks_passed=checks,
              pending=["Historical publication/revision equivalence", "Production snapshot cache and freshness gates", "Exact live forecast-run replacement metadata", "Any model performance improvement requires separate main experiment"],
              inventory=files)
(HERE / "보존검수.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: report[k] for k in ("status", "files", "bytes", "new_network_requests_stopped")}, ensure_ascii=False))
