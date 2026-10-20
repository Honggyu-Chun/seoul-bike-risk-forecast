"""Priority extensions. All scores remain development scores."""
import argparse
import gc
import json
import time
import joblib
import numpy as np
import pandas as pd
from scipy.special import logit
from .common import OUT,RECORD,save,digest,now,protocol,process_memory
from . import runner
from .context import ContextInputs

def extra_specs():
    base=dict(name='',kind='lgb',group='hazard_spatial',leaves=127,rounds=900,window=56,seed=20261020)
    result=[]
    for name,group in [('lightgbm_temporal','temporal'),('lightgbm_calendar','context_calendar'),
                       ('lightgbm_prior_year','context_prior_year'),('lightgbm_kbo','context_kbo'),
                       ('lightgbm_culture','context_culture'),('lightgbm_context_all','context_all')]:
        row=dict(base,name=name,group=group);result.append(row)
    return result

def blend(d,fold,name,components):
    path=OUT/fold/(name+'.json')
    if path.exists():
        previous=json.loads(path.read_text(encoding='utf-8'))
        assert previous['protocol_sha256']==digest(RECORD/'protocol.json')
        assert previous['spec']['components']==components
        return
    start=time.perf_counter();split=protocol()['development_folds'][fold]
    stages={s:d.indices(split[s+'_start'],split[s+'_end']) for s in ('cal','policy','valid')}
    pred={s:sum(np.load(OUT/'predictions'/fold/(n+'_'+s+'.npy'))*w for n,w in components.items()) for s in stages}
    cal=runner.calibrator(d.target[stages['cal']]==0,pred['cal'])
    cp={s:cal.predict_proba(logit(np.clip(p,1e-6,1-1e-6)).reshape(-1,1))[:,1].astype(np.float32) for s,p in pred.items()}
    q=d.q.ravel()[stages['policy']];y=d.target[stages['policy']]==0
    rule=runner.tune(y,cp['policy'],q);raw_rule=runner.tune(y,pred['policy'],q)
    valid=runner.summarize(d.target[stages['valid']]==0,cp['valid'],d.q.ravel()[stages['valid']],rule)
    joblib.dump(cal,OUT/fold/'models'/(name+'_calibrator.joblib'))
    for s in pred:np.save(OUT/'predictions'/fold/(name+'_'+s+'.npy'),pred[s].astype(np.float32))
    save(path,dict(name=name,status='ok',fold=fold,spec=dict(components=components,weights_selected_on='fixed before outside scores'),
        component_result_sha256={n:digest(OUT/fold/(n+'.json')) for n in components},rule=rule,
        validation=valid,raw_validation=runner.summarize(d.target[stages['valid']]==0,pred['valid'],d.q.ravel()[stages['valid']],raw_rule),
        stage_rows={s:len(ix) for s,ix in stages.items()},actual_train_rows=0,seconds=time.perf_counter()-start,memory=process_memory(),
        code_sha256=digest(__file__),protocol_sha256=digest(RECORD/'protocol.json'),independent_final_test=False,
        information_track='archived_weather_sensitivity' if any('weather' in n for n in components) else 'availability_timestamp_causal',completed_at_utc=now()))
    print('BLEND',fold,name,valid['overall']['f1'],flush=True);runner.refresh()

def main():
    p=argparse.ArgumentParser();p.add_argument('--folds',nargs='+',default=['A','B','C']);p.add_argument('--names',nargs='+');p.add_argument('--blends',action='store_true');p.add_argument('--archived-reference',action='store_true');args=p.parse_args()
    d=ContextInputs();selected=extra_specs()
    if args.names:selected=[s for s in selected if s['name'] in args.names]
    for fold in args.folds:
        for spec in selected:
            print('START',fold,spec['name'],now(),flush=True);runner.run_one(d,fold,spec)
            path=OUT/fold/(spec['name']+'.json');result=json.loads(path.read_text(encoding='utf-8'))
            result['extension_code_sha256']=digest(__file__)
            result['context_builder_sha256']=digest(__import__('bike_demo.followup_v4.context',fromlist=['x']).__file__)
            result['context_metadata_sha256']=digest(OUT/'data/context_2025.json')
            if spec['group'] in ('context_culture','context_all'):
                result['information_track']='archived_event_snapshot_sensitivity'
            save(path,result);runner.refresh()
        if args.blends:
            blend(d,fold,'M2_no_weather',{'lightgbm_full':.5,'catboost_temporal':.5})
            if args.archived_reference:blend(d,fold,'M2_refit',{'lightgbm_weather':.5,'catboost_temporal':.5})
        gc.collect()

if __name__=='__main__':main()
