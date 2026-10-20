"""CPU-bounded temporal MLP/TCN and sparse-neighbor ablations.

These are project adaptations, not full reproductions of published STID/STGCN.
"""
import argparse
import gc
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd
import torch
from torch import nn
from sklearn.neighbors import BallTree
from .common import ROOT,OUT,RECORD,save,digest,now
from . import runner

class TemporalModel(nn.Module):
    def __init__(self,n,length,kind,graph):
        super().__init__();self.kind=kind;self.graph=graph
        self.station=nn.Embedding(n,16);self.hour=nn.Embedding(24,8);self.weekday=nn.Embedding(7,4)
        if kind=='mlp':self.encoder=nn.Sequential(nn.Flatten(),nn.Linear(6*length,96),nn.ReLU(),nn.Dropout(.1))
        else:self.encoder=nn.Sequential(nn.Conv1d(6,32,3,padding=2,dilation=2),nn.ReLU(),nn.Conv1d(32,32,3,padding=4,dilation=4),nn.ReLU(),nn.AdaptiveAvgPool1d(1),nn.Flatten(),nn.Linear(32,96),nn.ReLU())
        self.head=nn.Sequential(nn.Linear(96*(2 if graph else 1)+28,96),nn.ReLU(),nn.Dropout(.1),nn.Linear(96,1))
    def forward(self,x,sid,hour,weekday,neighbor=None):
        parts=[self.encoder(x)]
        if self.graph:parts.append(self.encoder(neighbor))
        parts.extend([self.station(sid),self.hour(hour),self.weekday(weekday)])
        return self.head(torch.cat(parts,dim=1)).squeeze(1)

class SequenceInputs(runner.Inputs):
    def __init__(self):
        super().__init__();q=self.q;known=np.isfinite(q);value=np.nan_to_num(q)
        c=np.zeros((*q.shape,6),np.float32)
        c[:,:,0]=np.log1p(value);c[:,:,1]=known
        c[:,:,2]=(q==0);c[:,:,3]=(q<=3)
        for channel,lag in [(4,1),(5,24)]:
            valid=known[lag:]&known[:-lag]
            delta=value[lag:]-value[:-lag]
            c[lag:,:,channel]=np.where(valid,np.sign(delta)*np.log1p(np.abs(delta)),0)
        self.channels=c;self.neighbor=None
        stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
        self.station_ids=stations.station_id.to_list()
        tree=BallTree(np.radians(stations[['latitude','longitude']]),metric='haversine')
        near=tree.query(np.radians(stations[['latitude','longitude']]),k=6,return_distance=False)
        self.neighbors=np.array([row[row!=i][:5] for i,row in enumerate(near)])
        self.hours=self.times.hour.to_numpy();self.weekdays=self.times.weekday.to_numpy()
    def prepare_neighbors(self):
        if self.neighbor is not None:return
        self.neighbor=np.empty_like(self.channels)
        # Shared 5-nearest graph, mean aggregation; no dense N x N attention.
        for j in range(self.n):self.neighbor[:,j]=self.channels[:,self.neighbors[j]].mean(axis=1)
    def frame(self,index,columns):
        from .fast_frame import matrix_frame
        x=matrix_frame(self.matrix,self.columns,index,columns);x.attrs['flat_index']=np.asarray(index);return x
    def batch(self,index,length,graph,graph_identity=False):
        ti=index//self.n;sid=index%self.n;lag=np.arange(length-1,-1,-1)
        history=ti[:,None]-lag;valid=history>=0;history=np.maximum(history,0)
        def gather(values):
            x=values[history,sid[:,None],:]*valid[:,:,None]
            return torch.from_numpy(x.transpose(0,2,1).copy())
        tensors=[gather(self.channels),torch.from_numpy(sid.astype(np.int64)),torch.from_numpy(self.hours[ti]),torch.from_numpy(self.weekdays[ti])]
        if graph:tensors.append(gather(self.channels if graph_identity else self.neighbor))
        return tensors

class Predictor:
    def __init__(self,model,data,length,graph,graph_identity=False):
        self.model=model;self.data=data;self.length=length;self.graph=graph;self.graph_identity=graph_identity
    def predict_proba(self,x):
        indices=x.attrs['flat_index'];parts=[];self.model.eval()
        with torch.no_grad():
            for start in range(0,len(indices),2048):parts.append(torch.sigmoid(self.model(*self.data.batch(indices[start:start+2048],self.length,self.graph,self.graph_identity))).numpy())
        p=np.concatenate(parts);return np.column_stack([1-p,p])

