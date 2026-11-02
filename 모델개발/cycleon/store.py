"""Append-only SQLite reference store; a capture becomes visible atomically.

Backend implementations may use another database. Cursor values refer to committed
ingestion order, not observation time. A repeated conflicting revision is excluded.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .common import canonical, instant, now, stamp, process_alive
from .contracts import Forecast, Observation, Station

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS captures(
 capture_id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at TEXT NOT NULL,
 completed_at TEXT NOT NULL, expected_rows INTEGER NOT NULL, actual_rows INTEGER NOT NULL,
 state TEXT NOT NULL, summary_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS capture_time ON captures(source,completed_at);
CREATE TABLE IF NOT EXISTS stations(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT NOT NULL,
 received_at TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
 revision TEXT NOT NULL, payload TEXT NOT NULL, conflict INTEGER NOT NULL DEFAULT 0,
 UNIQUE(station_id,revision));
CREATE INDEX IF NOT EXISTS station_time ON stations(station_id,received_at);
CREATE TABLE IF NOT EXISTS observations(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, capture_id TEXT, source TEXT NOT NULL,
 entity_id TEXT NOT NULL, event_key TEXT NOT NULL, variable TEXT NOT NULL,
 revision TEXT NOT NULL, received_at TEXT NOT NULL, observed_at TEXT,
 payload TEXT NOT NULL, conflict INTEGER NOT NULL DEFAULT 0,
 UNIQUE(source,entity_id,event_key,variable,revision));
CREATE INDEX IF NOT EXISTS obs_time ON observations(source,received_at);
CREATE INDEX IF NOT EXISTS obs_capture ON observations(capture_id);
CREATE TABLE IF NOT EXISTS forecasts(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, grid_id TEXT NOT NULL,
 variable TEXT NOT NULL, issued_at TEXT NOT NULL, valid_at TEXT NOT NULL,
 received_at TEXT NOT NULL, revision TEXT NOT NULL, payload TEXT NOT NULL,
 conflict INTEGER NOT NULL DEFAULT 0,
 UNIQUE(source,grid_id,variable,issued_at,valid_at,revision));
CREATE INDEX IF NOT EXISTS forecast_time ON forecasts(received_at,valid_at);
CREATE TABLE IF NOT EXISTS requests(
 seq INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT NOT NULL, requested_at TEXT NOT NULL,
 day_kst TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT 'reserved', code TEXT);
CREATE INDEX IF NOT EXISTS request_budget ON requests(provider,day_kst);
CREATE TABLE IF NOT EXISTS provider_blocks(provider TEXT PRIMARY KEY, until_at TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS process_leases(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires_at TEXT NOT NULL);
"""


