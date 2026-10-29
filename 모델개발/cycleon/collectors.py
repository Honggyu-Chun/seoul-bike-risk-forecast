"""Bounded clients. Provider keys and authenticated URLs never enter logs/storage."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
import uuid

from .common import KST, canonical, instant, now, private_values, stamp
from .contracts import Forecast, Observation, Station
from .store import BudgetExceeded, Store


class CollectionError(RuntimeError):
    """Only bounded, developer-owned codes may be passed to this exception."""


def weather_number(variable, raw):
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0 if variable == "RN1" and raw == "강수없음" else None
    ranges = {"T1H": (-90, 60), "TMP": (-90, 60), "RN1": (0, 2000), "REH": (0, 100),
              "WSD": (0, 150), "VEC": (0, 360), "UUU": (-150, 150), "VVV": (-150, 150),
              "PTY": (0, 7), "SKY": (1, 4), "LGT": (0, 100)}
    low, high = ranges[variable]
    if not math.isfinite(value) or not low <= value <= high:
        return None
    return value


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(req.full_url).hostname != urlsplit(newurl).hostname:
            raise CollectionError("cross_host_redirect_rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def kma_grid(latitude: float, longitude: float) -> tuple[int, int]:
    # KMA DFS Lambert conformal conic projection: RE=6371.00877 km, GRID=5 km.
    rad = math.pi / 180
    slat1, slat2, olon, olat = 30 * rad, 60 * rad, 126 * rad, 38 * rad
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(math.tan(math.pi / 4 + slat2 / 2) / math.tan(math.pi / 4 + slat1 / 2))
    sf = math.tan(math.pi / 4 + slat1 / 2) ** sn * math.cos(slat1) / sn
    ro = 6371.00877 / 5 * sf / math.tan(math.pi / 4 + olat / 2) ** sn
    ra = 6371.00877 / 5 * sf / math.tan(math.pi / 4 + latitude * rad / 2) ** sn
    theta = ((longitude - 126 + 180) % 360 - 180) * rad * sn
    return math.floor(ra * math.sin(theta) + 43 + .5), math.floor(ro - ra * math.cos(theta) + 136 + .5)


class Client:
    def __init__(self, store: Store, policy: dict, values=None, clock=now, sleeper=time.sleep):
        self.store, self.policy = store, policy
        self.values = private_values() if values is None else values
        self.clock, self.sleep = clock, sleeper
        self.opener = build_opener(SameHostRedirect())

    def get_json(self, provider: str, url: str):
        cfg = self.policy["providers"][provider]
        # One provider request at a time in this client; the DB also prevents races
        # with a second process. No unbounded automatic retries.
        for attempt in range(2):
            at = self.clock()
            try:
                request_id = self.store.reserve_request(provider, at.astimezone(KST).date().isoformat(),
                    cfg["local_daily_budget"], at, cfg["minimum_interval_seconds"])
                break
            except BudgetExceeded as error:
                if str(error) != "local_minimum_interval" or attempt:
                    raise
                self.sleep(cfg["minimum_interval_seconds"] + .05)
        try:
            with self.opener.open(Request(url, headers={"User-Agent": "CycleOn-Research/1.0", "Accept": "application/json"}), timeout=18) as response:
                body = response.read(10_000_001)
            if len(body) > 10_000_000:
                raise CollectionError("response_too_large")
            try:
                obj = json.loads(body.decode("utf-8-sig"))
            except (ValueError, UnicodeError):
                raise CollectionError("unexpected_non_json_response") from None
            self.store.finish_request(request_id, "received")
            return obj, self.clock(), request_id
        except HTTPError as error:
            self.store.finish_request(request_id, "http_error", str(error.code))
            if error.code in {401, 403, 429}:
                self.store.block_provider(provider, self.clock() + timedelta(hours=24 if error.code != 429 else 1), "http_access_or_quota")
            raise CollectionError(f"http_{error.code}") from None
        except (URLError, TimeoutError, OSError):
            self.store.finish_request(request_id, "transport_error")
            raise CollectionError("transport_error") from None
        except CollectionError:
            self.store.finish_request(request_id, "schema_error")
            raise

    def provider_result(self, provider, request_id, code, accepted):
        safe_code = str(code) if re.fullmatch(r"[A-Za-z0-9_-]{1,24}", str(code)) else "UNKNOWN"
        self.store.finish_request(request_id, "accepted" if accepted else "provider_error", safe_code)
        if not accepted:
            # Explicit quota/access errors stop the provider; don't probe the limit.
            if safe_code in {"INFO-200", "ERROR-300", "ERROR-301", "ERROR-302", "ERROR-303", "ERROR-337", "20", "22", "30", "31", "32", "33"}:
                self.store.block_provider(provider, self.clock() + timedelta(hours=24), "provider_access_or_quota")
            raise CollectionError("provider_" + safe_code)

    def inventory(self):
        key = self.values.get("SEOUL_COLLECTION_API_KEY")
        if not key:
            raise CollectionError("missing_collection_key")
        started, capture_id = self.clock(), str(uuid.uuid4())
        records, stations, pages, total = [], [], [], None
        terminal_seen = False
        for start in range(1, 20001, 1000):
            obj, received, request_id = self.get_json("seoul", f"http://openapi.seoul.go.kr:8088/{key}/json/bikeList/{start}/{start + 999}/")
            payload = obj.get("rentBikeStatus", {})
            result = payload.get("RESULT", obj.get("RESULT", {}))
            if result.get("CODE") == "INFO-200" and records and len(pages) * 1000 == len(records):
                self.store.finish_request(request_id, "accepted_terminal_empty", "INFO-200")
                terminal_seen = True
                break
            self.provider_result("seoul", request_id, result.get("CODE"), result.get("CODE") == "INFO-000")
            try:
                count = int(payload["list_total_count"])
                rows = payload["row"]
                # bikeList reports list_total_count as THIS PAGE'S count on
                # current responses. Do not mistake the first 1,000 for Seoul.
                if count > len(rows) and total is not None and total != count:
                    raise CollectionError("pagination_total_changed")
                if count > len(rows):
                    total = count
                if not 0 < len(rows) <= 1000 or count < len(rows) or (total and total > 20000):
                    raise CollectionError("incomplete_inventory_page")
                page_invalid = 0
                for r in rows:
                    source_id, name = str(r["stationId"]), str(r["stationName"])
                    # No provider observation timestamp exists in bikeList.
                    # Invalid records are retained as missing; never replace them by 0.
                    try:
                        value = float(r["parkingBikeTotCnt"])
                        valid = math.isfinite(value) and value >= 0 and value.is_integer()
                    except (TypeError, ValueError):
                        value, valid = None, False
                    if not valid:
                        value = None; page_invalid += 1
                    # API ID is authoritative. Historical numeric name mapping must
                    # be checked separately before a historical model can use it.
                    sid = source_id
                    lat, lon = float(r["stationLatitude"]), float(r["stationLongitude"])
                    coordinate_valid = math.isfinite(lat) and math.isfinite(lon) and 37 <= lat <= 38 and 126 <= lon <= 128
                    station = Station(station_id=sid, source_station_id=source_id, name=name,
                        latitude=lat if coordinate_valid else None, longitude=lon if coordinate_valid else None,
                        valid_from=received, received_at=received, revision=capture_id,
                        identity_status="verified" if coordinate_valid and re.fullmatch(r"ST-\d+", source_id) else "unverified")
                    # Invalid coordinates are unknown, not invented locations.
                    stations.append(station)
                    records.append(Observation(source="seoul_bike", entity_id=sid, event_key=capture_id,
                        variable="inventory", value=value, unit="bikes", quality="valid" if valid else "invalid",
                        raw_value=str(r["parkingBikeTotCnt"])[:160], received_at=received, revision="0", observed_at=None, timing_basis="received"))
                pages.append({"first": start, "rows": len(rows), "received_at": stamp(received), "invalid_quantities": page_invalid})
            except (KeyError, TypeError, ValueError):
                raise CollectionError("inventory_schema_error") from None
            if len(rows) < 1000 or (total is not None and len(records) == total):
                terminal_seen = True
                break
        completed = self.clock()
        if not terminal_seen or (total is not None and len(records) != total):
            raise CollectionError("inventory_pagination_not_complete")
        if (completed - started).total_seconds() > self.policy.get("inventory_max_capture_duration_seconds", 45):
            raise CollectionError("inventory_capture_too_slow")
        self.store.commit_inventory(capture_id, started, completed, len(records), stations, records,
            {"pages": pages, "pagination_schema": 2, "provider_observed_at_present": False})
        return {"capture_id": capture_id, "rows": len(records), "pages": len(pages),
                "started_at": stamp(started), "completed_at": stamp(completed),
                "elapsed_seconds": (completed - started).total_seconds(),
                "eligible_station_coordinates": sum(s.identity_status == "verified" for s in stations),
                "planned_requests_per_day_at_one_minute": (len(pages) + int(len(records) % 1000 == 0)) * 1440}

    def weather(self, grid: tuple[int, int], forecast: bool):
        key = self.values.get("DATA_GO_KR_SERVICE_KEY")
        if not key:
            raise CollectionError("missing_public_data_key")
        current = self.clock().astimezone(KST)
        # Ncst HH:00 published after HH:40. Ultra-short forecast HH:30
        # published after HH:45. Use conservative 5-minute publication margins.
        if forecast:
            base = (current - timedelta(minutes=50)).replace(minute=30, second=0, microsecond=0)
            if base > current - timedelta(minutes=20):
                base -= timedelta(hours=1)
        else:
            base = (current - timedelta(minutes=45)).replace(minute=0, second=0, microsecond=0)
        endpoint = "getUltraSrtFcst" if forecast else "getUltraSrtNcst"
        params = {"serviceKey": key, "pageNo": 1, "numOfRows": 1000, "dataType": "JSON",
                  "base_date": base.strftime("%Y%m%d"), "base_time": base.strftime("%H%M"), "nx": grid[0], "ny": grid[1]}
        obj, received, request_id = self.get_json("kma_portal", "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/" + endpoint + "?" + urlencode(params))
        response = obj.get("response", {})
        code = response.get("header", {}).get("resultCode")
        self.provider_result("kma_portal", request_id, code, str(code) == "00")
        try:
            body = response["body"]
            items = body["items"]["item"]
            if int(body["totalCount"]) != len(items):
                raise CollectionError("weather_truncated")
            rows = []
            units = {"T1H": "degC", "TMP": "degC", "RN1": "mm", "REH": "%", "WSD": "m/s", "VEC": "degrees", "UUU": "m/s", "VVV": "m/s", "PTY": "code", "SKY": "code", "LGT": "code"}
            for r in items:
                variable = r["category"]
                if variable not in units:
                    continue
                raw = r["fcstValue"] if forecast else r["obsrValue"]
                # Censored rainfall intervals remain unknown numerically; retain
                # their original text rather than inventing a midpoint.
                value = weather_number(variable, raw)
                quality = "valid" if value is not None else "missing"
                issue = datetime.strptime(r["baseDate"] + r["baseTime"], "%Y%m%d%H%M").replace(tzinfo=KST)
                gid = f"{grid[0]},{grid[1]}"
                if forecast:
                    target = datetime.strptime(r["fcstDate"] + r["fcstTime"], "%Y%m%d%H%M").replace(tzinfo=KST)
                    rows.append(Forecast(source="kma_ultra_forecast", grid_id=gid, variable=variable, value=value,
                        raw_value=str(raw)[:160], unit=units[variable], issued_at=issue, valid_at=target, received_at=received, quality=quality,
                        revision=hashlib.sha256(str(raw).encode()).hexdigest()[:16]))
                else:
                    rows.append(Observation(source="kma_current", entity_id=gid, event_key=stamp(issue), variable=variable,
                        value=value, raw_value=str(raw)[:160], unit=units[variable], observed_at=None, issued_at=issue,
                        received_at=received, quality=quality, timing_basis="issued_and_received",
                        revision="base_issue_v2_" + hashlib.sha256(str(raw).encode()).hexdigest()[:16]))
        except (ValueError, KeyError, TypeError):
            raise CollectionError("weather_schema_error") from None
        if forecast:
            self.store.append_forecasts(rows)
        else:
            self.store.append_observations(rows)
        return {"grid_id": f"{grid[0]},{grid[1]}", "kind": endpoint, "rows": len(rows), "received_at": stamp(received)}

    def air(self):
        key = self.values.get("DATA_GO_KR_SERVICE_KEY")
        if not key:
            raise CollectionError("missing_public_data_key")
        params = {"serviceKey": key, "returnType": "json", "numOfRows": 1000, "pageNo": 1, "sidoName": "서울", "ver": "1.3"}
        obj, received, request_id = self.get_json("airkorea", "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty?" + urlencode(params))
        response = obj.get("response", {})
        code = response.get("header", {}).get("resultCode")
        self.provider_result("airkorea", request_id, code, str(code) == "00")
        try:
            body = response["body"]
            if int(body["totalCount"]) != len(body["items"]):
                raise CollectionError("air_truncated")
            rows = []
            for r in body["items"]:
                observed = datetime.strptime(r["dataTime"], "%Y-%m-%d %H:%M").replace(tzinfo=KST)
                for variable, unit in [("pm10Value", "ug/m3"), ("pm25Value", "ug/m3"), ("o3Value", "ppm"), ("no2Value", "ppm")]:
                    try:
                        value = float(r.get(variable, "-"))
                        if not math.isfinite(value) or value < 0:
                            value = None
                    except (ValueError, TypeError):
                        value = None
                    if r.get(variable.replace("Value", "Flag")):
                        value = None
                    rows.append(Observation(source="airkorea", entity_id=r["stationName"], event_key=stamp(observed),
                        variable=variable, value=value, raw_value=str(r.get(variable))[:160], unit=unit, observed_at=observed, received_at=received,
                        quality="valid" if value is not None else "missing", timing_basis="observed_and_received",
                        revision=hashlib.sha256(str(r.get(variable)).encode()).hexdigest()[:16]))
            self.store.append_observations(rows)
            return {"rows": len(rows), "received_at": stamp(received)}
        except (ValueError, KeyError, TypeError):
            raise CollectionError("air_schema_error") from None
