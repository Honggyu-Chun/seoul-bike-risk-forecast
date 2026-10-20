"""Resumable model search. Never reads new test scores during candidate selection."""
import argparse,gc,hashlib,json,time,traceback,os
from datetime import datetime,timezone
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.special import expit
from sklearn.metrics import log_loss
from .research_features import OUT,load_panel,build_features
from .data import ROOT
from .train import metrics

SEED=20261020


def dump(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n');temporary.replace(path)


def binary_counts(y,p,grid):
    # Sorted cumulative counts make a fine threshold grid inexpensive.
    order=np.argsort(p);pp=p[order];yy=np.asarray(y,dtype=np.int64)[order]
    cumulative=np.r_[0,np.cumsum(yy)];idx=np.searchsorted(pp,grid,side='left')
    tp=cumulative[-1]-cumulative[idx];fp=len(y)-idx-tp;fn=cumulative[-1]-tp
    return np.stack([tp,fp,fn],axis=-1)


def tune(y,p,current,coarse=False,minimum_recall=None):
    grid=np.arange(.05,1,.05) if coarse else np.arange(.01,1,.01)
    if minimum_recall is not None:grid=np.r_[0.,grid]
    counts=binary_counts(y,p,grid);den=2*counts[:,0]+counts[:,1]+counts[:,2]
    f=np.divide(2*counts[:,0],den,out=np.zeros(len(grid)),where=den>0)
    if minimum_recall is not None:f[counts[:,0]/max(1,np.sum(y))<minimum_recall]=-1
    i=np.flatnonzero(f==f.max())[-1]
    result={'kind':'global','threshold':float(grid[i]),'f1':float(f[i])}
    if not coarse:
        zero=np.asarray(current)==0;a=binary_counts(y[zero],p[zero],grid);b=binary_counts(y[~zero],p[~zero],grid)
        c=a[:,None,:]+b[None,:,:];den=2*c[:,:,0]+c[:,:,1]+c[:,:,2]
        f2=np.divide(2*c[:,:,0],den,out=np.zeros(den.shape),where=den>0);ij=np.argwhere(f2==f2.max())[-1]
        if minimum_recall is not None:
            f2[c[:,:,0]/max(1,np.sum(y))<minimum_recall]=-1
            ij=np.argwhere(f2==f2.max())[-1]
        if f2.max()>result['f1']:
            result={'kind':'current_state','zero_threshold':float(grid[ij[0]]),'positive_threshold':float(grid[ij[1]]),'f1':float(f2.max())}
    return result


def refine_rules(phase):
    """Additional validation-only decision rule satisfying the recall goal."""
    folder=OUT/phase;protocol=json.loads((ROOT/'config/improvement_v3.json').read_text());s=protocol[phase]
    times,stations,q=load_panel(end=s['valid_target_end_exclusive']);n=len(stations)
    target=np.vstack([q[1:],np.full((1,n),np.nan,dtype=np.float32)]).ravel();current=q.ravel()
    tt=np.repeat((times.as_unit('ns')+pd.Timedelta(hours=1)).to_numpy(),n)
    valid=np.isfinite(current)&np.isfinite(target)&(tt>=np.datetime64(s['valid_target_start']))&(tt<np.datetime64(s['valid_target_end_exclusive']))
    y=(target[valid]==0).astype(np.uint8);cq=current[valid]
    records=json.loads((folder/'leaderboard.json').read_text())
    baseline=max((r for r in records if r['status']=='ok' and r['name'].startswith('B1_original')),key=lambda r:r['rule']['f1'])
    minimum=baseline['validation']['recall']
    for record in records:
        if record['status']!='ok' or record['name'].startswith('B1_original'):continue
        p=np.load(folder/(record['name']+'_valid.npy'))
        rule=tune(y,p,cq,minimum_recall=minimum)
        record['selection_rule']=rule;record['selection_validation']=rule_metrics(y,p,cq,rule)
        dump(folder/(record['name']+'.json'),record)
    records.sort(key=lambda r:r.get('selection_rule',r.get('rule',{})).get('f1',-1),reverse=True)
    dump(folder/'leaderboard.json',records)
    # Parallel workers can finish in either order. Rebuild blends centrally from
    # every completed record instead of trusting a worker's earlier leaderboard.
    top=sorted((r for r in records if r['status']=='ok' and not r['name'].startswith('B1_original')),
               key=lambda r:-r['rule']['f1'])[:4]
    predictions={r['name']:np.load(folder/(r['name']+'_valid.npy')) for r in top}
    ensembles=[]
    for i,a in enumerate(top):
        for b in top[i+1:]:
            for weight in [.25,.5,.75]:
                p=weight*predictions[a['name']]+(1-weight)*predictions[b['name']]
                rule=tune(y,p,cq,minimum_recall=minimum)
                ensembles.append({'a':a['name'],'b':b['name'],'weight_a':weight,'rule':rule,
                                  'unconstrained_rule':tune(y,p,cq),'f1':rule['f1']})
    dump(folder/'ensembles.json',sorted(ensembles,key=lambda x:-x['f1']))
    dump(folder/'recall_constraint.json',{'minimum_validation_recall':minimum,'baseline':baseline['name'],'test_used':False})
    print('refined validation rules',phase,'recall floor',minimum,flush=True)


def decisions(p,q,rule):
    threshold=rule['threshold'] if rule['kind']=='global' else np.where(np.asarray(q)==0,rule['zero_threshold'],rule['positive_threshold'])
    return np.asarray(p)>=threshold


def fast_f1_monitor(y,p,current):
    """Validation-only binned F1 for checkpoint selection, not final reporting."""
    bins=np.minimum(100,np.maximum(0,(np.asarray(p)*100).astype(np.int32)))
    group=(np.asarray(current)==0).astype(np.int32);key=group*101+bins
    n=np.bincount(key,minlength=202).reshape(2,101)
    positives=np.bincount(key,weights=y,minlength=202).reshape(2,101)
    tp=np.cumsum(positives[:,::-1],axis=1)[:,::-1][:,1:100]
    predicted=np.cumsum(n[:,::-1],axis=1)[:,::-1][:,1:100]
    totals=positives.sum(axis=1)
    numerator=2*(tp[0,:,None]+tp[1,None,:]);denominator=predicted[0,:,None]+predicted[1,None,:]+totals.sum()
    return float(np.max(np.divide(numerator,denominator,out=np.zeros_like(numerator),where=denominator>0)))


def rule_metrics(y,p,current,rule):
    # Main score is computed from the frozen decisions. AP/Brier use raw scores.
    result=metrics(y,p,.5);pred=decisions(p,current,rule)
    fixed=metrics(y,pred.astype(float),.5)
    result.update({key:fixed[key] for key in ['f1','precision','recall','tn','fp','fn','tp']})
    result['rule']=rule
    result['threshold']=rule.get('threshold')
    return result


def candidates():
    c=[]
    def add(name,kind='lgb',group='temporal',**kw):c.append({'name':name,'kind':kind,'group':group,**kw})
    for leaves,leaf in [(31,300),(63,500)]:add(f'B1_original_{leaves}',group='basic',rounds=240,leaves=leaves,leaf=leaf,coarse=True)
    for leaves in [63,127]:add(f'B1_tuned_{leaves}',group='basic',rounds=1000,leaves=leaves,leaf=200)
    for leaves,leaf in [(31,200),(63,100),(63,500),(127,100),(127,500),(255,200)]:add(f'temporal_{leaves}_{leaf}',rounds=800,leaves=leaves,leaf=leaf)
    for leaves in [63,127]:add(f'spatial_multiscale_{leaves}',group='full',rounds=800,leaves=leaves,leaf=200)
    add('spatial_v1_long',group='spatial_v1',rounds=800,leaves=63,leaf=300)
    for days in [14,28]:add(f'recent_half_life_{days}',rounds=800,leaves=127,leaf=200,half_life=days)
    for scale in [2,4]:add(f'weighted_risk_{scale}',rounds=800,leaves=127,leaf=200,scale_pos_weight=scale)
    for gamma in [1,2]:add(f'focal_gamma_{gamma}',rounds=600,leaves=63,leaf=200,gamma=gamma)
    add('goss_temporal',rounds=800,leaves=127,leaf=200,data_sample_strategy='goss')
    add('dart_temporal',rounds=280,leaves=63,leaf=300,boosting='dart')
    add('random_forest_lgb',rounds=400,leaves=127,leaf=300,boosting='rf')
    add('poisson_quantity','poisson',rounds=600,leaves=127,leaf=200)
    add('lower_quantile_quantity','quantile',rounds=600,leaves=127,leaf=200)
    add('state_experts_temporal','experts',rounds=650,leaves=63,leaf=150)
    add('state_experts_spatial','experts',group='full',rounds=650,leaves=63,leaf=150)
    for depth in [6,8]:add(f'catboost_depth_{depth}','catboost',iterations=800,depth=depth)
    for depth in [6,9]:add(f'xgboost_depth_{depth}','xgboost',iterations=850,depth=depth)
    for alpha in [1e-5,1e-4]:add(f'linear_logit_{alpha}','linear',alpha=alpha)
    add('extra_trees','extra',sample=1000000)
    add('random_forest_sklearn','forest',sample=1000000)
    add('conditional_markov','markov',smoothing=100)
    add('conditional_markov_local','markov',smoothing=20)
    for leaves in [63,127]:add(f'causal_transition_{leaves}',group='hazard',rounds=1000,leaves=leaves,leaf=150)
    add('causal_transition_spatial',group='hazard_spatial',rounds=1000,leaves=127,leaf=150)
    for label in ['own','spatial']:
        add('causal_weather_'+label,group='weather_'+label,rounds=1000,leaves=127,leaf=150)
    return c


def focal_objective(gamma):
    def objective(raw,data):
        y=data.get_label();s=2*y-1;pt=np.clip(expit(s*raw),1e-7,1-1e-7);a=(1-pt)**gamma
        grad=s*a*(gamma*pt*np.log(pt)+pt-1)
        hess=a*pt*((1-pt)*(2*gamma+1+gamma*np.log(pt))-gamma*gamma*pt*np.log(pt))
        return grad,np.maximum(hess,1e-5)
    return objective


def fit_model(spec,train_x,y,valid_x,vy,tq,vq,tt,target_count,folder):
    kind=spec['kind'];cat=[c for c in ['station_code','hour','weekday'] if c in train_x]
    threads=int(os.environ.get('BIKE_RESEARCH_THREADS','4'))
    progress=int(os.environ.get('BIKE_PROGRESS_EVERY','100'))
    if kind in ['lgb','poisson','quantile','experts']:
        settings={'objective':'binary','metric':'binary_logloss','verbosity':-1,'learning_rate':.05,'num_threads':threads,'seed':SEED,'deterministic':True,'force_col_wise':True,'max_bin':127,'num_leaves':spec.get('leaves',63),'min_data_in_leaf':spec.get('leaf',200),'lambda_l2':2.,'feature_pre_filter':False}
        if spec.get('coarse'):settings['lambda_l2']=1.
        for k in ['scale_pos_weight','data_sample_strategy']: 
            if k in spec:settings[k]=spec[k]
        if 'boosting' in spec:
            settings['boosting_type']=spec['boosting']
            if spec['boosting']=='rf':settings.update(bagging_fraction=.8,bagging_freq=1,feature_fraction=.8)
        if kind=='poisson':settings.update(objective='poisson',metric='poisson')
        if kind=='quantile':settings.update(objective='quantile',metric='quantile',alpha=.15)
        gamma=spec.get('gamma')
        if gamma:settings.update(objective=focal_objective(gamma),metric='None')
        weights=None
        if 'half_life' in spec:
            age=(pd.Timestamp(tt.max())-pd.DatetimeIndex(tt)).total_seconds().to_numpy()/86400
            weights=np.exp(-np.log(2)*age/spec['half_life']).astype(np.float32)
        def fit_one(mask,v_mask,suffix=''):
            label=target_count[mask] if kind in ['poisson','quantile'] else y[mask]
            validation=vy[v_mask] if kind not in ['poisson','quantile'] else np.zeros(v_mask.sum())
            # Count objectives stop by binary validation logloss after transforming
            # their output; the count validation label is unnecessary for this feval.
            ds=lgb.Dataset(train_x.iloc[np.flatnonzero(mask)],label=label,weight=None if weights is None else weights[mask],categorical_feature=cat)
            vs=lgb.Dataset(valid_x.iloc[np.flatnonzero(v_mask)],label=validation,reference=ds)
            params=dict(settings);feval=None
            if gamma:
                def feval(raw,data):return 'prob_logloss',float(log_loss(data.get_label(),np.clip(expit(raw),1e-7,1-1e-7),labels=[0,1])),False
            if kind in ['poisson','quantile']:
                params['metric']='None'
                def feval(raw,data):return 'zero_logloss',float(log_loss(vy[v_mask],np.clip(np.exp(-np.maximum(raw,0)),1e-7,1-1e-7),labels=[0,1])),False
            if spec.get('select_f1'):
                params['metric']='None'
                def feval(raw,data):
                    prob=expit(raw) if gamma else np.exp(-np.maximum(raw,0)) if kind in ['poisson','quantile'] else raw
                    return 'validation_f1_binned',fast_f1_monitor(vy[v_mask],prob,vq[v_mask]),True
            callbacks=[lgb.log_evaluation(progress)]
            if spec.get('boosting')!='dart':callbacks.append(lgb.early_stopping(30 if spec.get('coarse') else 65,verbose=False))
            model=lgb.train(params,ds,num_boost_round=spec.get('rounds',800),valid_sets=[vs],feval=feval,callbacks=callbacks)
            path=folder/f'{spec["name"]}{suffix}.txt';model.save_model(str(path))
            return model,path.name
        if kind=='experts':
            prediction=np.empty(len(valid_x),dtype=np.float32);paths=[];iters=[]
            for empty in [False,True]:
                mask=(tq==0)==empty;vmask=(vq==0)==empty
                model,path=fit_one(mask,vmask,'_zero' if empty else '_positive');prediction[vmask]=model.predict(valid_x.iloc[np.flatnonzero(vmask)],num_threads=threads);paths.append(path);iters.append(model.best_iteration)
            return prediction,{'paths':paths,'iterations':iters}
        model,path=fit_one(np.ones(len(y),bool),np.ones(len(vy),bool))
        pred=model.predict(valid_x,num_threads=threads)
        if gamma:pred=expit(pred)
        if kind in ['poisson','quantile']:pred=np.exp(-np.maximum(pred,0))
        return pred.astype(np.float32),{'paths':[path],'iterations':[model.best_iteration]}
    if kind=='catboost':
        from catboost import CatBoostClassifier,Pool
        tx=train_x.copy();vx=valid_x.copy()
        for col in cat:tx[col]=tx[col].astype(np.int32);vx[col]=vx[col].astype(np.int32)
        model=CatBoostClassifier(iterations=spec['iterations'],depth=spec['depth'],learning_rate=.06,l2_leaf_reg=5,loss_function='Logloss',eval_metric='Logloss',random_seed=SEED,thread_count=4,border_count=64,has_time=True,verbose=False,allow_writing_files=False)
        model.fit(Pool(tx,y,cat_features=cat),eval_set=Pool(vx,vy,cat_features=cat),early_stopping_rounds=65,verbose=progress or False)
        path=folder/(spec['name']+'.cbm');model.save_model(str(path))
        return model.predict_proba(vx,thread_count=4)[:,1].astype(np.float32),{'paths':[path.name],'iterations':[model.tree_count_]}
    if kind=='xgboost':
        import xgboost as xgb
        tx=train_x.copy();vx=valid_x.copy()
        for col in cat:
            values=np.arange(int(max(tx[col].max(),vx[col].max()))+1)
            tx[col]=pd.Categorical(tx[col],categories=values);vx[col]=pd.Categorical(vx[col],categories=values)
        model=xgb.XGBClassifier(n_estimators=spec['iterations'],max_depth=spec['depth'],learning_rate=.06,min_child_weight=50,subsample=.9,colsample_bytree=.9,reg_lambda=5,tree_method='hist',max_bin=127,n_jobs=4,random_state=SEED,enable_categorical=True,eval_metric='logloss',early_stopping_rounds=65)
        model.fit(tx,y,eval_set=[(vx,vy)],verbose=progress or False)
        path=folder/(spec['name']+'.ubj');model.save_model(path)
        return model.predict_proba(vx)[:,1].astype(np.float32),{'paths':[path.name],'iterations':[model.best_iteration+1]}
    if kind in ['extra','forest','linear']:
        from sklearn.impute import SimpleImputer
        from sklearn.preprocessing import StandardScaler,OneHotEncoder
        from sklearn.compose import ColumnTransformer
        from sklearn.pipeline import make_pipeline
        rng=np.random.default_rng(SEED);idx=np.sort(rng.choice(len(y),min(len(y),spec.get('sample',1000000)),replace=False))
        if kind=='linear':
            from sklearn.linear_model import SGDClassifier
            numerical=[c for c in train_x if c not in cat]
            transform=ColumnTransformer([('category',OneHotEncoder(handle_unknown='ignore'),cat),('numeric',make_pipeline(SimpleImputer(add_indicator=True),StandardScaler()),numerical)],sparse_threshold=1.)
            model=make_pipeline(transform,SGDClassifier(loss='log_loss',alpha=spec['alpha'],max_iter=35,tol=1e-4,random_state=SEED,n_jobs=4))
        else:
            from sklearn.ensemble import ExtraTreesClassifier,RandomForestClassifier
            cls=ExtraTreesClassifier if kind=='extra' else RandomForestClassifier
            model=make_pipeline(SimpleImputer(add_indicator=True),cls(n_estimators=80,max_depth=22,min_samples_leaf=100,max_features=.7,class_weight='balanced',n_jobs=4,random_state=SEED))
        model.fit(train_x.iloc[idx],y[idx]);path=folder/(spec['name']+'.joblib');joblib.dump(model,path)
        return model.predict_proba(valid_x)[:,1].astype(np.float32),{'paths':[path.name],'sampled_train_rows':len(idx)}
    if kind=='markov':
        # Hierarchical smoothing, fitted to training labels only. No val targets.
        smooth=spec['smoothing'];bins=lambda a:np.minimum(np.asarray(a,dtype=int),8)
        tb=bins(tq);vb=bins(vq);station=train_x.station_code.to_numpy(dtype=int);vs=valid_x.station_code.to_numpy(dtype=int)
        hour=train_x.hour.to_numpy(dtype=int);vh=valid_x.hour.to_numpy(dtype=int)
        nstations=int(max(station.max(),vs.max()))+1
        counts=np.bincount(tb,minlength=9);positives=np.bincount(tb,weights=y,minlength=9)
        global_rate=(positives+1)/(counts+2)
        sk=station*9+tb;vsk=vs*9+vb
        counts=np.bincount(sk,minlength=nstations*9);positives=np.bincount(sk,weights=y,minlength=nstations*9)
        local=(positives+smooth*np.tile(global_rate,nstations))/(counts+smooth)
        key=(station*24+hour)*9+tb;vkey=(vs*24+vh)*9+vb
        counts=np.bincount(key,minlength=nstations*24*9);positives=np.bincount(key,weights=y,minlength=nstations*24*9)
        prior=np.repeat(local.reshape(nstations,9)[:,None,:],24,axis=1).ravel()
        rates=(positives+smooth*prior)/(counts+smooth)
        path=folder/(spec['name']+'.npz');np.savez_compressed(path,rates=rates)
        return rates[vkey].astype(np.float32),{'paths':[path.name]}
    raise ValueError(kind)


def setup(phase):
    p=json.loads((ROOT/'config/improvement_v3.json').read_text());s=p[phase]
    end=s['valid_target_end_exclusive'] if phase=='screen' else p['forward']['test_target_end_exclusive']
    times,stations,q=load_panel(end=end)
    print('building research features',phase,q.shape,flush=True);frame,groups=build_features(times,stations,q)
    n=len(stations);target=np.vstack([q[1:],np.full((1,n),np.nan,dtype=np.float32)]).ravel();current=q.ravel()
    target_time=np.repeat((times+pd.Timedelta(hours=1)).to_numpy(),n)
    observed=np.isfinite(current)&np.isfinite(target)
    train=observed&(target_time<np.datetime64(s['train_target_end_exclusive']))
    valid=observed&(target_time>=np.datetime64(s['valid_target_start']))&(target_time<np.datetime64(s['valid_target_end_exclusive']))
    print('data sizes',phase,int(train.sum()),int(valid.sum()),len(frame.columns),flush=True)
    return p,frame,groups,current,target,target_time,train,valid


def run_search(phase,limit=None,names=None):
    folder=OUT/phase;models=folder/'models';models.mkdir(parents=True,exist_ok=True)
    specs=candidates()
    if names:specs=[s for s in specs if s['name'] in names]
    elif phase=='forward':
        records=json.loads((OUT/'screen/leaderboard.json').read_text())
        # Retain six distinct learner/input representations. Near-duplicate
        # depths/leaves already competed on screening validation; refitting
        # them all would crowd out other methods on this five-CPU machine.
        top=[];seen=set()
        for row in records:
            if row.get('status')!='ok' or row['name'].startswith('B1_original'):continue
            key=(row['kind'],row.get('spec',{}).get('group'))
            if key in seen:continue
            seen.add(key);top.append(row['name'])
            if len(top)==6:break
        # Keep diverse tree families even if screening favors one representation.
        for kind in ['catboost','xgboost','experts']:
            row=next((r for r in records if r.get('kind')==kind and r.get('status')=='ok'),None)
            if row:top.append(row['name'])
        # Keep the best archived-weather sensitivity for a direct data-source
        # comparison even if the global top six favor another representation.
        row=next((r for r in records if r.get('status')=='ok' and r.get('spec',{}).get('group','').startswith('weather_')),None)
        if row:top.append(row['name'])
        top+=['B1_original_31','B1_original_63','B1_tuned_127']
        specs=[s for s in specs if s['name'] in set(top)]
    if limit:specs=specs[:limit]
    pending=[s for s in specs if not (folder/(s['name']+'.json')).exists()]
    if not pending:print('all candidates already recorded',phase,flush=True);return
    p,frame,groups,current,target,tt,train,valid=setup(phase)
    y=(target[train]==0).astype(np.uint8);vy=(target[valid]==0).astype(np.uint8);tq=current[train];vq=current[valid]
    dump(folder/'manifest.json',{'phase':phase,'protocol':p,'train_rows':int(train.sum()),'validation_rows':int(valid.sum()),'feature_groups':groups,'test_metrics_opened':False})
    for spec in pending:
        spec=dict(spec)
        if (phase=='forward' or os.environ.get('BIKE_F1_SCREEN')=='1') and not spec.get('coarse') and spec['kind'] in ['lgb','experts','poisson','quantile']:spec['select_f1']=True
        started=time.perf_counter();name=spec['name'];print('START',phase,name,flush=True)
        try:
            cols=groups[spec['group']];tx=frame.loc[train,cols];vx=frame.loc[valid,cols]
            pred,fit_info=fit_model(spec,tx,y,vx,vy,tq,vq,tt[train],target[train],models)
            assert np.isfinite(pred).all() and (pred>=0).all() and (pred<=1).all()
            rule=tune(vy,pred,vq,coarse=spec.get('coarse',False))
            result={'name':name,'kind':spec['kind'],'spec':spec,'status':'ok','validation':rule_metrics(vy,pred,vq,rule),'rule':rule,'fit':fit_info,'features':cols,'seconds':time.perf_counter()-started,'resource_threads':int(os.environ.get('BIKE_RESEARCH_THREADS','4')),'test_used':False}
            np.save(folder/(name+'_valid.npy'),pred)
            print('RESULT',phase,name,'F1',rule['f1'],'seconds',round(result['seconds'],1),flush=True)
            del tx,vx,pred
        except Exception as error:
            result={'name':name,'kind':spec['kind'],'spec':spec,'status':'error','error':str(error),'traceback':traceback.format_exc(),'seconds':time.perf_counter()-started};print('ERROR',name,str(error),flush=True)
        dump(folder/(name+'.json'),result)
        records=[json.loads(path.read_text()) for path in folder.glob('*.json') if path.name not in ['manifest.json','leaderboard.json','ensembles.json']]
        records=[r for r in records if 'status' in r];records.sort(key=lambda r:r.get('rule',{}).get('f1',-1),reverse=True)
        dump(folder/'leaderboard.json',records)
        gc.collect()
    # Small probability ensembles, selected only on this phase's validation.
    successes=[r for r in json.loads((folder/'leaderboard.json').read_text()) if r['status']=='ok' and not r['name'].startswith('B1_original')]
    top=successes[:4];ensembles=[]
    for i,a in enumerate(top):
        for b in top[i+1:]:
            pa=np.load(folder/(a['name']+'_valid.npy'));pb=np.load(folder/(b['name']+'_valid.npy'))
            for weight in [.25,.5,.75]:
                pred=weight*pa+(1-weight)*pb;rule=tune(vy,pred,vq)
                ensembles.append({'a':a['name'],'b':b['name'],'weight_a':weight,'rule':rule,'f1':rule['f1']})
    dump(folder/'ensembles.json',sorted(ensembles,key=lambda x:-x['f1']))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['prepare','screen','forward'],required=True);parser.add_argument('--limit',type=int);parser.add_argument('--names',nargs='*');parser.add_argument('--refine-rules',action='store_true');args=parser.parse_args()
    if args.phase=='prepare':load_panel()
    elif args.refine_rules:refine_rules(args.phase)
    else:run_search(args.phase,args.limit,args.names)


if __name__=='__main__':main()
