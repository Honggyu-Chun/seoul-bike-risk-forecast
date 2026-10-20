"""Freeze a validation-selected model and recommendation policy, then test once."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,gc
import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.special import expit
from sklearn.isotonic import IsotonicRegression
from .data import ROOT
from .research_features import OUT
from .research import setup,dump,decisions,rule_metrics
from .recommend import distances_m


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def predict_record(record,frame,folder):
    spec=record['spec'];kind=spec['kind'];x=frame[record['features']]
    if kind=='neural':
        from .neural_research import predict_saved
        return predict_saved(record,frame.index.to_numpy(),phase=folder.name)
    paths=[folder/'models'/p for p in record['fit']['paths']]
    cat=[c for c in ['station_code','hour','weekday'] if c in x]
    if kind in ['lgb','experts','poisson','quantile']:
        if kind=='experts':
            pred=np.empty(len(x),dtype=np.float32)
            for empty,path in zip([False,True],paths):
                mask=(x.quantity.to_numpy()==0)==empty
                model=lgb.Booster(model_file=str(path));pred[mask]=model.predict(x.iloc[np.flatnonzero(mask)],num_threads=4)
        else:
            model=lgb.Booster(model_file=str(paths[0]));pred=model.predict(x,num_threads=4)
            if spec.get('gamma'):pred=expit(pred)
            if kind in ['poisson','quantile']:pred=np.exp(-np.maximum(pred,0))
    elif kind=='catboost':
        from catboost import CatBoostClassifier
        x=x.copy()
        for c in cat:x[c]=x[c].astype(np.int32)
        model=CatBoostClassifier();model.load_model(str(paths[0]));pred=model.predict_proba(x,thread_count=4)[:,1]
    elif kind=='xgboost':
        import xgboost as xgb
        x=x.copy()
        for c in cat:
            size=2729 if c=='station_code' else 24 if c=='hour' else 7
            x[c]=pd.Categorical(x[c],categories=np.arange(size))
        model=xgb.XGBClassifier(n_jobs=4,enable_categorical=True);model.load_model(paths[0]);pred=model.predict_proba(x)[:,1]
    elif kind in ['extra','forest','linear']:
        model=joblib.load(paths[0]);pred=model.predict_proba(x)[:,1]
    elif kind=='markov':
        with np.load(paths[0]) as z:rates=z['rates']
        key=(x.station_code.to_numpy(dtype=int)*24+x.hour.to_numpy(dtype=int))*9+np.minimum(x.quantity.to_numpy(dtype=int),8)
        pred=rates[key]
    else:raise ValueError(kind)
    return np.asarray(pred,dtype=np.float32)


def selected_solution(folder):
    records=json.loads((folder/'leaderboard.json').read_text())
    expected=set(json.loads((ROOT/'config/improvement_v3.json').read_text()).get('expected_forward_candidates',[]))
    missing=expected-{row['name'] for row in records if row['status']=='ok'}
    if missing:raise ValueError('Planned forward candidates missing before final selection: '+', '.join(sorted(missing)))
    for row in records:
        if row['status']!='ok':continue
        chosen=row.get('selection_rule',row['rule'])
        measured=row.get('selection_validation',row['validation'])
        if abs(chosen['f1']-measured['f1'])>1e-10:
            raise ValueError('Threshold search score disagrees with its actual decisions: '+row['name'])
    ok=[{**r,'rule':r.get('selection_rule',r.get('rule'))} for r in records if r['status']=='ok']
    original=max((r for r in ok if r['name'].startswith('B1_original')),key=lambda r:r['rule']['f1'])
    # A longer-trained baseline is a control, not automatically a novel proposal.
    proposals=[r for r in ok if not r['name'].startswith('B1_')]
    best=max(proposals,key=lambda r:r['rule']['f1'])
    proposal={'kind':'single','members':[best['name']],'weights':[1.0],'rule':best['rule'],'validation_f1':best['rule']['f1']}
    ensembles=json.loads((folder/'ensembles.json').read_text()) if (folder/'ensembles.json').exists() else []
    # At least one non-baseline member is required to call an ensemble a proposal.
    for e in ensembles:
        if e['a'].startswith('B1_') and e['b'].startswith('B1_'):continue
        if e['f1']>proposal['validation_f1']:
            proposal={'kind':'blend','members':[e['a'],e['b']],'weights':[e['weight_a'],1-e['weight_a']],'rule':e['rule'],'validation_f1':e['f1']}
    return {r['name']:r for r in ok},original,proposal


def recommendation_queries(times,stations,current,target,mask,base_prob,proposal_prob,target_start=None,target_end=None,base_utility_prob=None):
    """Fixed origins and only fixed weekday commute hours; same eligible pool."""
    origins=json.loads((ROOT/'artifacts/evaluation/evaluation_origins.json').read_text())
    if isinstance(origins,dict):origins=origins.get('origins',origins.get('rows'))
    print('recommendation origins',len(origins),flush=True)
    n=len(stations);q=current.reshape(-1,n);future=target.reshape(-1,n);eligible=mask.reshape(-1,n)
    pbase=base_prob.reshape(-1,n);pprop=proposal_prob.reshape(-1,n)
    pbase_utility=pbase if base_utility_prob is None else base_utility_prob.reshape(-1,n)
    origin_arrays=[]
    for origin in origins:
        lat=origin['latitude'];lon=origin['longitude'];d=distances_m(lat,lon,stations.latitude,stations.longitude)
        ids=np.flatnonzero(d<=500);order=np.lexsort((stations.station_id.to_numpy()[ids],d[ids]));ids=ids[order]
        origin_arrays.append((origin,ids,d[ids]))
    active=np.flatnonzero(eligible.any(axis=1))
    if not len(active) and not (target_start and target_end):return [],{'attempted':0,'common_queries':0,'excluded_no_candidate':0}
    start=pd.Timestamp(target_start) if target_start else times[active[0]]+pd.Timedelta(hours=1)
    end=pd.Timestamp(target_end) if target_end else times[active[-1]]+pd.Timedelta(hours=2)
    rows=[];attempted=0;excluded=0
    for ti,timestamp in enumerate(times):
        target_timestamp=timestamp+pd.Timedelta(hours=1)
        if target_timestamp<start or target_timestamp>=end or timestamp.dayofweek>=5 or timestamp.hour not in [7,8,18,19]:continue
        for index,(origin,ids,distance) in enumerate(origin_arrays):
            attempted+=1;valid=eligible[ti,ids]&(q[ti,ids]>0);ii=ids[valid];dd=distance[valid]
            if not len(ii):excluded+=1;continue
            rows.append({'date':str(timestamp.date()),'input_time':timestamp.isoformat(),'origin_id':int(origin.get('origin_id',index)),
                         'ids':stations.station_id.to_numpy()[ii],'distance':dd,'quantity':q[ti,ii],
                         'future':future[ti,ii],'base':pbase[ti,ii],'base_utility':pbase_utility[ti,ii],'proposal':pprop[ti,ii]})
    return rows,{'attempted':attempted,'common_queries':len(rows),'excluded_no_candidate':excluded}


def choose(row,policy):
    distance=row['distance'];quantity=row['quantity'];kind=policy['kind']
    if kind=='distance':return 0
    if kind=='gate':
        idx=np.flatnonzero(row[policy['model']]<policy['threshold']);return int(idx[0]) if len(idx) else None
    if kind=='utility':cost=distance+policy['weight']*row[policy['model']]
    elif kind=='quantity_utility':cost=distance+policy['weight']/(quantity+1)
    else:raise ValueError(kind)
    return int(np.lexsort((row['ids'],distance,cost))[0])


def service_metrics(rows,policy):
    good=0;recommended=0;extra=[];choices=[]
    for row in rows:
        i=choose(row,policy);ok=i is not None and row['future'][i]>=1
        good+=int(ok);recommended+=int(i is not None)
        if i is not None:extra.append(float(row['distance'][i]-row['distance'][0]))
        choices.append({'date':row['date'],'input_time':row['input_time'],'origin_id':row['origin_id'],'station_id':None if i is None else int(row['ids'][i]),'valid':int(ok),'distance_m':None if i is None else float(row['distance'][i])})
    n=len(rows)
    return {'requests':n,'valid_recommendations':good,'recommended':recommended,'effective_rate':good/n if n else None,'hit_rate':good/recommended if recommended else None,'abstention_rate':1-recommended/n if n else None,'extra_distance_m':float(np.mean(extra)) if extra else None},choices


def service_day_bootstrap(choices):
    """Exploratory paired intervals, retaining the full request denominator."""
    frame=pd.DataFrame(choices)
    paired=frame.pivot(index=['date','input_time','origin_id'],columns='method',values='valid')
    required=['R0','R1u','Rq','R2']
    if not set(required).issubset(paired.columns) or paired[required].isna().any().any():
        raise ValueError('Unpaired recommendation choices; do not compare different denominators')
    counts=paired.groupby(level='date').size().to_numpy()
    totals=paired.groupby(level='date').sum()
    if not len(counts):raise ValueError('No paired recommendation choices')
    indices=np.random.default_rng(20261020).integers(0,len(counts),size=(2000,len(counts)))
    denominator=counts[indices].sum(axis=1);comparisons={}
    for baseline in ['R0','R1u','Rq']:
        delta=(totals.R2-totals[baseline]).to_numpy();draws=100*delta[indices].sum(axis=1)/denominator
        comparisons[baseline]={'lower_pp':float(np.quantile(draws,.025)),'upper_pp':float(np.quantile(draws,.975))}
    return {'unit':'whole input day; paired origins/times','replicates':2000,'days':len(counts),
            'paired_requests':len(paired),'comparisons_R2_minus_baseline':comparisons}


def fit_calibration(p,y,q,mask):
    models={}
    for zero in [False,True]:
        sub=mask&((q==0)==zero)
        model=IsotonicRegression(out_of_bounds='clip',y_min=1e-6,y_max=1-1e-6)
        model.fit(p[sub],y[sub]);models[zero]=model
    return models


def calibrate(models,p,q):
    result=np.empty(len(p),dtype=np.float32)
    for zero in [False,True]:
        mask=(q==0)==zero
        result[mask]=models[zero].predict(p[mask])
    return result


def bootstrap(days,y,a,b,qa,ra,rb):
    unique=np.unique(days);counts=[]
    for day in unique:
        mask=days==day;yy=y[mask];aa=decisions(a[mask],qa[mask],ra);bb=decisions(b[mask],qa[mask],rb)
        counts.append([[int((yy&pred).sum()),int(((1-yy)&pred).sum()),int((yy&~pred).sum())] for pred in [aa,bb]])
    counts=np.array(counts);rng=np.random.default_rng(20261020);diff=[]
    for _ in range(2000):
        ix=rng.integers(0,len(unique),len(unique));s=counts[ix].sum(axis=0);f=2*s[:,0]/np.maximum(1,2*s[:,0]+s[:,1]+s[:,2]);diff.append(100*(f[1]-f[0]))
    return {'unit':'whole target day','replicates':2000,'days':len(unique),'lower_pp':float(np.quantile(diff,.025)),'upper_pp':float(np.quantile(diff,.975))}


def main():
    parser=argparse.ArgumentParser();action=parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--open-final-test',action='store_true');action.add_argument('--validate-predictors',action='store_true');args=parser.parse_args()
    folder=OUT/'forward';final=OUT/'final';final.mkdir(exist_ok=True)
    if (final/'results.json').exists():raise SystemExit('Final test already evaluated. Preserve it; use a genuinely new protocol/test for further research.')
    records,baseline,proposal=selected_solution(folder)
    weather_used=any(any(c.startswith('w_') for c in records[name]['features']) for name in proposal['members'])
    weather_context=json.loads((OUT/'weather_audit.json').read_text()) if weather_used else None
    p,features,groups,q,target,tt,train,valid=setup('forward');n=2729
    if args.validate_predictors:
        checks=[];x=features.loc[valid]
        for name,record in records.items():
            expected=np.load(folder/(name+'_valid.npy'));actual=predict_record(record,x,folder)
            difference=float(np.max(np.abs(expected-actual)));assert difference<1e-5,(name,difference)
            disagreements=int(np.count_nonzero(decisions(expected,q[valid],record['rule'])!=decisions(actual,q[valid],record['rule'])))
            assert disagreements==0,(name,'reloaded decision disagreements',disagreements)
            checks.append({'model':name,'validation_max_absolute_prediction_difference':difference,'frozen_decision_disagreements':disagreements})
            print('predictor verified',name,difference,flush=True)
        dump(folder/'predictor_roundtrip_checks.json',{'new_test_scored':False,'checks':checks})
        return
    stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    times=pd.DatetimeIndex(np.asarray(tt)[::n])-pd.Timedelta(hours=1)
    vy=(target[valid]==0).astype(np.uint8);vq=q[valid]
    bp=np.load(folder/(baseline['name']+'_valid.npy'))
    pp=sum(w*np.load(folder/(name+'_valid.npy')) for name,w in zip(proposal['members'],proposal['weights']))
    assert abs(rule_metrics(vy,pp,vq,proposal['rule'])['f1']-proposal['validation_f1'])<1e-10,'Selected validation F1 does not match actual frozen decisions'
    # Calibration uses April1-7, recommendation selection April8-14.
    calibration=np.asarray(tt[valid])<np.datetime64('2025-04-08')
    calibrators=fit_calibration(pp,vy,vq,calibration);joblib.dump(calibrators,final/'probability_calibration.joblib')
    calibrated=calibrate(calibrators,pp,vq)
    baseline_calibrators=fit_calibration(bp,vy,vq,calibration);joblib.dump(baseline_calibrators,final/'baseline_probability_calibration.joblib')
    baseline_calibrated=calibrate(baseline_calibrators,bp,vq)
    val_selection=valid&(tt>=np.datetime64('2025-04-08'))
    base_grid=np.full(len(q),np.nan,dtype=np.float32);proposal_grid=base_grid.copy();base_grid[valid]=bp;proposal_grid[valid]=calibrated
    baseline_utility_grid=np.full(len(q),np.nan,dtype=np.float32);baseline_utility_grid[valid]=baseline_calibrated
    queries,manifest=recommendation_queries(times,stations,q,target,val_selection,base_grid,proposal_grid,'2025-04-08','2025-04-15',base_utility_prob=baseline_utility_grid)
    policies=[]
    for weight in [0,100,250,500,1000,2000,4000]:
        for identifier,kind,model in [('utility','utility','proposal'),('base_utility','utility','base_utility'),('quantity_utility','quantity_utility','proposal')]:
            policy={'kind':kind,'weight':weight}
            if kind=='utility':policy['model']=model
            score,_=service_metrics(queries,policy)
            policies.append({'search_id':identifier,'policy':policy,'metrics':score})
    selected={}
    for identifier in ['utility','base_utility','quantity_utility']:
        feasible=[x for x in policies if x['search_id']==identifier and x['metrics']['extra_distance_m']<=100]
        selected[identifier]=max(feasible,key=lambda x:(x['metrics']['effective_rate'],-x['metrics']['extra_distance_m']))['policy']
    hashes={}
    needed=set(proposal['members']+[baseline['name']])
    # The strongest tuned own-information model is reported as an additional control.
    controls=[r for r in records.values() if r['name'].startswith('B1_tuned')]
    tuned=max(controls,key=lambda r:r['rule']['f1']) if controls else None
    if tuned:needed.add(tuned['name'])
    for name in needed:
        for path in records[name]['fit']['paths']:hashes[str(folder/'models'/path)]=sha(folder/'models'/path)
    freeze={'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':sha(ROOT/'config/improvement_v3.json'),'baseline':baseline,'proposal':proposal,'tuned_control':tuned,
            'weather_used_by_proposal':weather_used,'weather_assumptions_if_used':weather_context,
            'recommendation_policies':selected,'validation_policy_search':policies,'model_hashes':hashes,'calibration_sha256':sha(final/'probability_calibration.joblib'),'baseline_calibration_sha256':sha(final/'baseline_probability_calibration.joblib'),
            'code_hashes':{str(path.relative_to(ROOT)):sha(path) for path in [ROOT/'bike_demo/research.py',ROOT/'bike_demo/research_features.py',Path(__file__)]},'test_used_for_selection':False}
    frozen_path=final/'selection_frozen_before_test.json'
    if frozen_path.exists():
        previous=json.loads(frozen_path.read_text())
        for key in ['protocol_sha256','baseline','proposal','tuned_control','recommendation_policies','model_hashes','calibration_sha256','baseline_calibration_sha256','weather_used_by_proposal','weather_assumptions_if_used']:
            if previous[key]!=freeze[key]:raise RuntimeError('A prior frozen selection exists; refusing to change it after opening test: '+key)
        dump(final/'evaluation_retry_integrity.json',{'original_freeze_sha256':sha(frozen_path),'current_code_hashes':freeze['code_hashes'],'selection_unchanged':True})
    else:dump(frozen_path,freeze)
    print('SELECTION FROZEN. Opening April15-May14 once.',flush=True)
    mask=np.isfinite(q)&np.isfinite(target)&(tt>=np.datetime64(p['forward']['test_target_start']))&(tt<np.datetime64(p['forward']['test_target_end_exclusive']))
    tx=features.loc[mask];y=(target[mask]==0).astype(np.uint8);cq=q[mask]
    preds={}
    for name in sorted(needed):
        print('test prediction',name,flush=True);preds[name]=predict_record(records[name],tx,folder)
    proposed=sum(w*preds[name] for name,w in zip(proposal['members'],proposal['weights']))
    metrics={'B0':rule_metrics(y,(cq==0).astype(np.float32),cq,{'kind':'global','threshold':.5}),
             'B1':rule_metrics(y,preds[baseline['name']],cq,baseline['rule']),
             'M2':rule_metrics(y,proposed,cq,proposal['rule'])}
    if tuned:metrics['B1_tuned']=rule_metrics(y,preds[tuned['name']],cq,tuned['rule'])
    subgroup={}
    for label,sub in [('current_positive',cq>0),('current_1_to_3',(cq>=1)&(cq<=3))]:
        subgroup[label]={'B1':rule_metrics(y[sub],preds[baseline['name']][sub],cq[sub],baseline['rule']),
                         'M2':rule_metrics(y[sub],proposed[sub],cq[sub],proposal['rule'])}
        if tuned:subgroup[label]['B1_tuned']=rule_metrics(y[sub],preds[tuned['name']][sub],cq[sub],tuned['rule'])
    calibrated=calibrate(calibrators,proposed,cq);base_grid[:]=np.nan;proposal_grid[:]=np.nan;base_grid[mask]=preds[baseline['name']];proposal_grid[mask]=calibrated
    baseline_utility_grid[:]=np.nan;baseline_utility_grid[mask]=calibrate(baseline_calibrators,preds[baseline['name']],cq)
    queries,manifest=recommendation_queries(times,stations,q,target,mask,base_grid,proposal_grid,p['forward']['test_target_start'],p['forward']['test_target_end_exclusive'],base_utility_prob=baseline_utility_grid)
    base_threshold=baseline['rule']['threshold']
    policies={'R0':{'kind':'distance'},'R1':{'kind':'gate','model':'base','threshold':base_threshold},'R1u':selected['base_utility'],'Rq':selected['quantity_utility'],'R2':selected['utility']}
    service={};choices=[]
    for name,policy in policies.items():
        score,selected_rows=service_metrics(queries,policy);service[name]=score
        choices.extend({'method':name,**row} for row in selected_rows)
    # Prespecified diagnostic: a citizen sees few bikes at their nearest option.
    # Keep exactly the full-validation-selected policies and the original goals.
    low_stock=[row for row in queries if 1<=row['quantity'][0]<=3]
    service_subgroups={'nearest_current_1_to_3':{name:service_metrics(low_stock,policy)[0] for name,policy in policies.items()}}
    pd.DataFrame(choices).to_csv(final/'recommendation_choices.csv',index=False)
    model_delta=100*(metrics['M2']['f1']-metrics['B1']['f1']);service_delta=100*(service['R2']['effective_rate']-service['R0']['effective_rate'])
    oracle=sum(any(row['future']>=1) for row in queries)/len(queries)
    result={'experiment_id':p['experiment_id'],'test_period':[p['forward']['test_target_start'],p['forward']['test_target_end_exclusive']],
            'weather_used_by_proposal':weather_used,'weather_assumptions_if_used':weather_context,
            'test_rows':len(y),'metrics':metrics,'subgroups':subgroup,'proposal':proposal,'baseline_name':baseline['name'],'recommendation':{'manifest':manifest,'metrics':service,'subgroups':service_subgroups,'policies':policies,'perfect_future_oracle_rate':oracle,'day_bootstrap':service_day_bootstrap(choices)},
            'deltas':{'f1_pp':model_delta,'recall_pp':100*(metrics['M2']['recall']-metrics['B1']['recall']),'effective_recommendation_pp':service_delta},
            'goals':{'f1_5pp':model_delta>=5,'recall_no_worse':metrics['M2']['recall']>=metrics['B1']['recall'],'effective_recommendation_5pp':service_delta>=5,'extra_distance_100m':service['R2']['extra_distance_m']<=100},
            'goal_values':{'model':p['primary_goal'],'service':p['original_service_goal']},'goals_are_not_school_cutoffs':True,
            'f1_day_bootstrap':bootstrap(pd.DatetimeIndex(tt[mask]).strftime('%Y-%m-%d').to_numpy(),y,preds[baseline['name']],proposed,cq,baseline['rule'],proposal['rule']),
            'interpretation':'New chronological archived test, not actual rental success or proof of short-minute live accuracy. All selection frozen before this evaluation.'}
    dump(final/'results.json',result)
    columns={'input_time':pd.DatetimeIndex(tt[mask])-pd.Timedelta(hours=1),'target_time':pd.DatetimeIndex(tt[mask]),'station_id':stations.station_id.to_numpy()[np.flatnonzero(mask)%n],
             'quantity':cq,'target_quantity':target[mask],'target_zero':y,'p_B1':preds[baseline['name']],'p_M2':proposed,'risk_B1':decisions(preds[baseline['name']],cq,baseline['rule']),'risk_M2':decisions(proposed,cq,proposal['rule'])}
    pd.DataFrame(columns).to_parquet(final/'test_predictions.parquet',index=False)
    print(json.dumps({'model_delta_f1_pp':model_delta,'service_delta_pp':service_delta,'goals':result['goals']},indent=2),flush=True)


if __name__=='__main__':main()
