"""Keyless, bounded collection of two primary-source historical feature candidates.

No model training and no outcome values are read here. No KOPIS requests are made.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import io
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEST = ROOT / "data/raw/followup_v4_extra"
DEST.mkdir(parents=True, exist_ok=True)


def get(url, path, limit=20 * 1024 * 1024):
    if path.exists():
        return path.read_bytes()
    request = urllib.request.Request(url, headers={"User-Agent": "PProjectResearch/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError("Response exceeds local collection size limit")
    path.write_bytes(body)
    return body


def entry(url, path, **extra):
    return dict(source_url=url, path=str(path.relative_to(ROOT)),
                bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                received_at_utc=datetime.now(timezone.utc).isoformat(), **extra)


def air():
    catalog = "https://data.seoul.go.kr/dataList/OA-2221/S/1/datasetView.do"
    # infSeq=3 and seq=36 come from the public catalog's frmFile and downloadFile.
    url = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?useCache=false&infId=OA-2221&infSeq=3&seq=36"
    path = DEST / "seoul_air_2025.zip"
    body = get(url, path)
    if not zipfile.is_zipfile(io.BytesIO(body)):
        raise ValueError("Public archive response is not ZIP")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert archive.testzip() is None
        members = [dict(name=item.filename, bytes=item.file_size, crc32=f"{item.CRC:08x}")
                   for item in archive.infolist()]
        for name in archive.namelist()[:1]:
            sample = archive.read(name)[:1600]
            for encoding in ("utf-8-sig", "cp949", "utf-16"):
                try:
                    print("archive_head", encoding, sample.decode(encoding))
                    break
                except UnicodeError:
                    continue
    report = entry(url, path, catalog_url=catalog, crc_passed=True, members=members,
                   catalog_file_modified="2026-04-01", historical_as_issued_verified=False)
    (HERE / "air_download_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("air_archive", report["bytes"], members)


VARIABLES = ["temperature_2m", "precipitation", "relative_humidity_2m", "wind_speed_10m", "cloud_cover"]
POINTS = [("center", 37.5665, 126.9780), ("northwest", 37.65, 126.85),
          ("northeast", 37.65, 127.10), ("southwest", 37.45, 126.85),
          ("southeast", 37.45, 127.10)]


def forecast(probe=False):
    records = []
    selected = POINTS[:1] if probe else POINTS
    for name, latitude, longitude in selected:
        params = dict(latitude=latitude, longitude=longitude,
                      start_date="2025-01-01", end_date="2025-01-07" if probe else "2025-06-30",
                      timezone="Asia/Seoul", hourly=",".join(v + "_previous_day1" for v in VARIABLES),
                      models="gfs_global", wind_speed_unit="ms")
        url = "https://previous-runs-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
        path = DEST / (f"gfs_day1_{name}_" + ("probe.json" if probe else "2025h1.json"))
        try:
            body = get(url, path)
            payload = json.loads(body)
            if payload.get("error"):
                raise ValueError(str(payload.get("reason")))
            n = len(payload.get("hourly", {}).get("time", []))
            report = entry(url, path, point_id=name, request_latitude=latitude, request_longitude=longitude,
                           grid_latitude=payload.get("latitude"), grid_longitude=payload.get("longitude"),
                           model="gfs_global", fixed_lead_hours=24, n_hours=n,
                           null_counts={k: sum(v is None for v in values) for k, values in payload.get("hourly", {}).items() if k != "time"},
                           timezone=payload.get("timezone"), units=payload.get("hourly_units"),
                           individual_run_initialization_and_publication_timestamp_available=False)
            records.append(report)
            print("forecast", name, n, report["null_counts"], flush=True)
            time.sleep(1)
        except urllib.error.HTTPError as error:
            response = error.read(4000).decode("utf-8", errors="replace")
            records.append(dict(point_id=name, status="error", http_status=error.code, response=response))
            print("forecast_error", name, error.code, response, flush=True)
            break
    out = "forecast_probe_manifest.json" if probe else "forecast_download_manifest.json"
    (HERE / out).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["air", "forecast_probe", "forecast"])
    args = parser.parse_args()
    if args.provider == "air":
        air()
    else:
        forecast(probe=args.provider == "forecast_probe")
