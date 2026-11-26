"""Generate a clearly synthetic, current-hour snapshot for integration testing."""
from datetime import datetime, timedelta, timezone


def snapshot(now=None):
    now = now or datetime.now(timezone.utc)
    base = now.replace(minute=0, second=0, microsecond=0)
    earlier = base - timedelta(days=35)
    stations = []
    histories = {}
    forecasts = []
    for i, code in enumerate((102,103,104,105,106,108)):
        sid = f"DEMO-{code}"
        revision = f"demo-revision-{code}"
        stock = [(k//3+i) % 9 for k in range(672)]
        stations.append(dict(station_id=sid, model_station_code=code, name=f"Demo {code}",
            latitude=37.56+i*0.001, longitude=126.98+i*0.001, identity_verified=True,
            identity_verified_at=earlier.isoformat(), identity_evidence="synthetic demonstration",
            identity_revision=revision, inventory_at_base=stock[-1],
            base_inventory_received_at=(base-timedelta(seconds=45)).isoformat(),
            latest_inventory=stock[-1], latest_inventory_received_at=now.isoformat()))
        histories[sid] = dict(identity_revision=revision, continuous_since=earlier.isoformat(), prior_inventory=stock[:-1])
    for h in (1,2,3):
        forecasts.append(dict(horizon=h, issued_at=(base-timedelta(minutes=70)).isoformat(),
            received_at=(base-timedelta(minutes=5)).isoformat(), target_at=(base+timedelta(hours=h)).isoformat(),
            by_station={s["station_id"]:dict(T1H=7.0+h, RN1=0.0, REH=60, WSD=2.0, PTY=0) for s in stations}))
    return dict(capture_complete=True, source_capture_id="synthetic-developer-example",
        base_at=base.isoformat(), materialized_at=now.isoformat(), stations=stations,
        final_inputs=dict(product="kma_completed_hour_v2", history_received_at=(base-timedelta(minutes=2)).isoformat(),
                          histories=histories, forecasts=forecasts))
