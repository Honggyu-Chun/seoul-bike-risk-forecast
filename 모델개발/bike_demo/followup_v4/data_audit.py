"""Audit development source rows before training; never opens 2025 Q3/Q4."""
import argparse
import gc
import json
import time
import zipfile
import numpy as np
import pandas as pd
from .common import ROOT, OUT, RECORD, digest, save, now

def build(year):
    destination = OUT / "data" / f"panel_{year}.npz"
    audit_path = OUT / "data" / f"audit_{year}.json"
    if destination.exists() and audit_path.exists():
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        assert digest(destination) == audit["panel_sha256"]
        print("already audited", year, flush=True)
        return
    start = time.perf_counter()
    stations = pd.read_parquet(ROOT / "data/processed/rider_stations.parquet")
    ids = pd.Index(stations.station_id)
    assert len(ids) == 2729 and ids.is_unique
    end = "2025-01-01" if year == 2024 else "2025-07-01"
    times = pd.date_range(f"{year}-01-01", end, inclusive="left", freq="h")
    quantity = np.full((len(times), len(ids)), np.nan, dtype=np.float32)
    seen = np.zeros(quantity.size, dtype=bool)
    conflict = np.zeros(quantity.size, dtype=bool)
    flat = quantity.ravel()
    sources = [ROOT / "data/raw/research_20261020" / f"availability_2024q{q}.zip" for q in (1,2,3,4)] if year == 2024 else [ROOT / "data/raw/availability_2025q1.zip", ROOT / "data/raw/improvement/availability_2025q2.zip"]
    monthly = []
    for source in sources:
        source_hash = digest(source)
        with zipfile.ZipFile(source) as z:
            for member in sorted(n for n in z.namelist() if n.lower().endswith(".csv")):
                stats = dict(member=member, source_rows=0, matched_cohort_rows=0,
                             outside_cohort=0, invalid_time=0, invalid_quantity=0,
                             duplicate_rows=0, conflicting_rows=0)
                with z.open(member) as f:
                    for df in pd.read_csv(f, encoding="cp949", usecols=["일시","대여소번호","시간대","거치대수량"], chunksize=200000):
                        stats["source_rows"] += len(df)
                        date = pd.to_datetime(df["일시"], errors="coerce")
                        hour = pd.to_numeric(df["시간대"], errors="coerce")
                        timestamp = date + pd.to_timedelta(hour, unit="h")
                        ti = times.get_indexer(timestamp)
                        station = ids.get_indexer(pd.to_numeric(df["대여소번호"], errors="coerce"))
                        q = pd.to_numeric(df["거치대수량"], errors="coerce").to_numpy(dtype=np.float32)
                        valid_time = (ti >= 0) & hour.between(0, 23).to_numpy() & hour.mod(1).eq(0).to_numpy()
                        valid_q = np.isfinite(q) & (q >= 0) & (q == np.floor(q))
                        stats["outside_cohort"] += int((station < 0).sum())
                        stats["invalid_time"] += int((~valid_time).sum())
                        stats["invalid_quantity"] += int((~valid_q).sum())
                        take = (station >= 0) & valid_time & valid_q
                        key = ti[take].astype(np.int64) * len(ids) + station[take]
                        value = q[take]
                        stats["matched_cohort_rows"] += len(key)
                        # Sort only within a source chunk; never choose the last conflicting value.
                        order = np.argsort(key, kind="stable")
                        key, value = key[order], value[order]
                        unique, first, count = np.unique(key, return_index=True, return_counts=True)
                        low = np.minimum.reduceat(value, first)
                        high = np.maximum.reduceat(value, first)
                        old = seen[unique]
                        stats["duplicate_rows"] += int(np.maximum(count-1,0).sum() + old.sum())
                        bad = (low != high) | (old & ~conflict[unique] & (flat[unique] != low))
                        stats["conflicting_rows"] += int(bad.sum())
                        conflict[unique] |= bad
                        flat[unique] = low
                        flat[unique[conflict[unique]]] = np.nan
                        seen[unique] = True
                monthly.append(stats)
                print("AUDIT", year, member.rsplit("/",1)[-1], json.dumps(stats, ensure_ascii=False), flush=True)
        monthly[-1]["source_zip_sha256"] = source_hash
        monthly[-1]["source_zip"] = str(source.relative_to(ROOT))
    original_match = None
    if year == 2025:
        with np.load(ROOT / "artifacts/improvement/panel.npz") as old:
            original_match = bool(np.array_equal(quantity[:len(old["times"])], old["quantity"], equal_nan=True))
        if not original_match:
            raise ValueError("New parser differs from preserved Jan-May14 panel; investigate before training")
    destination.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(destination, quantity=quantity, times=times.to_numpy(), station_ids=ids.to_numpy())
    audit = dict(year=year, completed_at_utc=now(), source_archives=[str(p.relative_to(ROOT)) for p in sources],
                 timezone="Asia/Seoul", station_count=len(ids), hours=len(times),
                 first_grid=str(times[0]), last_grid=str(times[-1]),
                 observed_cells=int(np.isfinite(quantity).sum()), missing_cells=int(np.isnan(quantity).sum()),
                 conflicting_keys_excluded=int(conflict.sum()), duplicate_policy="collapse_identical_discard_all_conflicting_values",
                 missing_are_not_zero=True, target="next exact hourly grid recorded quantity equals zero",
                 original_panel_matches=original_match, monthly=monthly, panel_sha256=digest(destination),
                 untouched_quarters_opened=False, seconds=time.perf_counter()-start)
    save(audit_path, audit)
    print("DATA_AUDIT_COMPLETE", year, audit["observed_cells"], audit["conflicting_keys_excluded"], flush=True)
    del quantity, flat, seen, conflict
    gc.collect()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--year", type=int, choices=[2024,2025], required=True)
    build(p.parse_args().year)
