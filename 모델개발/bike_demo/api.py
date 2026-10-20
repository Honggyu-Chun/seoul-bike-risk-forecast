"""FastAPI demo: archived proposal-model replay, measured results, live baseline preview."""
from functools import lru_cache
from pathlib import Path
from datetime import timedelta
import json
import numpy as np
import pandas as pd
import lightgbm as lgb
from fastapi import FastAPI,HTTPException,Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .data import ROOT
from .recommend import distances_m,choose_station
from .live import fetch_live

STATIC=Path(__file__).parent/'static'
app=FastAPI(title='따릉이 어디로? 시민용 연구 데모',version='2.0.0')
app.mount('/static',StaticFiles(directory=STATIC),name='static')


def read_json(path):
    return json.loads(path.read_text()) if path.exists() else None


@lru_cache(maxsize=1)
def store():
    if not (ROOT/'artifacts/evaluation/model_results.json').exists():
        raise HTTPException(503,'실험 산출물이 없습니다. 먼저 학습 명령을 실행해 주세요.')
    stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    data=pd.read_parquet(ROOT/'artifacts/evaluation/test_predictions.parquet')
    return {'stations':stations,'data':data,'groups':data.groupby('input_time').indices,
            'metadata':read_json(ROOT/'artifacts/models/metadata.json')}


@lru_cache(maxsize=1)
def snapshot_model():
    # LightGBM's native file loader cannot open some non-ASCII Windows paths.
    # Reading the unchanged model in Python keeps the same trees and scores.
    text=(ROOT/'artifacts/models/B1S.txt').read_text(encoding='utf-8')
    return lgb.Booster(params={'num_threads':2},model_str=text)


@app.get('/')
def index():return FileResponse(STATIC/'index.html')


@app.get('/offline')
def offline():
    path=ROOT/'deliverables/따릉이_시민용_오프라인데모.html'
    if not path.exists():raise HTTPException(404,'오프라인 파일을 먼저 생성해 주세요.')
    return FileResponse(path,media_type='text/html')


@app.get('/api/health')
def health():
    required=['artifacts/evaluation/model_results.json','artifacts/evaluation/test_predictions.parquet',
              'artifacts/models/metadata.json','artifacts/models/B1S.txt','data/processed/rider_stations.parquet',
              'data/processed/rider_q1_panel.npz']
    ready=all((ROOT/path).is_file() for path in required)
    return {'status':'ok' if ready else 'incomplete','models_ready':ready,'name':'Seoul Bike Rider Research Demo','version':'2.0.0'}


@app.get('/api/overview')
def overview():
    db=store();times=sorted(pd.Timestamp(t).isoformat() for t in db['groups'])
    return {'title':'따릉이, 어디로?','mode_labels':{'replay':'2025년 3월 과거 검증','live':'현재 수량·기본모델 참고 실행'},
            'times':times,'stations':len(db['stations']),'data_quality':read_json(ROOT/'artifacts/evaluation/data_quality.json'),
            'splits':read_json(ROOT/'artifacts/evaluation/split_manifest.json'),
            'model_results':read_json(ROOT/'artifacts/evaluation/model_results.json'),
            'recommendation_results':read_json(ROOT/'artifacts/evaluation/recommendation_results.json'),
            'subgroups':read_json(ROOT/'artifacts/evaluation/subgroup_metrics.json'),
            'feature_importance':read_json(ROOT/'artifacts/evaluation/feature_importance.json'),
            'thresholds':{k:v['threshold'] for k,v in db['metadata']['models'].items()},
            'limitations':['관측 수량의 다음 기록 0대 여부를 예측한 과거 시험입니다.',
                           '자료 변동 확인 코호트이며 공식 운영 상태가 확인된 서울 전체의 효과는 아닙니다.',
                           '실시간 샘플의 기본모델 B1S는 본 비교의 B1·M1과 별도입니다.',
                           '실제 시민의 대여 성공·헛걸음 감소를 검증한 결과는 아닙니다.']}


@app.get('/api/stations')
def stations(q:str=Query('',max_length=80)):
    frame=store()['stations']
    if q:frame=frame[frame['name'].astype(str).str.contains(q,regex=False)|frame.station_id.astype(str).str.contains(q,regex=False)]
    return frame[['station_id','name','district','latitude','longitude']].head(3000).to_dict('records')


@app.get('/api/cases')
def cases():return read_json(ROOT/'artifacts/evaluation/recommendation_cases.json') or {'helped':[],'hurt':[]}


