"""CycleOn entrypoint. Secrets are read in-process, never put in CLI arguments."""
import argparse
import atexit
from datetime import timedelta
import json
import os
import shutil
from pathlib import Path
import sys
import time
import uuid

# Small service processes do not need NumPy/BLAS thread pools with dozens of
# reserved stacks. This affects only newly launched CycleOn children.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "4"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cycleon.common import ROOT, now, private_values, runtime_dir, save_json, stamp
from cycleon.collection_state import begin_continuous_window
from cycleon.store import Store
from cycleon.collectors import Client, kma_grid


def collect(args):
    root = runtime_dir(); root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "backend.sqlite3")
    policy = json.loads((ROOT / "config/service_collection_policy.json").read_text(encoding="utf-8"))
    client = Client(store, policy)
    owner = f"{os.getpid()}-{uuid.uuid4()}"
    started = now(); deadline = time.monotonic() + args.duration_hours * 3600 if args.duration_hours else None
    inventory_due, weather_due, air_due = 0, 0, 0
    previous = json.loads((root / "collection_state.json").read_text(encoding="utf-8")) if (root / "collection_state.json").exists() else {}
    if previous.get("run_id"):
        save_json(root / "run_history" / (previous["run_id"] + ".json"), previous)
    trial_start, continuity = begin_continuous_window(
        previous, started, policy["inventory_interval_seconds"],
        policy["inventory_max_capture_duration_seconds"])
    atexit.register(store.release, "collector", owner)
    state = {"run_id": owner, "started_at": stamp(started), "pid": os.getpid(), "status": "starting",
             "successful_captures": 0, "failed_captures": 0, "weather_results": 0, "air_results": 0,
             "excluded_sources": ["NEIS", "SGIS", "DEM"], "previous_run": previous.get("run_id"), "trial_started_at": trial_start,
             **continuity,
             "collection_mode": "finite_trial" if args.duration_hours else "continuous",
             "duration_hours": args.duration_hours, "minimum_free_disk_GiB": 20}
    while True:
        if not store.lease("collector", owner, now(), seconds=600):
            raise RuntimeError("Another collector holds the lease")
        if deadline and time.monotonic() >= deadline:
            state.update(status="duration_finished", ended_at=stamp(now()))
            save_json(root / "collection_state.json", state)
            return
        if shutil.disk_usage(root).free < 20 * 2**30:
            state.update(status="waiting_disk_space", updated_at=stamp(now()))
            save_json(root / "collection_state.json", state)
            time.sleep(30)
            continue
        if time.monotonic() >= inventory_due:
            tick = time.monotonic()
            try:
                capture = client.inventory()
                state["latest_capture"] = capture
                state["successful_captures"] += 1
                pages = capture["pages"]
                budget = policy["providers"]["seoul"]["local_daily_budget"]
                interval = max(policy["inventory_interval_seconds"], pages * 86400 / (budget * .90))
                state["inventory_effective_interval_seconds"] = interval
                state["one_minute_target_met_by_local_budget"] = interval <= 60
                state["last_inventory_error"] = None
                if args.once:
                    state.update(status="one_capture_finished", store=store.summary())
                    save_json(root / "collection_state.json", state)
                    store.release("collector", owner)
                    print(json.dumps(state, ensure_ascii=False, indent=2))
                    return
            except Exception as error:
                # Only exception class and controlled CollectionError/Budget codes.
                from cycleon.collectors import CollectionError
                from cycleon.store import BudgetExceeded
                code = str(error) if isinstance(error, (CollectionError, BudgetExceeded)) else type(error).__name__
                state.update(last_inventory_error=code, last_failure_at=stamp(now()))
                state["failed_captures"] += 1
                interval = 60
                if args.once:
                    save_json(root / "collection_state.json", state)
                    store.release("collector", owner)
                    raise RuntimeError(code) from None
            inventory_due = max(tick + interval, time.monotonic() + 1)
            state.update(status="collecting", updated_at=stamp(now()))
            save_json(root / "collection_state.json", state)
        # Spread weather over cycles so a full grid refresh never blocks minute
        # inventory collection. Both variable groups share the same DFS grid.
        if time.monotonic() >= weather_due and not state.get("weather_queue"):
            grids = sorted({kma_grid(s["latitude"], s["longitude"]) for s in store.stations_at(now()) if s["identity_status"] == "verified"})
            state["grid_count"] = len(grids)
            state["weather_planned_requests_per_day"] = len(grids) * 2 * 24
            state["weather_queue"] = [[*g, fc] for g in grids for fc in (False, True)]
            weather_due = time.monotonic() + policy["weather_interval_seconds"]
        if state.get("weather_queue") and inventory_due - time.monotonic() > 23:
            nx, ny, fc = state["weather_queue"].pop(0)
            try:
                client.weather((nx, ny), fc)
                state["weather_results"] += 1
                state["last_weather_error"] = None
            except Exception as error:
                from cycleon.collectors import CollectionError
                from cycleon.store import BudgetExceeded
                state["last_weather_error"] = str(error) if isinstance(error, (CollectionError, BudgetExceeded)) else type(error).__name__
            save_json(root / "collection_state.json", state)
        elif time.monotonic() >= air_due and inventory_due - time.monotonic() > 23:
            try:
                client.air(); state["air_results"] += 1
                state["last_air_error"] = None
            except Exception as error:
                from cycleon.collectors import CollectionError
                from cycleon.store import BudgetExceeded
                state["last_air_error"] = str(error) if isinstance(error, (CollectionError, BudgetExceeded)) else type(error).__name__
            air_due = time.monotonic() + policy["air_interval_seconds"]
            save_json(root / "collection_state.json", state)
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect"); c.add_argument("--once", action="store_true"); c.add_argument("--duration-hours", type=float)
    for name, default in (("backend", 8002), ("ai", 8001)):
        p = sub.add_parser(name); p.add_argument("--port", type=int, default=default)
        if name == "ai":
            p.add_argument("--backend-url", default="http://127.0.0.1:8002")
            p.add_argument("--bundle", type=Path)
    sub.add_parser("export-openapi")
    args = parser.parse_args()
    if args.command == "collect":
        return collect(args)
    from cycleon.api import ai_app, backend_app
    root = runtime_dir(); values = private_values()
    token = values.get("CYCLEON_SERVICE_TOKEN", "")
    if args.command == "export-openapi":
        from cycleon.engine import Engine
        # A fixed dummy token is only used to instantiate schema objects offline.
        # No request is sent and no production authentication is weakened.
        dummy = "schema-export-only-" + "x" * 32
        store = Store(root / "schema_export.sqlite3")
        for name, app in (("backend", backend_app(store, dummy)), ("ai", ai_app(Engine(store), dummy))):
            save_json(ROOT / f"docs/cycleon_{name}_openapi.json", app.openapi())
        return
    if args.command == "backend":
        app = backend_app(Store(root / "backend.sqlite3"), token)
    else:
        from cycleon.engine import Engine
        from cycleon.backend import HTTPBackend
        from cycleon.models import ModelBundle
        cache = Store(root / "ai_cache.sqlite3")
        backend = HTTPBackend(args.backend_url, token, cache, root / "sync_state.json")
        engine = Engine(cache, ModelBundle(args.bundle) if args.bundle else None)
        app = ai_app(engine, token, backend)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
