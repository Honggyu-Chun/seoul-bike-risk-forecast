import numpy as np
import pandas as pd
import pytest
from bike_demo.followup_v4.air_runtime import build_runtime_air_features,POLLUTANTS

def test_runtime_schema_matches_training_and_never_substitutes_older_or_late_values():
    cache=pd.DataFrame({'timestamp':['2025-05-01 07:00','2025-05-01 08:00'],
                        'received_at':['2025-05-01 08:00','2025-05-01 10:01'],
                        'district':['종로구','종로구'],**{p:[7.,8.] for p in POLLUTANTS}})
    request=pd.DataFrame({'input_time':['2025-05-01 10:00'],'district':['종로구']})
    late=build_runtime_air_features(cache,request)
    assert np.isnan(late.iloc[0].air_pm10_ug_m3_lag2h)
    assert not late.iloc[0].operational_eligible
    cache.loc[1,'received_at']='2025-05-01 09:00'
    ontime=build_runtime_air_features(cache,request)
    assert ontime.iloc[0].air_pm10_ug_m3_lag2h==8.
    assert ontime.iloc[0].air_pm10_ug_m3_lag2h_qc==0
    request.loc[0,'input_time']='2025-05-01 10:15'
    with pytest.raises(ValueError,match='hourly'):build_runtime_air_features(cache,request)
