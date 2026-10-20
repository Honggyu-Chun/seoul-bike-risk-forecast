"""Small CPU temporal neural classifiers; adaptations, not paper replications."""
import argparse,json,time,gc
from pathlib import Path
import numpy as np
import pandas as pd
import torch
from torch import nn
from scipy.sparse import csr_matrix
from sklearn.neighbors import BallTree
from .data import ROOT,EARTH_M
from .research_features import OUT,load_panel
from .research import dump,tune,rule_metrics

SEED=20261020
torch.set_num_threads(1)


class TemporalClassifier(nn.Module):
    def __init__(self,architecture,stations=2729):
        super().__init__();self.architecture=architecture
        self.station=nn.Embedding(stations,8);self.hour=nn.Embedding(24,4);self.weekday=nn.Embedding(7,3)
        if architecture=='mlp':self.temporal=nn.Sequential(nn.Flatten(),nn.Linear(96,64),nn.ReLU(),nn.Linear(64,32),nn.ReLU())
        elif architecture=='tcn':self.temporal=nn.Sequential(nn.Conv1d(4,24,3),nn.ReLU(),nn.Conv1d(24,24,3,dilation=2),nn.ReLU(),nn.Conv1d(24,32,3,dilation=4),nn.ReLU(),nn.AdaptiveAvgPool1d(1),nn.Flatten())
        elif architecture=='gru':self.temporal=nn.GRU(4,32,batch_first=True)
        else:raise ValueError(architecture)
        self.head=nn.Sequential(nn.Linear(32+8+4+3+1,32),nn.ReLU(),nn.Dropout(.1),nn.Linear(32,1))

    def forward(self,window,station,hour,weekday):
        if self.architecture=='gru':_,hidden=self.temporal(window);embedding=hidden[-1]
        elif self.architecture=='tcn':embedding=self.temporal(window.transpose(1,2))
        else:embedding=self.temporal(window)
        features=torch.cat([embedding,self.station(station),self.hour(hour),self.weekday(weekday),window[:,-1,:1]],dim=1)
        return self.head(features).squeeze(1)


def data(phase,end_override=None):
    protocol=json.loads((ROOT/'config/improvement_v3.json').read_text());split=protocol[phase]
    times,stations,q=load_panel(end=end_override or split['valid_target_end_exclusive']);n=len(stations)
    coords=np.radians(stations[['latitude','longitude']].to_numpy());near=BallTree(coords,metric='haversine').query_radius(coords,r=500/EARTH_M)
    near=[v[v!=i] for i,v in enumerate(near)];rows=np.repeat(np.arange(n),[len(v) for v in near]);cols=np.concatenate(near)
    graph=csr_matrix((np.ones(len(rows),dtype=np.float32),(rows,cols)),shape=(n,n))
    known=np.isfinite(q);den=(graph@known.astype(np.float32).T).T
    mean=np.divide((graph@np.nan_to_num(q,nan=0).T).T,den,out=np.zeros_like(q),where=den>0)
    zero=np.divide((graph@(q==0).astype(np.float32).T).T,den,out=np.zeros_like(q),where=den>0)
    channels=np.stack([np.log1p(np.nan_to_num(q,nan=0))/5,known.astype(np.float32),np.log1p(mean)/5,zero],axis=-1)
    target=np.vstack([q[1:],np.full((1,n),np.nan,dtype=np.float32)]).ravel();tt=np.repeat((times.as_unit('ns')+pd.Timedelta(hours=1)).to_numpy(),n)
    observed=np.isfinite(q.ravel())&np.isfinite(target)
    train=np.flatnonzero(observed&(tt<np.datetime64(split['train_target_end_exclusive'])))
    valid=np.flatnonzero(observed&(tt>=np.datetime64(split['valid_target_start']))&(tt<np.datetime64(split['valid_target_end_exclusive'])))
    return {'times':times,'q':q,'channels':channels,'target':target,'tt':tt,'train':train,'valid':valid,'n':n}


def batch(d,indices):
    ti=indices//d['n'];station=indices%d['n'];window=ti[:,None]-np.arange(23,-1,-1)
    x=d['channels'][np.maximum(window,0),station[:,None]].copy();x[window<0]=0
    return (torch.from_numpy(x),torch.as_tensor(station,dtype=torch.long),torch.as_tensor(d['times'].hour.to_numpy()[ti],dtype=torch.long),torch.as_tensor(d['times'].dayofweek.to_numpy()[ti],dtype=torch.long))


