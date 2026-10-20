"""Checks against time leakage and incorrect metric/objective implementations."""
import numpy as np
import pandas as pd
import pytest
import json
from scipy.special import expit
from bike_demo.research_features import build_features
from bike_demo.research import tune,decisions,rule_metrics,focal_objective,fast_f1_monitor


def test_weather_uses_only_earlier_observation_times_and_preserves_missing(tmp_path):
    import io,zipfile
    from bike_demo.research_features import past_weather
    weather_times=pd.date_range('2025-01-01',periods=5,freq='h')
    fields=['기온(°C)','강수량(mm)','풍속(m/s)','습도(%)','현지기압(hPa)','전운량(10분위)','시정(10m)','적설(cm)']
    data=pd.DataFrame({'지점':108,'일시':weather_times,**{field:np.arange(5,dtype=float) for field in fields}})
    data['강수량(mm)']=np.nan
    path=tmp_path/'weather.zip'
    def write():
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,'w') as archive:archive.writestr('weather.csv',data.to_csv(index=False).encode('cp949'))
        with zipfile.ZipFile(path,'w') as archive:archive.writestr('inner.zip',inner.getvalue())
    write();times=weather_times[1:3];before=past_weather(times,path)
    assert before['w_temperature_lag1'].tolist()==[0.,1.]
    assert before['w_rain_lag1'].isna().all()
    data.loc[2:,fields]=999.;write()
    pd.testing.assert_frame_equal(before,past_weather(times,path))


def test_all_research_features_are_unchanged_by_future_observations():
    times=pd.date_range('2025-01-01',periods=10,freq='h')
    stations=pd.DataFrame({'latitude':[37.55,37.551,37.57],'longitude':[126.91,126.912,126.94],
                           'qr':[10,10,20],'lcd':[0,0,0],'district':['마포구','마포구','서대문구']})
    q=np.array([[i,2*i,0] for i in range(10)],dtype=np.float32);q[2,0]=np.nan
    before,groups=build_features(times,stations,q)
    modified=q.copy();modified[6:]=999
    after,_=build_features(times,stations,modified)
    pd.testing.assert_frame_equal(before.iloc[:18],after.iloc[:18])
    assert len(groups['basic'])==16 and len(groups['full'])>90
    assert np.isnan(before.iloc[6]['quantity'])


def test_state_threshold_search_optimizes_actual_pooled_f1():
    y=np.array([1,1,0,0,1,1,0,0]);p=np.array([.9,.8,.7,.6,.3,.25,.2,.1]);q=np.array([0,0,0,0,1,1,1,1])
    rule=tune(y,p,q)
    assert rule['kind']=='current_state'
    assert decisions(p,q,rule).tolist()==y.astype(bool).tolist()
    m=rule_metrics(y,p,q,rule)
    assert m['f1']==1 and m['brier']>0


def test_focal_gradient_matches_numerical_loss_derivative():
    y=np.array([0,1,0,1],dtype=float);raw=np.array([-1.2,-.4,.5,1.7]);gamma=2
    class Data:
        def get_label(self):return y
    grad,hess=focal_objective(gamma)(raw,Data())
    def loss(z):
        pt=expit((2*y-1)*z)
        return -(1-pt)**gamma*np.log(pt)
    epsilon=1e-5;numeric=(loss(raw+epsilon)-loss(raw-epsilon))/(2*epsilon)
    np.testing.assert_allclose(grad,numeric,rtol=1e-5,atol=1e-7)
    assert np.isfinite(hess).all() and (hess>0).all()


def test_recall_floor_can_require_a_lower_decision_boundary():
    y=np.array([1,1,1,0,0,0]);p=np.array([.9,.7,.001,.8,.1,.05]);q=np.array([0,0,1,0,1,1])
    rule=tune(y,p,q,minimum_recall=1.)
    assert decisions(p,q,rule)[y==1].all()


def test_checkpoint_metric_matches_validation_threshold_search_on_regular_scores():
    y=np.array([1,1,0,0,1,1,0,0]);p=np.array([.901,.801,.701,.601,.301,.251,.201,.101]);q=np.array([0,0,0,0,1,1,1,1])
    assert fast_f1_monitor(y,p,q)==pytest.approx(tune(y,p,q)['f1'])


