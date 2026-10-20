"""Behavioral checks for causal features, risk metrics, recommendations and API contracts."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from bike_demo.data import lag,rolling,feature_frame,ROOT
from bike_demo.train import metrics,choose_threshold
from bike_demo.recommend import choose_station,distances_m
from bike_demo.api import app


def test_missing_hour_is_not_skipped_by_lag():
    q=np.array([[2.],[np.nan],[1.]],dtype=np.float32)
    assert np.isnan(lag(q,1)[2,0])
    mean,n=rolling(q,2)
    assert mean[2,0]==1 and n[2,0]==1


def test_future_quantity_cannot_change_present_features():
    times=pd.date_range('2025-01-01',periods=8,freq='h')
    stations=pd.DataFrame({'latitude':[37.55,37.551],'longitude':[126.91,126.912]})
    q=np.array([[i,i+1] for i in range(8)],dtype=np.float32)
    original,_=feature_frame(times,stations,q)
    altered=q.copy();altered[5:]=999
    changed,_=feature_frame(times,stations,altered)
    pd.testing.assert_frame_equal(original.iloc[:10],changed.iloc[:10])


def test_metrics_positive_class_is_zero_risk_not_availability():
    m=metrics(np.array([1,1,0,0]),np.array([.9,.2,.8,.1]),.5)
    assert (m['tp'],m['fp'],m['fn'],m['tn'])==(1,1,1,1)
    assert m['f1']==.5 and m['recall']==.5


def test_threshold_is_chosen_from_validation_grid_only():
    t,score=choose_threshold(np.array([1,0]),np.array([.6,.4]),[.3,.5,.7])
    assert t==.5 and score==1


def test_nearest_candidate_tie_and_risk_gate():
    ids=np.array([103,102,104]);q=np.array([2,2,0]);d=np.array([100,100,10])
    assert choose_station(ids,q,d)==1
    assert choose_station(ids,q,d,np.array([.2,.7,.1]),.35)==0
    assert choose_station(ids,q,d,np.ones(3),.35) is None


def test_distance_zero_and_rough_scale():
    assert distances_m(37.5,127,[37.5],[127])[0]==0
    assert 110<distances_m(37.5,127,[37.501],[127])[0]<112


@pytest.mark.skipif(not (ROOT/'artifacts/evaluation/model_results.json').exists(),reason='Run the frozen experiment first')
def test_api_replay_and_invalid_queries():
    with TestClient(app) as client:
        assert client.get('/api/health').json()['models_ready']
        assert client.get('/api/cases').status_code==200
        response=client.get('/api/snapshot',params={'at':'2025-03-20T07:00','lat':37.5665,'lon':126.978,'radius':500})
        assert response.status_code==200
        data=response.json();assert data['mode']=='replay' and data['target_time']=='2025-03-20T08:00:00'
        assert all(row['distance_m']<=500 for row in data['stations'])
        assert all('risk_M1' in row and 'actual_next_quantity' in row for row in data['stations'])
        assert client.get('/api/snapshot',params={'at':'2026-01-01','lat':37.5,'lon':127}).status_code==404
        assert client.get('/api/snapshot',params={'at':'2025-03-20T07:00','lat':0}).status_code==422


@pytest.mark.skipif(not (ROOT/'artifacts/evaluation/recommendation_results.json').exists(),reason='Run recommendation evaluation first')
def test_report_denominators_and_provenance_agree():
    model=json.loads((ROOT/'artifacts/evaluation/model_results.json').read_text())
    rec=json.loads((ROOT/'artifacts/evaluation/recommendation_results.json').read_text())
    for row in model['metrics'].values():
        assert row['tp']+row['fp']+row['fn']+row['tn']==row['n']
        assert row['tp']+row['fn']==row['zero_cases']
    for row in rec['metrics'].values():
        assert row['effective_rate']==pytest.approx(row['valid_recommendations']/row['requests'])
        assert row['coverage']==pytest.approx(row['recommended']/row['requests'])
    # The original service target cannot be presented as achieved or feasible at this ceiling.
    assert rec['metrics']['R0']['effective_rate']+.05>1
    assert not rec['goal_verdict']['effective_5pp']


@pytest.mark.skipif(not (ROOT/'data/processed/rider_q1_panel.npz').exists(),reason='Archived quantities required')
def test_detail_history_is_bounded_by_input_and_uses_actual_observations():
    from bike_demo.api import quantity_history
    with TestClient(app) as client:
        detail=client.get('/api/station/102',params={'at':'2025-03-20T07:00'}).json()
    times=[pd.Timestamp(h['time']) for h in detail['history']]
    assert len(times)==24 and times[-1]==pd.Timestamp('2025-03-20T07:00')
    assert max(times)<=pd.Timestamp(detail['input_time'])
    assert 'target_quantity' not in detail and 'actual_next_quantity' not in detail
    panel=quantity_history();col=list(panel['ids']).index(102)
    for item,time in zip(detail['history'],times):
        expected=panel['quantity'][panel['times'].get_loc(time),col]
        assert item['quantity']==(None if np.isnan(expected) else int(expected))


@pytest.mark.skipif(not (ROOT/'data/processed/rider_q1_panel.npz').exists(),reason='Archived quantities required')
def test_detail_neighborhood_excludes_self_and_preserves_unknown():
    with TestClient(app) as client:
        response=client.get('/api/station/102',params={'at':'2025-03-20T07:00'})
        assert response.status_code==200
        d=response.json();n=d['neighbors']
        assert 0<=n['empty']<=n['observed']<=n['total']
        assert all(s['station_id']!=102 and s['distance_m']<=500 for s in n['stations'])
        assert all(s['quantity'] is None or s['quantity']>=0 for s in n['stations'])
        assert client.get('/api/station/999999',params={'at':'2025-03-20T07:00'}).status_code==404
        assert client.get('/api/station/102',params={'at':'NaT'}).status_code==422


@pytest.mark.skipif(not (ROOT/'artifacts/evaluation/test_predictions.parquet').exists(),reason='Frozen predictions required')
def test_city_is_same_timestamp_and_not_a_current_citywide_claim():
    with TestClient(app) as client:
        packet=client.get('/api/city',params={'at':'2025-03-20T07:00'}).json()
        assert packet['input_time']=='2025-03-20T07:00:00'
        assert packet['scope']=='historical_test_records_only'
        assert len(packet['rows'])==len({r[0] for r in packet['rows']})
        assert all(r[1]>=0 and 0<=r[2]<=1 and 0<=r[3]<=1 for r in packet['rows'])
        assert client.get('/api/city',params={'at':'2026-01-01'}).status_code==404


def test_research_panel_does_not_present_development_scores_as_a_new_test():
    with TestClient(app) as client:
        response=client.get('/api/research');assert response.status_code==200
        packet=response.json()
        if packet['status']=='evaluated':
            assert packet['scope']=='new_2025_april_may_archived_test'
            assert packet['results']['test_period']==['2025-04-15','2025-05-15']
        else:
            assert 'not the new final test' in packet['scope']
            assert 'results' not in packet
