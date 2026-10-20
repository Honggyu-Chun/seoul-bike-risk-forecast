"""Use live-source-compatible air fields with explicit archive provenance."""
import argparse
import json
import numpy as np
import pandas as pd
from .common import ROOT,OUT,RECORD,save,digest,now
from .context import ContextInputs
from . import runner

SOURCE=ROOT/'data/raw/followup_v4_extra/model_ready/air_2025h1_input_lags2_3_24.parquet'

def prepare_air():
    path=OUT/'data/air_features_2025.npy';meta=OUT/'data/air_features_2025.json'
    if path.exists() and meta.exists():
        previous=json.loads(meta.read_text(encoding='utf-8'))
        assert previous['source_sha256']==digest(SOURCE)
        return
    source=pd.read_parquet(SOURCE)
    assert not source.duplicated(['timestamp','district']).any()
    stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    with np.load(OUT/'data/panel_2025.npz') as p:times=pd.DatetimeIndex(p['times']);shape=p['quantity'].shape
    indexed=source.set_index(['timestamp','district'])
    columns=[c for c in source if c.startswith('air_') and any(f'lag{h}h' in c for h in (2,3,24))]
    groups={f'air_lag{h}':[c for c in columns if c.endswith(f'lag{h}h') or c.endswith(f'lag{h}h_qc')] for h in (2,3,24)}
    groups['air_lag2_24']=groups['air_lag2']+groups['air_lag24']
    matrix=np.lib.format.open_memmap(path,mode='w+',dtype=np.float32,shape=(int(np.prod(shape)),len(columns)))
    for j,name in enumerate(columns):
        table=indexed[name].unstack('district').reindex(times)
        positions=table.columns.get_indexer(stations.district)
        assert (positions>=0).all(),'Unknown station district; do not invent air values'
        matrix[:,j]=table.to_numpy(dtype=np.float32)[:,positions].ravel()
    matrix.flush()
    save(meta,dict(columns=columns,groups=groups,shape=list(matrix.shape),source_sha256=digest(SOURCE),builder_sha256=digest(__file__),
        operational_source='ListAirQualityByDistrictService',source_granularity='district average sensor, not a sensor at each bike station',
        live_path_verified=True,historical_received_at_verified=False,historical_revision_verified=False,
        information_track='live_source_backed_historical_archive',
        temporal_rule='only measurement t-2h, t-3h or t-24h; never fill from later values',
        operational_rule='received_at<=input t, observation-age gate, stale/missing fallback; retained snapshots needed for lag24',
        note='A live source is available; historical lagged scores do not prove historical as-received operation'))
    print('AIR_FEATURES_READY',len(columns),flush=True)

class AirInputs(ContextInputs):
    def __init__(self):
        super().__init__();prepare_air()
        self.air_meta=json.loads((OUT/'data/air_features_2025.json').read_text(encoding='utf-8'))
        self.air_columns=self.air_meta['columns'];self.air=np.load(OUT/'data/air_features_2025.npy',mmap_mode='r')
        for key,cols in self.air_meta['groups'].items():self.meta['groups'][key]=self.meta['groups']['hazard_spatial']+cols
    def frame(self,index,columns):
        frame=super().frame(index,[c for c in columns if c not in self.air_columns])
        for c in columns:
            if c in self.air_columns:frame[c]=self.air[index,self.air_columns.index(c)]
        return frame if list(frame.columns)==list(columns) else frame[columns]

def air_specs():
    return [dict(name='lightgbm_'+group,kind='lgb',group=group,leaves=127,rounds=900,window=56,seed=20261020) for group in ('air_lag2','air_lag3','air_lag2_24')]

def main():
    p=argparse.ArgumentParser();p.add_argument('--prepare-only',action='store_true');p.add_argument('--folds',nargs='+',default=['A']);p.add_argument('--names',nargs='+');args=p.parse_args()
    if args.prepare_only:prepare_air();return
    data=AirInputs()
    for fold in args.folds:
        for spec in air_specs():
            if args.names and spec['name'] not in args.names:continue
            print('START',fold,spec['name'],now(),flush=True);runner.run_one(data,fold,spec)
            path=OUT/fold/(spec['name']+'.json');result=json.loads(path.read_text(encoding='utf-8'))
            result.update(information_track='live_source_backed_historical_archive',extension_code_sha256=digest(__file__),
                air_metadata_sha256=digest(OUT/'data/air_features_2025.json'),historical_received_at_verified=False,
                supplemental_specs_sha256=digest(RECORD/'supplemental_specs.json'),
                interpretation='conditional offline improvement estimate; live fields match, historical revision and reception unverified')
            save(path,result);runner.refresh()

if __name__=='__main__':main()
