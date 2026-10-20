"""Inspect downloaded files and explicitly aggregate rentals without inventing zeros.

This utility does not assume a Seoul source's schema. Choose columns only after
opening an actual source file. All encodings are strict; malformed rows fail.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd


@contextmanager
def source_stream(path, member=None):
    path = Path(path)
    if zipfile.is_zipfile(path) and path.suffix.lower() != ".xlsx":
        with zipfile.ZipFile(path) as archive:
            candidates = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if member is None:
                if len(candidates) != 1:
                    raise ValueError("Choose --member from ZIP inventory; do not merge different schemas implicitly")
                member = candidates[0]
            if not member.lower().endswith(".csv"):
                raise ValueError("Only CSV archive members are supported")
            with archive.open(member) as stream:
                yield stream
    else:
        if member:
            raise ValueError("--member is only valid for ZIP files")
        with path.open("rb") as stream:
            yield stream


def csv_chunks(path, encoding, member=None, chunksize=100_000):
    with source_stream(path, member) as stream:
        yield from pd.read_csv(stream, encoding=encoding, dtype="string", chunksize=chunksize)


def station_ids(series):
    # Keep prefixes and leading zeros. Numeric equivalence is not an ID mapping.
    return series.astype("string").str.strip().replace("", pd.NA)


def timestamps(frame, time_col, hour_col=None):
    value = pd.to_datetime(frame[time_col], errors="coerce", format="mixed")
    if getattr(value.dtype, "tz", None) is not None:
        raise ValueError("Normalize source timestamps to timezone-naive Asia/Seoul before aggregation")
    if hour_col:
        if (value.dropna() != value.dropna().dt.normalize()).any():
            raise ValueError("Date column contains times; do not add an hour column to it")
        hour = pd.to_numeric(frame[hour_col], errors="coerce")
        valid_hour = hour.notna() & hour.between(0, 23) & hour.eq(np.floor(hour))
        value = value + pd.to_timedelta(hour.where(valid_hour), unit="h")
    return value


def aggregate_chunk(frame, station_col, time_col, count_col=None, hour_col=None):
    ids = station_ids(frame[station_col])
    time = timestamps(frame, time_col, hour_col)
    counts = pd.to_numeric(frame[count_col], errors="coerce") if count_col else pd.Series(1, index=frame.index)
    invalid_count = counts.isna() | ~np.isfinite(counts) | (counts < 0) | (counts != np.floor(counts))
    invalid = {"station": int(ids.isna().sum()), "time": int(time.isna().sum()), "count": int(invalid_count.sum())}
    if any(invalid.values()):
        raise ValueError(f"Invalid source rows: {invalid}; inspect/resolve explicitly, no silent dropping")
    normalized = pd.DataFrame({"station_id": ids, "time": time.dt.floor("h"), "rentals": counts.astype("int64")})
    return normalized.groupby(["station_id", "time"], sort=True, as_index=False).rentals.sum()


def inspect(args):
    record = {"file": str(args.file), "bytes": args.file.stat().st_size, "sample_limit": args.limit}
    with args.file.open("rb") as stream:
        record["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    if zipfile.is_zipfile(args.file) and args.file.suffix.lower() != ".xlsx":
        with zipfile.ZipFile(args.file) as archive:
            record["zip_members"] = [
                {"name": i.filename, "bytes": i.file_size, "compressed_bytes": i.compress_size, "crc": i.CRC}
                for i in archive.infolist()
            ]
        if not args.member:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            print("ZIP inventory saved; choose --member to inspect rows:", args.output)
            return
    if args.file.suffix.lower() == ".xlsx":
        frame = pd.read_excel(args.file, nrows=args.limit, dtype="string", sheet_name=args.sheet)
        record["sheet"] = args.sheet
    else:
        # Try candidate encodings against a parsed sample, without replacement.
        failures = []
        for encoding in ([args.encoding] if args.encoding else ["utf-8-sig", "cp949", "euc-kr"]):
            try:
                with source_stream(args.file, args.member) as stream:
                    frame = pd.read_csv(stream, encoding=encoding, nrows=args.limit, dtype="string")
                record["encoding_sample_read_success"] = encoding
                break
            except UnicodeDecodeError as error:
                failures.append(type(error).__name__ + ": " + encoding)
        else:
            raise ValueError("No strict sample decoding succeeded: " + ", ".join(failures))
    record.update(
        member=args.member, rows_examined=len(frame), scope="sample_only_not_full_file_quality",
        columns=list(frame.columns),
        nulls_in_sample={c: int(frame[c].isna().sum()) for c in frame},
        exact_duplicate_rows_in_sample=int(frame.duplicated().sum()),
        sample_rows=json.loads(frame.head(5).to_json(orient="records", force_ascii=False)),
    )
    if args.time_col:
        time = timestamps(frame, args.time_col, args.hour_col)
        record["time_sample"] = {"invalid": int(time.isna().sum()), "min": str(time.min()), "max": str(time.max())}
    if args.station_col:
        ids = station_ids(frame[args.station_col])
        record["station_sample"] = {"null": int(ids.isna().sum()), "distinct": int(ids.nunique()), "examples": ids.dropna().unique()[:10].tolist()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"Examined {len(frame)} sample rows; {len(frame.columns)} columns. Report: {args.output}")


def aggregate(args):
    if args.mode == "counts" and not args.count_col:
        raise ValueError("counts mode requires a verified --count-col; rows are not rental counts")
    if args.mode == "trips" and args.count_col:
        raise ValueError("trips mode counts rows, not a count column")
    result = pd.DataFrame(columns=["station_id", "time", "rentals"])
    input_rows = 0
    for chunk in csv_chunks(args.file, args.encoding, args.member):
        input_rows += len(chunk)
        grouped = aggregate_chunk(chunk, args.station_col, args.time_col, args.count_col, args.hour_col)
        result = pd.concat([result, grouped], ignore_index=True)
        result = result.groupby(["station_id", "time"], as_index=False).rentals.sum()
    if result.empty:
        raise ValueError("No source records; zero rows do not establish zero demand")
    result = result.sort_values(["station_id", "time"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    metadata = {
        "source": str(args.file), "member": args.member, "encoding": args.encoding,
        "mode": args.mode, "station_col": args.station_col, "time_col": args.time_col,
        "hour_col": args.hour_col, "count_col": args.count_col,
        "input_rows": input_rows, "observed_station_hours": len(result),
        "total_rentals": int(result.rentals.sum()), "timezone_assumption": "Asia/Seoul",
        "min_time": str(result.time.min()), "max_time": str(result.time.max()),
        "limitations": [
            "No missing station-hour was filled with zero",
            "No duplicate source row was automatically removed: confirm source-specific deduplication semantics",
            "No claim about station operating periods, feed completeness, or source publication delay",
            "Forecasting requires an independently verified coverage mask and continuous hourly panel",
        ],
    }
    args.output.with_suffix(".metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("file", type=Path)
    p.add_argument("--member")
    p.add_argument("--encoding")
    p.add_argument("--sheet", default=0)
    p.add_argument("--limit", type=int, default=10_000)
    p.add_argument("--time-col")
    p.add_argument("--hour-col")
    p.add_argument("--station-col")
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(func=inspect)
    p = sub.add_parser("aggregate")
    p.add_argument("file", type=Path)
    p.add_argument("--member")
    p.add_argument("--encoding", required=True)
    p.add_argument("--mode", choices=["trips", "counts"], required=True)
    p.add_argument("--station-col", required=True)
    p.add_argument("--time-col", required=True)
    p.add_argument("--hour-col")
    p.add_argument("--count-col")
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(func=aggregate)
    args = parser.parse_args()
    if getattr(args, "limit", 1) <= 0:
        parser.error("--limit must be positive")
    args.func(args)


if __name__ == "__main__":
    main()
