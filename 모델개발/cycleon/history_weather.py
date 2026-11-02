"""Resumable, quota-aware historical grid API acquisition.

Each API response contains the whole national grid, so all Seoul points share
one request. Archive retrieval time is NOT a fabricated historical receipt time.
Only development years are enumerated. Final-period collection needs its own gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from contextlib import contextmanager
import gzip
import hashlib
import json
from .platform_support import msvcrt
import os
from pathlib import Path
import sqlite3
import shutil
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

import numpy as np

from .common import KST, ROOT, instant, now, private_values, save_json, stamp
from .collectors import SameHostRedirect

OUT = ROOT / "artifacts/cycleon_service_v1"
CACHE = ROOT / "data/raw/cycleon_kma_history_api"
VARIABLES = ("T1H", "RN1", "REH", "WSD", "PTY")
GRID_SIZE = 149 * 253


def parse_grid(data: bytes, variable: str):
    text = data.decode("ascii")
    # A full-string grammar prevents HTML/error numbers becoming a weather grid.
    import re
    if re.search(r"[^0-9eE+.,\-\s]", text):
        raise ValueError("not_a_numeric_grid")
    values = np.fromstring(text.replace(",", " "), sep=" ")
    if values.size != GRID_SIZE:
        raise ValueError("unexpected_grid_length")
    ranges = {"T1H": (-90, 60), "RN1": (0, 2000), "REH": (0, 100), "WSD": (0, 150), "PTY": (0, 7)}
    low, high = ranges[variable]
    valid = np.isfinite(values) & (values >= low) & (values <= high)
    result = np.where(valid, values, np.nan).astype(np.float32)
    if not valid.any():
        raise ValueError("all_grid_cells_missing")
    return result


def job_parameters(base: datetime, variable: str, horizon: int | None):
    if variable not in VARIABLES or horizon not in (None, 1, 2, 3):
        raise ValueError("Unsupported weather variable or horizon")
    local = base.astimezone(KST)
    if horizon is None:
        issue = local - timedelta(hours=1)
        endpoint = "nph-dfs_odam_grd"
        params = {"tmfc": issue.strftime("%Y%m%d%H%M"), "vars": variable}
        valid_at = issue
    else:
        # Same vintage as the operational ultra-short path at exact-hour base.
        issue = local - timedelta(minutes=30)
        valid_at = local + timedelta(hours=horizon)
        endpoint = "nph-dfs_vsrt_grd"
        params = {"tmfc": issue.strftime("%Y%m%d%H%M"), "tmef": valid_at.strftime("%Y%m%d%H"), "vars": variable}
    return {"endpoint": endpoint, "parameters": params, "variable": variable, "base_at": stamp(base),
            "horizon": horizon, "requested_issue_at": stamp(issue), "requested_valid_at": stamp(valid_at)}


def job_key(job):
    p = job["parameters"]
    return job["endpoint"] + "_" + p["tmfc"] + "_" + p.get("tmef", "OBS") + "_" + p["vars"]


def iter_jobs():
    q = np.load(OUT / "data/inventory_development.npy", mmap_mode="r")
    # This only avoids hours that cannot produce ANY valid inventory prediction;
    # no eligible station or training row is sampled out.
    useful = np.array([np.isfinite(q[t]).any() for t in range(len(q))], bool)
    start = datetime(2022, 1, 1, tzinfo=KST)
    end = datetime(2025, 7, 1, tzinfo=KST)
    # Current-weather ablation can finish before the larger forecast archive.
    for horizon in (None, 1, 2, 3):
        for t in np.flatnonzero(useful):
            base = start + timedelta(hours=int(t))
            if horizon and base + timedelta(hours=horizon) >= end:
                continue
            for variable in VARIABLES:
                yield job_parameters(base, variable, horizon)


class Ledger:
    def __init__(self, path):
        self.path = path
        with self.db() as db:
            db.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,day TEXT,requested_at TEXT,job_key TEXT,
                reserved_bytes INTEGER,charged_bytes INTEGER,status TEXT);
            CREATE INDEX IF NOT EXISTS requests_day ON requests(day);
            CREATE TABLE IF NOT EXISTS cache(job_key TEXT PRIMARY KEY,completed_at TEXT,path TEXT,sha256 TEXT);
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, key, at, count_cap, byte_cap):
        day = at.astimezone(KST).date().isoformat()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            count, used = db.execute("SELECT count(*),coalesce(sum(reserved_bytes+charged_bytes),0) FROM requests WHERE day=?", (day,)).fetchone()
            if count >= count_cap or used + 500000 > byte_cap:
                return None
            return db.execute("INSERT INTO requests(day,requested_at,job_key,reserved_bytes,charged_bytes,status) VALUES(?,?,?,500000,0,'reserved')",
                              (day, stamp(at), key)).lastrowid

    def complete(self, request_id, charged_bytes, success):
        with self.db() as db:
            db.execute("UPDATE requests SET reserved_bytes=0,charged_bytes=?,status=? WHERE id=?",
                       (charged_bytes, "success" if success else "failed", request_id))

    def cached(self, key):
        with self.db() as db:
            return db.execute("SELECT path,sha256 FROM cache WHERE job_key=?", (key,)).fetchone()

    def done(self, key, path, sha256):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?,?)", (key, stamp(now()), str(path), sha256))

    def stats(self):
        with self.db() as db:
            return {"cached_responses": db.execute("SELECT count(*) FROM cache").fetchone()[0],
                "daily_usage": [{"day": day, "requests": count, "charged_or_reserved_bytes": charged} for day, count, charged in db.execute(
                    "SELECT day,count(*),sum(reserved_bytes+charged_bytes) FROM requests GROUP BY day")]}


def run(max_requests=None):
    CACHE.mkdir(parents=True, exist_ok=True)
    state_path = OUT / "history_weather_state.json"
    policy = {"local_daily_request_cap": 14000, "local_daily_byte_cap": 4_000_000_000,
              "minimum_interval_seconds": 1.5, "official_general_member_requests": 20000,
              "official_general_member_bytes_description": "5GB/day", "source": "https://apihub.kma.go.kr/apiInfo.do",
              "actual_account_limits": "may_differ; stop_on_provider_access_or_quota_errors"}
    save_json(OUT / "history_weather_policy.json", policy)
    lock = (CACHE / "collector.lock").open("a+b")
    if lock.tell() == 0:
        lock.write(b"0"); lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise RuntimeError("Another historical weather collector is active") from None
    ledger = Ledger(CACHE / "requests.sqlite3")
    key = private_values()["KMA_APIHUB_AUTH_KEY"]
    opener = build_opener(SameHostRedirect())
    completed_this_run = 0
    try:
        for job in iter_jobs():
            name = job_key(job)
            cached = ledger.cached(name)
            if cached and Path(cached[0]).exists():
                continue
            while shutil.disk_usage(CACHE).free < 20 * 2**30:
                save_json(state_path, {"at": stamp(now()), "pid": os.getpid(), "status": "waiting_disk_space",
                    "required_free_GiB": 20, "next_job": job, "usage": ledger.stats(), "final_H2_opened": False})
                time.sleep(60)
            daydir = CACHE / job["parameters"]["tmfc"][:6]; daydir.mkdir(exist_ok=True)
            final = daydir / (name + ".txt.gz")
            for attempt in range(1, 4):
                reservation = ledger.reserve(name, now(), policy["local_daily_request_cap"], policy["local_daily_byte_cap"])
                while reservation is None:
                    save_json(state_path, {"at": stamp(now()), "pid": os.getpid(), "status": "waiting_daily_budget_reset", "next_job": job,
                        "usage": ledger.stats(), "final_H2_opened": False})
                    time.sleep(60)
                    reservation = ledger.reserve(name, now(), policy["local_daily_request_cap"], policy["local_daily_byte_cap"])
                tick = time.monotonic()
                url = "https://apihub.kma.go.kr/api/typ01/cgi-bin/url/" + job["endpoint"] + "?" + urlencode(job["parameters"] | {"authKey": key})
                try:
                    with opener.open(Request(url, headers={"User-Agent": "CycleOn-Research/1.0", "Accept-Encoding": "gzip"}), timeout=20) as response:
                        transport = response.read(1_000_001)
                        data = gzip.decompress(transport) if response.headers.get("Content-Encoding", "").lower() == "gzip" else transport
                    charged = max(len(data), len(transport))
                    if len(data) > 1_000_000 or key.encode() in data:
                        raise ValueError("invalid_response_size_or_secret_echo")
                    grid = parse_grid(data, job["variable"])
                    ledger.complete(reservation, charged, True)
                    temp = final.with_name(final.name + ".partial")
                    with gzip.open(temp, "wb", compresslevel=6) as f:
                        f.write(data)
                    temp.replace(final)
                    sha256 = hashlib.sha256(data).hexdigest()
                    save_json(final.with_suffix(".json"), job | {"retrieved_at": stamp(now()), "historical_received_at": None,
                        "historical_publication_receipt_verified": False, "timing_basis": "archive_query_issue_time; historical_availability_requires_disclosed_schedule_assumption",
                        "bytes": len(data), "transport_bytes": len(transport), "raw_sha256": sha256,
                        "grid_width": 149, "grid_height": 253, "valid_grid_cells": int(np.isfinite(grid).sum()),
                        "source_documentation": "https://apihub.kma.go.kr/apiList.do?seqApi=10&seqApiSub=286"})
                    ledger.done(name, final, sha256); completed_this_run += 1
                    save_json(state_path, {"at": stamp(now()), "pid": os.getpid(), "status": "collecting", "latest_job": job,
                        "latest_raw_bytes": len(data), "completed_this_run": completed_this_run, "usage": ledger.stats(), "final_H2_opened": False})
                    break
                except HTTPError as error:
                    ledger.complete(reservation, 500000, False)
                    save_json(OUT / "history_weather_failure.json", {"at": stamp(now()), "job": job, "http_status": error.code, "attempt": attempt,
                        "stop_reason": "provider_access_or_quota" if error.code in (401, 403, 429) else "http_error"})
                    if error.code in (401, 403, 429) or attempt == 3:
                        raise RuntimeError("Historical weather provider refused; inspect safe failure metadata") from None
                except (URLError, TimeoutError, OSError, ValueError, UnicodeError) as error:
                    ledger.complete(reservation, 500000, False)
                    save_json(OUT / "history_weather_failure.json", {"at": stamp(now()), "job": job, "error_type": type(error).__name__, "attempt": attempt,
                        "stop_reason": "transport_or_grid_schema_error; no_empty_or_zero_weather_substitution"})
                    if attempt == 3:
                        raise RuntimeError("Historical weather failure; preserved hash/metadata requires review") from None
                finally:
                    delay = policy["minimum_interval_seconds"] - (time.monotonic() - tick)
                    if delay > 0:
                        time.sleep(delay)
                time.sleep(30 * attempt)
            if max_requests and completed_this_run >= max_requests:
                return
        save_json(OUT / "history_weather_complete.json", {"at": stamp(now()), "usage": ledger.stats(), "final_H2_opened": False,
            "ready_for": "grid_orientation_and_vintage_audit_then_conditional_historical_feature_join", "whole_project_complete": False})
    finally:
        lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1); lock.close()


if __name__ == "__main__":
    run()