def fit_neural(data,spec,tx,y,sx,sy,model_dir):
    torch.set_num_threads(4);torch.manual_seed(spec['seed']);rng=np.random.default_rng(spec['seed'])
    identity=spec.get('graph_identity',False)
    if spec['graph'] and not identity:data.prepare_neighbors()
    model=TemporalModel(data.n,spec['length'],spec['architecture'],spec['graph'])
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=1e-4);lossfn=nn.BCEWithLogitsLoss()
    indices=tx.attrs['flat_index'];stop=sx.attrs['flat_index']
    # Deterministic checkpoint sample only; final outside evaluation uses all rows.
    if len(stop)>100000:stop=np.sort(rng.choice(stop,100000,replace=False))
    stop_y=torch.from_numpy((data.target[stop]==0).astype(np.float32));best=float('inf');stale=0;history=[];best_state=None
    start=time.perf_counter()
    for epoch in range(spec['epochs']):
        model.train();total=0.;order=rng.permutation(len(indices))
        for batch in range(0,len(order),1024):
            selected=order[batch:batch+1024];ix=indices[selected];label=torch.from_numpy(y[selected].astype(np.float32))
            optimizer.zero_grad(set_to_none=True);loss=lossfn(model(*data.batch(ix,spec['length'],spec['graph'],identity)),label)
            loss.backward();nn.utils.clip_grad_norm_(model.parameters(),2.);optimizer.step();total+=loss.item()*len(ix)
        model.eval();stoploss=0.
        with torch.no_grad():
            for batch in range(0,len(stop),2048):
                pred=model(*data.batch(stop[batch:batch+2048],spec['length'],spec['graph'],identity))
                stoploss+=lossfn(pred,stop_y[batch:batch+2048]).item()*len(pred)
        stoploss/=len(stop);history.append(dict(epoch=epoch+1,train_logloss=total/len(indices),stop_logloss=stoploss))
        print('NEURAL_EPOCH',spec['name'],epoch+1,'stop',round(stoploss,6),'seconds',round(time.perf_counter()-start,1),flush=True)
        if stoploss<best-1e-5:best=stoploss;stale=0;best_state={k:v.detach().clone() for k,v in model.state_dict().items()}
        else:stale+=1
        if stale>=4:break
    model.load_state_dict(best_state);torch.save(dict(state=best_state,spec=spec,station_ids=data.station_ids,model_class='TemporalModel',code_sha256=digest(__file__)),model_dir/(spec['name']+'.pt'))
    return Predictor(model,data,spec['length'],spec['graph'],identity),dict(epochs_run=len(history),history=history,stop_sample_rows=len(stop),sampled_train_rows=len(indices),parameters=sum(x.numel() for x in model.parameters()),adaptation_not_paper_reproduction=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--folds',nargs='+',default=['A']);p.add_argument('--names',nargs='+');args=p.parse_args()
    data=SequenceInputs();original=runner.fit
    def fit(spec,tx,y,sx,sy,train_times,count,model_dir):
        if spec['kind']=='neural':return fit_neural(data,spec,tx,y,sx,sy,model_dir)
        return original(spec,tx,y,sx,sy,train_times,count,model_dir)
    runner.fit=fit
    variants=[('temporal_mlp24',24,'mlp',False),('temporal_mlp168',168,'mlp',False),('tcn24',24,'tcn',False),('graph_tcn24_identity',24,'tcn',True),('graph_tcn24',24,'tcn',True)]
    for fold in args.folds:
        for name,length,architecture,graph in variants:
            if args.names and name not in args.names:continue
            spec=dict(name=name,kind='neural',group='basic',window=56,seed=20261020,sample=300000,epochs=20,length=length,architecture=architecture,graph=graph,graph_identity=name.endswith('_identity'))
            runner.run_one(data,fold,spec)
            path=OUT/fold/(name+'.json');result=json.loads(path.read_text(encoding='utf-8'));result['extension_code_sha256']=digest(__file__)
            result['comparison_scope']='CPU pilot: fixed 300k training sample, full outside validation; no claim of equal budget to trees'
            result['sequence_features']=['log_current_quantity','observed_mask','zero_mask','low3_mask','change1','change24']
            result['graph_definition']=('identity self-only edges, same graph head and parameters' if spec['graph_identity'] else '5 nearest training-cohort station positions; shared temporal encoding of mean neighbor signals') if graph else None
            save(path,result);gc.collect()

if __name__=='__main__':main()
