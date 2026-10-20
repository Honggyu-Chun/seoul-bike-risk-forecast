"""Calendar, prior-year and archived event ablations, isolated from frozen v3."""
import json
import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree
from .common import ROOT,OUT,RECORD,save,digest
from .runner import Inputs
from .fast_frame import matrix_frame

EARTH_M=6371000.
HOLIDAYS=['2025-01-01','2025-01-28','2025-01-29','2025-01-30','2025-03-01','2025-03-03','2025-05-05','2025-05-06','2025-06-06']
TEMP_HOLIDAYS={'2025-01-27':'2025-01-14','2025-06-03':'2025-04-08'}

def holiday_at(dates,issued_at):
    dates=pd.DatetimeIndex(dates).normalize();issued_at=pd.DatetimeIndex(issued_at).normalize()
    flag=np.isin(dates,np.array(HOLIDAYS,dtype='datetime64[ns]'))
    for day,publication in TEMP_HOLIDAYS.items():flag|=(dates==pd.Timestamp(day))&(issued_at>=pd.Timestamp(publication))
    return flag.astype(np.float32)

def prepare_context():
    path=OUT/'data/context_2025.npy';meta=OUT/'data/context_2025.json'
    if path.exists() and meta.exists():return
    with np.load(OUT/'data/panel_2025.npz') as p:q=p['quantity'];times=pd.DatetimeIndex(p['times'])
    with np.load(OUT/'data/panel_2024.npz') as p:prior=p['quantity'];pt=pd.DatetimeIndex(p['times'])
    stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet');n=len(stations)
    features={}
    for name,delta in [('holiday_today',0),('holiday_tomorrow',1),('holiday_yesterday',-1)]:
        values=holiday_at(times+pd.Timedelta(days=delta),times)
        features['c_'+name]=np.broadcast_to(values[:,None],q.shape)
    ix=pt.get_indexer(times-pd.Timedelta(days=364));good=ix>=0
    py=np.full(q.shape,np.nan,np.float32);pyn=py.copy()
    py[good]=prior[ix[good]]
    good_next=(ix>=0)&(ix+1<len(prior));pyn[good_next]=prior[ix[good_next]+1]
    features.update(c_prior364_q=py,c_prior364_next_q=pyn,c_prior364_change=pyn-py,
                    c_prior364_known=np.isfinite(py).astype(np.float32))
    tree=BallTree(np.radians(stations[['latitude','longitude']].to_numpy()),metric='haversine')
    schedule=pd.read_csv(ROOT/'data/raw/research_20261020/kbo_2025_original_schedule.csv')
    # A pre-existing station point is an explicit stadium-location proxy.
    for radius in (500,1000):
        daily=np.zeros(q.shape,dtype=np.float32)
        for venue,station_id in [('잠실',1205),('고척',1928)]:
            point=stations.loc[stations.station_id==station_id,['latitude','longitude']].to_numpy()
            near=tree.query_radius(np.radians(point),r=radius/EARTH_M)[0]
            dates=pd.to_datetime(schedule.loc[schedule.venue==venue,'date']).dt.normalize().unique()
            active=np.isin(times.normalize(),dates)
            daily[np.ix_(active,near)]+=1
        features[f'c_kbo_day_{radius}m']=daily
    source=ROOT/'data/raw/followup_v4_external/culture_all.json'
    events=pd.DataFrame(json.loads(source.read_text(encoding='utf-8')))
    for c in ('STRTDATE','END_DATE','RGSTDATE'):events[c]=pd.to_datetime(events[c],errors='coerce')
    events['LAT']=pd.to_numeric(events.LAT,errors='coerce');events['LOT']=pd.to_numeric(events.LOT,errors='coerce')
    types=['콘서트','클래식','독주/독창회','연극','뮤지컬/오페라','국악','무용']
    eligible=(events.CODENAME.isin(types)|events.CODENAME.str.startswith('축제-',na=False))
    eligible&=(events.STRTDATE<'2025-07-01')&(events.END_DATE>='2025-01-01')
    eligible&=events.LAT.between(37.35,37.8)&events.LOT.between(126.7,127.3)&events.RGSTDATE.notna()
    selected=events.loc[eligible]
    daily_dates=pd.date_range(times[0].normalize(),times[-1].normalize(),freq='D')
    for radius in (500,1000):
        daily=np.zeros((len(daily_dates),n),np.float32)
        for _,event in selected.iterrows():
            near=tree.query_radius(np.radians([[event.LAT,event.LOT]]),r=radius/EARTH_M)[0]
            active=(daily_dates>=max(event.STRTDATE.normalize(),event.RGSTDATE.normalize()))&(daily_dates<=event.END_DATE.normalize())
            daily[np.ix_(active,near)]+=1
        # Day-level planned-interval exposure, NOT actual shows or attendance.
        features[f'c_culture_interval_{radius}m']=daily[daily_dates.get_indexer(times.normalize())]
    names=list(features)
    matrix=np.lib.format.open_memmap(path,mode='w+',dtype=np.float32,shape=(q.size,len(names)))
    for j,name in enumerate(names):matrix[:,j]=features[name].ravel()
    matrix.flush()
    groups={'calendar':names[:3],'prior_year':names[3:7],'kbo':names[7:9],'culture':names[9:]}
    save(meta,dict(columns=names,groups=groups,shape=list(matrix.shape),prior_year_observed_fraction=float(np.isfinite(py).mean()),
        culture_eligible_rows=len(selected),culture_registered_too_late_excluded=True,culture_historical_revision_verified=False,
        culture_definition='registered planned date-interval proximity; no inferred show count or attendance',
        kbo_definition='2024-12-20 original schedule; game-day only; station 1205/1928 position proxies',
        culture_sha256=digest(source),calendar_temporary_holiday_publication_dates=TEMP_HOLIDAYS,
        code_sha256=digest(__file__),panel2024_sha256=digest(OUT/'data/panel_2024.npz')))

class ContextInputs(Inputs):
    def __init__(self):
        super().__init__();prepare_context()
        self.context_meta=json.loads((OUT/'data/context_2025.json').read_text(encoding='utf-8'))
        self.context=np.load(OUT/'data/context_2025.npy',mmap_mode='r')
        self.context_columns=self.context_meta['columns']
        for key,cols in self.context_meta['groups'].items():
            self.meta['groups']['context_'+key]=self.meta['groups']['hazard_spatial']+cols
        self.meta['groups']['context_all']=self.meta['groups']['hazard_spatial']+self.context_columns
    def frame(self,index,columns):
        base=[c for c in columns if c not in self.context_columns]
        frame=matrix_frame(self.matrix,self.columns,index,base)
        for c in columns:
            if c in self.context_columns:frame[c]=self.context[index,self.context_columns.index(c)]
        return frame if list(frame.columns)==list(columns) else frame[columns]

if __name__=='__main__':prepare_context()