class BudgetExceeded(RuntimeError):
    pass


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _append(db, table: str, columns: dict, identity: dict, payload: dict):
        encoded = canonical(payload)
        where = " AND ".join(f"{k}=?" for k in identity)
        old = db.execute(f"SELECT seq,payload,conflict FROM {table} WHERE {where}", tuple(identity.values())).fetchone()
        if old:
            # A repeated fetch of an identical provider revision keeps its FIRST
            # receipt time. A genuinely different value under the same revision
            # is a conflict; ordering of fetches must not resolve it silently.
            prior = json.loads(old["payload"])
            if table == "stations":
                # Schema defaults describe the already stored first-seen
                # interval. Adding these fields on the wire is not a provider
                # revision or a metadata conflict; do not rewrite the original.
                prior = Station.model_validate(prior).model_dump(mode="json")
                payload = Station.model_validate(payload).model_dump(mode="json")
            comparison_keys = (set(prior) | set(payload)) - {"received_at"}
            # Backward-compatible raw-text retention added in schema v1.1.
            if "raw_value" not in prior:
                comparison_keys.discard("raw_value")
            different = any(prior.get(k) != payload.get(k) for k in comparison_keys)
            if different and not old["conflict"]:
                # Reissue the changed record at a new ingestion cursor so an
                # already-synced AI cache learns about conflict invalidation.
                db.execute(f"UPDATE {table} SET conflict=1,seq=(SELECT coalesce(max(seq),0)+1 FROM {table}) WHERE seq=?", (old["seq"],))
            return old["seq"]
        values = columns | {"payload": encoded}
        keys = ",".join(values)
        return db.execute(f"INSERT INTO {table}({keys}) VALUES({','.join('?' for _ in values)})", tuple(values.values())).lastrowid

    def commit_inventory(self, capture_id: str, started_at: datetime, completed_at: datetime,
                         expected_rows: int, stations: Iterable[Station], observations: Iterable[Observation],
                         summary: dict | None = None):
        stations, observations = list(stations), list(observations)
        ids = [o.entity_id for o in observations]
        if expected_rows <= 0 or len(ids) != expected_rows or len(set(ids)) != len(ids):
            raise ValueError("Incomplete capture or duplicate station IDs")
        if set(ids) != {s.station_id for s in stations}:
            raise ValueError("Station and inventory identities differ")
        if instant(completed_at) < instant(started_at):
            raise ValueError("Capture time reversed")
        for o in observations:
            if o.source != "seoul_bike" or o.variable != "inventory" or not instant(started_at) <= o.received_at <= instant(completed_at):
                raise ValueError("Invalid inventory capture metadata")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT summary_json FROM captures WHERE capture_id=?", (capture_id,)).fetchone()
            fingerprint = hashlib.sha256(canonical([o.model_dump(mode="json") for o in observations]).encode()).hexdigest()
            details = (summary or {}) | {"payload_sha256": fingerprint}
            if existing:
                if json.loads(existing[0])["payload_sha256"] != fingerprint:
                    raise ValueError("Capture ID reused with a different payload")
                return
            db.execute("INSERT INTO captures VALUES(?,?,?,?,?,?,?,?)", (
                capture_id, "seoul_bike", stamp(started_at), stamp(completed_at), expected_rows, len(ids), "complete", canonical(details)))
            for s in stations:
                p = s.model_dump(mode="json")
                previous = db.execute("SELECT payload FROM stations WHERE station_id=? ORDER BY seq DESC LIMIT 1", (s.station_id,)).fetchone()
                stable_fields = ("source_station_id", "name", "latitude", "longitude", "identity_status", "source")
                if previous and all(json.loads(previous[0])[k] == p[k] for k in stable_fields):
                    continue
                self._append(db, "stations", {"station_id": s.station_id, "received_at": stamp(s.received_at),
                    "valid_from": stamp(s.valid_from), "valid_to": stamp(s.valid_to) if s.valid_to else None, "revision": s.revision},
                    {"station_id": s.station_id, "revision": s.revision}, p)
            for o in observations:
                self._observation(db, o, capture_id)

    def _observation(self, db, o: Observation, capture_id: str | None = None):
        identity = {"source": o.source, "entity_id": o.entity_id, "event_key": o.event_key,
                    "variable": o.variable, "revision": o.revision}
        self._append(db, "observations", identity | {"capture_id": capture_id, "received_at": stamp(o.received_at),
            "observed_at": stamp(o.observed_at) if o.observed_at else None}, identity, o.model_dump(mode="json"))

    def append_observations(self, records: Iterable[Observation]):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for o in records:
                self._observation(db, o)

    def append_forecasts(self, records: Iterable[Forecast]):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for f in records:
                identity = {"source": f.source, "grid_id": f.grid_id, "variable": f.variable,
                    "issued_at": stamp(f.issued_at), "valid_at": stamp(f.valid_at), "revision": f.revision}
                self._append(db, "forecasts", identity | {"received_at": stamp(f.received_at)}, identity, f.model_dump(mode="json"))

    def page(self, table: str, *, cursor=0, limit=1000, source=None, start=None, end=None, watermark=None):
        if table not in {"stations", "observations", "forecasts"} or not 1 <= limit <= 10000 or cursor < 0:
            raise ValueError("Invalid page request")
        with self.connection() as db:
            # A caller pins this watermark while draining a finite initial snapshot.
            if watermark is None:
                watermark = db.execute(f"SELECT coalesce(max(seq),0) FROM {table}").fetchone()[0]
            clauses, params = ["seq>?", "seq<=?"], [cursor, watermark]
            if source:
                if table == "stations":
                    raise ValueError("Station source filter unsupported")
                clauses.append("source=?"); params.append(source)
            if start:
                clauses.append("received_at>=?"); params.append(stamp(start))
            if end:
                clauses.append("received_at<=?"); params.append(stamp(end))
            rows = db.execute(f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} ORDER BY seq LIMIT ?", (*params, limit + 1)).fetchall()
            more = len(rows) > limit
            items = [{"cursor": r["seq"], "conflict": bool(r["conflict"]), "record": json.loads(r["payload"]),
                      **({"capture_id": r["capture_id"]} if table == "observations" else {})} for r in rows[:limit]]
            capture_ids = sorted({item["capture_id"] for item in items if item.get("capture_id")})
            captures = [dict(r) for cid in capture_ids for r in db.execute("SELECT * FROM captures WHERE capture_id=?", (cid,))]
            return {"items": items, "next_cursor": items[-1]["cursor"] if more else watermark,
                    "watermark": watermark, "has_more": more, "captures": captures}

    def stations_at(self, as_of: datetime):
        at = stamp(as_of)
        with self.connection() as db:
            rows = db.execute("SELECT payload,conflict FROM stations WHERE seq IN (SELECT max(seq) FROM stations WHERE received_at<=? AND valid_from<=? GROUP BY station_id)", (at, at)).fetchall()
            return [json.loads(r["payload"]) for r in rows if not r["conflict"] and
                    (not json.loads(r["payload"])["valid_to"] or instant(json.loads(r["payload"])["valid_to"]) > instant(as_of))]

    def import_page(self, table: str, page: dict):
        """AI disk cache adapter. Keep upstream cursors; capture rows imported atomically.

        Incomplete imported captures must remain invisible until every expected
        station observation has arrived, even when pagination divides a capture.
        """
        if table not in {"stations", "observations", "forecasts"}:
            raise ValueError("Invalid cache table")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for c in page.get("captures", []):
                db.execute("INSERT OR IGNORE INTO captures VALUES(?,?,?,?,?,?,?,?)", (
                    c["capture_id"], c["source"], stamp(c["started_at"]), stamp(c["completed_at"]), c["expected_rows"], c["actual_rows"],
                    "importing" if c["state"] == "complete" else c["state"], c["summary_json"]))
            for item in page["items"]:
                p, seq = item["record"], item["cursor"]
                if table == "observations":
                    o = Observation.model_validate(p)
                    if item.get("capture_id"):
                        if o.source != "seoul_bike" or o.variable != "inventory":
                            raise ValueError("Non-inventory record attached to inventory capture")
                    self._observation(db, o, item.get("capture_id"))
                    identity = (o.source, o.entity_id, o.event_key, o.variable, o.revision)
                    if item["conflict"]:
                        db.execute("UPDATE observations SET conflict=1 WHERE source=? AND entity_id=? AND event_key=? AND variable=? AND revision=?", identity)
                elif table == "stations":
                    s = Station.model_validate(p)
                    self._append(db, table, {"station_id": s.station_id, "received_at": stamp(s.received_at), "valid_from": stamp(s.valid_from),
                        "valid_to": stamp(s.valid_to) if s.valid_to else None, "revision": s.revision}, {"station_id": s.station_id, "revision": s.revision}, p)
                    if item["conflict"]:
                        db.execute("UPDATE stations SET conflict=1 WHERE station_id=? AND revision=?", (s.station_id, s.revision))
                else:
                    f = Forecast.model_validate(p)
                    identity = {"source": f.source, "grid_id": f.grid_id, "variable": f.variable, "issued_at": stamp(f.issued_at), "valid_at": stamp(f.valid_at), "revision": f.revision}
                    self._append(db, table, identity | {"received_at": stamp(f.received_at)}, identity, p)
                    if item["conflict"]:
                        db.execute("UPDATE forecasts SET conflict=1 WHERE source=? AND grid_id=? AND variable=? AND issued_at=? AND valid_at=? AND revision=?", tuple(identity.values()))
            db.execute("""UPDATE captures SET state='complete' WHERE state='importing'
                AND expected_rows=(SELECT count(*) FROM observations o WHERE o.capture_id=captures.capture_id)
                AND expected_rows=(SELECT count(DISTINCT entity_id) FROM observations o WHERE o.capture_id=captures.capture_id)
                AND NOT EXISTS(SELECT 1 FROM observations o WHERE o.capture_id=captures.capture_id
                    AND (o.source!='seoul_bike' OR o.variable!='inventory' OR o.received_at<captures.started_at OR o.received_at>captures.completed_at))""")

    def inventory_snapshot(self, as_of: datetime, max_age_seconds: float | None = None):
        with self.connection() as db:
            capture = db.execute("SELECT * FROM captures WHERE source='seoul_bike' AND state='complete' AND completed_at<=? ORDER BY completed_at DESC LIMIT 1", (stamp(as_of),)).fetchone()
            if not capture:
                return None
            age = (instant(as_of) - instant(capture["completed_at"])).total_seconds()
            if max_age_seconds is not None and age >= max_age_seconds:
                return None
            rows = db.execute("SELECT payload,conflict FROM observations WHERE capture_id=?", (capture["capture_id"],)).fetchall()
            return {"capture_id": capture["capture_id"], "completed_at": capture["completed_at"], "age_seconds": age,
                    "rows": [json.loads(r["payload"]) | {"conflict": bool(r["conflict"])} for r in rows]}

    def captures(self, start: datetime, end: datetime):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM captures WHERE completed_at>=? AND completed_at<=? ORDER BY completed_at", (stamp(start), stamp(end)))]

    def reserve_request(self, provider: str, day_kst: str, budget: int, at: datetime, min_interval: float = 1):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            block = db.execute("SELECT until_at FROM provider_blocks WHERE provider=?", (provider,)).fetchone()
            if block and instant(block[0]) > instant(at):
                raise BudgetExceeded("provider_cooldown")
            used, last = db.execute("SELECT count(*),max(requested_at) FROM requests WHERE provider=? AND day_kst=?", (provider, day_kst)).fetchone()
            if used >= budget:
                raise BudgetExceeded("local_daily_budget")
            if last and (instant(at) - instant(last)).total_seconds() < min_interval:
                raise BudgetExceeded("local_minimum_interval")
            return db.execute("INSERT INTO requests(provider,requested_at,day_kst) VALUES(?,?,?)", (provider, stamp(at), day_kst)).lastrowid

    def finish_request(self, seq: int, outcome: str, code: str | None = None):
        with self.connection() as db:
            db.execute("UPDATE requests SET outcome=?,code=? WHERE seq=?", (outcome, code, seq))

    def block_provider(self, provider: str, until: datetime, reason: str):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO provider_blocks VALUES(?,?,?)", (provider, stamp(until), reason))

    def lease(self, name: str, owner: str, at: datetime, seconds=180) -> bool:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner,expires_at FROM process_leases WHERE name=?", (name,)).fetchone()
            if row and row["owner"] != owner and instant(row["expires_at"]) > instant(at):
                try:
                    prior_pid = int(row["owner"].split("-", 1)[0])
                except ValueError:
                    return False
                if process_alive(prior_pid):
                    return False
            db.execute("INSERT OR REPLACE INTO process_leases VALUES(?,?,?)", (name, owner, stamp(instant(at) + timedelta(seconds=seconds))))
            return True

    def release(self, name: str, owner: str):
        with self.connection() as db:
            db.execute("DELETE FROM process_leases WHERE name=? AND owner=?", (name, owner))

    def summary(self):
        with self.connection() as db:
            return {"captures": db.execute("SELECT count(*) FROM captures WHERE state='complete'").fetchone()[0],
                    "invalid_captures": db.execute("SELECT count(*) FROM captures WHERE state NOT IN ('complete','importing')").fetchone()[0],
                    "latest_complete_at": db.execute("SELECT max(completed_at) FROM captures WHERE state='complete'").fetchone()[0],
                    "observations": db.execute("SELECT count(*) FROM observations").fetchone()[0],
                    "forecasts": db.execute("SELECT count(*) FROM forecasts").fetchone()[0],
                    "requests_by_provider_day": [dict(r) for r in db.execute("SELECT provider,day_kst,count(*) requests FROM requests GROUP BY provider,day_kst")]}
