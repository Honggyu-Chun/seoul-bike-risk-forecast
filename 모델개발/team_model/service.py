"""Authenticated team reference service; no collector or background training."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import math
import secrets
from threading import RLock
from zoneinfo import ZoneInfo
from typing import Literal
import numpy as np
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator
from .runtime import Runtime
KST = ZoneInfo('Asia/Seoul')

def instant(x):
    if isinstance(x, str): x = datetime.fromisoformat(x.replace('Z', '+00:00'))
    if x.tzinfo is None or x.utcoffset() is None: raise ValueError('Explicit timezone required')
    return x.astimezone(timezone.utc)
def hour(at): return instant(at).astimezone(KST).replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)
def stamp(at): return instant(at).isoformat()
def distance(a,b,c,d):
    a,c,db,dd=map(math.radians,(a,c,c-a,d-b))
    z=math.sin(db/2)**2+math.cos(a)*math.cos(c)*math.sin(dd/2)**2
    return 6371008.8*2*math.asin(math.sqrt(min(1,max(0,z))))
class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
class StationInput(Strict):
    station_id: str = Field(min_length=1,max_length=80)
    model_station_code: StrictInt = Field(gt=0)
    name: str = Field(min_length=1,max_length=200)
    latitude: float = Field(ge=33,le=39)
    longitude: float = Field(ge=124,le=132)
    identity_verified: StrictBool
    identity_verified_at: datetime
    identity_evidence: str = Field(min_length=1,max_length=1000)
    identity_revision: str = Field(min_length=1,max_length=100)
    inventory_at_base: StrictInt | None = Field(ge=0)
    base_inventory_received_at: datetime | None
    latest_inventory: StrictInt | None = Field(ge=0)
    latest_inventory_received_at: datetime | None
    latest_inventory_observed_at: datetime | None = None
    @field_validator('identity_verified_at','base_inventory_received_at','latest_inventory_received_at','latest_inventory_observed_at')
    @classmethod
    def zoned(cls,x): return None if x is None else instant(x)
    @model_validator(mode='after')
    def paired(self):
        if (self.inventory_at_base is None)!=(self.base_inventory_received_at is None):
            raise ValueError('Base value and received_at must be supplied together')
        if (self.latest_inventory is None)!=(self.latest_inventory_received_at is None):
            raise ValueError('Latest value and received_at must be supplied together')
        return self
class Snapshot(Strict):
    capture_complete: Literal[True]
    source_capture_id: str = Field(min_length=1,max_length=200)
    base_at: datetime
    materialized_at: datetime
    stations: list[StationInput] = Field(max_length=5000)
    @field_validator('base_at','materialized_at')
    @classmethod
    def zoned(cls,x): return instant(x)
    @model_validator(mode='after')
    def cutoff(self):
        if self.base_at != hour(self.base_at): raise ValueError('base_at must be an exact KST hour')
        if self.materialized_at < self.base_at: raise ValueError('materialized_at precedes base_at')
        ids=[s.station_id for s in self.stations];codes=[s.model_station_code for s in self.stations]
        if len(ids)!=len(set(ids)) or len(codes)!=len(set(codes)):
            raise ValueError('Duplicate station identity or historical model code')
        for s in self.stations:
            if s.base_inventory_received_at and s.base_inventory_received_at > self.base_at:
                raise ValueError('Future receipt cannot be a base-hour input')
            if s.latest_inventory_received_at and s.latest_inventory_received_at > self.materialized_at:
                raise ValueError('Latest receipt is after snapshot materialization')
            if s.latest_inventory_observed_at and (not s.latest_inventory_received_at or s.latest_inventory_observed_at > s.latest_inventory_received_at):
                raise ValueError('Observation is after receipt')
            if s.identity_verified and s.identity_verified_at > self.base_at:
                raise ValueError('Identity was not known by base_at')
        return self
class PredictionRequest(Strict):
    station_ids: list[str] = Field(min_length=1,max_length=500)
    horizons_hours: list[Literal[1,2,3]] = Field(default_factory=lambda:[1,2,3],min_length=1,max_length=3)
    @field_validator('station_ids')
    @classmethod
    def station_lengths(cls,x):
        if any(not 1 <= len(s) <=80 for s in x):raise ValueError('Invalid station ID length')
        return x
    @field_validator('horizons_hours',mode='before')
    @classmethod
    def strict_horizon(cls,x):
        if not isinstance(x,list) or any(type(v) is not int for v in x):raise ValueError('Integer horizons required')
        return x
class RecommendationRequest(Strict):
    latitude: float=Field(ge=33,le=39)
    longitude: float=Field(ge=124,le=132)
    horizon_hours: Literal[1,2,3]
    radius_m: float=Field(default=500,gt=0,le=1500)
    @field_validator('horizon_hours',mode='before')
    @classmethod
    def strict_horizon(cls,x):
        if type(x) is not int:raise ValueError('Integer horizon required')
        return x
class Prediction(Strict):
    station_id:str
    horizon_hours:Literal[1,2,3]
    target_at:datetime
    risk_probability:float | None=Field(ge=0,le=1)
    available_probability:float | None=Field(ge=0,le=1)
    is_risk:bool | None
    model_id:str | None
    reason:str | None
    input_group:str | None
    latest_inventory:int | None
    latest_inventory_received_at:datetime | None
    latest_inventory_age_seconds:float | None
    latest_inventory_observed_at:datetime | None
class PredictionResponse(Strict):
    base_at:datetime
    requested_at:datetime
    items:list[Prediction]
class RankedPrediction(Prediction):
    name:str
    latitude:float
    longitude:float
    distance_m:float
    score:float
class RecommendationResponse(Strict):
    base_at:datetime
    requested_at:datetime
    horizon_hours:Literal[1,2,3]
    radius_m:float
    distance_type:Literal['straight_line']='straight_line'
    automatic_radius_expansion:Literal[False]=False
    recommended:RankedPrediction | None
    alternatives:list[RankedPrediction]=Field(max_length=2)
    abstained:bool
    reason:str | None
    nearby_count:int
    eligible_count:int

class ServiceData(Strict):
    model_route:Literal['C_only']='C_only'
    real_model_loaded:Literal[True]=True
    primary_weather_routes_enabled:Literal[False]=False
    whole_service_approved:Literal[False]=False
    station_count:int
class ServiceStatus(Strict):
    service:Literal['CycleOn']='CycleOn'
    status:Literal['ready','not_ready']
    server_at:datetime
    base_at:datetime
    supported_targets:list[datetime]
    model_bundle_id:str
    model_stage:Literal['team_C_reference_requires_live_acceptance']
    last_precompute_at:datetime | None
    precompute_error:str | None
    latest_inventory_age_limit_seconds:Literal[300]=300
    time_policy:str
    data:ServiceData
class SnapshotAccepted(Strict):
    status:Literal['accepted']='accepted'
    base_at:datetime
    stations:int
    qualified_stations:int
    model_route:Literal['C_only']='C_only'
    real_model_loaded:Literal[True]=True
    primary_weather_routes_enabled:Literal[False]=False

class Service:
    def __init__(self,runtime,clock=lambda:datetime.now(timezone.utc)):
        self.runtime,self.clock=runtime,clock
        self.lock=RLock();self.snapshot=None;self.predictions={};self.last_precompute_at=None
    def ingest(self,data):
        now=instant(self.clock())
        if data.materialized_at>now or data.base_at>hour(now): raise ValueError('Snapshot is in the future')
        with self.lock:
            if self.snapshot and (data.materialized_at <= self.snapshot.materialized_at or data.base_at<self.snapshot.base_at):
                raise ValueError('Snapshot rollback or non-increasing materialization')
            if self.snapshot and data.base_at == self.snapshot.base_at:
                previous = {s.station_id:s.model_dump(exclude={'latest_inventory','latest_inventory_received_at','latest_inventory_observed_at'}) for s in self.snapshot.stations}
                for station in data.stations:
                    fixed = station.model_dump(exclude={'latest_inventory','latest_inventory_received_at','latest_inventory_observed_at'})
                    if station.station_id in previous and fixed != previous[station.station_id]:
                        raise ValueError('Base-hour input changed after first materialization')
            eligible=[];reasons={};pred={}
            for s in data.stations:
                reason=None
                if not s.identity_verified:reason='station_identity_not_verified_for_model'
                elif s.model_station_code not in self.runtime.allowed_codes:reason='historical_model_code_unknown'
                elif s.inventory_at_base is None:reason='base_inventory_unavailable'
                elif (data.base_at-s.base_inventory_received_at).total_seconds()>=300:reason='base_inventory_stale'
                if reason:reasons[s.station_id]=reason
                else:eligible.append(s)
            local=data.base_at.astimezone(KST)
            for h in (1,2,3):
                if eligible:
                    matrix=np.asarray([[s.model_station_code,s.inventory_at_base,np.log1p(np.float32(s.inventory_at_base)),local.hour,local.weekday(),local.month,int(local.weekday()>=5),h] for s in eligible],np.float32)
                    probabilities,threshold=self.runtime.predict_c(matrix,h)
                    for s,risk in zip(eligible,probabilities):
                        pred[s.station_id,h]=(float(risk),bool(risk>=threshold))
            self.snapshot,self.predictions,self.reasons=data,pred,reasons
            self.last_precompute_at=stamp(now)
            return {'status':'accepted','base_at':stamp(data.base_at),'stations':len(data.stations),'qualified_stations':len(eligible),'model_route':'C_only','real_model_loaded':True,'primary_weather_routes_enabled':False}
    def predict(self,station_ids,horizons):
        now=instant(self.clock());base=hour(now)
        with self.lock:
            current={s.station_id:s for s in self.snapshot.stations} if self.snapshot else {}
            ready=self.snapshot is not None and self.snapshot.base_at==base
            items=[]
            for sid in dict.fromkeys(station_ids):
                s=current.get(sid)
                for h in dict.fromkeys(horizons):
                    risk,flag=self.predictions.get((sid,h),(None,None)) if ready else (None,None)
                    reason=('base_not_precomputed' if not ready else self.reasons.get(sid,'station_unknown_or_ineligible')) if risk is None else None
                    age=(now-s.latest_inventory_received_at).total_seconds() if s and s.latest_inventory_received_at else None
                    items.append({'station_id':sid,'horizon_hours':h,'target_at':stamp(base+timedelta(hours=h)),
                        'risk_probability':risk,'available_probability':None if risk is None else 1-risk,'is_risk':flag,
                        'model_id':None if risk is None else f'C_h{h}','reason':reason,'input_group':None if risk is None else 'C',
                        'latest_inventory':s.latest_inventory if s else None,'latest_inventory_received_at':stamp(s.latest_inventory_received_at) if s and s.latest_inventory_received_at else None,
                        'latest_inventory_age_seconds':age,'latest_inventory_observed_at':stamp(s.latest_inventory_observed_at) if s and s.latest_inventory_observed_at else None})
            return {'base_at':stamp(base),'requested_at':stamp(now),'items':items}
    def status(self):
        now=instant(self.clock());base=hour(now)
        with self.lock:
            ready=self.snapshot is not None and self.snapshot.base_at==base and bool(self.predictions)
            return {'service':'CycleOn','status':'ready' if ready else 'not_ready','server_at':stamp(now),'base_at':stamp(base),
                'supported_targets':[stamp(base+timedelta(hours=h)) for h in (1,2,3)],'model_bundle_id':self.runtime.manifest['id'],
                'model_stage':'team_C_reference_requires_live_acceptance','last_precompute_at':self.last_precompute_at,
                'precompute_error':None,'latest_inventory_age_limit_seconds':300,'time_policy':'latest_exact_hour_base; latest_inventory_separate',
                'data':{'model_route':'C_only','real_model_loaded':True,'primary_weather_routes_enabled':False,
                    'whole_service_approved':False,'station_count':len(self.snapshot.stations) if self.snapshot else 0}}
    def recommend(self,request):
        with self.lock:
            stations={s.station_id:s for s in self.snapshot.stations} if self.snapshot else {}
            results=self.predict(sorted(stations),[request.horizon_hours]);nearby=[];eligible=[]
            for item in results['items']:
                s=stations[item['station_id']]
                d=distance(request.latitude,request.longitude,s.latitude,s.longitude)
                if d>request.radius_m:continue
                nearby.append(s.station_id)
                age=item['latest_inventory_age_seconds']
                if not s.identity_verified or item['risk_probability'] is None or item['latest_inventory'] is None or item['latest_inventory']<=0 or age is None or not 0<=age<300:continue
                eligible.append({**item,'name':s.name,'latitude':s.latitude,'longitude':s.longitude,'distance_m':d,'score':item['risk_probability']+.05*d/request.radius_m})
            eligible.sort(key=lambda r:(r['score'],r['distance_m'],r['station_id']))
            return {**{k:results[k] for k in ('base_at','requested_at')},'horizon_hours':request.horizon_hours,'radius_m':request.radius_m,
                'distance_type':'straight_line','automatic_radius_expansion':False,'recommended':eligible[0] if eligible else None,
                'alternatives':eligible[1:3],'abstained':not bool(eligible),'reason':None if eligible else 'no_station_within_radius' if not nearby else 'no_fresh_positive_eligible_prediction',
                'nearby_count':len(nearby),'eligible_count':len(eligible)}

def create_app(token,runtime=None,clock=lambda:datetime.now(timezone.utc)):
    if not token or len(token)<32:raise ValueError('Set CYCLEON_SERVICE_TOKEN to at least 32 characters')
    runtime=runtime or Runtime();service=Service(runtime,clock)
    bearer=HTTPBearer(auto_error=False)
    def auth(credentials=Depends(bearer)):
        if not credentials or not secrets.compare_digest(credentials.credentials,token):raise HTTPException(401,'service_authentication_required')
    app=FastAPI(title='CycleOn team C model service',version='1.0.0',dependencies=[Depends(auth)])
    app.state.service=service
    @app.get('/v1/status',response_model=ServiceStatus)
    def status():return service.status()
    @app.post('/v1/predictions',response_model=PredictionResponse)
    def predictions(request:PredictionRequest):return service.predict(request.station_ids,request.horizons_hours)
    @app.post('/v1/recommendations',response_model=RecommendationResponse)
    def recommendations(request:RecommendationRequest):return service.recommend(request)
    @app.post('/internal/ai-data/v1/snapshot',response_model=SnapshotAccepted)
    def snapshot(request:Snapshot):
        try:return service.ingest(request)
        except ValueError as e:raise HTTPException(422,str(e)) from None
    return app
