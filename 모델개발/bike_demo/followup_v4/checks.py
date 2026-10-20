"""Reload saved models and compare raw probabilities with persisted predictions."""
import gc
import json
import tempfile
from pathlib import Path
import joblib
import numpy as np
from .common import OUT,RECORD,save,digest,now,protocol
from .context import ContextInputs
from . import runner

def main():
    import lightgbm as lgb
    has_air=any(list((OUT/f).glob('lightgbm_air_*.json')) for f in ('A','B','C'))
    has_forecast=any((OUT/f/'lightgbm_gfs_prior_day.json').exists() for f in ('A','B','C'))
    if has_forecast:
        from .forecast import ForecastInputs
        data=ForecastInputs()
    elif has_air:
        from .air import AirInputs
        data=AirInputs()
    else:data=ContextInputs()
    checks=[];sequence=None
    for fold in ('A','B','C'):
        split=protocol()['development_folds'][fold];all_indices=data.indices(split['cal_start'],split['cal_end'])
        positions=np.linspace(0,len(all_indices)-1,min(512,len(all_indices)),dtype=int);index=all_indices[positions]
        for path in sorted((OUT/fold).glob('*.json')):
            result=json.loads(path.read_text(encoding='utf-8'));kind=result.get('spec',{}).get('kind')
            if result.get('status')!='ok' or kind in ('rule','markov',None):continue
            name=result['name'];folder=OUT/fold/'models';spec=result['spec'];x=data.frame(index,result['features']);models=None
            if kind in ('lgb','poisson','ordinal','experts'):
                def load(suffix=''):return lgb.Booster(model_str=(folder/(name+suffix+'.txt')).read_text(encoding='utf-8'))
                if kind=='experts':
                    models=[load('_positive'),load('_zero')];zero=x.quantity.to_numpy()==0;p=np.empty(len(x))
                    for flag,m in enumerate(models):
                        mask=zero==bool(flag);p[mask]=m.predict(x.loc[mask],num_threads=1)
                else:
                    model=load();p=model.predict(x,num_threads=1)
                    if kind=='poisson':p=np.exp(-np.maximum(0,p))
                    if kind=='ordinal':p=p[:,0]
            elif kind=='cat':
                from catboost import CatBoostClassifier
                for col in ('station_code','hour','weekday'):
                    if col in x:x[col]=x[col].astype(np.int32)
                with tempfile.NamedTemporaryFile(suffix='.cbm',delete=False) as temp:temp_path=Path(temp.name)
                try:
                    temp_path.write_bytes((folder/(name+'.cbm')).read_bytes());model=CatBoostClassifier();model.load_model(str(temp_path));p=model.predict_proba(x,thread_count=1)[:,1]
                finally:temp_path.unlink(missing_ok=True)
            elif kind=='xgb':
                import xgboost as xgb
                model=xgb.XGBClassifier(n_jobs=1);model.load_model(bytearray((folder/(name+'.ubj')).read_bytes()));p=model.predict_proba(x)[:,1]
            elif kind=='neural':
                import torch
                from .neural import SequenceInputs,TemporalModel,Predictor
                if sequence is None:sequence=SequenceInputs()
                if spec['graph'] and not spec.get('graph_identity'):sequence.prepare_neighbors()
                ckpt=torch.load(folder/(name+'.pt'),map_location='cpu',weights_only=False)
                assert ckpt['station_ids']==sequence.station_ids
                model=TemporalModel(sequence.n,spec['length'],spec['architecture'],spec['graph']);model.load_state_dict(ckpt['state'])
                nx=sequence.frame(index,sequence.meta['groups']['basic']);p=Predictor(model,sequence,spec['length'],spec['graph'],spec.get('graph_identity',False)).predict_proba(nx)[:,1]
            else:model=joblib.load(folder/(name+'.joblib'));p=model.predict_proba(x)[:,1]
            expected=np.load(OUT/'predictions'/fold/(name+'_cal.npy'),mmap_mode='r')[positions]
            error=float(np.max(np.abs(p-expected)));checks.append(dict(fold=fold,model=name,rows=len(index),maximum_absolute_error=error,passed=bool(error<2e-6),result_sha256=digest(path)))
            print('ROUNDTRIP',fold,name,error,flush=True)
            del x;model=None;models=None;gc.collect()
    save(RECORD/'roundtrip_checks.json',dict(checked_at_utc=now(),checks=checks,all_passed=all(c['passed'] for c in checks),tolerance=2e-6,comparison='saved raw calibration probabilities; fixed equally spaced 512 positions'))
    assert checks and all(c['passed'] for c in checks)

if __name__=='__main__':main()