def predict(model,d,indices):
    model.eval();out=np.empty(len(indices),dtype=np.float32)
    with torch.inference_mode():
        for start in range(0,len(indices),8192):
            part=indices[start:start+8192];out[start:start+len(part)]=torch.sigmoid(model(*batch(d,part))).numpy()
    return out


def run(phase,names=None):
    folder=OUT/phase;(folder/'models').mkdir(parents=True,exist_ok=True)
    specs=[{'name':'neural_mlp','architecture':'mlp','positive_weight':1.},{'name':'neural_tcn','architecture':'tcn','positive_weight':1.},{'name':'neural_gru','architecture':'gru','positive_weight':1.},{'name':'neural_tcn_weighted','architecture':'tcn','positive_weight':4.}]
    if names:specs=[s for s in specs if s['name'] in names]
    if phase=='forward' and not names:
        screen=[json.loads(p.read_text()) for p in (OUT/'screen').glob('neural_*.json')]
        best=max(screen,key=lambda r:r['rule']['f1'])['name'];specs=[s for s in specs if s['name']==best]
    specs=[s for s in specs if not (folder/(s['name']+'.json')).exists()]
    if not specs:return
    d=data(phase);rng=np.random.default_rng(SEED);train=rng.choice(d['train'],min(1200000,len(d['train'])),replace=False)
    valid=d['valid'];vy=(d['target'][valid]==0).astype(np.uint8);vq=d['q'].ravel()[valid]
    for spec in specs:
        start=time.perf_counter();torch.manual_seed(SEED);np.random.seed(SEED);model=TemporalClassifier(spec['architecture'],d['n'])
        optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=1e-4)
        criterion=nn.BCEWithLogitsLoss(pos_weight=torch.tensor(spec['positive_weight']))
        best=-1.;stale=0;curves=[];best_pred=None;best_rule=None;best_epoch=0
        name=spec['name'];path=folder/'models'/(name+'.pt')
        for epoch in range(8):
            model.train();order=rng.permutation(train);losses=[]
            for i in range(0,len(order),4096):
                part=order[i:i+4096];label=torch.from_numpy((d['target'][part]==0).astype(np.float32))
                optimizer.zero_grad(set_to_none=True);loss=criterion(model(*batch(d,part)),label);loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5);optimizer.step();losses.append(float(loss.detach()))
            p=predict(model,d,valid);rule=tune(vy,p,vq);score=rule['f1'];curves.append({'epoch':epoch+1,'train_loss':float(np.mean(losses)),'validation_f1':score})
            print(name,'epoch',epoch+1,'validation F1',score,'elapsed',round(time.perf_counter()-start,1),flush=True)
            if score>best:
                best=score;stale=0;best_pred=p.copy();best_rule=rule;best_epoch=epoch+1
                torch.save({'state_dict':model.state_dict(),'architecture':spec['architecture'],'stations':d['n']},path)
            else:stale+=1
            if stale>=3:break
        record={'name':name,'kind':'neural','spec':{'kind':'neural','group':'sequence',**spec},'status':'ok','rule':best_rule,'validation':rule_metrics(vy,best_pred,vq,best_rule),'fit':{'paths':[path.name],'epochs':best_epoch,'sampled_train_rows':len(train),'threads':1,'sequence_hours':24,'channels':['log_quantity','observed_flag','neighbor_log_mean','neighbor_zero_fraction']},'features':[],'learning_curves':curves,'seconds':time.perf_counter()-start,'test_used':False}
        np.save(folder/(name+'_valid.npy'),best_pred)
        temp=folder/(name+'.json.tmp');dump(temp,record);temp.replace(folder/(name+'.json'))
        del model,optimizer,best_pred;gc.collect()


def predict_saved(record,indices,phase='forward'):
    # Test inference uses the same causal input builder, extended only to its dates.
    protocol=json.loads((ROOT/'config/improvement_v3.json').read_text())
    # Avoid modifying the frozen protocol on disk: load input panel directly,
    # construct channels using the helper with an explicit end override.
    d=data_with_end(phase,protocol[phase]['test_target_end_exclusive'])
    checkpoint=torch.load(OUT/phase/'models'/record['fit']['paths'][0],map_location='cpu',weights_only=True)
    model=TemporalClassifier(checkpoint['architecture'],checkpoint['stations']);model.load_state_dict(checkpoint['state_dict'])
    return predict(model,d,np.asarray(indices,dtype=np.int64))


def data_with_end(phase,end):
    return data(phase,end_override=end)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['screen','forward'],required=True);parser.add_argument('--names',nargs='*');args=parser.parse_args();run(args.phase,args.names)
