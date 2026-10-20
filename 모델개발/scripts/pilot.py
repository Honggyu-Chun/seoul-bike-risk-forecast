"""Offline rolling one-hour pilot on an audited canonical hourly panel.

No real data is bundled. A completed command is not evidence of live forecast
availability: source latency is an explicit retrospective assumption. Without
weather/holiday input this is B1-lite, not the proposed full B1 baseline.
"""
import argparse
import json
import os
from pathlib import Path
import time

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / ".cache" / "matplotlib"))
from lightgbm import LGBMRegressor
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error


def prepare(panel, latency_hours):
    if not isinstance(latency_hours, int) or not 0 <= latency_hours <= 24:
        raise ValueError("Choose an explicit nonnegative source latency, at most 24 hours for this pilot")
    panel = panel.copy()
    required = {"station_id", "time", "rentals", "coverage_verified"}
    if not required.issubset(panel.columns):
        raise ValueError("Panel requires " + str(sorted(required)))
    if panel.station_id.isna().any() or panel.station_id.astype(str).str.strip().eq("").any():
        raise ValueError("Missing station ID")
    panel["station_id"] = panel.station_id.astype("string")
    panel["time"] = pd.to_datetime(panel.time, errors="raise")
    if panel.time.isna().any() or panel.time.dt.tz is not None:
        raise ValueError("Use complete timezone-naive Asia/Seoul hourly timestamps")
    if (panel.time != panel.time.dt.floor("h")).any():
        raise ValueError("time must be the start of an exact hourly bin")
    if panel.duplicated(["station_id", "time"]).any():
        raise ValueError("Duplicate station-hour labels")
    panel["rentals"] = pd.to_numeric(panel.rentals, errors="raise")
    y = panel.rentals.dropna()
    if ((y < 0) | ~np.isfinite(y) | (y != np.floor(y))).any():
        raise ValueError("rentals must be finite nonnegative integer counts or missing")
    if not panel.coverage_verified.isin([0, 1]).all():
        raise ValueError("coverage_verified must be 0 or 1, established from source completeness/operation checks")
    panel.loc[panel.coverage_verified != 1, "rentals"] = np.nan
    pieces = []
    delay = 1 + latency_hours
    for station, group in panel.groupby("station_id", sort=True):
        group = group.set_index("time").sort_index()
        group = group.reindex(pd.date_range(group.index.min(), group.index.max(), freq="h", name="time"))
        group["station_id"] = station
        # Reindex FIRST: a shift is an hour rather than an arbitrary record.
        group["lag_recent"] = group.rentals.shift(delay)
        group["lag_day"] = group.rentals.shift(24 if delay <= 24 else 48)
        group["lag_week"] = group.rentals.shift(168)
        group["mean_24h"] = group.rentals.shift(delay).rolling(24, min_periods=1).mean()
        group["known_hours_24h"] = group.rentals.shift(delay).rolling(24, min_periods=1).count()
        group["hour"] = group.index.hour
        group["weekday"] = group.index.dayofweek
        pieces.append(group.reset_index())
    if not pieces:
        raise ValueError("Empty panel")
    return pd.concat(pieces, ignore_index=True)


def metric_rows(frame, predictions, split):
    output = []
    segments = {"all": np.ones(len(frame), dtype=bool)}
    if "event_window" in frame:
        if not frame.event_window.isin([0, 1]).all():
            raise ValueError("event_window must be predeclared 0/1; unknown must not become non-event")
        segments.update(recorded_start_window=frame.event_window.eq(1).to_numpy(), outside_recorded_window=frame.event_window.eq(0).to_numpy())
    segments.update({"station:" + str(st): frame.station_id.eq(st).to_numpy() for st in frame.station_id.unique()})
    for name, mask in segments.items():
        for model, pred in predictions.items():
            n = int(mask.sum())
            output.append({"split": split, "segment": name, "model": model, "n": n,
                           "mae": float(mean_absolute_error(frame.rentals.to_numpy()[mask], pred[mask])) if n else None,
                           "rmse": float(np.sqrt(mean_squared_error(frame.rentals.to_numpy()[mask], pred[mask]))) if n else None})
    return output


