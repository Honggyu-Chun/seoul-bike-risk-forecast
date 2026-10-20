"""Match the exact-lag offline feature definition at operational input time."""
import pandas as pd
from .operational_inputs import select_observed_inputs

POLLUTANTS=['pm10_ug_m3','pm25_ug_m3','o3_ppm','no2_ppm','co_ppm','so2_ppm']

def build_runtime_air_features(snapshots,requests,lag_hours=2):
    """Consume canonical-unit, timestamped received snapshots; never fetch data.

    Both age boundaries equal the trained lag. If an exact event is absent or
    late, use missing values instead of silently substituting an older reading.
    Queries must be on the hourly decision grid used by the model.
    """
    if lag_hours not in (2,3):raise ValueError('Supported trained lag is 2h or 3h')
    times=pd.to_datetime(requests.input_time)
    if not (times==times.dt.floor('h')).all():raise ValueError('Model requests must use exact hourly decision grids')
    result=select_observed_inputs(snapshots,requests,POLLUTANTS,min_observation_lag=f'{lag_hours}h',max_observation_age=f'{lag_hours}h')
    rename={name:f'air_{name}_lag{lag_hours}h' for name in POLLUTANTS}
    rename.update({name+'_qc':f'air_{name}_lag{lag_hours}h_qc' for name in POLLUTANTS})
    return result.rename(columns=rename)
