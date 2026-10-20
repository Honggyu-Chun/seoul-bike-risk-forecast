"""Causal transition, temporal and multiscale context features for research.

Every observation timestamp used for features at t is at or before t. Weather
uses earlier observation times; archive revisions and live publication delays
are explicitly unverified in weather_audit.json.
"""
from pathlib import Path
import hashlib,json,zipfile,io
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.neighbors import BallTree
from .data import ROOT,lag,rolling,feature_frame,EARTH_M

OUT=ROOT/'artifacts/improvement'


def past_weather(times,path=None):
    """Only earlier ASOS observation timestamps; archived-vintage caveat applies."""
    path=Path(path) if path is not None else ROOT/'data/raw/citywide_review/kma_seoul_2025.zip'
    body=path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(body)) as outer:
        members=outer.namelist()
        if len(members)!=1:raise ValueError('Expected one weather archive member')
        name=members[0];inner=outer.read(name)
    if name.endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(inner)) as archive:
            names=[s for s in archive.namelist() if s.endswith('.csv')]
            if len(names)!=1:raise ValueError('Expected a single ASOS CSV')
            inner=archive.read(names[0])
    data=pd.read_csv(io.BytesIO(inner),encoding='cp949')
    if not data['지점'].eq(108).all():raise ValueError('Weather must be Seoul station 108')
    data.index=pd.DatetimeIndex(pd.to_datetime(data['일시']))
    if data.index.has_duplicates:raise ValueError('Duplicate weather observation hour')
    fields={'temperature':'기온(°C)','rain':'강수량(mm)','wind':'풍속(m/s)','humidity':'습도(%)',
            'pressure':'현지기압(hPa)','cloud':'전운량(10분위)','visibility':'시정(10m)','snow':'적설(cm)'}
    result={}
    for label,column in fields.items():
        values=pd.to_numeric(data[column],errors='raise')
        result['w_'+label+'_lag1']=values.reindex(times-pd.Timedelta(hours=1)).to_numpy(dtype=np.float32)
        if label in ['temperature','rain']:
            result['w_'+label+'_lag24']=values.reindex(times-pd.Timedelta(hours=24)).to_numpy(dtype=np.float32)
    result['w_temperature_change23']=result['w_temperature_lag1']-result['w_temperature_lag24']
    return pd.DataFrame(result,index=times)


