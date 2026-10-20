"""Check whether successive hourly quantity records exist; do not train a model."""
import json
from pathlib import Path
import zipfile
import pandas as pd
import numpy as np
from sklearn.neighbors import BallTree

root = Path(__file__).resolve().parents[1]
with zipfile.ZipFile(root / 'data/raw/availability_2025q1.zip') as z:
    member = next(n for n in z.namelist() if n.endswith('data_2501.csv'))
    with z.open(member) as stream:
        data = pd.read_csv(stream, encoding='cp949', usecols=['일시', '대여소번호', '시간대', '거치대수량'])
data['time'] = pd.to_datetime(data['일시'], errors='raise') + pd.to_timedelta(data['시간대'], unit='h')
data['station_id'] = pd.to_numeric(data['대여소번호'], errors='raise')
quantity = pd.to_numeric(data['거치대수량'], errors='raise')
assert not data.duplicated(['station_id', 'time']).any()
assert not quantity.isna().any() and quantity.ge(0).all()
data = data.assign(quantity=quantity).sort_values(['station_id', 'time'])
next_time = data.groupby('station_id')['time'].shift(-1)
next_quantity = data.groupby('station_id')['quantity'].shift(-1)
valid = next_time.sub(data.time).dt.total_seconds().eq(3600)
locations = pd.read_excel(root / 'data/raw/stations_202412.xlsx', header=None).iloc[5:].copy()
locations.columns = ['station_id', 'name', 'district', 'address', 'latitude', 'longitude', 'installed', 'lcd', 'qr', 'type']
locations['station_id'] = pd.to_numeric(locations['station_id'], errors='raise').astype(int)
assert not locations.station_id.duplicated().any()
for col in ['latitude', 'longitude']:
    locations[col] = pd.to_numeric(locations[col], errors='coerce')
source_ids = set(data.station_id.unique())
mapped = locations[locations.station_id.isin(source_ids)].copy()
usable = mapped[mapped.latitude.between(37, 38) & mapped.longitude.between(126, 128)].copy()
coords = np.radians(usable[['latitude', 'longitude']].to_numpy(dtype=float))
neighbors = BallTree(coords, metric='haversine').query_radius(coords, r=500/6_371_000, count_only=True) - 1
weather = pd.read_csv(root / 'data/processed/kma_seoul_202501.csv')
assert '일시' in weather.columns
weather_times = pd.to_datetime(weather['일시'], errors='raise')
assert not weather_times.duplicated().any()
quantity_times = pd.DatetimeIndex(data.time.unique())
out = {
    'source': 'Seoul OA-22382 official archived hourly quantity; January 2025 member',
    'source_rows': len(data), 'station_ids': int(data.station_id.nunique()),
    'unique_record_hours': int(data.time.nunique()),
    'missing_city_hours': int(744 - data.time.nunique()),
    'duplicate_station_hours': 0, 'quantity_missing': 0, 'quantity_negative': 0,
    'zero_quantity_rows': int(data.quantity.eq(0).sum()),
    'zero_quantity_rate': float(data.quantity.eq(0).mean()),
    'exactly_one_hour_successive_pairs': int(valid.sum()),
    'target_zero_rows_in_successive_pairs': int(next_quantity[valid].eq(0).sum()),
    'source_column': '거치대수량',
    'spatial_mapping': {
        'location_snapshot': '2024-12', 'source_station_ids': len(source_ids),
        'matched_ids': len(mapped), 'valid_coordinates': len(usable),
        'unmatched_ids': sorted(int(v) for v in source_ids-set(mapped.station_id)),
        'stations_with_neighbor_within_500m': int((neighbors > 0).sum()),
        'median_neighbors_within_500m': float(np.median(neighbors)),
        'scope': 'coordinate matching and straight-line geometric feasibility, not routing or operating status',
    },
    'weather_mapping': {
        'quantity_unique_hours': len(quantity_times),
        'matched_weather_hours': int(quantity_times.isin(weather_times).sum()),
        'scope': 'archived time alignment only; future actual weather is not available forecast input',
    },
    'limitations': [
        'No model training, performance result, or live API verification.',
        'This confirms adjacent record availability, not exact within-hour observation time.',
        'A zero record does not establish an entire hour of stockout or actual rider failure.',
        'Provider meaning, station operating state, and recording cadence must be checked before operational labels.',
        'Hourly records cannot validate a 5- or 10-minute arrival forecast.',
    ],
}
path = root / 'reports/consumer_availability_feasibility.json'
path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(out, ensure_ascii=False, indent=2))
