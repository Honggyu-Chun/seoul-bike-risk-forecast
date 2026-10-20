"""Auditable historical feature tables; no model fitting and no outcome access."""
from pathlib import Path
import io
import json
import hashlib
import zipfile
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = ROOT / "data/raw/followup_v4_extra"
READY = RAW / "model_ready"
READY.mkdir(exist_ok=True)

FIELDS = {
    "미세먼지 1시간(㎍/㎥)": "pm10_ug_m3",
    "미세먼지 24시간(㎍/㎥)": "pm10_24h_source_ug_m3",
    "초미세먼지(㎍/㎥)": "pm25_ug_m3",
    "오존(ppm)": "o3_ppm",
    "이산화질소농도(ppm)": "no2_ppm",
    "일산화탄소농도(ppm)": "co_ppm",
    "아황산가스농도(ppm)": "so2_ppm",
}
USE_FIELDS = [v for v in FIELDS.values() if "24h_source" not in v]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def air():
    rawpath = RAW / "seoul_air_2025.zip"
    with zipfile.ZipFile(rawpath) as archive:
        name = archive.namelist()[0]
        source = pd.read_csv(io.BytesIO(archive.read(name)), encoding="cp949", dtype=str)
    source["timestamp"] = pd.to_datetime(source["측정일시"], format="%Y%m%d%H%M", errors="coerce")
    report = dict(source_rows=len(source), invalid_timestamps=int(source.timestamp.isna().sum()),
                  source_sha256=digest(rawpath), timezone="Asia/Seoul",
                  provider_qc_column_available=False, historical_as_issued_verified=False)
    subset = source[(source.timestamp >= "2025-01-01") & (source.timestamp < "2025-07-01")].copy()
    assert (subset.timestamp.dt.minute == 0).all()
    subset.rename(columns={"측정소명": "district", "측정소코드": "air_station_code", **FIELDS}, inplace=True)
    districts = sorted(subset.district.unique())
    report.update(h1_source_rows=len(subset), n_districts=len(districts), districts=districts,
                  min_timestamp=str(subset.timestamp.min()), max_timestamp=str(subset.timestamp.max()))
    report["values"] = {}
    for col in FIELDS.values():
        raw = subset[col].fillna("").str.strip()
        value = pd.to_numeric(raw, errors="coerce")
        missing = raw.eq("") | raw.str.lower().isin(["nan", "null", "none", "-"])
        badparse = value.isna() & ~missing
        invalid = (~np.isfinite(value) | value.lt(0)) & ~missing & ~badparse
        subset[col + "_qc"] = np.select([missing, badparse, invalid], [1, 3, 2], default=0).astype("uint8")
        subset[col] = value.mask(missing | badparse | invalid).astype("float32")
        report["values"][col] = dict(source_missing=int(missing.sum()), nonparseable=int(badparse.sum()),
                                      invalid_negative_or_nonfinite=int(invalid.sum()),
                                      nonparseable_examples=raw[badparse].value_counts().head(10).to_dict(),
                                      min=None if subset[col].isna().all() else float(subset[col].min()),
                                      max=None if subset[col].isna().all() else float(subset[col].max()))
    keys = ["timestamp", "district"]
    ndup = subset.duplicated(keys, keep=False)
    conflicts = subset.loc[ndup].groupby(keys)[list(FIELDS.values())].nunique(dropna=False).gt(1).any(axis=1)
    report.update(duplicate_rows=int(ndup.sum()), conflicting_duplicate_keys=int(conflicts.sum()))
    keep = subset.drop_duplicates(keys, keep="first").set_index(keys)
    if conflicts.any():
        conflict_index = conflicts[conflicts].index
        for col in FIELDS.values():
            keep.loc[conflict_index, col] = np.nan
            keep.loc[conflict_index, col + "_qc"] = 4
    grid = pd.MultiIndex.from_product([pd.date_range("2025-01-01", "2025-06-30 23:00", freq="h"), districts], names=keys)
    tidy = keep.reindex(grid)
    tidy["source_row_present"] = tidy.air_station_code.notna()
    for col in FIELDS.values():
        tidy[col + "_qc"] = tidy[col + "_qc"].fillna(1).astype("uint8")
    tidy = tidy[["air_station_code", "source_row_present"] + [c for col in FIELDS.values() for c in (col, col + "_qc")]].reset_index()
    report.update(expected_rows=len(grid), missing_hour_district_rows=int((~tidy.source_row_present).sum()))
    tidy.to_parquet(READY / "air_2025h1_observations.parquet", index=False)
    tidy.to_csv(READY / "air_2025h1_observations.csv", index=False, encoding="utf-8-sig")

    # timestamp in this table is the model INPUT timestamp t, never the target timestamp.
    lags = pd.DataFrame(index=grid)
    for lag in (1, 24):
        for col in USE_FIELDS:
            matrix = tidy.pivot(index="timestamp", columns="district", values=col).reindex(columns=districts)
            qcmatrix = tidy.pivot(index="timestamp", columns="district", values=col + "_qc").reindex(columns=districts)
            shifted = matrix.shift(lag)
            qshift = qcmatrix.shift(lag).fillna(1).astype("uint8")
            # C-order flatten matches timestamp-major MultiIndex product above.
            lags[f"air_{col}_lag{lag}h"] = shifted.to_numpy().reshape(-1).astype("float32")
            lags[f"air_{col}_lag{lag}h_qc"] = qshift.to_numpy().reshape(-1).astype("uint8")
    lags = lags.reset_index()
    lags.to_parquet(READY / "air_2025h1_input_lags.parquet", index=False)
    lags.to_csv(READY / "air_2025h1_input_lags.csv", index=False, encoding="utf-8-sig")
    stations = pd.read_parquet(ROOT / "data/processed/rider_stations.parquet")
    report.update(cohort_station_rows=len(stations), cohort_district_matched_rows=int(stations.district.isin(districts).sum()),
                  unmatched_districts=sorted(set(stations.district) - set(districts)),
                  coordinate_join="Pre-existing station metadata district; air values are district-level, not point sensor measurements at bike stations",
                  lag_table_timestamp="Input t, sources exactly t-1h and t-24h; no forward fill or interpolation")
    stations[["station_id", "district"]].to_csv(READY / "station_air_district_join.csv", index=False, encoding="utf-8-sig")
    report["outputs"] = {p.name: dict(rows=len(tidy) if "observations" in p.name else len(lags) if "lags" in p.name else len(stations), bytes=p.stat().st_size, sha256=digest(p))
                         for p in READY.glob("*air*.*")}
    (HERE / "air_curation_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    plot_air(tidy)
    print(json.dumps({k: report[k] for k in ("h1_source_rows", "n_districts", "duplicate_rows", "conflicting_duplicate_keys", "expected_rows", "missing_hour_district_rows", "cohort_district_matched_rows")}, ensure_ascii=False))


def plot_air(tidy):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False
    figures = HERE / "시각화"
    figures.mkdir(exist_ok=True)
    daily = tidy.groupby(tidy.timestamp.dt.floor("D"))[["pm10_ug_m3", "pm25_ug_m3"]].median()
    fig, ax = plt.subplots(figsize=(11, 4.2))
    ax.plot(daily.index, daily.pm10_ug_m3, label="PM10")
    ax.plot(daily.index, daily.pm25_ug_m3, label="PM2.5")
    ax.set(title="2025년 상반기 서울 자치구 대기질의 일별 중앙값", ylabel="농도 (㎍/㎥)", xlabel="날짜")
    ax.legend(); ax.grid(alpha=.25);fig.tight_layout()
    fig.savefig(figures / "01_대기질_기간추이.png", dpi=160); plt.close(fig)
    columns = ["pm10_ug_m3", "pm25_ug_m3", "o3_ppm", "no2_ppm", "co_ppm", "so2_ppm"]
    missing = tidy.groupby("district")[columns].agg(lambda v: v.isna().mean() * 100)
    fig, ax = plt.subplots(figsize=(9, 8))
    im = ax.imshow(missing.to_numpy(), aspect="auto", cmap="YlOrRd", vmin=0)
    ax.set_yticks(range(len(missing)), missing.index); ax.set_xticks(range(len(columns)), ["PM10", "PM2.5", "O3", "NO2", "CO", "SO2"])
    ax.set_title("자치구·항목별 결측률 (상반기 전체 시간 격자)")
    fig.colorbar(im, ax=ax, label="결측률 (%)");fig.tight_layout()
    fig.savefig(figures / "02_대기질_결측률.png", dpi=160); plt.close(fig)


def forecast():
    records = json.loads((HERE / "forecast_download_manifest.json").read_text(encoding="utf-8"))
    frames, points = [], []
    rename = {"temperature_2m_previous_day1": "gfs_day1_temperature_c",
              "precipitation_previous_day1": "gfs_day1_precipitation_mm",
              "relative_humidity_2m_previous_day1": "gfs_day1_relative_humidity_pct",
              "wind_speed_10m_previous_day1": "gfs_day1_wind_speed_m_s",
              "cloud_cover_previous_day1": "gfs_day1_cloud_cover_pct"}
    for record in records:
        if "path" not in record:
            continue
        payload = json.loads((ROOT / record["path"]).read_text(encoding="utf-8"))
        frame = pd.DataFrame(payload["hourly"]).rename(columns={"time": "valid_time", **rename})
        frame["valid_time"] = pd.to_datetime(frame.valid_time)
        frame["timestamp"] = frame.valid_time - pd.Timedelta(hours=1)
        frame["point_id"] = record["point_id"]
        frame["fixed_lead_hours"] = 24
        frame["historical_publication_time_verified"] = False
        for col in rename.values():
            frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float32")
        frames.append(frame)
        points.append(dict(point_id=record["point_id"], latitude=record["grid_latitude"], longitude=record["grid_longitude"],
                           request_latitude=record["request_latitude"], request_longitude=record["request_longitude"]))
    ready = pd.concat(frames, ignore_index=True).sort_values(["timestamp", "point_id"])
    assert not ready.duplicated(["timestamp", "point_id"]).any()
    assert (ready.valid_time - ready.timestamp == pd.Timedelta(hours=1)).all()
    ready.to_parquet(READY / "gfs_day1_2025h1_input.parquet", index=False)
    ready.to_csv(READY / "gfs_day1_2025h1_input.csv", index=False, encoding="utf-8-sig")
    pointframe = pd.DataFrame(points)
    pointframe.to_csv(READY / "gfs_day1_points.csv", index=False, encoding="utf-8-sig")
    stations = pd.read_parquet(ROOT / "data/processed/rider_stations.parquet")
    # Associate with the returned grid, not the originally requested coordinates.
    rlat = np.deg2rad(stations.latitude.to_numpy(float))[:, None]
    rlon = np.deg2rad(stations.longitude.to_numpy(float))[:, None]
    glat = np.deg2rad(pointframe.latitude.to_numpy(float))[None, :]
    glon = np.deg2rad(pointframe.longitude.to_numpy(float))[None, :]
    h = np.sin((rlat-glat)/2)**2 + np.cos(rlat)*np.cos(glat)*np.sin((rlon-glon)/2)**2
    distance = 6371.0088 * 2 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
    nearest = np.argmin(distance, axis=1)
    join = pd.DataFrame(dict(station_id=stations.station_id,
                             point_id=pointframe.point_id.to_numpy()[nearest],
                             grid_distance_km=distance[np.arange(len(stations)), nearest]))
    join.to_csv(READY / "station_gfs_point_join.csv", index=False, encoding="utf-8-sig")
    report = dict(n_rows=len(ready), n_points=len(pointframe), n_unique_grid_coordinates=len(pointframe.drop_duplicates(["latitude", "longitude"])),
                  hours_per_point=ready.groupby("point_id").size().to_dict(),
                  min_valid_time=str(ready.valid_time.min()), max_valid_time=str(ready.valid_time.max()),
                  min_input_time=str(ready.timestamp.min()), max_input_time=str(ready.timestamp.max()),
                  null_counts=ready[list(rename.values())].isna().sum().to_dict(),
                  fixed_lead_hours=24, target_valid_time_minus_input_hours=1,
                  first_publication_time_verified=False,
                  information_track="Archived fixed-day1 forecast sensitivity, not verified historical operational feed or 1h-lead weather forecast",
                  cohort_rows=len(join), cohort_assigned_rows=int(join.point_id.notna().sum()),
                  nearest_grid_distance_km=join.grid_distance_km.describe(percentiles=[.5, .9, .95]).to_dict(),
                  point_assignment_counts=join.point_id.value_counts().to_dict(),
                  source_manifest_sha256=digest(HERE / "forecast_download_manifest.json"),
                  outputs={p.name: dict(bytes=p.stat().st_size, sha256=digest(p)) for p in READY.glob("*gfs*.*")})
    (HERE / "forecast_curation_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("n_rows", "n_points", "n_unique_grid_coordinates", "null_counts", "point_assignment_counts")}, ensure_ascii=False))


def air_runtime_lags():
    observations = pd.read_parquet(READY / "air_2025h1_observations.parquet")
    districts = sorted(observations.district.unique())
    grid = pd.MultiIndex.from_product([sorted(observations.timestamp.unique()), districts], names=["timestamp", "district"])
    frame = pd.DataFrame(index=grid)
    for col in USE_FIELDS:
        matrix = observations.pivot(index="timestamp", columns="district", values=col).reindex(columns=districts)
        qcmatrix = observations.pivot(index="timestamp", columns="district", values=col + "_qc").reindex(columns=districts)
        for lag in (2, 3, 24):
            frame[f"air_{col}_lag{lag}h"] = matrix.shift(lag).to_numpy().reshape(-1).astype("float32")
            frame[f"air_{col}_lag{lag}h_qc"] = qcmatrix.shift(lag).fillna(1).to_numpy().reshape(-1).astype("uint8")
    frame = frame.reset_index()
    path = READY / "air_2025h1_input_lags2_3_24.parquet"
    frame.to_parquet(path, index=False)
    missing = observations.groupby("district")[USE_FIELDS].agg(lambda v: v.isna().mean()*100)
    missing.to_csv(HERE / "air_district_missing_percent.csv", encoding="utf-8-sig")
    report = dict(path=str(path.relative_to(ROOT)), rows=len(frame), sha256=digest(path),
                  input_time_key="timestamp, naive Asia/Seoul INPUT t", district_key="district",
                  source_lags_hours=[2, 3, 24], numerical_columns=[c for c in frame if c not in ("timestamp", "district") and not c.endswith("_qc")],
                  runtime_rule="Use only snapshots received_at<=t, source event_time<=t-lag; stale or missing values remain missing. Local historical snapshot cache is required; current API alone does not return 24h-old snapshots.",
                  historical_revision_limit="2025 archive revision/publication history unknown; lag alone does not prove historical as-issued equivalence.")
    (HERE / "air_runtime_lags_schema.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("air_runtime_lags", len(frame), len(report["numerical_columns"]))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", nargs="?", choices=["air", "forecast", "air_runtime_lags"], default="air")
    args = parser.parse_args()
    {"air": air, "forecast": forecast, "air_runtime_lags": air_runtime_lags}[args.provider]()
