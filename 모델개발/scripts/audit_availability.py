"""Audit January availability as a separate observation, never a rental label."""
import json
from pathlib import Path
import zipfile

import pandas as pd


def main():
    selected = pd.read_csv("data/processed/selected_stations.csv", dtype={"station_id": "string"})
    parts, global_times = [], []
    total = 0
    with zipfile.ZipFile("data/raw/availability_2025q1.zip") as archive:
        member = next(n for n in archive.namelist() if n.endswith("data_2501.csv"))
        with archive.open(member) as stream:
            for chunk in pd.read_csv(stream, encoding="cp949", dtype="string", chunksize=100_000):
                total += len(chunk)
                source_columns = list(chunk.columns)
                chunk["station_id"] = pd.to_numeric(chunk["대여소번호"], errors="raise").astype(int).astype("string")
                chunk["time"] = pd.to_datetime(chunk["일시"], errors="raise") + pd.to_timedelta(pd.to_numeric(chunk["시간대"], errors="raise"), unit="h")
                global_times.append(chunk.groupby("time").size())
                parts.append(chunk[chunk.station_id.isin(selected.station_id)].copy())
    data = pd.concat(parts, ignore_index=True)
    data["reported_quantity"] = pd.to_numeric(data["거치대수량"], errors="raise")
    if data.duplicated(["station_id", "time"]).any():
        raise ValueError("Duplicate availability key; do not silently merge")
    data.to_csv("data/processed/availability_selected_january.csv", index=False)
    counts = pd.concat(global_times).groupby(level=0).sum()
    expected = pd.date_range("2025-01-01", "2025-01-31 23:00", freq="h")
    missing = expected.difference(counts.index)
    stats = data.groupby("station_id").reported_quantity.agg(["count", "min", "max", "nunique"])
    rentals = pd.read_csv("data/processed/pilot_panel.csv", dtype={"station_id": "string"}, parse_dates=["time"])
    paired = rentals.merge(data[["station_id", "time", "reported_quantity"]], on=["station_id", "time"], how="left", validate="one_to_one")
    paired.to_csv("data/processed/rentals_availability_audit.csv", index=False)
    record = {
        "source_rows_january": total, "source_member": member, "encoding": "cp949", "columns": source_columns,
        "source_unique_hours": len(counts), "source_rows_per_hour_min": int(counts.min()), "source_rows_per_hour_max": int(counts.max()),
        "missing_city_hours_count": len(missing), "missing_city_hours": [str(t) for t in missing],
        "selected_rows": len(data), "selected_duplicate_station_hours": 0,
        "selected_quantity_zero_records": int(data.reported_quantity.eq(0).sum()),
        "selected_quantity_zero_rate": float(data.reported_quantity.eq(0).mean()),
        "missing_rental_bins_with_availability_record": int((paired.rentals.isna() & paired.reported_quantity.notna()).sum()),
        "positive_rental_bins_with_zero_reported_quantity": int((paired.rentals.gt(0) & paired.reported_quantity.eq(0)).sum()),
        "per_station": stats.reset_index().to_dict("records"),
        "interpretation": [
            "Page describes hourly rental availability; actual column name is 거치대수량",
            "Quantity varies by hour and can exceed physical QR rack count; not static installed capacity",
            "Exact within-hour sampling/aggregation timestamp is not documented in the examined file",
            "A zero reading does not prove a whole hour of stockout, and cannot be substituted for rental count",
            "Unavailable source time bins stay missing; no rental label was filled from inventory",
        ],
    }
    Path("reports/availability_quality.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in record.items() if k not in ["per_station", "missing_city_hours"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