def run(panel, train_end, valid_end, test_end, latency_hours, event_features=None):
    train_end, valid_end, test_end = map(pd.Timestamp, [train_end, valid_end, test_end])
    if not train_end < valid_end < test_end:
        raise ValueError("Require chronological, exclusive train_end < valid_end < test_end")
    frame = prepare(panel, latency_hours)
    base = ["station_code", "hour", "weekday", "lag_recent", "lag_day", "lag_week", "mean_24h", "known_hours_24h"]
    before = len(frame)
    frame = frame[(frame.time < test_end)].dropna(subset=["rentals", "lag_recent", "lag_day", "lag_week", "mean_24h"]).copy()
    event_features = event_features or []
    if event_features:
        needed = set(event_features + ["event_available_at", "event_coverage_verified", "event_window"])
        if not needed.issubset(frame.columns):
            raise ValueError("Event ablation requires " + str(sorted(needed)))
        availability = pd.to_datetime(frame.event_available_at, errors="raise")
        if availability.isna().any() or (availability > frame.time).any() or not frame.event_coverage_verified.eq(1).all():
            raise ValueError("Event coverage or point-in-time availability unverified; do not treat unknown as no event")
        for col in event_features:
            frame[col] = pd.to_numeric(frame[col], errors="raise")
        if not np.isfinite(frame[event_features].to_numpy()).all():
            raise ValueError("This pilot requires fully verified finite event features on the common cohort")
    train = frame[frame.time < train_end].copy()
    valid = frame[(frame.time >= train_end) & (frame.time < valid_end)].copy()
    test = frame[(frame.time >= valid_end) & (frame.time < test_end)].copy()
    if any(x.empty for x in [train, valid, test]):
        raise ValueError("A split contains zero eligible rows after lag/coverage checks")
    vocabulary = {station: i for i, station in enumerate(sorted(train.station_id.unique()))}
    for split in [train, valid, test]:
        if not split.station_id.isin(vocabulary).all():
            raise ValueError("Unseen stations require an explicit cold-start evaluation")
        split["station_code"] = split.station_id.map(vocabulary).astype(int)
    parameters = dict(n_estimators=200, learning_rate=0.05, num_leaves=15,
                      min_child_samples=30, random_state=20261019, n_jobs=2,
                      verbosity=-1, deterministic=True, force_col_wise=True)
    configs = {"B1_lite_calendar_lags": base}
    if event_features:
        configs["M1_lite_plus_events"] = base + event_features
    predictions = {"validation": {"B0_previous_week": valid.lag_week.to_numpy()},
                   "test": {"B0_previous_week": test.lag_week.to_numpy()}}
    inference = {}
    for name, columns in configs.items():
        model = LGBMRegressor(**parameters)
        model.fit(train[columns], train.rentals, categorical_feature=["station_code"])
        for split_name, split in [("validation", valid), ("test", test)]:
            start = time.perf_counter()
            predictions[split_name][name] = np.maximum(0, model.predict(split[columns]))
            inference[name + ":" + split_name] = {"rows": len(split), "batch_seconds": time.perf_counter() - start}
    metrics = metric_rows(valid, predictions["validation"], "validation") + metric_rows(test, predictions["test"], "test")
    output = test[["station_id", "time", "rentals"]].copy()
    for name, values in predictions["test"].items():
        output[name] = values
    metadata = {
        "scope": "offline_pilot_not_final_or_live_performance", "timezone": "Asia/Seoul",
        "latency_hours_assumption": latency_hours, "target": "rentals in [time, time+1h)",
        "evaluation_mode": "rolling_one_hour; earlier validation/test labels used only after assumed latency",
        "train_end_exclusive": str(train_end), "valid_end_exclusive": str(valid_end), "test_end_exclusive": str(test_end),
        "rows_after_hourly_reindex": before, "eligible_rows": len(frame),
        "split_rows": {"train": len(train), "validation": len(valid), "test": len(test)},
        "features": configs, "model_parameters": parameters, "inference": inference,
        "limitations": ["No weather or holiday features: this is B1-lite, not full B1",
                        "Rolling means use only known hours and include their count; missing hours are not zero",
                        "No hyperparameter search; equal fixed model settings",
                        "Batch model time is not API response latency",
                        "No causal effect, unmet demand, or rebalancing claim",
                        "Source vintage, schedule evidence, and split event overlap require separate audit"],
    }
    return metadata, pd.DataFrame(metrics), output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, required=True)
    parser.add_argument("--train-end", required=True)
    parser.add_argument("--valid-end", required=True)
    parser.add_argument("--test-end", required=True)
    parser.add_argument("--latency-hours", type=int, required=True)
    parser.add_argument("--event-features", nargs="*", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    panel = pd.read_csv(args.panel, dtype={"station_id": "string"})
    meta, metrics, predictions = run(panel, args.train_end, args.valid_end, args.test_end, args.latency_hours, args.event_features)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "conditions.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    metrics.to_csv(args.output_dir / "metrics.csv", index=False)
    predictions.to_csv(args.output_dir / "predictions.csv", index=False)
    first = predictions.station_id.iloc[0]
    sample = predictions[predictions.station_id == first].head(168).set_index("time").drop(columns="station_id")
    sample.plot(figsize=(12, 4), title=f"Offline pilot: station {first}, first test week")
    plt.ylabel("observed rentals / hour")
    plt.tight_layout()
    plt.savefig(args.output_dir / "pilot.png", dpi=150)
    plt.close()
    print(metrics[metrics.segment == "all"].to_string(index=False))


if __name__ == "__main__":
    main()
