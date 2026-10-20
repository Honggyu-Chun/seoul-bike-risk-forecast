"""Frozen, chronological Q1 experiment. Test is opened only after validation selection."""
import gc
import hashlib
import json
import time
from datetime import datetime,timezone
from pathlib import Path
import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import precision_score, recall_score, f1_score, average_precision_score, confusion_matrix, brier_score_loss
from .data import ROOT, load_protocol, parse_quarter, feature_frame,panel_contract

OUT=ROOT/'artifacts/evaluation'; MODELS=ROOT/'artifacts/models'
OUT.mkdir(parents=True,exist_ok=True);MODELS.mkdir(parents=True,exist_ok=True)


def dump(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')


def metrics(y,p,threshold=.5):
    pred=np.asarray(p)>=threshold
    tn,fp,fn,tp=confusion_matrix(y,pred,labels=[0,1]).ravel()
    return {'n':len(y),'zero_cases':int(np.sum(y)), 'zero_rate':float(np.mean(y)),
            'f1':float(f1_score(y,pred,zero_division=0)),
            'precision':float(precision_score(y,pred,zero_division=0)),
            'recall':float(recall_score(y,pred,zero_division=0)),
            'average_precision':float(average_precision_score(y,p)) if np.sum(y)>0 else None,
            'brier':float(brier_score_loss(y,p)),
            'tn':int(tn),'fp':int(fp),'fn':int(fn),'tp':int(tp),'threshold':threshold}


def choose_threshold(y,p,grid):
    candidates=[]
    for t in grid:
        score=f1_score(y,p>=t,zero_division=0)
        candidates.append((float(score),float(t)))
    score,threshold=max(candidates)
    return threshold,score


def load_panel():
    panel=ROOT/'data/processed/rider_q1_panel.npz'
    if not panel.exists():return parse_quarter()
    quality=json.loads((OUT/'data_quality.json').read_text())
    if quality.get('panel_contract')!=panel_contract():return parse_quarter()
    x=np.load(panel)
    return pd.DatetimeIndex(x['times']),pd.read_parquet(ROOT/'data/processed/rider_stations.parquet'),x['quantity'],quality


def main():
    started=time.perf_counter();protocol=load_protocol()
    protocol_hash=hashlib.sha256((ROOT/'config/experiment_v1.json').read_bytes()).hexdigest()
    times,stations,q,quality=load_panel()
    print('building causal features',q.shape,flush=True)
    features,groups=feature_frame(times,stations,q,protocol['neighbor_radius_m'])
    s=len(stations);target_q=np.vstack([q[1:],np.full((1,s),np.nan,dtype=np.float32)]).ravel()
    observed=np.isfinite(q.ravel())&np.isfinite(target_q)
    target_time=np.repeat((times+pd.Timedelta(hours=1)).to_numpy(),s)
    y=(target_q==0).astype(np.uint8)
    train_mask=observed&(target_time<np.datetime64(protocol['train_target_end_exclusive']))
    valid_mask=observed&(target_time>=np.datetime64(protocol['train_target_end_exclusive']))&(target_time<np.datetime64(protocol['validation_target_end_exclusive']))
    test_mask=observed&(target_time>=np.datetime64(protocol['validation_target_end_exclusive']))&(target_time<np.datetime64(protocol['test_target_end_exclusive']))
    splits={}
    for name,mask in [('train',train_mask),('validation',valid_mask),('test',test_mask)]:
        splits[name]={'rows':int(mask.sum()),'zero_cases':int(y[mask].sum()),'zero_rate':float(y[mask].mean()),'target_start':str(target_time[mask].min()),'target_end':str(target_time[mask].max())}
    dump('split_manifest.json',{'protocol_sha256':protocol_hash,'split_on':'target_time','splits':splits,'cohort_stations':s,'missing_labels_excluded':int((~observed).sum()),'test_not_used_for_selection':True})
    print('splits',splits,flush=True)
    selected={};search=[];histories={}
    for name,group in [('B1S','snapshot'),('B1','own'),('M1','spatial')]:
        cols=groups[group]
        print(f'{name}: {len(cols)} features; training validation candidates',flush=True)
        train_x=features.loc[train_mask,cols];valid_x=features.loc[valid_mask,cols]
        dtrain=lgb.Dataset(train_x,label=y[train_mask],categorical_feature=groups['categorical'],free_raw_data=False)
        dvalid=lgb.Dataset(valid_x,label=y[valid_mask],reference=dtrain,free_raw_data=False)
        best=None
        for index,setting in enumerate(protocol['candidate_models']):
            history={};tic=time.perf_counter()
            params={'objective':'binary','metric':'binary_logloss','verbosity':-1,'learning_rate':protocol['training']['learning_rate'],
                    'num_threads':protocol['training']['num_threads'],'seed':protocol['training']['seed'],
                    'deterministic':True,'force_col_wise':True,'max_bin':127,'feature_pre_filter':False,'lambda_l2':1.0,**setting}
            booster=lgb.train(params,dtrain,num_boost_round=protocol['training']['num_boost_round'],valid_sets=[dvalid],valid_names=['validation'],
                              callbacks=[lgb.early_stopping(protocol['training']['early_stopping_rounds'],verbose=False),lgb.record_evaluation(history),lgb.log_evaluation(60)])
            vp=booster.predict(valid_x,num_iteration=booster.best_iteration,num_threads=4)
            threshold,score=choose_threshold(y[valid_mask],vp,protocol['threshold_grid'])
            record={'model':name,'candidate':index,'setting':setting,'best_iteration':booster.best_iteration,'threshold':threshold,'validation_f1':score,'seconds':time.perf_counter()-tic}
            search.append(record);histories[f'{name}_{index}']=history
            print(json.dumps(record),flush=True)
            key=(score,threshold,-index)
            if best is None or key>best[0]:best=(key,booster,record)
        model_path=MODELS/f'{name}.txt'
        best[1].save_model(str(model_path),num_iteration=best[2]['best_iteration'])
        selected[name]={'features':cols,**best[2],'model_sha256':hashlib.sha256(model_path.read_bytes()).hexdigest()}
        del train_x,valid_x,dtrain,dvalid,best,booster,vp
        gc.collect()
    dump('validation_search.json',search);dump('learning_curves.json',histories)
    dump('selection_frozen_before_test.json',{'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'protocol_sha256':protocol_hash,'models':selected,'selection_uses_test':False})
    (MODELS/'metadata.json').write_text(json.dumps({'protocol':protocol,'groups':groups,'models':selected,'station_ids':stations.station_id.tolist()},ensure_ascii=False,indent=2))
    print('SELECTION FROZEN. Opening final test once.',flush=True)
    test_index=np.flatnonzero(test_mask);test_y=y[test_mask];test_target=target_q[test_mask]
    predictions={'B0':(q.ravel()[test_mask]==0).astype(np.float32)}
    results={'B0':metrics(test_y,predictions['B0'])};importance={};latency={}
    for name,record in selected.items():
        model=lgb.Booster(model_file=str(MODELS/f'{name}.txt'))
        test_x=features.loc[test_mask,record['features']]
        tic=time.perf_counter();prob=model.predict(test_x,num_threads=4).astype('float32');seconds=time.perf_counter()-tic
        predictions[name]=prob;results[name]=metrics(test_y,prob,record['threshold'])
        latency[name]={'test_batch_seconds':seconds,'test_rows':len(test_x),'seconds_per_1000_rows':seconds/len(test_x)*1000}
        importance[name]=sorted([{'feature':col,'gain':float(gain)} for col,gain in zip(record['features'],model.feature_importance(importance_type='gain'))],key=lambda v:-v['gain'])
        del test_x,model
        print(name,'TEST',json.dumps(results[name]),flush=True)
    result={'experiment_id':protocol['experiment_id'],'protocol_sha256':protocol_hash,'metrics':results,'latency':latency,
            'deltas':{'m1_minus_b1_f1_pp':100*(results['M1']['f1']-results['B1']['f1']),
                      'm1_minus_b1_recall_pp':100*(results['M1']['recall']-results['B1']['recall'])},
            'goals':protocol['provisional_goals'],'goals_are_not_school_cutoffs':True}
    result['goal_verdict']={'f1_5pp':result['deltas']['m1_minus_b1_f1_pp']>=5,'recall_no_worse':result['deltas']['m1_minus_b1_recall_pp']>=0}
    target_datetime=pd.to_datetime(target_time[test_mask]).as_unit('ns')
    frame=pd.DataFrame({'input_time':target_datetime-pd.Timedelta(hours=1),'target_time':target_datetime,
                        'station_code':(test_index%s).astype(np.int32),'station_id':stations.station_id.to_numpy()[test_index%s],
                        'quantity':q.ravel()[test_mask],'target_quantity':test_target,'target_zero':test_y,**{f'p_{name}':value for name,value in predictions.items()}})
    frame.to_parquet(OUT/'test_predictions.parquet',index=False)
    # Snapshot is complete enough to serve a map without loading the feature matrix.
    subgroups={}
    for label,mask in {'current_positive':frame.quantity.gt(0).to_numpy(), 'current_1_to_3':frame.quantity.between(1,3).to_numpy(),
                       'commute':(frame.input_time.dt.dayofweek.lt(5)&frame.input_time.dt.hour.isin([7,8,18,19])).to_numpy()}.items():
        subgroups[label]={name:metrics(test_y[mask],prob[mask],selected.get(name,{}).get('threshold',.5)) for name,prob in predictions.items()}
    dump('subgroup_metrics.json',subgroups)
    daily=[]
    for date,day in frame.groupby(frame.target_time.dt.date):
        for name in predictions:
            row=metrics(day.target_zero.to_numpy(),day[f'p_{name}'].to_numpy(),selected.get(name,{}).get('threshold',.5))
            daily.append({'date':str(date),'model':name,**row})
    daily_frame=pd.DataFrame(daily);daily_frame.to_csv(OUT/'daily_metrics.csv',index=False)
    a=daily_frame[daily_frame.model.eq('B1')][['tp','fp','fn']].to_numpy()
    b=daily_frame[daily_frame.model.eq('M1')][['tp','fp','fn']].to_numpy()
    rng=np.random.default_rng(20261019);differences=[]
    for _ in range(1000):
        idx=rng.integers(0,len(a),len(a));aa=a[idx].sum(axis=0);bb=b[idx].sum(axis=0)
        fa=2*aa[0]/(2*aa[0]+aa[1]+aa[2]) if aa.sum() else 0
        fb=2*bb[0]/(2*bb[0]+bb[1]+bb[2]) if bb.sum() else 0
        differences.append(100*(fb-fa))
    result['day_block_bootstrap_f1_delta_pp']={'replicates':1000,'lower_2_5':float(np.quantile(differences,.025)),'upper_97_5':float(np.quantile(differences,.975)),
        'interpretation':'Exploratory paired resampling of whole days; short 17-day test, not independent-season or field evidence.'}
    pred_m=frame.p_M1.ge(selected['M1']['threshold'])
    failures=frame[frame.target_zero.eq(1)&~pred_m].sort_values('p_M1').head(30).copy()
    failures=failures.merge(stations[['station_id','name','district']],on='station_id',how='left')
    failures.to_csv(OUT/'false_negative_cases.csv',index=False)
    transitions=frame.groupby('station_id').agg(rows=('target_zero','size'),zero_rate=('target_zero','mean'),mean_quantity=('quantity','mean'))
    station_rows=[]
    for sid,part in frame.groupby('station_id'):
        for name in ['B0','B1','M1']:
            station_rows.append({'station_id':int(sid),'model':name,**metrics(part.target_zero,part[f'p_{name}'],selected.get(name,{}).get('threshold',.5))})
    pd.DataFrame(station_rows).to_parquet(OUT/'station_metrics.parquet',index=False)
    dump('feature_importance.json',importance)
    result['total_pipeline_seconds']=time.perf_counter()-started
    dump('model_results.json',result)
    pd.DataFrame([{'model':name,**score} for name,score in results.items()]).to_csv(OUT/'model_metrics.csv',index=False)
    print('finished training/evaluation',result['deltas'],flush=True)


if __name__=='__main__':main()
