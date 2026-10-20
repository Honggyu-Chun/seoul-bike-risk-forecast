"""Strict source parsing and causal, grid-aligned features."""
from pathlib import Path
import json
import hashlib
import zipfile
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.neighbors import BallTree

ROOT = Path(__file__).resolve().parents[1]
EARTH_M = 6_371_000.0


def load_protocol():
    return json.loads((ROOT/'config/experiment_v1.json').read_text())


def panel_contract():
    p=load_protocol()
    def digest(path):
        with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
    return {'input_period':p['input_period'],'training_end':p['train_target_end_exclusive'],
            'cohort_rule':p['station_cohort_train_only'],
            'quantity_zip_sha256':digest(ROOT/'data/raw/availability_2025q1.zip'),
            'location_sha256':digest(ROOT/'data/raw/stations_202412.xlsx')}


def station_locations():
    raw = pd.read_excel(ROOT/'data/raw/stations_202412.xlsx', header=None)
    df = raw.iloc[5:].copy()
    df.columns = ['station_id','name','district','address','latitude','longitude','installed','lcd','qr','type']
    df['station_id'] = pd.to_numeric(df.station_id, errors='raise').astype(int)
    for col in ['latitude','longitude']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['installed'] = pd.to_datetime(df.installed, errors='coerce')
    if df.station_id.duplicated().any():
        raise ValueError('Duplicate station metadata IDs')
    return df


def parse_quarter():
    parts, audit = [], []
    with zipfile.ZipFile(ROOT/'data/raw/availability_2025q1.zip') as archive:
        for month in ['01','02','03']:
            member = next(n for n in archive.namelist() if n.endswith(f'data_25{month}.csv'))
            with archive.open(member) as stream:
                df = pd.read_csv(stream, encoding='cp949', usecols=['일시','대여소번호','시간대','거치대수량'])
            df = df.rename(columns={'대여소번호':'station_id','거치대수량':'quantity'})
            hour = pd.to_numeric(df['시간대'], errors='raise')
            if not hour.between(0,23).all() or not hour.eq(np.floor(hour)).all():
                raise ValueError('Invalid source hour')
            df['time'] = pd.to_datetime(df['일시'], errors='raise') + pd.to_timedelta(hour, unit='h')
            df['station_id'] = pd.to_numeric(df.station_id, errors='raise').astype('int32')
            q = pd.to_numeric(df.quantity, errors='raise')
            if q.isna().any() or not q.ge(0).all() or not q.eq(np.floor(q)).all():
                raise ValueError('Quantity must be known nonnegative integers')
            df['quantity'] = q.astype('float32')
            if df.duplicated(['station_id','time']).any():
                raise ValueError('Duplicate station-hour; no automatic averaging')
            audit.append({'month':f'2025-{month}', 'rows':len(df), 'station_ids':int(df.station_id.nunique()), 'hours':int(df.time.nunique()), 'zero_rows':int(q.eq(0).sum()), 'min':float(q.min()), 'max':float(q.max())})
            parts.append(df[['station_id','time','quantity']])
            print(f'parsed {month}: {len(df):,} rows', flush=True)
    df = pd.concat(parts, ignore_index=True)
    if df.duplicated(['station_id','time']).any():
        raise ValueError('Duplicate across monthly files')
    protocol = load_protocol()
    times = pd.date_range(*protocol['input_period'], freq='h')
    ids = np.sort(df.station_id.unique())
    ti = times.get_indexer(df.time)
    si = pd.Index(ids).get_indexer(df.station_id)
    if (ti<0).any():
        raise ValueError('Timestamp outside fixed quarter')
    grid = np.full((len(times),len(ids)),np.nan,dtype=np.float32)
    grid[ti,si] = df.quantity.to_numpy()
    loc = station_locations().set_index('station_id').reindex(ids)
    training = times < pd.Timestamp(protocol['train_target_end_exclusive'])
    qt = grid[training]
    valid_loc = loc.latitude.between(37,38).to_numpy() & loc.longitude.between(126,128).to_numpy()
    finite = np.isfinite(qt)
    minima = np.where(finite,qt,np.inf).min(axis=0)
    maxima = np.where(finite,qt,-np.inf).max(axis=0)
    rule = protocol['station_cohort_train_only']
    cohort = (finite.sum(axis=0)>=rule['minimum_known_train_hours']) & ((qt>0).sum(axis=0)>=rule['minimum_nonzero_train_hours']) & (maxima>minima) & valid_loc
    selected = loc.iloc[np.flatnonzero(cohort)].reset_index()
    selected['station_code'] = np.arange(len(selected),dtype=np.int32)
    q = grid[:,cohort]
    metadata = {
        'monthly':audit, 'source_rows':len(df), 'source_station_ids':len(ids), 'grid_hours':len(times),
        'cohort_station_ids':int(cohort.sum()), 'excluded_station_ids':int((~cohort).sum()),
        'cohort_rule_uses_train_only':True, 'cohort_rule':rule,
        'panel_contract':panel_contract(),
        'missing_city_hours':int((~np.isfinite(grid).any(axis=1)).sum()),
        'quantity_quantiles_train':{str(v):float(np.nanquantile(qt,v)) for v in [.5,.9,.99,.999]},
        'exclusion_note':'Excluded if no historical coordinate, insufficient training coverage, or no observed training quantity variation/positive quantity. This is not an official operating-status filter.'
    }
    processed = ROOT/'data/processed'; processed.mkdir(exist_ok=True)
    np.savez_compressed(processed/'rider_q1_panel.npz',quantity=q,times=times.to_numpy(),station_ids=selected.station_id.to_numpy())
    selected.to_parquet(processed/'rider_stations.parquet',index=False)
    (ROOT/'artifacts/evaluation/data_quality.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    pd.DataFrame({'station_id':ids,'known_train_hours':finite.sum(axis=0),'positive_train_hours':(qt>0).sum(axis=0),
                  'train_min':np.where(np.isfinite(minima),minima,np.nan),'train_max':np.where(np.isfinite(maxima),maxima,np.nan),
                  'valid_coordinate':valid_loc,'included':cohort}).to_csv(ROOT/'artifacts/evaluation/station_cohort_audit.csv',index=False)
    return times, selected, q, metadata


