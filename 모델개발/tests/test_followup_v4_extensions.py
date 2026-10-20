import numpy as np
import pandas as pd
import pytest
from bike_demo.followup_v4.context import holiday_at
from bike_demo.followup_v4.external_data import xml_rows

def test_temporary_holiday_requires_prior_announcement():
    dates=pd.to_datetime(['2025-01-27','2025-01-27','2025-06-03','2025-06-03'])
    issuance=pd.to_datetime(['2025-01-13','2025-01-14','2025-04-07','2025-04-08'])
    assert holiday_at(dates,issuance).tolist()==[0,1,0,1]

def test_api_error_xml_is_not_silently_empty():
    assert xml_rows(b'<dbs/>')==[]
    assert xml_rows(b'<dbs><db><mt20id>PF1</mt20id></db></dbs>')==[{'mt20id':'PF1'}]
    with pytest.raises(RuntimeError):xml_rows(b'<error><code>INVALID_KEY</code></error>')

def test_neural_output_shapes_and_finite_values():
    import torch
    from bike_demo.followup_v4.neural import TemporalModel
    for kind,length,graph in [('mlp',24,False),('mlp',168,False),('tcn',24,False),('tcn',24,True)]:
        model=TemporalModel(12,length,kind,graph).eval()
        x=torch.zeros(4,6,length);idx=torch.arange(4)
        with torch.no_grad():y=model(x,idx,idx,idx,x if graph else None)
        assert y.shape==(4,) and torch.isfinite(y).all()

def test_graph_removal_control_keeps_self_inputs_and_ignores_future_history():
    import torch
    from bike_demo.followup_v4.neural import SequenceInputs
    data=SequenceInputs.__new__(SequenceInputs)
    data.n=3;data.hours=np.arange(4,dtype=np.int64);data.weekdays=np.arange(4,dtype=np.int64)
    data.channels=np.arange(72,dtype=np.float32).reshape(4,3,6)
    data.neighbor=data.channels+100
    index=np.array([7])
    identity=data.batch(index,2,True,True)
    graph=data.batch(index,2,True,False)
    assert torch.equal(identity[0],identity[4])
    assert not torch.equal(graph[0],graph[4])
    earlier=identity[0].clone();data.channels[3:]+=1000
    assert torch.equal(earlier,data.batch(index,2,True,True)[0])
