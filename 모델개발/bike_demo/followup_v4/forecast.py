"""Conditional ablation of prior-day forecasts; never future observations."""
import json
import numpy as np
import pandas as pd
from .common import ROOT,OUT,RECORD,save,digest,now
from .air import AirInputs
from . import runner

SOURCE=ROOT/'data/raw/followup_v4_extra/model_ready/gfs_day1_2025h1_input.parquet'
JOIN=ROOT/'data/raw/followup_v4_extra/model_ready/station_gfs_point_join.csv'

def prepare_forecast():
    path=OUT/'data/forecast_features_2025.npy';meta=OUT/'data/forecast_features_2025.json'
    if path.exists() and meta.exists():
        previous=json.loads(meta.read_text(encoding='utf-8'));assert previous['source_sha256']==digest(SOURCE);return
    source=pd.read_parquet(SOURCE);join=pd.read_csv(JOIN)
    assert not source.duplicated(['timestamp','point_id']).any()
    assert ((source.valid_time-source.timestamp)==pd.Timedelta('1h')).all()
    assert source.historical_publication_time_verified.eq(False).all()
    stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    point_ids=join.set_index('station_id').point_id.reindex(stations.station_id)
    assert point_ids.notna().all()
    with np.load(OUT/'data/panel_2025.npz') as p:times=pd.DatetimeIndex(p['times']);shape=p['quantity'].shape
    columns=[c for c in source if c.startswith('gfs_day1_')]
    indexed=source.set_index(['timestamp','point_id']);matrix=np.lib.format.open_memmap(path,mode='w+',dtype=np.float32,shape=(int(np.prod(shape)),len(columns)))
    for j,col in enumerate(columns):
        table=indexed[col].unstack('point_id').reindex(times);positions=table.columns.get_indexer(point_ids)
        assert (positions>=0).all();matrix[:,j]=table.to_numpy(dtype=np.float32)[:,positions].ravel()
    matrix.flush();save(meta,dict(columns=columns,source_sha256=digest(SOURCE),station_join_sha256=digest(JOIN),builder_sha256=digest(__file__),
        live_source_path_verified=True,historical_received_at_verified=False,historical_run_issue_time_verified=False,
        requested_prior_day_offset_hours=24,actual_per_row_issue_time_known=False,
        definition='5 prior-day forecast variables, valid at bicycle target t+1, nearest returned forecast grid; never actual future weather',
        interpretation='conditional offline ablation; nominal/minimum day1 lead is not a reconstructed per-row issue timestamp'))

class ForecastInputs(AirInputs):
    def __init__(self):
        super().__init__();prepare_forecast()
        self.forecast_meta=json.loads((OUT/'data/forecast_features_2025.json').read_text(encoding='utf-8'))
        self.forecast_columns=self.forecast_meta['columns'];self.forecast=np.load(OUT/'data/forecast_features_2025.npy',mmap_mode='r')
        self.meta['groups']['gfs_day1']=self.meta['groups']['hazard_spatial']+self.forecast_columns
    def frame(self,index,columns):
        frame=super().frame(index,[c for c in columns if c not in self.forecast_columns])
        for col in columns:
            if col in self.forecast_columns:frame[col]=self.forecast[index,self.forecast_columns.index(col)]
        return frame if list(frame.columns)==list(columns) else frame[columns]

def forecast_spec():return dict(name='lightgbm_gfs_prior_day',kind='lgb',group='gfs_day1',leaves=127,rounds=900,window=56,seed=20261020)

def decorate(fold):
    path=OUT/fold/'lightgbm_gfs_prior_day.json';record=json.loads(path.read_text(encoding='utf-8'))
    record.update(information_track='live_source_backed_forecast_archive',historical_received_at_verified=False,historical_run_issue_time_verified=False,
        forecast_metadata_sha256=digest(OUT/'data/forecast_features_2025.json'),forecast_plan_sha256=digest(RECORD/'forecast_experiment_plan.json'),extension_code_sha256=digest(__file__))
    save(path,record);runner.refresh()

def run_pilot():
    data=ForecastInputs();runner.run_one(data,'A',forecast_spec());decorate('A')

if __name__=='__main__':prepare_forecast();print('FORECAST_FEATURES_READY',flush=True)
