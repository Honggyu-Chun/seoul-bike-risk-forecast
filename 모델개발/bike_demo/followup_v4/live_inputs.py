"""One-shot live station capture; never implies a future target is observed."""
import json
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from .external_data import Client,RAW
from .common import ROOT,RECORD,save,now

def capture_bikes():
    client=Client(budget=12);key=client.values.get('SEOUL_API_KEY')
    if not key:raise RuntimeError('SEOUL_API_KEY is missing')
    rows=[];page_results=[];start_utc=now()
    for start in range(1,10001,1000):
        end=start+999;label=f'live_bikes_{start:05d}'
        body=client.request(f'http://openapi.seoul.go.kr:8088/{urllib.parse.quote(key,safe="")}/json/bikeList/{start}/{end}/',label)
        (RAW/(label+'.json')).write_bytes(body)
        reply=json.loads(body);service=reply.get('rentBikeStatus')
        if not service:raise RuntimeError('Live bike service unavailable; raw sanitized response saved')
        page=service.get('row',[]);rows.extend(page);page_results.append(dict(start=start,rows=len(page),received_at_utc=now()))
        if len(page)<1000:break
    frame=pd.DataFrame(rows);frame['station_id']=pd.to_numeric(frame.stationName.str.extract(r'^\s*(\d+)\.')[0],errors='coerce').astype('Int64')
    frame['quantity']=pd.to_numeric(frame.parkingBikeTotCnt,errors='coerce')
    frame['received_at_utc']=now();frame['received_at_kst']=datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
    frame.to_csv(RAW/'live_bikes_latest.csv',index=False,encoding='utf-8-sig')
    cohort=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    match=cohort.station_id.isin(frame.station_id.dropna())
    audit=dict(status='ok',service='bikeList / rentBikeStatus',request_start_utc=start_utc,pages=page_results,rows=len(frame),fields=list(frame.columns),
        numeric_quantity_rows=int(frame.quantity.notna().sum()),matched_historical_cohort=int(match.sum()),historical_cohort_rows=len(cohort),
        historical_hourly_grid_equivalence_verified=False,provider_observation_timestamp_present=False,
        timestamp_policy='received_at is acquisition time only, not a fabricated provider measurement time',
        execution_scope='one-shot capture, no recurring automation or service deployment',
        operational_requirements=['retain timestamped snapshots at hourly decision grid','validate source measurement age','allow fallback when previous hourly features are unavailable','measure prediction latency separately'])
    save(RECORD/'live_bikes_audit.json',audit);print('LIVE_BIKES',len(frame),'matched',int(match.sum()),flush=True)

if __name__=='__main__':capture_bikes()
