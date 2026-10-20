"""One bounded current capture per provider. Never schedules or loops live collection."""
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import urllib.request
import urllib.parse
import urllib.error
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = ROOT / "data/raw/followup_v4_extra"
KST = ZoneInfo("Asia/Seoul")


def credentials():
    result = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def capture(url, label, redact=()):
    path = RAW / (label + ".json")
    meta = HERE / (label + "_audit.json")
    if path.exists() and meta.exists():
        return json.loads(path.read_text(encoding="utf-8")), json.loads(meta.read_text(encoding="utf-8"))
    started = datetime.now(timezone.utc)
    report = dict(started_at_utc=started.isoformat(), credential_values_omitted=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "PProjectAcademicResearch/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read(2 * 1024 * 1024 + 1)
            report.update(http_status=response.status, server_date_header=response.headers.get("Date"),
                          cache_control=response.headers.get("Cache-Control"), age_header=response.headers.get("Age"))
        report["received_at_utc"] = datetime.now(timezone.utc).isoformat()
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("Response exceeds bounded snapshot size")
        for value in redact:
            if value:
                body = body.replace(value.encode(), b"[REDACTED]")
        payload = json.loads(body)
        path.write_bytes(body)
        report.update(status="received", bytes=len(body), sha256=hashlib.sha256(body).hexdigest(),
                      file=str(path.relative_to(ROOT)))
    except Exception as error:
        report.update(status="error", error_type=type(error).__name__)
        if isinstance(error, urllib.error.HTTPError):
            report["http_status"] = error.code
        payload = {}
    meta.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload, report


def air():
    keys = credentials()
    key = keys.get("SEOUL_API_KEY")
    if not key:
        raise RuntimeError("Seoul key is not configured")
    service = "ListAirQualityByDistrictService"
    url = "http://openapi.seoul.go.kr:8088/" + urllib.parse.quote(key, safe="") + "/json/" + service + "/1/25/"
    payload, report = capture(url, "air_live_20261020", keys.values())
    envelope = payload.get(service, {})
    rows = envelope.get("row", [])
    report.update(service=service, catalog_url="https://data.seoul.go.kr/dataList/OA-1200/A/1/datasetView.do",
                  result_code=envelope.get("RESULT", payload.get("RESULT", {})).get("CODE"),
                  total_reported=envelope.get("list_total_count"), returned_rows=len(rows),
                  fields=sorted({key for row in rows for key in row}), provider_publication_time_field_available=False)
    if rows:
        frame = pd.DataFrame(rows)
        time_values = frame["MSRMT_YMD"].astype(str)
        observed = pd.to_datetime(time_values, format="%Y%m%d%H%M", errors="coerce").dt.tz_localize("Asia/Seoul")
        received = pd.Timestamp(report["received_at_utc"]).tz_convert("Asia/Seoul")
        frame["event_time"] = observed
        frame["received_at"] = received
        frame["event_to_received_minutes"] = (received - observed).dt.total_seconds() / 60
        report.update(received_at_kst=str(received), measured_timestamps=time_values.unique().tolist(),
                      event_to_received_minutes=frame.event_to_received_minutes.describe().to_dict(),
                      distinct_districts=int(frame.MSRSTN_NM.nunique()),
                      empty_field_counts=frame.replace("", pd.NA).isna().sum().to_dict(),
                      status="live_response_verified")
        frame.to_csv(RAW / "model_ready/air_live_20261020.csv", index=False, encoding="utf-8-sig")
    (HERE / "air_live_20261020_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("status", "service", "result_code", "returned_rows", "measured_timestamps", "received_at_kst", "event_to_received_minutes")}, ensure_ascii=False))


def forecast():
    variables = ["temperature_2m", "precipitation", "relative_humidity_2m", "wind_speed_10m", "cloud_cover"]
    params = dict(latitude=37.5665, longitude=126.978, timezone="Asia/Seoul", models="gfs_global",
                  forecast_days=2, wind_speed_unit="ms", hourly=",".join(v + "_previous_day1" for v in variables))
    url = "https://previous-runs-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    payload, report = capture(url, "gfs_day1_live_20261020")
    report.update(source_url=url, model="gfs_global", fixed_lead_hours=24,
                  publication_time_field_available=False, run_initialization_field_available=False)
    if payload.get("hourly"):
        frame = pd.DataFrame(payload["hourly"])
        frame["valid_time"] = pd.to_datetime(frame.pop("time")).dt.tz_localize("Asia/Seoul")
        received = pd.Timestamp(report["received_at_utc"]).tz_convert("Asia/Seoul")
        eligible = frame[frame.valid_time >= received + pd.Timedelta(hours=1)]
        report.update(status="live_forecast_response_verified", received_at_kst=str(received),
                      future_hours_received_at_least_1h_before_target=len(eligible),
                      first_future_example=eligible.head(1).assign(valid_time=lambda f: f.valid_time.astype(str)).to_dict("records"),
                      null_counts=frame.isna().sum().to_dict(),
                      response_fields=list(payload), response_units=payload.get("hourly_units"),
                      caution="This capture proves present future-target availability, not 2025 as-issued publication or exact model-run replacement time")
        frame.to_csv(RAW / "model_ready/gfs_day1_live_20261020.csv", index=False, encoding="utf-8-sig")
    (HERE / "gfs_day1_live_20261020_audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("status", "received_at_kst", "future_hours_received_at_least_1h_before_target", "first_future_example")}, ensure_ascii=False))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["air", "forecast"])
    args = parser.parse_args()
    air() if args.provider == "air" else forecast()
