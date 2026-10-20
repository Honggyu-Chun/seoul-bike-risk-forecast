import numpy as np
from sklearn.calibration import calibration_curve
from bike_demo.followup_v4.calibration_analysis import bin_summary


def test_reliability_bins_match_official_boundaries_and_keep_empty_bins():
    y=np.array([0,1,0,1])
    p=np.array([0,.1,.5,1.])
    table,ece=bin_summary(y,p,10)
    actual,expected=calibration_curve(y,p,n_bins=10,strategy='uniform')
    filled=table[table.n>0]
    np.testing.assert_allclose(filled.mean_predicted,expected)
    np.testing.assert_allclose(filled.observed_fraction,actual)
    assert table.n.sum()==4 and table.positive.sum()==2
    assert table.loc[table.n==0,'mean_predicted'].isna().all()
    assert abs(ece-.35)<1e-12
