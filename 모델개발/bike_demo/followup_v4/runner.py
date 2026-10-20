"""Resumable chronological baseline and tabular comparisons on development folds."""
import argparse
import gc
import json
import os
import time
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from scipy.special import expit,logit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from .common import ROOT,OUT,RECORD,digest,save,now,protocol,process_memory
from ..research import tune,rule_metrics

def specs():
    base=[dict(name="persistence",kind="rule",rule="current",group="basic"),
          dict(name="seasonal_day",kind="rule",rule="q_lag_24",group="basic"),
          dict(name="seasonal_week",kind="rule",rule="q_lag_168",group="basic"),
          dict(name="smoothed_markov",kind="markov",group="basic"),
          dict(name="logistic",kind="linear",group="basic",sample=1000000),
          dict(name="random_forest",kind="forest",group="basic",sample=500000,trees=100),
          dict(name="lightgbm_basic",kind="lgb",group="basic",leaves=63,rounds=600),
          dict(name="lightgbm_full",kind="lgb",group="hazard_spatial",leaves=127,rounds=900),
          dict(name="catboost_temporal",kind="cat",group="temporal",depth=8,rounds=800),
          dict(name="xgboost_temporal",kind="xgb",group="temporal",depth=8,rounds=800),
          dict(name="lightgbm_weather",kind="lgb",group="weather_spatial",leaves=127,rounds=900)]
    for row in base:row.update(window=56,seed=20261020)
    extra=[]
    for name,changes in [("lightgbm_full_112d",dict(window=112)),("lightgbm_full_recency28",dict(half_life=28)),
                         ("lightgbm_full_63",dict(leaves=63)),("lightgbm_full_focal",dict(focal=2)),
                         ("lightgbm_ordinal",dict(kind="ordinal")),("lightgbm_poisson",dict(kind="poisson")),
                         ("lightgbm_state_experts",dict(kind="experts"))]:
        row=dict(next(x for x in base if x['name']=='lightgbm_full'));row.update(changes);row['name']=name;extra.append(row)
    return base+extra

class Inputs:
    def __init__(self):
        with np.load(OUT/"data/panel_2025.npz") as p:self.q=p['quantity'];self.times=pd.DatetimeIndex(p['times'])
        self.n=self.q.shape[1];self.meta=json.loads((OUT/"data/features_2025.json").read_text(encoding='utf-8'))
        self.columns=self.meta['columns'];self.matrix=np.load(OUT/"data/features_2025.npy",mmap_mode='r')
        self.target=np.vstack([self.q[1:],np.full((1,self.n),np.nan,dtype=np.float32)]).ravel()
        self.tt=np.repeat((self.times+pd.Timedelta(hours=1)).to_numpy(),self.n)
        self.known=np.isfinite(self.q.ravel())&np.isfinite(self.target)
    def indices(self,start,end):
        return np.flatnonzero(self.known&(self.tt>=np.datetime64(start))&(self.tt<np.datetime64(end)))
    def frame(self,index,columns):
        # Index columns separately to avoid an all-feature advanced-index copy.
        out=np.empty((len(index),len(columns)),dtype=np.float32)
        for j,name in enumerate(columns):out[:,j]=self.matrix[index,self.columns.index(name)]
        return pd.DataFrame(out,columns=columns,copy=False)

def predict_model(model,x,kind):
    if kind in ('lgb','poisson','ordinal','experts'):
        if kind=='experts':
            p=np.empty(len(x));zero=x.quantity.to_numpy()==0
            for flag,m in enumerate(model):
                mask=zero==bool(flag)
                p[mask]=m.predict(x.loc[mask],num_threads=4)
            return p
        p=model.predict(x,num_threads=4)
        if kind=='poisson':return np.exp(-np.maximum(0,p))
        if kind=='ordinal':return p[:,0]
        return p
    return model.predict_proba(x)[:,0] if kind=='cat_ordinal' else model.predict_proba(x)[:,1]

