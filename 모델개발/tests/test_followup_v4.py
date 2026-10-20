import numpy as np
import pandas as pd
from bike_demo.followup_v4.features import chunk_features
from bike_demo.research_features import build_features, transition_history, runs

def fixture_data():
    rng=np.random.default_rng(27)
    times=pd.date_range('2025-01-01',periods=500,freq='h')
    stations=pd.DataFrame({'station_id':np.arange(8),'latitude':37.55+np.arange(8)*.001,
                          'longitude':126.98+np.arange(8)*.001,'district':['a']*4+['b']*4,
                          'qr':[10]*8,'lcd':[0]*8})
    q=rng.integers(0,20,size=(len(times),len(stations))).astype(np.float32)
    q[:,0]=0 # run length exceeds context window
    q[17:24,3]=np.nan
    return times,stations,q

def test_chunk_features_preserve_full_history_and_reference_columns():
    times,stations,q=fixture_data()
    whole,expected_groups=build_features(times,stations,q)
    history=transition_history(times,q);known=np.isfinite(q)
    durations={'t_zero_run':runs(q==0,known),'t_low3_run':runs(q<=3,known),'t_positive_run':runs(q>0,known)}
    part,groups=chunk_features(times,stations,q,410,450,history,durations)
    assert groups==expected_groups
    np.testing.assert_allclose(part.to_numpy(),whole.iloc[410*8:450*8].to_numpy(),rtol=1e-5,atol=1e-5,equal_nan=True)
    assert part.t_zero_run.iloc[0]==411

def test_features_at_input_do_not_change_when_future_quantity_changes():
    times,stations,q=fixture_data()
    old,_=build_features(times[:150],stations,q[:150])
    changed=q[:150].copy();changed[100:]=99
    new,_=build_features(times[:150],stations,changed)
    np.testing.assert_allclose(old.iloc[:100*8],new.iloc[:100*8],rtol=1e-6,equal_nan=True)

def test_fold_selection_has_strictly_earlier_label_windows():
    from bike_demo.followup_v4.common import protocol
    for f in protocol()['development_folds'].values():
        assert pd.Timestamp(f['train_end'])==pd.Timestamp(f['stop_start'])
        assert f['stop_end']<=f['cal_start']<f['cal_end']<=f['policy_start']<f['policy_end']<=f['valid_start']<f['valid_end']