@app.get('/api/research')
def research():
    final=ROOT/'artifacts/improvement/final/results.json'
    if final.exists():
        return {'status':'evaluated','scope':'new_2025_april_may_archived_test',
                'results':read_json(final),'report_ready':(ROOT/'deliverables/따릉이_성능개선_실제실험결과_쉬운보고서.docx').exists()}
    completed=[]
    for path in (ROOT/'artifacts/improvement/screen').glob('*.json'):
        try:record=read_json(path)
        except (json.JSONDecodeError,OSError):continue
        if isinstance(record,dict) and record.get('status')=='ok':completed.append(record)
    best=max((r.get('selection_rule',r.get('rule',{})).get('f1',0) for r in completed),default=None)
    return {'status':'search_in_progress' if completed else 'not_started','completed_screen_models':len(completed),
            'best_development_validation_f1':best,'scope':'March1-14 development validation; not the new final test'}


@app.get('/api/research/report')
def research_report():
    path=ROOT/'deliverables/따릉이_성능개선_실제실험결과_쉬운보고서.docx'
    if not path.exists():raise HTTPException(404,'최종 시험 결과 보고서를 아직 생성하지 않았습니다.')
    return FileResponse(path,filename=path.name)


def replay_timestamp(value):
    try:timestamp=pd.Timestamp(value).as_unit('ns')
    except (ValueError,TypeError):raise HTTPException(422,'날짜·시간 형식을 확인해 주세요.')
    if pd.isna(timestamp) or timestamp.tz is not None:raise HTTPException(422,'서울 시각의 시간대 없는 값을 사용해 주세요.')
    if timestamp not in store()['groups']:raise HTTPException(404,'해당 시각은 고정 시험 범위에 없습니다.')
    return timestamp


@lru_cache(maxsize=1)
def quantity_history():
    with np.load(ROOT/'data/processed/rider_q1_panel.npz') as panel:
        return {'times':pd.DatetimeIndex(panel['times']), 'ids':panel['station_ids'], 'quantity':panel['quantity']}


@app.get('/api/city')
def city(at:str):
    db=store();timestamp=replay_timestamp(at)
    part=db['data'].iloc[db['groups'][timestamp]]
    rows=part[['station_id','quantity','p_B1','p_M1']]
    return {'input_time':timestamp.isoformat(),'scope':'historical_test_records_only',
            'rows':[[int(r.station_id),int(r.quantity),float(r.p_B1),float(r.p_M1)] for r in rows.itertuples()],
            'note':'해당 시각에 시험 정답이 연결된 대여소만 표시합니다. 현재 서울 전체 운영 현황이 아닙니다.'}


@app.get('/api/station/{station_id}')
def station_detail(station_id:int,at:str):
    db=store();timestamp=replay_timestamp(at);meta=db['stations'].set_index('station_id')
    if station_id not in meta.index:raise HTTPException(404,'학습 자료에 없는 대여소입니다.')
    st=meta.loc[station_id];panel=quantity_history()
    col=np.flatnonzero(panel['ids']==station_id)
    if not len(col):raise HTTPException(404,'수량 이력이 없는 대여소입니다.')
    at_index=panel['times'].get_indexer([timestamp])[0]
    if at_index<0:raise HTTPException(404,'해당 시각의 원본 기록이 없습니다.')
    values=panel['quantity'];col=int(col[0]);start=max(0,at_index-23)
    known=lambda v:None if not np.isfinite(v) else int(v)
    history=[{'time':panel['times'][i].isoformat(),'quantity':known(values[i,col])} for i in range(start,at_index+1)]
    distances=distances_m(st.latitude,st.longitude,db['stations'].latitude,db['stations'].longitude)
    # Panel and station metadata share station order; never use future labels in context.
    assert np.array_equal(panel['ids'],db['stations'].station_id.to_numpy())
    neighborhood=np.flatnonzero((distances<=500)&(db['stations'].station_id.to_numpy()!=station_id))
    nearby=values[at_index,neighborhood];observed=nearby[np.isfinite(nearby)]
    closest=sorted(neighborhood,key=lambda i:(distances[i],int(panel['ids'][i])))[:5]
    part=db['data'].iloc[db['groups'][timestamp]];prediction=part[part.station_id.eq(station_id)]
    scores=None if prediction.empty else {'B1':round(float(prediction.iloc[0].p_B1),4),'M1':round(float(prediction.iloc[0].p_M1),4)}
    previous=known(values[at_index-1,col]) if at_index else None;current=known(values[at_index,col])
    return {'station_id':station_id,'name':str(st['name']).strip(),'district':str(st.district),'address':str(st.address),
            'latitude':float(st.latitude),'longitude':float(st.longitude),'input_time':timestamp.isoformat(),
            'quantity':current,'previous_quantity':previous,'change_1h':None if previous is None or current is None else current-previous,
            'history':history,'scores':scores,
            'neighbors':{'radius_m':500,'total':len(neighborhood),'observed':len(observed),'empty':int((observed==0).sum()),
                         'mean_quantity':None if not len(observed) else round(float(observed.mean()),2),
                         'stations':[{'station_id':int(panel['ids'][i]),'name':str(db['stations'].iloc[i]['name']).strip(),
                                      'quantity':known(values[at_index,i]),'distance_m':round(float(distances[i]))} for i in closest]},
            'history_scope':'observations_at_or_before_input_time',
            'context_note':'현재·과거 관측을 보여 주는 참고 정보입니다. 개별 예측의 인과적 원인이나 기여도를 분석한 결과는 아닙니다.'}