def fit(spec,tx,y,sx,sy,train_times,count,model_dir):
    import lightgbm as lgb
    cat=[x for x in ('station_code','hour','weekday') if x in tx]
    kind=spec['kind'];seed=spec['seed']
    if kind in ('lgb','poisson','ordinal','experts'):
        weights=None
        if spec.get('half_life'):
            age=(train_times.max()-train_times).astype('timedelta64[h]').astype(float)/24
            weights=np.exp(-np.log(2)*age/spec['half_life']).astype(np.float32)
        params=dict(objective='binary',metric='binary_logloss',verbosity=-1,learning_rate=.05,
                    num_leaves=spec['leaves'],min_data_in_leaf=200,lambda_l2=2,max_bin=127,
                    num_threads=4,seed=seed,deterministic=True,force_col_wise=True)
        labels=y
        if kind=='poisson':
            params.update(objective='poisson',metric='None');labels=count
        if kind=='ordinal':
            params.update(objective='multiclass',num_class=4,metric='multi_logloss');labels=np.minimum(count,3).astype(np.int32)
        def one(x,label,vx,vy,suffix='',w=None):
            ds=lgb.Dataset(x,label=label,weight=w,categorical_feature=cat,free_raw_data=True)
            vl=lgb.Dataset(vx,label=vy,reference=ds,categorical_feature=cat,free_raw_data=True)
            feval=None
            if kind=='poisson':
                feval=lambda pred,data:('zero_logloss',float(log_loss(sy,np.clip(np.exp(-np.maximum(pred,0)),1e-7,1-1e-7),labels=[0,1])),False)
            if spec.get('focal'):
                from ..research import focal_objective
                params.update(objective=focal_objective(spec['focal']),metric='None')
                feval=lambda raw,data:('logloss',float(log_loss(data.get_label(),expit(raw),labels=[0,1])),False)
            model=lgb.train(params,ds,num_boost_round=spec['rounds'],valid_sets=[vl],feval=feval,
                            callbacks=[lgb.early_stopping(60,verbose=False),lgb.log_evaluation(100)])
            # LightGBM's native Windows file writer does not accept this Korean
            # workspace path. Python handles Unicode paths correctly.
            (model_dir/(spec['name']+suffix+'.txt')).write_text(model.model_to_string(num_iteration=model.best_iteration),encoding='utf-8')
            del ds,vl;gc.collect();return model
        if kind=='experts':
            models=[]
            for empty in (False,True):
                mask=(tx.quantity.to_numpy()==0)==empty;vm=(sx.quantity.to_numpy()==0)==empty
                models.append(one(tx.loc[mask],labels[mask],sx.loc[vm],sy[vm],suffix='_zero' if empty else '_positive'))
            return models,dict(iterations=[m.best_iteration for m in models])
        stop_labels=np.minimum(sx.attrs['target_count'],3).astype(np.int32) if kind=='ordinal' else sy
        model=one(tx,labels,sx,stop_labels,w=weights)
        return model,dict(iterations=model.best_iteration)
    if kind=='cat':
        from catboost import CatBoostClassifier,Pool
        from .snapshots import catboost_snapshot
        for x in (tx,sx):
            for col in cat:x[col]=x[col].astype(np.int32)
        snapshot_options,snapshot_info=catboost_snapshot(model_dir,spec,tx,y,sx,sy)
        print('CATBOOST_SNAPSHOT',snapshot_info,flush=True)
        model=CatBoostClassifier(iterations=spec['rounds'],depth=spec['depth'],learning_rate=.06,l2_leaf_reg=5,
                                loss_function='Logloss',eval_metric='Logloss',random_seed=seed,thread_count=4,
                                border_count=64,has_time=True,verbose=100,**snapshot_options)
        model.fit(Pool(tx,y,cat_features=cat),eval_set=Pool(sx,sy,cat_features=cat),early_stopping_rounds=60)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.cbm',delete=False) as tmp:temp_path=Path(tmp.name)
        try:
            model.save_model(str(temp_path));(model_dir/(spec['name']+'.cbm')).write_bytes(temp_path.read_bytes())
        finally:temp_path.unlink(missing_ok=True)
        return model,dict(iterations=model.tree_count_,**snapshot_info)
    if kind=='xgb':
        import xgboost as xgb
        model=xgb.XGBClassifier(n_estimators=spec['rounds'],max_depth=spec['depth'],learning_rate=.06,
                               min_child_weight=50,subsample=.9,colsample_bytree=.9,reg_lambda=5,
                               tree_method='hist',max_bin=127,n_jobs=4,random_state=seed,
                               eval_metric='logloss',early_stopping_rounds=60)
        model.fit(tx,y,eval_set=[(sx,sy)],verbose=100)
        (model_dir/(spec['name']+'.ubj')).write_bytes(model.get_booster().save_raw(raw_format='ubj'))
        return model,dict(iterations=model.best_iteration+1,station_codes_treated_as='numeric; categorical treatment is a separate variant')
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    if kind=='forest':
        from sklearn.ensemble import RandomForestClassifier
        model=make_pipeline(SimpleImputer(add_indicator=True),RandomForestClassifier(n_estimators=spec['trees'],
                                max_depth=20,min_samples_leaf=100,max_features=.7,n_jobs=4,random_state=seed))
    elif kind=='linear':
        from sklearn.compose import ColumnTransformer
        from sklearn.preprocessing import OneHotEncoder,StandardScaler
        from sklearn.linear_model import SGDClassifier
        num=[c for c in tx if c not in cat]
        prep=ColumnTransformer([('cat',OneHotEncoder(handle_unknown='ignore'),cat),
                                ('num',make_pipeline(SimpleImputer(add_indicator=True),StandardScaler()),num)],sparse_threshold=1.)
        model=make_pipeline(prep,SGDClassifier(loss='log_loss',alpha=1e-4,max_iter=40,tol=1e-4,random_state=seed,n_jobs=4))
    else:raise ValueError(kind)
    model.fit(tx,y);joblib.dump(model,model_dir/(spec['name']+'.joblib'))
    return model,dict(sampled_train_rows=len(tx))

