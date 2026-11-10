"""Real saved C weights on synthetic inputs, no raw/H2/provider data."""
import copy
from datetime import datetime,timedelta,timezone
import json
import math
from pathlib import Path
import unittest
import numpy as np
from fastapi.testclient import TestClient
from .runtime import Runtime,sha,RELEASE
from .service import create_app
TOKEN='test-only-private-token-not-for-production-0001'
BASE=datetime(2026,11,10,5,tzinfo=timezone.utc)
NOW=BASE+timedelta(minutes=40)
def fixture():
    return {'capture_complete':True,'source_capture_id':'synthetic-complete-capture','base_at':BASE.isoformat(),'materialized_at':(NOW-timedelta(seconds=1)).isoformat(),'stations':[
        {'station_id':'ST-DEMO-102','model_station_code':102,'name':'합성 입력 대여소','latitude':37.5665,'longitude':126.978,
         'identity_verified':True,'identity_verified_at':(BASE-timedelta(days=1)).isoformat(),'identity_evidence':'synthetic_test_only_not_real_identity','identity_revision':'fixture-v1',
         'inventory_at_base':2,'base_inventory_received_at':(BASE-timedelta(seconds=30)).isoformat(),
         'latest_inventory':2,'latest_inventory_received_at':(NOW-timedelta(seconds=30)).isoformat(),'latest_inventory_observed_at':None}]}