def prepare_panel():
    OUT.mkdir(parents=True,exist_ok=True)
    stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    with np.load(ROOT/'data/processed/rider_q1_panel.npz') as old:
        times=pd.date_range('2025-01-01','2025-05-14T23:00',freq='h')
        q=np.full((len(times),len(stations)),np.nan,dtype=np.float32)
        q[:len(old['times'])]=old['quantity']
    path=ROOT/'data/raw/improvement/availability_2025q2.zip'
    meta=[]
    with zipfile.ZipFile(path) as archive:
        for month in ['04','05']:
            name=next(n for n in archive.namelist() if n.endswith(f'data_25{month}.csv'))
            rows=0;seen=set();missing=0
            with archive.open(name) as stream:
                for df in pd.read_csv(stream,encoding='cp949',usecols=['일시','대여소번호','시간대','거치대수량'],chunksize=250000):
                    rows+=len(df)
                    df['time']=pd.to_datetime(df['일시'])+pd.to_timedelta(df['시간대'],unit='h')
                    df=df[df.time.le(times[-1])]
                    if df.empty:continue
                    ti=times.get_indexer(df.time);si=pd.Index(stations.station_id).get_indexer(df['대여소번호'])
                    values=pd.to_numeric(df['거치대수량'],errors='raise').to_numpy()
                    assert np.isfinite(values).all() and (values>=0).all() and np.equal(values,np.floor(values)).all()
                    valid=(ti>=0)&(si>=0)
                    keys=ti[valid].astype(np.int64)*len(stations)+si[valid]
                    assert len(np.unique(keys))==len(keys) and not any(int(k) in seen for k in keys),'duplicate station hour'
                    seen.update(map(int,keys));q[ti[valid],si[valid]]=values[valid]
            meta.append({'member':name,'source_rows_read':rows,'matched_window_rows':len(seen)})
            print('prepared source',month,rows,len(seen),flush=True)
    np.savez_compressed(OUT/'panel.npz',times=times.to_numpy(),quantity=q)
    with path.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    audit={'new_source_zip_sha256':digest,'cohort_stations':len(stations),'input_start':str(times[0]),'input_end':str(times[-1]),'monthly':meta,'missing_are_not_zero':True,'new_test_scores_computed':False}
    (OUT/'source_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    return times,stations,q


def load_panel(end=None):
    if not (OUT/'panel.npz').exists():times,stations,q=prepare_panel()
    else:
        with np.load(OUT/'panel.npz') as p:times=pd.DatetimeIndex(p['times']);q=p['quantity']
        stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    if end is not None:
        mask=times<pd.Timestamp(end);times=times[mask];q=q[mask]
    return times,stations,q


def runs(mask,known):
    result=np.zeros(mask.shape,dtype=np.float32)
    for i in range(len(mask)):
        result[i]=np.where(known[i]&mask[i],1+(result[i-1] if i else 0),0)
        result[i,~known[i]]=np.nan
    return result


def transition_history(times,q):
    """Past-transition encodings updated only once their outcome is observed.

    At t the transition t-1 -> t is known; t -> t+1 is never touched. The
    station/hour/state statistics therefore remain usable at prediction time.
    """
    n_t,n_s=q.shape;k=7;sid=np.arange(n_s)
    def bucket(values):return np.searchsorted([0,1,2,3,5,10],np.nan_to_num(values,nan=0),side='left')
    bins=bucket(q);known=np.isfinite(q);duration=runs(q==0,known)
    ages=np.searchsorted([1,3,6,24],np.nan_to_num(duration,nan=0),side='left')
    g_n=np.zeros(k);g_y=g_n.copy();s_n=np.zeros((n_s,k));s_y=s_n.copy()
    h_n=np.zeros((n_s,24,k));h_y=h_n.copy();d_n=np.zeros((n_s,7,k));d_y=d_n.copy()
    a_n=np.zeros((n_s,k,5));a_y=a_n.copy();r_n=s_n.copy();r_y=s_n.copy()
    names=['global','station','station_hour','station_weekday','station_duration','recent_station','station_known']
    result={name:np.full(q.shape,np.nan,dtype=np.float32) for name in names}
    decay=np.exp(-np.log(2)/168)
    for t in range(n_t):
        r_n*=decay;r_y*=decay
        if t:
            valid=known[t-1]&known[t];i=sid[valid];b=bins[t-1,valid];a=ages[t-1,valid];y=(q[t,valid]==0).astype(float)
            np.add.at(g_n,b,1);np.add.at(g_y,b,y)
            s_n[i,b]+=1;s_y[i,b]+=y;r_n[i,b]+=1;r_y[i,b]+=y
            hour=times[t-1].hour;day=times[t-1].dayofweek
            h_n[i,hour,b]+=1;h_y[i,hour,b]+=y;d_n[i,day,b]+=1;d_y[i,day,b]+=y
            a_n[i,b,a]+=1;a_y[i,b,a]+=y
        b=bins[t];a=ages[t];global_rate=(g_y[b]+1)/(g_n[b]+2)
        local=(s_y[sid,b]+20*global_rate)/(s_n[sid,b]+20)
        values=[global_rate,local,
                (h_y[sid,times[t].hour,b]+10*local)/(h_n[sid,times[t].hour,b]+10),
                (d_y[sid,times[t].dayofweek,b]+10*local)/(d_n[sid,times[t].dayofweek,b]+10),
                (a_y[sid,b,a]+10*local)/(a_n[sid,b,a]+10),
                (r_y[sid,b]+10*local)/(r_n[sid,b]+10),np.log1p(s_n[sid,b])]
        for name,value in zip(names,values):result[name][t]=np.where(known[t],value,np.nan)
    return result


def build_features(times,stations,q):
    frame,base=feature_frame(times,stations,q,500)
    n_t,n_s=q.shape;known=np.isfinite(q)
    def add(name,value):frame[name]=np.asarray(value,dtype=np.float32).reshape(-1)
    # Dense short-term shape plus previous days/weeks at the same hour.
    for hours in [3,4,5,6,8,12,23,25,47,48,49,72,96,120,144,167,169,192,336]:add(f'q_lag_{hours}',lag(q,hours))
    for window in [3,6,12,24,48,168]:
        mean,count=rolling(q,window);mean2,_=rolling(q*q,window)
        add(f't_std_{window}',np.sqrt(np.maximum(0,mean2-mean*mean)))
        if window not in [3,24]:add(f't_mean_{window}',mean)
        zero,_=rolling(np.where(known,q==0,np.nan).astype(np.float32),window)
        add(f't_zero_fraction_{window}',zero)
        if window in [6,24,168]:
            low,_=rolling(np.where(known,q<=2,np.nan).astype(np.float32),window);add(f't_low2_fraction_{window}',low)
    add('t_zero_run',runs(q==0,known));add('t_low3_run',runs(q<=3,known));add('t_positive_run',runs(q>0,known))
    add('t_delta2',q-lag(q,2));add('t_delta6',q-lag(q,6));add('t_acceleration',q-2*lag(q,1)+lag(q,2))
    add('t_relative_change',(q-lag(q,1))/(1+lag(q,1)))
    daily=np.stack([lag(q,24*d) for d in range(1,8)])
    counts=np.isfinite(daily).sum(axis=0)
    seasonal=np.divide(np.nansum(daily,axis=0),counts,out=np.full(q.shape,np.nan,dtype=np.float32),where=counts>0)
    add('t_samehour_mean7d',seasonal);add('t_samehour_deviation',q-seasonal)
    add('t_samehour_zero7d',np.divide(np.where(np.isfinite(daily),daily==0,0).sum(axis=0),counts,out=np.full(q.shape,np.nan),where=counts>0))
    del daily
    capacity=pd.to_numeric(stations.qr,errors='coerce').fillna(0)+pd.to_numeric(stations.lcd,errors='coerce').fillna(0)
    cap=np.maximum(1,capacity.to_numpy(dtype=np.float32))
    add('t_nominal_capacity',np.broadcast_to(cap,q.shape));add('t_capacity_ratio',q/cap)
    for name,v in [('hour_sin',np.sin(2*np.pi*times.hour/24)),('hour_cos',np.cos(2*np.pi*times.hour/24)),('week_sin',np.sin(2*np.pi*(times.dayofweek*24+times.hour)/168)),('week_cos',np.cos(2*np.pi*(times.dayofweek*24+times.hour)/168))]:add('t_'+name,np.repeat(v,n_s))
    add('t_latitude',np.broadcast_to(stations.latitude.to_numpy(),q.shape));add('t_longitude',np.broadcast_to(stations.longitude.to_numpy(),q.shape))
    districts=pd.Categorical(stations.district).codes;add('t_district',np.broadcast_to(districts,q.shape))
    own_extended=[c for c in frame if c in base['own'] or c.startswith('t_') or c.startswith('q_lag_')]
    coords=np.radians(stations[['latitude','longitude']].to_numpy(dtype=float));tree=BallTree(coords,metric='haversine')
    for label,neighbors in [('250m',tree.query_radius(coords,r=250/EARTH_M)),('1000m',tree.query_radius(coords,r=1000/EARTH_M)),('knn5',tree.query(coords,k=min(6,n_s),return_distance=False))]:
        neighbors=[v[v!=i] for i,v in enumerate(neighbors)]
        rows=np.repeat(np.arange(n_s),[len(v) for v in neighbors]);cols=np.concatenate(neighbors)
        graph=csr_matrix((np.ones(len(rows),dtype=np.float32),(rows,cols)),shape=(n_s,n_s))
        den=(graph@known.astype(np.float32).T).T
        for field,value in [('mean',q),('zero',np.where(known,q==0,np.nan)),('low2',np.where(known,q<=2,np.nan)),('change',q-lag(q,1))]:
            valid=np.isfinite(value);d=(graph@valid.astype(np.float32).T).T
            total=(graph@np.where(valid,value,0).T).T
            add(f'g_{label}_{field}',np.divide(total,d,out=np.full(q.shape,np.nan,dtype=np.float32),where=d>0))
    for label,ids in [('city',np.zeros(n_s,dtype=int)),('district',districts)]:
        values=np.full(q.shape,np.nan,dtype=np.float32)
        for group in np.unique(ids):
            selected=ids==group;z=known[:,selected];den=z.sum(axis=1)
            mean=np.divide(((q[:,selected]==0)&z).sum(axis=1),den,out=np.full(len(q),np.nan),where=den>0)
            values[:,selected]=mean[:,None]
        add(f'g_{label}_zero',values)
    # Keep the original feature groups stable for saved-model compatibility.
    full=list(frame)
    frame=frame.copy()
    for name,values in transition_history(times,q).items():add('h_'+name,values)
    groups={'basic':base['own'],'spatial_v1':base['spatial'],'temporal':own_extended,'full':full,
            'hazard':own_extended+[c for c in frame if c.startswith('h_')],
            'hazard_spatial':list(frame),'categorical':base['categorical']}
    weather_path=ROOT/'data/raw/citywide_review/kma_seoul_2025.zip'
    if weather_path.exists():
        weather=past_weather(times,weather_path)
        for name in weather:add(name,np.repeat(weather[name].to_numpy(),n_s))
        groups['weather_own']=groups['hazard']+list(weather)
        groups['weather_spatial']=groups['hazard_spatial']+list(weather)
    frame=frame.copy() # consolidate fragmented column additions before model copies
    return frame,groups