def calibrator(y,p):
    model=LogisticRegression(C=100,max_iter=100)
    model.fit(logit(np.clip(p,1e-6,1-1e-6)).reshape(-1,1),y)
    return model

def summarize(y,p,q,rule):
    answer={'overall':rule_metrics(y,p,q,rule)}
    for name,mask in [('current_positive',q>0),('current_1_to_3',(q>=1)&(q<=3))]:
        answer[name]=rule_metrics(y[mask],p[mask],q[mask],rule) if mask.any() else None
    return answer

def rule_predictions(spec,d,index,train):
    quantity=d.q.ravel()
    if spec['kind']=='rule':
        values=quantity[index] if spec['rule']=='current' else np.asarray(d.matrix[index,d.columns.index(spec['rule'])])
        return np.where(np.isfinite(values),values==0,np.mean(d.target[train]==0)).astype(np.float32)
    # Hierarchical station-hour/count conditional rates fitted to past train labels.
    n=d.n;tb=np.minimum(quantity[train].astype(int),8);vb=np.minimum(quantity[index].astype(int),8)
    sid=train%n;vid=index%n;hour=d.times.hour.to_numpy()[train//n];vhour=d.times.hour.to_numpy()[index//n]
    y=(d.target[train]==0).astype(np.float32)
    cnt=np.bincount(tb,minlength=9);pos=np.bincount(tb,weights=y,minlength=9);global_rate=(pos+1)/(cnt+2)
    sk=sid*9+tb;cnt=np.bincount(sk,minlength=n*9);pos=np.bincount(sk,weights=y,minlength=n*9)
    local=(pos+20*np.tile(global_rate,n))/(cnt+20)
    key=(sid*24+hour)*9+tb;vkey=(vid*24+vhour)*9+vb
    cnt=np.bincount(key,minlength=n*24*9);pos=np.bincount(key,weights=y,minlength=n*24*9)
    prior=np.repeat(local.reshape(n,1,9),24,axis=1).ravel()
    return ((pos+20*prior)/(cnt+20))[vkey].astype(np.float32)

def run_one(d,fold,spec):
    cfg=protocol();split=cfg['development_folds'][fold]
    folder=OUT/fold;path=folder/(spec['name']+'.json');models=folder/'models';models.mkdir(parents=True,exist_ok=True)
    code_hash=digest(__file__);protocol_hash=digest(RECORD/'protocol.json')
    if path.exists():
        previous=json.loads(path.read_text(encoding='utf-8'))
        if previous['status']=='ok':
            assert previous['protocol_sha256']==protocol_hash,'Protocol changed; use a new run ID'
            print('SKIP',fold,spec['name'],flush=True);return
    start=time.perf_counter();save(OUT/'worker.json',dict(pid=os.getpid(),phase='fit',fold=fold,candidate=spec['name'],updated_at_utc=now()))
    train_start=pd.Timestamp(split['train_end'])-pd.Timedelta(days=spec['window'])
    train=d.indices(train_start,split['train_end']);full_train_rows=len(train)
    if spec.get('sample') and len(train)>spec['sample']:
        train=np.sort(np.random.default_rng(spec['seed']).choice(train,spec['sample'],replace=False))
    stages={name:d.indices(split[name+'_start'],split[name+'_end']) for name in ('stop','cal','policy','valid')}
    ys={k:(d.target[ix]==0).astype(np.uint8) for k,ix in stages.items()}
    pred={};fit_info={};columns=d.meta['groups'][spec['group']]
    if spec['kind'] in ('rule','markov'):
        for stage in ('cal','policy','valid'):pred[stage]=rule_predictions(spec,d,stages[stage],train)
    else:
        tx=d.frame(train,columns);sx=d.frame(stages['stop'],columns);sx.attrs['target_count']=d.target[stages['stop']]
        model,fit_info=fit(spec,tx,(d.target[train]==0).astype(np.uint8),sx,ys['stop'],d.tt[train],d.target[train],models)
        del tx,sx;gc.collect()
        for stage in ('cal','policy','valid'):
            x=d.frame(stages[stage],columns)
            if spec['kind']=='cat':
                for col in ('station_code','hour','weekday'):
                    if col in x:x[col]=x[col].astype(np.int32)
            pred[stage]=predict_model(model,x,spec['kind'])
            if spec.get('focal'):pred[stage]=expit(pred[stage])
            del x;gc.collect()
        del model;gc.collect()
    for value in pred.values():assert np.isfinite(value).all() and (value>=0).all() and (value<=1).all()
    cal=calibrator(ys['cal'],pred['cal']);joblib.dump(cal,models/(spec['name']+'_calibrator.joblib'))
    calibrated={s:cal.predict_proba(logit(np.clip(p,1e-6,1-1e-6)).reshape(-1,1))[:,1].astype(np.float32) for s,p in pred.items()}
    pq=d.q.ravel()[stages['policy']];rule=tune(ys['policy'],calibrated['policy'],pq)
    validation=summarize(ys['valid'],calibrated['valid'],d.q.ravel()[stages['valid']],rule)
    raw_rule=tune(ys['policy'],pred['policy'],pq)
    raw=summarize(ys['valid'],pred['valid'],d.q.ravel()[stages['valid']],raw_rule)
    prediction_dir=OUT/'predictions'/fold;prediction_dir.mkdir(parents=True,exist_ok=True)
    for stage in pred:
        np.save(prediction_dir/(spec['name']+'_'+stage+'.npy'),pred[stage].astype(np.float32))
    save(path,dict(name=spec['name'],status='ok',fold=fold,spec=spec,features=columns,fit=fit_info,
                   actual_train_rows=len(train),full_train_rows=full_train_rows,stage_rows={k:len(v) for k,v in stages.items()},
                   rule=rule,validation=validation,raw_validation=raw,raw_rule=raw_rule,
                   calibrated_on=split['cal_start']+'/'+split['cal_end'],seconds=time.perf_counter()-start,
                   memory=process_memory(),code_sha256=code_hash,
                   protocol_sha256=protocol_hash,data_sha256=d.meta['panel_sha256'],completed_at_utc=now(),
                   information_track='archived_weather_sensitivity' if spec['group'].startswith('weather') else 'availability_timestamp_causal',
                   independent_final_test=False))
    print('RESULT',fold,spec['name'],'F1',round(validation['overall']['f1'],6),'transition recall',round(validation['current_positive']['recall'],6),'seconds',round(time.perf_counter()-start,1),flush=True)
    refresh()

def refresh():
    rows=[]
    for f in ('A','B','C'):
        for p in (OUT/f).glob('*.json'):
            r=json.loads(p.read_text(encoding='utf-8'))
            if r.get('status')!='ok':continue
            rows.append(dict(fold=f,model=r['name'],F1=r['validation']['overall']['f1'],AP=r['validation']['overall']['average_precision'],
                             recall=r['validation']['overall']['recall'],transition_recall=r['validation']['current_positive']['recall'],
                             transition_precision=r['validation']['current_positive']['precision'],FP=r['validation']['overall']['fp'],
                             FN=r['validation']['overall']['fn'],Brier=r['validation']['overall']['brier'],seconds=r['seconds'],
                             train_rows=r['actual_train_rows'],track=r['information_track']))
    save(OUT/'leaderboard.json',rows)
    if rows:pd.DataFrame(rows).to_csv(RECORD/'leaderboard.csv',index=False,encoding='utf-8-sig')

def main():
    p=argparse.ArgumentParser();p.add_argument('--folds',nargs='+',default=['A']);p.add_argument('--names',nargs='+');args=p.parse_args()
    available=specs();selected=[s for s in available if not args.names or s['name'] in args.names]
    assert selected and (not args.names or set(args.names)<={s['name'] for s in selected})
    d=Inputs()
    for f in args.folds:
        for s in selected:
            print('START',f,s['name'],now(),flush=True)
            try:run_one(d,f,s)
            except Exception as exc:
                import traceback
                save(OUT/f/(s['name']+'_error.json'),dict(status='error',message=str(exc),traceback=traceback.format_exc(),spec=s))
                traceback.print_exc()
                raise
    save(OUT/'worker.json',dict(pid=os.getpid(),phase='completed',updated_at_utc=now()))

if __name__=='__main__':main()
