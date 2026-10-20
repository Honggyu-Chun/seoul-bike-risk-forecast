"""Audit the actual downloaded Seoul formats and build a conservative January pilot.

Selection is based on event metadata and distance, before prediction metrics:
Sejong Center, nearest <=20 stable snapshot IDs within 1km, January 2025.
Missing rental bins remain NaN. Events are used only for descriptive grouping;
the current export does not establish historical schedule versions.
"""
import json
import os
from datetime import timedelta
from pathlib import Path
import re

os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / ".cache" / "matplotlib"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from audit_data import aggregate_chunk
from link_events import haversine

RAW = Path("data/raw")
OUT = Path("data/processed")
REPORT = Path("reports")


def write_json(path, record):
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n")


def station_table(filename):
    raw = pd.read_excel(RAW / filename, header=None)
    if "대여소" not in str(raw.iloc[0, 0]) or raw.shape[1] != 10:
        raise ValueError("Unexpected station workbook schema")
    frame = raw.iloc[5:].copy()
    frame.columns = ["station_id", "station_name", "district", "address", "latitude", "longitude", "installed", "lcd_racks", "qr_racks", "type"]
    frame["station_id"] = pd.to_numeric(frame.station_id, errors="raise").astype(int).astype(str)
    frame["installed"] = pd.to_datetime(frame.installed, errors="coerce")
    for c in ["latitude", "longitude"]:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    if frame.station_id.duplicated().any():
        raise ValueError("Duplicate station IDs require explicit version resolution")
    return frame


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (REPORT / "figures").mkdir(exist_ok=True)
    original = pd.DataFrame(json.loads((RAW / "events_all.json").read_text())["DATA"])
    events = original.replace({"": None, "null": None}).copy()
    for raw, parsed in [("strtdate", "start_date"), ("end_date", "end_date_parsed")]:
        events[parsed] = pd.to_datetime(events[raw], unit="ms", utc=True, errors="coerce").dt.tz_convert("Asia/Seoul").dt.tz_localize(None)
    events["registered_date"] = pd.to_datetime(events.rgstdate, errors="coerce")
    events["latitude"] = pd.to_numeric(events.lat, errors="coerce")
    events["longitude"] = pd.to_numeric(events.lot, errors="coerce")
    events["event_id"] = events.hmpg_addr.str.extract(r"[?&]cultcode=(\d+)")[0]
    events["coordinates_ok"] = events.latitude.between(37, 38) & events.longitude.between(126, 128)
    events["date_order_ok"] = events.start_date <= events.end_date_parsed
    events["one_day"] = events.start_date == events.end_date_parsed
    # Only exact single time or explicit start-end clock strings; no inferred
    # daily repeats, holidays, duration, cancellation, or overnight sessions.
    pattern = r"^\s*([01]?\d|2[0-3]):([0-5]\d)(?:\s*[~-]\s*(?:[01]?\d|2[0-3]):[0-5]\d)?\s*$"
    clock = events.pro_time.astype("string").str.extract(pattern)
    events["simple_start"] = events.one_day & clock[0].notna()
    minutes = pd.to_numeric(clock[0], errors="coerce") * 60 + pd.to_numeric(clock[1], errors="coerce")
    events["start_time"] = events.start_date + pd.to_timedelta(minutes, unit="m")
    events.loc[~events.simple_start, "start_time"] = pd.NaT
    january_candidates = events[(events.start_date < "2025-02-01") & (events.end_date_parsed >= "2025-01-01")].copy()
    january = january_candidates[january_candidates.date_order_ok].copy()
    # Exact venue names: '세종' alone also matches Sejong University elsewhere.
    venue_names = ["세종체임버홀", "세종대극장", "세종문화회관 대극장", "세종M씨어터", "세종S씨어터"]
    sejong = january[january.place.isin(venue_names)].copy()
    starts = sejong[sejong.simple_start & sejong.coordinates_ok & sejong.date_order_ok].copy()
    starts = starts.drop_duplicates("event_id")
    center_lat, center_lon = starts.latitude.median(), starts.longitude.median()
    if starts.empty:
        raise ValueError("No simple event starts at the declared venue; do not select another based on metrics")
    before, after = station_table("stations_202412.xlsx"), station_table("stations_202506.xlsx")
    paired = before.merge(after[["station_id", "latitude", "longitude"]], on="station_id", suffixes=("", "_later"))
    paired["location_change_m"] = haversine(paired.latitude, paired.longitude, paired.latitude_later, paired.longitude_later)
    stable = paired[(paired.installed < "2025-01-01") & (paired.location_change_m <= 10)].copy()
    stable["distance_m"] = haversine(stable.latitude, stable.longitude, center_lat, center_lon)
    candidates = stable[stable.distance_m <= 1000].sort_values(["distance_m", "station_id"])
    selected = candidates.head(20).copy()
    selected.to_csv(OUT / "selected_stations.csv", index=False)
    events[["event_id", "title", "place", "start_date", "end_date_parsed", "registered_date", "pro_time", "latitude", "longitude", "simple_start", "start_time", "hmpg_addr"]].to_csv(OUT / "events_normalized.csv", index=False)
    sejong.to_csv(OUT / "sejong_january_events.csv", index=False)
    starts.to_csv(OUT / "sejong_simple_starts.csv", index=False)
    links = selected[["station_id", "station_name", "distance_m"]].merge(
        starts[["event_id", "title", "start_time", "rgstdate", "pro_time", "hmpg_addr"]], how="cross")
    links["within_500m"] = links.distance_m <= 500
    links.to_csv(REPORT / "sejong_event_station_links.csv", index=False)
    event_audit = {
        "rows": len(events), "columns": list(original.columns), "scope": "complete_current_export_not_historical_snapshot",
        "start_min": events.start_date.min(), "start_max": events.start_date.max(),
        "starts_by_year": events.start_date.dt.year.value_counts().sort_index().to_dict(),
        "missing_fields": events[list(original.columns)].isna().sum().to_dict(),
        "exact_duplicate_rows": int(original.duplicated().sum()),
        "duplicate_event_ids": int(events.event_id.duplicated().sum()),
        "invalid_end_timestamp": int(events.end_date_parsed.isna().sum()),
        "reversed_date_intervals": int((events.start_date > events.end_date_parsed).sum()),
        "invalid_or_missing_coordinates": int((~events.coordinates_ok).sum()),
        "registered_after_start_date": int((events.registered_date > events.start_date).sum()),
        "simple_one_day_starts": int(events.simple_start.sum()),
        "january_overlap_candidates_before_interval_validation": len(january_candidates),
        "january_overlap_events": len(january), "january_sejong_venue_events": len(sejong),
        "january_simple_sejong_starts": len(starts),
        "registered_on_or_after_event_day_in_selected": int((starts.registered_date >= starts.start_date).sum()),
        "historical_version_proof": "unavailable; registration date does not prove original schedule contents",
        "source_json_timestamp_unit": "Unix milliseconds, UTC converted to Asia/Seoul",
        "csv_problem": "strict CP949 decode fails at byte 5382564; official UTF-8 JSON export used instead",
        "invalid_date_rows": events[~events.date_order_ok][["event_id", "title", "date"]].to_dict("records"),
    }
    write_json(REPORT / "events_quality.json", event_audit)
    station_audit = {
        "202412_rows": len(before), "202506_rows": len(after), "shared_ids": len(paired),
        "stable_location_and_installed_before_january": len(stable),
        "within_500m": int((candidates.distance_m <= 500).sum()), "within_1000m": len(candidates),
        "selected_count": len(selected), "center": [center_lat, center_lon],
        "selection": "nearest up to 20 within 1000m, <=10m snapshot displacement, installed before month; no rental/test scores used",
        "operational_caveat": "Two snapshots and install dates do not prove uninterrupted operation in between",
    }
    write_json(REPORT / "stations_quality.json", station_audit)
    selected_ids = set(selected.station_id)
    parts, hashes, city_parts = [], [], []
    all_ids = set()
    missing = {}
    row_count = rental_count = id_name_mismatches = invalid_rows = 0
    for chunk in pd.read_csv(RAW / "bike_hourly_202501.csv", encoding="cp949", dtype="string", chunksize=100_000):
        row_count += len(chunk)
        for key, value in chunk.isna().sum().items():
            missing[key] = missing.get(key, 0) + int(value)
        hashes.append(pd.util.hash_pandas_object(chunk, index=False).to_numpy())
        if not chunk["대여소번호"].str.fullmatch(r"\d+").all():
            raise ValueError("Non-numeric station IDs require a separate mapping")
        numeric = pd.to_numeric(chunk["대여소번호"], errors="raise")
        chunk["station_id"] = numeric.astype(int).astype(str)
        all_ids.update(chunk.station_id.unique())
        prefix = pd.to_numeric(chunk["대여소명"].str.extract(r"^(\d+)\.")[0], errors="coerce")
        id_name_mismatches += int((prefix != numeric).sum())
        counts = pd.to_numeric(chunk["이용건수"], errors="raise")
        rental_count += int(counts.sum())
        date = pd.to_datetime(chunk["대여일자"], errors="raise")
        hour = pd.to_numeric(chunk["대여시간"], errors="raise")
        bad = ((date < "2025-01-01") | (date >= "2025-02-01") | ~hour.between(0, 23) |
               hour.ne(np.floor(hour)) | counts.isna() | ~np.isfinite(counts) | (counts < 0) | counts.ne(np.floor(counts)))
        invalid_rows += int(bad.sum())
        if invalid_rows:
            raise ValueError("Invalid monthly row; inspect before continuing")
        city = pd.DataFrame({"time": date + pd.to_timedelta(hour, unit="h"), "rentals": counts})
        city_parts.append(city.groupby("time").rentals.sum())
        sub = chunk[chunk.station_id.isin(selected_ids)]
        if not sub.empty:
            parts.append(aggregate_chunk(sub, "station_id", "대여일자", "이용건수", "대여시간"))
    observed = pd.concat(parts).groupby(["station_id", "time"], as_index=False).rentals.sum()
    city = pd.concat(city_parts).groupby(level=0).sum()
    observed.to_csv(OUT / "sejong_observed_hours.csv", index=False)
    index = pd.MultiIndex.from_product([selected.station_id, pd.date_range("2025-01-01", "2025-01-31 23:00", freq="h")], names=["station_id", "time"])
    panel = observed.set_index(["station_id", "time"]).reindex(index).reset_index()
    # Observed aggregate labels only. No assumption that unrepresented hours
    # are zero, and no claims that station service continued without interruption.
    panel["coverage_verified"] = panel.rentals.notna().astype(int)
    panel["event_window"] = 0
    distances = selected.set_index("station_id").distance_m
    near = panel.station_id.map(distances) <= 500
    for start in starts.start_time:
        start = pd.Timestamp(start).to_pydatetime()
        within = (panel.time >= start - timedelta(hours=2)) & (panel.time < start + timedelta(hours=2))
        panel.loc[within & near, "event_window"] = 1
    panel.to_csv(OUT / "pilot_panel.csv", index=False)
    quality = {
        "source_rows": row_count, "total_rentals_sum": rental_count, "columns": list(chunk.columns[:-1]),
        "encoding": "cp949", "source_station_ids": len(all_ids),
        "all_source_date_hours_present": len(city), "expected_month_hours": 31 * 24,
        "global_hourly_min_rentals": int(city.min()), "first_hour": city.index.min(), "last_hour": city.index.max(),
        "id_name_prefix_mismatches": id_name_mismatches,
        "ids_absent_from_202412_snapshot": sorted(all_ids - set(before.station_id)),
        "numeric_id_rule": "strip zero padding only after numeric validation; cross-check station-name numeric prefix",
        "missing_by_column": missing,
        "row_hash_duplicate_candidates": int(pd.Series(np.concatenate(hashes)).duplicated().sum()),
        "duplicates_policy": "not removed; duplicate-looking grouped records need provider semantics, not blind deduplication",
        "selected_station_hours_grid": len(panel), "selected_hours_with_source_records": int(panel.rentals.notna().sum()),
        "selected_hours_missing_unknown": int(panel.rentals.isna().sum()),
        "selected_explicit_zero_hours": int(panel.rentals.eq(0).sum()),
        "selected_rentals_sum": int(panel.rentals.sum()),
        "scope": "conditional-on-observed-hours pilot; no full zero-demand evaluation and no operational uptime proof",
        "event_window_definition": "only parsed single-day Sejong starts, station <=500m, bin start in [start-2h,start+2h); no claim of exhaustive events or end times",
    }
    write_json(REPORT / "hourly_quality.json", quality)
    per_station = panel.groupby("station_id").agg(observed_hours=("rentals", "count"), rental_sum=("rentals", "sum"))
    selected.merge(per_station, on="station_id").to_csv(REPORT / "selected_station_summary.csv", index=False)
    # Descriptive visual of the geographically closest station, not selected
    # for its response to events. Missing bins are gaps, never zeros.
    station = selected.station_id.iloc[0]
    example = panel[panel.station_id == station].set_index("time")
    fig, axes = plt.subplots(2, 1, figsize=(13, 7))
    axes[0].plot(example.index, example.rentals, linewidth=0.8)
    for start in starts.start_time:
        axes[0].axvline(start, color="tomato", alpha=0.35, linewidth=0.8)
    axes[0].set(title=f"Station {station}: Jan 2025 observed hourly rentals; red = recorded starts", ylabel="Rentals (gaps unknown)")
    tmp = example.reset_index()
    tmp["weekday"] = tmp.time.dt.dayofweek
    tmp["hour"] = tmp.time.dt.hour
    for weekday, group in tmp.groupby("weekday"):
        axes[1].plot(group.groupby("hour").rentals.mean(), label=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][weekday])
    axes[1].set(xlabel="Hour, Asia/Seoul", ylabel="Mean over observed bins only", title="Descriptive weekday pattern; unknown bins excluded")
    axes[1].legend(ncol=7)
    fig.tight_layout()
    fig.savefig(REPORT / "figures/sejong_january.png", dpi=160)
    plt.close(fig)
    print(json.dumps({"events": event_audit, "stations": station_audit, "rentals": quality}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
