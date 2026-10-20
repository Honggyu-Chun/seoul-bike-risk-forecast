"""Spatially link explicitly normalized, versioned stations and event sessions.

Canonical input schemas are a proposed contract, not verified Seoul/KOPIS fields.
This script never converts a date range into daily performances.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = [np.radians(x) for x in (lat1, lon1, lat2, lon2)]
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6_371_008.8 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def validate_coordinates(frame):
    for col in ["latitude", "longitude"]:
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if not (frame.latitude.between(37, 38) & frame.longitude.between(126, 128)).all():
        raise ValueError("Coordinates missing, reversed, or outside the broad Seoul region; inspect source")


def link(stations, sessions, radius_m):
    if not 0 < radius_m <= 10_000:
        raise ValueError("radius_m must be in (0, 10000]")
    stations, sessions = stations.copy(), sessions.copy()
    for frame in [stations, sessions]:
        validate_coordinates(frame)
    if stations.station_id.isna().any() or sessions.session_id.isna().any() or sessions.event_id.isna().any():
        raise ValueError("IDs must be present")
    if sessions.session_id.duplicated().any():
        raise ValueError("session_id must identify one actual performance/session")
    for frame, columns in [(stations, ["valid_from", "valid_to"]), (sessions, ["start", "end"])]:
        for col in columns:
            frame[col] = pd.to_datetime(frame[col], errors="raise")
            if frame[col].isna().any() or frame[col].dt.tz is not None:
                raise ValueError("Use explicit Asia/Seoul naive timestamps and complete interval endpoints")
        if not (frame[columns[0]] < frame[columns[1]]).all():
            raise ValueError("Intervals must have positive duration")
    for _, group in stations.groupby("station_id"):
        ordered = group.sort_values("valid_from")
        if (ordered.valid_from.iloc[1:].to_numpy() < ordered.valid_to.iloc[:-1].to_numpy()).any():
            raise ValueError("Station metadata validity intervals overlap")
    if "schedule_available_at" in sessions:
        supplied = sessions.schedule_available_at
        sessions["schedule_available_at"] = pd.to_datetime(supplied, errors="raise")
    else:
        sessions["schedule_available_at"] = pd.NaT
    if "schedule_evidence" not in sessions:
        sessions["schedule_evidence"] = pd.NA
    links = []
    for row in sessions.itertuples(index=False):
        # A version must cover the complete session. A station opened afterwards
        # cannot be used as a historical station near this session.
        eligible = stations[(stations.valid_from <= row.start) & (stations.valid_to >= row.end)].copy()
        eligible["distance_m"] = haversine(eligible.latitude, eligible.longitude, row.latitude, row.longitude)
        eligible = eligible[eligible.distance_m <= radius_m]
        for station in eligible.itertuples(index=False):
            links.append({
                "station_id": station.station_id, "event_id": row.event_id, "session_id": row.session_id,
                "start": row.start, "end": row.end, "distance_m": round(station.distance_m, 2),
                "schedule_available_at": row.schedule_available_at, "schedule_evidence": row.schedule_evidence,
                "availability_evidence_present": bool(pd.notna(row.schedule_available_at) and pd.notna(row.schedule_evidence)),
            })
    return pd.DataFrame(links, columns=["station_id", "event_id", "session_id", "start", "end", "distance_m", "schedule_available_at", "schedule_evidence", "availability_evidence_present"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--radius-m", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stations = pd.read_csv(args.stations, dtype={"station_id": "string"})
    sessions = pd.read_csv(args.sessions, dtype={"event_id": "string", "session_id": "string"})
    result = link(stations, sessions, args.radius_m)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    print(f"Linked {len(result)} station-session pairs. This establishes geometry/interval overlap, not predictive lift.")


if __name__ == "__main__":
    main()