def lag(a, hours):
    out=np.full_like(a,np.nan,dtype=np.float32)
    if hours and hours < len(a): out[hours:]=a[:-hours]
    return out


def rolling(a, window):
    known=np.isfinite(a)
    sums=np.vstack([np.zeros((1,a.shape[1])),np.cumsum(np.where(known,a,0),axis=0,dtype=np.float64)])
    counts=np.vstack([np.zeros((1,a.shape[1])),np.cumsum(known,axis=0,dtype=np.float64)])
    idx=np.arange(1,len(a)+1); start=np.maximum(0,idx-window)
    total=sums[idx]-sums[start]; n=counts[idx]-counts[start]
    mean=np.divide(total,n,out=np.full_like(total,np.nan),where=n>0)
    return mean.astype('float32'), n.astype('float32')


def feature_frame(times, stations, q, radius=500):
    """At input t, uses only observations at or before t. Target is q[t+1]."""
    n_t,n_s=q.shape
    feats={'station_code':np.broadcast_to(np.arange(n_s,dtype=np.int32),(n_t,n_s)).ravel(),
           'hour':np.repeat(times.hour.to_numpy(dtype=np.int32),n_s),
           'weekday':np.repeat(times.dayofweek.to_numpy(dtype=np.int32),n_s),
           'weekend':np.repeat((times.dayofweek>=5).astype(np.float32),n_s),
           'quantity':q.ravel(), 'log_quantity':np.log1p(q).ravel()}
    snapshot=list(feats)
    for hours in [1,2,24,168]: feats[f'q_lag_{hours}']=lag(q,hours).ravel()
    for window in [3,24]:
        mean,count=rolling(q,window)
        feats[f'q_mean_{window}']=mean.ravel();feats[f'q_known_{window}']=count.ravel()
    feats['q_change_1']=(q-lag(q,1)).ravel()
    feats['q_change_3']=(q-lag(q,3)).ravel()
    own=list(feats)
    coords=np.radians(stations[['latitude','longitude']].to_numpy(dtype=float))
    near=BallTree(coords,metric='haversine').query_radius(coords,r=radius/EARTH_M)
    near=[v[v!=i] for i,v in enumerate(near)]
    rows=np.repeat(np.arange(n_s),[len(v) for v in near]);cols=np.concatenate(near)
    graph=csr_matrix((np.ones(len(rows),dtype=np.float32),(rows,cols)),shape=(n_s,n_s))
    valid=np.isfinite(q);filled=np.nan_to_num(q,nan=0)
    observed=(graph@valid.astype('float32').T).T
    def avg(values):
        sums=(graph@np.where(valid,values,0).T).T
        return np.divide(sums,observed,out=np.full_like(sums,np.nan),where=observed>0)
    mean=avg(filled);squared=avg(filled**2)
    degree=np.array([len(v) for v in near],dtype=np.float32)
    feats['neighbor_mean']=mean.ravel()
    feats['neighbor_std']=np.sqrt(np.maximum(0,squared-mean**2)).ravel()
    feats['neighbor_zero_fraction']=avg(q==0).ravel()
    feats['neighbor_low2_fraction']=avg(q<=2).ravel()
    feats['neighbor_observed_count']=observed.ravel()
    feats['neighbor_degree']=np.broadcast_to(degree,q.shape).ravel()
    feats['neighbor_coverage']=np.divide(observed,degree,out=np.zeros_like(observed),where=degree>0).ravel()
    minimum=np.full_like(q,np.nan)
    safe=np.where(valid,q,np.inf)
    for i,neighbors in enumerate(near):
        if len(neighbors): minimum[:,i]=safe[:,neighbors].min(axis=1)
    minimum[~np.isfinite(minimum)]=np.nan
    feats['neighbor_min']=minimum.ravel()
    feats['neighbor_change_1']=(mean-lag(mean,1)).ravel()
    feats['neighbor_change_3']=(mean-lag(mean,3)).ravel()
    feats['own_minus_neighbor']=(q-mean).ravel()
    frame=pd.DataFrame(feats,copy=False)
    for col in frame:
        if frame[col].dtype.kind=='f':frame[col]=frame[col].astype('float32')
    masks={
        'snapshot':snapshot, 'own':own, 'spatial':list(frame),
        'categorical':['station_code','hour','weekday']
    }
    return frame,masks
