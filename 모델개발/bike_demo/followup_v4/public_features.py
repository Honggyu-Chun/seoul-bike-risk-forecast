"""Small prior-public-data arrays for later conditional comparisons, without labels."""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
from .common import ROOT, save, digest, now

RECORD = ROOT / 'experiments/20261020_추가공공데이터적용'
DEST = ROOT / 'artifacts/followup_public_features_20261020'
BUS_SOURCE = ROOT / 'experiments/20261020_공공데이터추가조사2/공간시설/feature_staging/station_busstop_static_20241209.csv'
OD_SOURCE = RECORD / '정제본/202411_코호트_요일시간_조건부프로필.parquet'
BUS_COLUMNS = ['nearest_bus_stop_distance_m', 'bus_stop_count_300m', 'bus_stop_count_500m', 'bus_stop_count_1000m']
OD_COLUMNS = ['od_log_reported_departures_mean', 'od_log_reported_arrivals_mean',
              'od_departure_reported_day_fraction', 'od_arrival_reported_day_fraction',
              'od_departure_report_present', 'od_arrival_report_present', 'od_current_crosswalk_linked']
BUS_PROXY_START = '2024-12-11'
OD_PROXY_START = '2024-12-09'


def prepare():
    DEST.mkdir(parents=True, exist_ok=True)
    audit = json.loads((RECORD / '202411_OD_전수검수.json').read_text(encoding='utf-8'))
    assert audit['historical_first_publication_and_reception_proven'] is False
    sources = {str(p.relative_to(ROOT)): digest(p) for p in
               (BUS_SOURCE, OD_SOURCE, RECORD / '202411_OD_전수검수.json', RECORD / '준비계획.md')}
    meta_path = DEST / 'metadata.json'
    if meta_path.exists():
        old = json.loads(meta_path.read_text(encoding='utf-8'))
        assert old['source_hashes'] == sources, 'Source changed; prepare a new version instead of silently overwriting'
        for key in ('bus_array', 'od_array'):
            assert digest(DEST / old[key]['path']) == old[key]['sha256']
        return old
    stations = pd.read_parquet(ROOT / 'data/processed/rider_stations.parquet').station_id.to_numpy()
    bus = pd.read_csv(BUS_SOURCE).set_index('station_id').reindex(stations)[BUS_COLUMNS].to_numpy(np.float32)
    assert np.isfinite(bus).all() and (bus >= 0).all()
    source = pd.read_parquet(OD_SOURCE).set_index(['station_id', 'weekday', 'hour'])
    order = pd.MultiIndex.from_product([stations, range(7), range(24)], names=source.index.names)
    assert not source.index.duplicated().any() and len(source) == len(order)
    source = source.reindex(order)
    allowed = source.conditional_link_allowed.to_numpy(bool)
    departure = source.departure_mean_per_reported_day.to_numpy(float)
    arrival = source.arrival_mean_per_reported_day.to_numpy(float)
    values = np.column_stack([np.log1p(departure), np.log1p(arrival),
                              source.departure_reported_day_fraction, source.arrival_reported_day_fraction,
                              np.where(allowed, np.isfinite(departure).astype(float), np.nan),
                              np.where(allowed, np.isfinite(arrival).astype(float), np.nan), allowed.astype(float)])
    assert np.isnan(values[~allowed, :6]).all()
    od = values.astype(np.float32).reshape(len(stations), 7, 24, len(OD_COLUMNS)).transpose(1, 2, 0, 3)
    np.save(DEST / 'bus.npy', bus)
    np.save(DEST / 'prior_od.npy', od)
    meta = dict(created_at_utc=now(), station_ids=stations.tolist(), source_hashes=sources,
                bus_columns=BUS_COLUMNS, od_columns=OD_COLUMNS,
                bus_array=dict(path='bus.npy', shape=list(bus.shape), sha256=digest(DEST / 'bus.npy')),
                od_array=dict(path='prior_od.npy', shape=list(od.shape), sha256=digest(DEST / 'prior_od.npy')),
                builder_sha256=digest(__file__), information_track='public_archive_and_current_crosswalk_sensitivity',
                historical_reception_verified=False, historical_station_ID_identity_verified=False,
                input_date_proxy=dict(bus=BUS_PROXY_START, od=OD_PROXY_START),
                input_date_proxy_meaning='day after displayed modified date, not proof of first publication or reception',
                calendar_lookup='weekday and hour of target t+1; source values fixed in 2024-11',
                observed_flow_mean='mean over days with a source row; absent records stay NaN; coverage separate',
                new_2025_labels_read=False, model_training_performed=False)
    save(meta_path, meta)
    print('PUBLIC_FEATURE_ARRAYS_READY', bus.shape, od.shape, flush=True)
    return meta


def select(index, input_times, station_count, bus, od, columns):
    """Join static rows and next-hour calendar profiles while retaining masks and order."""
    index = np.asarray(index, dtype=np.intp)
    times = pd.DatetimeIndex(input_times)
    if index.ndim != 1 or np.any(index < 0) or np.any(index >= len(times) * station_count):
        raise ValueError('Invalid flattened station-time indices')
    if any(c not in BUS_COLUMNS + OD_COLUMNS for c in columns) or len(set(columns)) != len(columns):
        raise ValueError('Unknown or repeated public-data columns')
    observed_t = times[index // station_count]
    target_t = observed_t + pd.Timedelta(hours=1)
    sid = index % station_count
    frame = pd.DataFrame(index=np.arange(len(index)))
    for name in columns:
        if name in BUS_COLUMNS:
            value = np.asarray(bus[sid, BUS_COLUMNS.index(name)], dtype=np.float32).copy()
            value[observed_t < pd.Timestamp(BUS_PROXY_START)] = np.nan
        else:
            value = np.asarray(od[target_t.weekday, target_t.hour, sid, OD_COLUMNS.index(name)], dtype=np.float32).copy()
            value[observed_t < pd.Timestamp(OD_PROXY_START)] = np.nan
        frame[name] = value
    return frame


class Store:
    def __init__(self):
        self.meta = prepare()
        self.bus = np.load(DEST / self.meta['bus_array']['path'], mmap_mode='r')
        self.od = np.load(DEST / self.meta['od_array']['path'], mmap_mode='r')

    def frame(self, index, input_times, station_count, columns):
        if station_count != len(self.meta['station_ids']):
            raise ValueError('Station cohort changed')
        return select(index, input_times, station_count, self.bus, self.od, columns)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.parse_args()
    prepare()
