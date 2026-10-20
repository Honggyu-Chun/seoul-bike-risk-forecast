"""Synthetic fixtures test code behavior only; they are NOT Seoul data evidence."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import numpy as np
import pandas as pd
import pytest

from audit_data import aggregate_chunk, csv_chunks, inspect
from link_events import link
from pilot import prepare, run


def test_demographic_rows_sum_count_column_not_rows():
    frame = pd.DataFrame({"id": ["001", "001", "002"], "date": ["2024-01-01"] * 3,
                          "hour": ["9", "9", "10"], "count": ["4", "7", "2"]})
    result = aggregate_chunk(frame, "id", "date", "count", "hour")
    assert result[result.station_id == "001"].rentals.iloc[0] == 11
    assert len(result) == 2  # Missing bins are NOT zero-filled.


@pytest.mark.parametrize("column,value", [("id", ""), ("date", "nonsense"), ("hour", "24"), ("count", "-1"), ("count", "2.5")])
def test_bad_source_rows_do_not_silently_disappear(column, value):
    frame = pd.DataFrame({"id": ["001"], "date": ["2024-01-01"], "hour": ["9"], "count": ["3"]})
    frame.loc[0, column] = value
    with pytest.raises(ValueError):
        aggregate_chunk(frame, "id", "date", "count", "hour")


def test_cp949_zip_inventory_and_member_inspection(tmp_path):
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("synthetic.csv", "번호,대여일,시간,건수\n001,2024-01-01,9,4\n".encode("cp949"))
    chunks = list(csv_chunks(path, "cp949", "synthetic.csv", chunksize=1))
    assert chunks[0].iloc[0]["번호"] == "001"
    args = argparse.Namespace(file=path, output=tmp_path / "inspection.json", member="synthetic.csv",
                              encoding=None, limit=10, sheet=0, time_col="대여일", hour_col="시간", station_col="번호")
    inspect(args)
    record = json.loads(args.output.read_text())
    assert record["rows_examined"] == 1
    assert record["scope"] == "sample_only_not_full_file_quality"
    assert record["encoding_sample_read_success"] == "cp949"


def test_multimember_archive_requires_explicit_choice(tmp_path):
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.csv", "a\n1\n")
        archive.writestr("b.csv", "different\n2\n")
    with pytest.raises(ValueError, match="Choose --member"):
        list(csv_chunks(path, "utf-8"))


def station_fixture():
    return pd.DataFrame({"station_id": ["old", "new", "far"], "latitude": [37.55, 37.55, 37.7],
                         "longitude": [126.98, 126.98, 127.2], "valid_from": ["2023-01-01", "2025-01-01", "2023-01-01"],
                         "valid_to": ["2025-01-01", "2027-01-01", "2025-01-01"]})


def session_fixture():
    return pd.DataFrame({"event_id": ["synthetic-e"], "session_id": ["synthetic-e-1"], "latitude": [37.55],
                         "longitude": [126.98], "start": ["2024-01-01 18:00"], "end": ["2024-01-01 20:00"]})


def test_current_station_cannot_be_joined_to_past_event():
    result = link(station_fixture(), session_fixture(), 500)
    assert result.station_id.tolist() == ["old"]
    assert result.distance_m.iloc[0] == 0
    assert not result.availability_evidence_present.any()


def test_reversed_coordinates_fail():
    sessions = session_fixture()
    sessions[["latitude", "longitude"]] = sessions[["longitude", "latitude"]].to_numpy()
    with pytest.raises(ValueError, match="Coordinates"):
        link(station_fixture(), sessions, 500)


def panel_fixture():
    times = pd.date_range("2024-01-01", periods=24 * 40, freq="h")
    return pd.DataFrame({"station_id": "synthetic-001", "time": times,
                         "rentals": (times.hour >= 8).astype(int) * 3 + times.dayofweek,
                         "coverage_verified": 1})


def test_hour_gap_is_unknown_not_previous_row_or_zero():
    panel = panel_fixture().drop(index=10)
    result = prepare(panel, 0).set_index("time")
    assert pd.isna(result.loc["2024-01-01 10:00", "rentals"])
    assert pd.isna(result.loc["2024-01-01 11:00", "lag_recent"])


def test_invalid_coverage_masks_labels_and_lags():
    panel = panel_fixture()
    panel.loc[10, "coverage_verified"] = 0
    result = prepare(panel, 0)
    assert pd.isna(result.loc[10, "rentals"])
    assert pd.isna(result.loc[11, "lag_recent"])


def test_daily_lag_keeps_same_hour_when_available():
    panel = panel_fixture()
    panel["rentals"] = np.arange(len(panel))
    result = prepare(panel, 1)
    assert result.loc[48, "lag_day"] == 24
    assert result.loc[48, "lag_recent"] == 46


def test_future_labels_cannot_change_past_features():
    panel = panel_fixture()
    first = prepare(panel, 1)
    panel.loc[panel.time >= "2024-01-20", "rentals"] += 500
    second = prepare(panel, 1)
    cols = ["lag_recent", "lag_day", "lag_week", "mean_24h"]
    cutoff = first.time <= "2024-01-20"
    pd.testing.assert_frame_equal(first.loc[cutoff, cols], second.loc[cutoff, cols])


def test_duplicate_station_hour_fails():
    panel = panel_fixture()
    with pytest.raises(ValueError, match="Duplicate"):
        prepare(pd.concat([panel, panel.iloc[:1]]), 0)


def test_posthoc_event_features_rejected():
    panel = panel_fixture().assign(event_count=1, event_coverage_verified=1, event_window=1,
                                   event_available_at="2025-01-01")
    with pytest.raises(ValueError, match="availability"):
        run(panel, "2024-01-25", "2024-02-01", "2024-02-10", 1, ["event_count"])


def test_unknown_event_coverage_rejected():
    panel = panel_fixture().assign(event_count=0, event_coverage_verified=0, event_window=0,
                                   event_available_at="2023-12-01")
    with pytest.raises(ValueError, match="coverage"):
        run(panel, "2024-01-25", "2024-02-01", "2024-02-10", 1, ["event_count"])


def test_offline_pilot_cli_completes_on_synthetic_data(tmp_path):
    panel = panel_fixture()
    panel["event_count"] = panel.time.dt.dayofweek.eq(5).astype(int)
    panel["event_window"] = panel.event_count
    panel["event_available_at"] = "2023-12-01"
    panel["event_coverage_verified"] = 1
    source = tmp_path / "synthetic_panel.csv"
    panel.to_csv(source, index=False)
    out = tmp_path / "synthetic_result"
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/pilot.py"),
               "--panel", str(source), "--train-end", "2024-01-25", "--valid-end", "2024-02-01",
               "--test-end", "2024-02-10", "--latency-hours", "1", "--event-features", "event_count",
               "--output-dir", str(out)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    meta = json.loads((out / "conditions.json").read_text())
    assert all(n > 0 for n in meta["split_rows"].values())
    assert len(meta["features"]) == 2
    metrics = pd.read_csv(out / "metrics.csv")
    for _, group in metrics.groupby(["split", "segment"]):
        assert group.n.nunique() == 1  # Fair common cohort.
    assert (out / "pilot.png").stat().st_size > 0