class TestRealModelService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.rt=Runtime()
    def setUp(self):
        self.now=[NOW];self.app=create_app(TOKEN,self.rt,clock=lambda:self.now[0]);self.client=TestClient(self.app);self.headers={'Authorization':'Bearer '+TOKEN}
    def post(self,path,data):return self.client.post(path,json=data,headers=self.headers)
    def ingest(self,data=None):
        r=self.post('/internal/ai-data/v1/snapshot',data or fixture());self.assertEqual(r.status_code,200,r.text);return r
    def test_auth_and_empty(self):
        self.assertEqual(self.client.get('/v1/status').status_code,401)
        self.assertEqual(self.client.get('/v1/status',headers=self.headers).json()['status'],'not_ready')
    def test_three_horizons_and_unknown(self):
        self.ingest();data=self.post('/v1/predictions',{'station_ids':['ST-DEMO-102','missing'],'horizons_hours':[1,2,3]}).json()
        self.assertEqual(len(data['items']),6)
        for item in data['items'][:3]:
            self.assertEqual(item['available_probability'],1-item['risk_probability']);self.assertEqual(item['model_id'],'C_h'+str(item['horizon_hours']))
            self.assertEqual(datetime.fromisoformat(item['target_at']),BASE+timedelta(hours=item['horizon_hours']))
        self.assertTrue(all(x['risk_probability'] is None for x in data['items'][3:]))
    def test_train_serving_C_kernel_parity(self):
        from cycleon.features import kernel
        self.ingest()
        s=self.app.state.service
        for h in (1,2,3):
            matrix=kernel(np.array([[2]],np.float32),BASE,np.array([102],np.float32),h,'C')
            expected,threshold=self.rt.predict_c(matrix,h)
            actual=s.predict(['ST-DEMO-102'],[h])['items'][0]
            self.assertEqual(actual['risk_probability'],float(expected[0]))
            self.assertEqual(actual['is_risk'],bool(expected[0]>=threshold))
    def test_zero_is_prediction_but_not_recommendation(self):
        x=fixture();x['stations'][0]['latest_inventory']=0;self.ingest(x)
        prediction=self.post('/v1/predictions',{'station_ids':['ST-DEMO-102']}).json()['items'][0]
        self.assertIsNotNone(prediction['risk_probability'])
        rec=self.post('/v1/recommendations',{'latitude':37.5665,'longitude':126.978,'horizon_hours':1}).json();self.assertTrue(rec['abstained'])
    def test_positive_recommendation_and_stale_cutoff(self):
        self.ingest();request={'latitude':37.5665,'longitude':126.978,'horizon_hours':1}
        self.assertFalse(self.post('/v1/recommendations',request).json()['abstained'])
        self.now[0]+=timedelta(seconds=270)
        self.assertTrue(self.post('/v1/recommendations',request).json()['abstained'])
    def test_future_receipt_duplicate_unknown_field(self):
        x=fixture();x['stations'][0]['base_inventory_received_at']=(BASE+timedelta(seconds=1)).isoformat()
        self.assertEqual(self.post('/internal/ai-data/v1/snapshot',x).status_code,422)
        x=fixture();x['stations']*=2;self.assertEqual(self.post('/internal/ai-data/v1/snapshot',x).status_code,422)
        self.assertEqual(self.post('/v1/predictions',{'station_ids':['s'],'extra':1}).status_code,422)
        self.assertEqual(self.post('/v1/predictions',{'station_ids':['s'],'horizons_hours':[True]}).status_code,422)
    def test_identity_gate_missing_base_and_stale_base(self):
        for field,value in [('identity_verified',False),('inventory_at_base',None),('base_inventory_received_at',(BASE-timedelta(seconds=300)).isoformat())]:
            self.setUp();x=fixture();x['stations'][0][field]=value
            if field=='inventory_at_base':x['stations'][0]['base_inventory_received_at']=None
            self.ingest(x)
            self.assertIsNone(self.post('/v1/predictions',{'station_ids':['ST-DEMO-102']}).json()['items'][0]['risk_probability'])
    def test_rollback_and_no_rewrite_at_same_base(self):
        self.ingest();self.assertEqual(self.post('/internal/ai-data/v1/snapshot',fixture()).status_code,422)
        x=fixture();x['materialized_at']=NOW.isoformat();x['stations'][0]['inventory_at_base']=3
        self.assertEqual(self.post('/internal/ai-data/v1/snapshot',x).status_code,422)
    def test_rollover_midnight(self):
        self.ingest();self.now[0]=BASE+timedelta(hours=1)
        self.assertEqual(self.post('/v1/predictions',{'station_ids':['ST-DEMO-102']}).json()['items'][0]['reason'],'base_not_precomputed')
        self.setUp();self.now[0]=datetime(2026,11,10,14,40,tzinfo=timezone.utc);x=fixture();x['base_at']='2026-11-10T14:00:00+00:00';x['materialized_at']='2026-11-10T14:39:59+00:00';x['stations'][0]['base_inventory_received_at']='2026-11-10T13:59:30+00:00';x['stations'][0]['latest_inventory_received_at']='2026-11-10T14:39:30+00:00';self.ingest(x)
        items=self.post('/v1/predictions',{'station_ids':['ST-DEMO-102']}).json()['items']
        self.assertEqual(datetime.fromisoformat(items[0]['target_at']).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Seoul')).day,11)
    def test_incomplete_capture_rejected(self):
        x=fixture();x['capture_complete']=False
        self.assertEqual(self.post('/internal/ai-data/v1/snapshot',x).status_code,422)
    def test_all_shipped_model_headers(self):
        import lightgbm as lgb
        for route in self.rt.manifest['routes']:
            model=lgb.Booster(model_file=str(self.rt.directory/route['model_file']))
            self.assertEqual(model.feature_name(),route['columns'])
            self.assertEqual(model.num_feature(),len(route['columns']))
    def test_radius_bound_and_repeat(self):
        self.ingest();req={'station_ids':['ST-DEMO-102'],'horizons_hours':[1,2,3]}
        self.assertEqual(self.post('/v1/predictions',req).json(),self.post('/v1/predictions',req).json())
        self.assertEqual(self.post('/v1/recommendations',{'latitude':37.5665,'longitude':126.978,'horizon_hours':1,'radius_m':1501}).status_code,422)
        r=self.post('/v1/recommendations',{'latitude':37.5,'longitude':127.5,'horizon_hours':1,'radius_m':1}).json()
        self.assertEqual(r['reason'],'no_station_within_radius');self.assertFalse(r['automatic_radius_expansion'])
if __name__=='__main__':unittest.main()