def test_neural_sequence_stops_at_the_input_and_architectures_execute():
    pytest.importorskip('torch')
    from bike_demo.neural_research import TemporalClassifier,batch
    times=pd.date_range('2025-01-01',periods=30,freq='h')
    channels=np.arange(30*2*4,dtype=np.float32).reshape(30,2,4)
    d={'times':times,'n':2,'channels':channels}
    inputs=batch(d,np.array([24*2+1]))
    np.testing.assert_array_equal(inputs[0].numpy()[0,-1],channels[24,1])
    np.testing.assert_array_equal(inputs[0].numpy()[0,0],channels[1,1])
    for architecture in ['mlp','tcn','gru']:
        output=TemporalClassifier(architecture,2)(*inputs)
        assert tuple(output.shape)==(1,) and np.isfinite(output.detach().numpy()).all()


def test_service_manifest_counts_scheduled_requests_even_when_hour_is_missing(tmp_path,monkeypatch):
    from bike_demo import evaluate_research as evaluation
    directory=tmp_path/'artifacts/evaluation';directory.mkdir(parents=True)
    (directory/'evaluation_origins.json').write_text(json.dumps([{'origin_id':0,'latitude':37.5,'longitude':127.0}]))
    monkeypatch.setattr(evaluation,'ROOT',tmp_path)
    times=pd.date_range('2025-04-08T07:00',periods=2,freq='h')
    stations=pd.DataFrame({'station_id':[102],'latitude':[37.5],'longitude':[127.]})
    current=np.array([2.,2.]);target=np.array([1.,np.nan]);mask=np.array([True,False]);prob=np.array([.1,np.nan])
    rows,manifest=evaluation.recommendation_queries(times,stations,current,target,mask,prob,prob,'2025-04-08','2025-04-09')
    assert manifest=={'attempted':2,'common_queries':1,'excluded_no_candidate':1}
    rows,manifest=evaluation.recommendation_queries(times,stations,current,target,np.zeros(2,bool),prob,prob,'2025-04-08','2025-04-09')
    assert not rows and manifest['attempted']==2 and manifest['excluded_no_candidate']==2


def test_matching_service_policy_uses_the_baseline_scores_independently(tmp_path,monkeypatch):
    from bike_demo import evaluate_research as evaluation
    directory=tmp_path/'artifacts/evaluation';directory.mkdir(parents=True)
    (directory/'evaluation_origins.json').write_text(json.dumps([{'origin_id':0,'latitude':37.5,'longitude':127.0}]))
    monkeypatch.setattr(evaluation,'ROOT',tmp_path)
    times=pd.DatetimeIndex(['2025-04-08T07:00'])
    stations=pd.DataFrame({'station_id':[102,103],'latitude':[37.5,37.5001],'longitude':[127.,127.]})
    rows,_=evaluation.recommendation_queries(times,stations,np.array([2.,2.]),np.array([1.,1.]),np.ones(2,bool),np.array([.2,.3]),np.array([.1,.9]),'2025-04-08','2025-04-09',base_utility_prob=np.array([.9,.1]))
    assert evaluation.choose(rows[0],{'kind':'utility','model':'proposal','weight':100})==0
    assert evaluation.choose(rows[0],{'kind':'utility','model':'base_utility','weight':100})==1


def test_service_intervals_preserve_pairs_and_percentage_point_units():
    from bike_demo.evaluate_research import service_day_bootstrap
    choices=[]
    for date in ['2025-04-15','2025-04-16']:
        for origin in range(3):
            for method in ['R0','R1u','Rq','R2']:
                choices.append({'date':date,'input_time':date+'T07:00','origin_id':origin,'method':method,
                                'valid':int(method in ['R1u','R2'] or origin<2)})
    result=service_day_bootstrap(choices);ci=result['comparisons_R2_minus_baseline']
    assert result['paired_requests']==6 and result['days']==2
    assert ci['R1u']=={'lower_pp':0.,'upper_pp':0.}
    assert ci['R0']['lower_pp']==pytest.approx(100/3) and ci['R0']['upper_pp']==pytest.approx(100/3)
    with pytest.raises(ValueError,match='paired'):service_day_bootstrap(choices[:-1])
