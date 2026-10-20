import numpy as np
from bike_demo.followup_v4.uncertainty import daily_confusions, metrics, moving_blocks, paired_ranges


def test_target_day_confusions_preserve_observations_and_metric_denominators():
    y=np.array([1,1,0,0,1,0],dtype=bool)
    pred=np.array([1,0,1,0,1,0],dtype=bool)
    matrix=daily_confusions(y,pred,np.array([0,0,0,1,1,1]),2,np.ones(6,dtype=bool))
    np.testing.assert_array_equal(matrix.sum(axis=0),[6,3,2,1,1,2])
    assert metrics(matrix.sum(axis=0))['f1']==2/3


def test_moving_blocks_keep_contiguous_days_and_are_reproducible():
    draws=moving_blocks(17,7,200)
    np.testing.assert_array_equal(draws,moving_blocks(17,7,200))
    assert draws.shape==(200,17)
    assert draws.min()>=0 and draws.max()<17
    np.testing.assert_array_equal(np.diff(draws[:,:7],axis=1),np.ones((200,6)))
    np.testing.assert_array_equal(np.diff(draws[:,7:14],axis=1),np.ones((200,6)))


def test_identical_models_have_zero_paired_range_and_no_positive_prediction_is_defined():
    counts=np.array([[10,3,2,1,1,6],[10,4,3,1,1,5],[10,2,1,0,1,8]])
    for item in paired_ranges(counts,counts,moving_blocks(3,1,100)).values():
        assert set(item.values())=={0.0}
    assert metrics(np.array([10,3,0,0,3,7]))==dict(f1=0,precision=0,recall=0)