@app.get('/api/snapshot')
def snapshot(at:str,lat:float=Query(37.5665,ge=37,le=38),lon:float=Query(126.978,ge=126,le=128),radius:int=Query(500,ge=100,le=1500)):
    db=store();timestamp=replay_timestamp(at)
    part=db['data'].iloc[db['groups'][timestamp]].copy()
    part=part.merge(db['stations'][['station_id','name','district','latitude','longitude']],on='station_id',validate='many_to_one')
    part['distance_m']=distances_m(lat,lon,part.latitude,part.longitude)
    part=part[part.distance_m.le(radius)].sort_values(['distance_m','station_id']).reset_index(drop=True)
    thresholds={name:record['threshold'] for name,record in db['metadata']['models'].items()}
    recommendations={}
    for method,model in [('R0',None),('R1','B1'),('R2','M1')]:
        idx=choose_station(part.station_id.to_numpy(),part.quantity.to_numpy(),part.distance_m.to_numpy(),None if model is None else part[f'p_{model}'].to_numpy(),None if model is None else thresholds[model])
        if idx is None:recommendations[method]=None
        else:
            row=part.iloc[idx]
            recommendations[method]={'station_id':int(row.station_id),'name':row['name'],'distance_m':round(float(row.distance_m)),
                                     'quantity':int(row.quantity),'actual_next_quantity':int(row.target_quantity),'actual_available':bool(row.target_quantity>=1)}
    records=[]
    for _,row in part.iterrows():
        records.append({'station_id':int(row.station_id),'name':row['name'],'district':row.district,'latitude':float(row.latitude),'longitude':float(row.longitude),
            'quantity':int(row.quantity),'distance_m':round(float(row.distance_m)), 'actual_next_quantity':int(row.target_quantity),
            'risk_B0':bool(row.p_B0>=.5),'risk_B1':bool(row.p_B1>=thresholds['B1']),'risk_M1':bool(row.p_M1>=thresholds['M1']),
            'score_B1':round(float(row.p_B1),4),'score_M1':round(float(row.p_M1),4)})
    return {'mode':'replay','input_time':timestamp.isoformat(),'target_time':(timestamp.to_pydatetime()+timedelta(hours=1)).isoformat(),
            'origin':{'latitude':lat,'longitude':lon},'radius_m':radius,'stations':records,'recommendations':recommendations,
            'thresholds':thresholds,'warning':'실제 과거 시험의 사전 계산 결과입니다. 확률의 현장 보정·현재 이용 성공을 보장하지 않습니다.'}


@app.get('/api/live')
def live():
    try:packet=fetch_live()
    except RuntimeError as error:raise HTTPException(502,str(error))
    db=store();meta=db['metadata']['models']['B1S'];stations=db['stations'].set_index('station_id')
    now=pd.Timestamp(packet['received_kst']).as_unit('ns');model=snapshot_model();features=[];indices=[]
    for i,row in enumerate(packet['rows']):
        sid=row['station_id']
        row.update({'risk_score':None,'risk_signal':None,'prediction_model':'B1S snapshot-only baseline','proposal_prediction':None})
        if sid in stations.index:
            st=stations.loc[sid]
            moved=float(distances_m(row['latitude'],row['longitude'],[st.latitude],[st.longitude])[0])>500
            if moved:row['prediction_status']='과거 위치와 차이가 커 예측 보류';continue
            features.append({'station_code':int(st.station_code),'hour':now.hour,'weekday':now.dayofweek,'weekend':float(now.dayofweek>=5),
                             'quantity':row['quantity'],'log_quantity':float(np.log1p(row['quantity']))});indices.append(i)
        else:row['prediction_status']='학습 대상에 없는 대여소로 예측 보류'
    if features:
        frame=pd.DataFrame(features)[meta['features']]
        probabilities=model.predict(frame,num_threads=2)
        for i,p in zip(indices,probabilities):
            packet['rows'][i].update(risk_score=round(float(p),4),risk_signal=bool(p>=meta['threshold']),
                                    prediction_status='실시간 수량 입력·기본 모형 실행 확인. 현재 정확도는 미검증.')
    packet['preview_model']='B1S';packet['threshold']=meta['threshold']
    packet['proposal_status']='M1의 서울 전체·과거 이력 입력은 아직 충족되지 않아 실시간 제안 모델 결과로 표시하지 않습니다.'
    packet['nominal_target_hour']=(now.floor('h')+pd.Timedelta(hours=1)).isoformat()
    return packet
