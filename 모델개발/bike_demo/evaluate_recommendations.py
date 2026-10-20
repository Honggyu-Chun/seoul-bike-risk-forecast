"""Identical archived queries, abstention-aware recommendation evaluation."""
import json
import numpy as np
import pandas as pd
from sklearn.neighbors import BallTree
from .data import ROOT,EARTH_M,load_protocol
from .recommend import distances_m,choose_station


def main():
    out=ROOT/'artifacts/evaluation';protocol=load_protocol();rule=protocol['recommendation']
    stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    predictions=pd.read_parquet(out/'test_predictions.parquet')
    meta=json.loads((ROOT/'artifacts/models/metadata.json').read_text())
    rng=np.random.default_rng(rule['seed'])
    indices=rng.choice(len(stations),size=min(rule['origin_count'],len(stations)),replace=False)
    origins=stations.iloc[indices][['station_id','name','latitude','longitude']].copy().reset_index(drop=True)
    # Positions are constructed from training-cohort locations, without seeing test outcomes.
    angle=rng.uniform(0,2*np.pi,len(origins));offset=rng.uniform(50,140,len(origins))
    origins['latitude']+=np.sin(angle)*offset/111_195
    origins['longitude']+=np.cos(angle)*offset/(111_195*np.cos(np.radians(origins.latitude)))
    origins['origin_id']=np.arange(len(origins))
    origins.to_json(out/'evaluation_origins.json',orient='records',force_ascii=False,indent=2)
    coordinates=np.radians(stations[['latitude','longitude']].to_numpy())
    candidates=BallTree(coordinates,metric='haversine').query_radius(np.radians(origins[['latitude','longitude']].to_numpy()),r=rule['candidate_radius_m']/EARTH_M)
    commute=predictions.input_time.dt.hour.isin(rule['input_hours'])
    if rule['weekdays_only']:commute &= predictions.input_time.dt.dayofweek.lt(5)
    records=[];skipped=0;attempted=0
    for timestamp,part in predictions[commute].groupby('input_time',sort=True):
        part=part.set_index('station_code')
        for origin_index,codes in enumerate(candidates):
            attempted+=1
            part_codes=np.intersect1d(codes,part.index.to_numpy())
            local=part.loc[part_codes]
            local=local[local.quantity.gt(0)].copy()
            if local.empty:skipped+=1;continue
            origin=origins.iloc[origin_index]
            loc=stations.iloc[local.index]
            d=distances_m(origin.latitude,origin.longitude,loc.latitude,loc.longitude)
            sid=local.station_id.to_numpy();q=local.quantity.to_numpy()
            choices={
                'R0':choose_station(sid,q,d),
                'R1':choose_station(sid,q,d,local.p_B1.to_numpy(),meta['models']['B1']['threshold']),
                'R2':choose_station(sid,q,d,local.p_M1.to_numpy(),meta['models']['M1']['threshold']),
            }
            base=choices['R0']
            record={'input_time':str(timestamp),'target_time':str(timestamp+pd.Timedelta(hours=1)),'origin_id':int(origin.origin_id),
                    'latitude':float(origin.latitude),'longitude':float(origin.longitude),'origin_near':origin['name'],'eligible_candidates':len(local)}
            for model,index in choices.items():
                record.update({f'{model}_station_id':None if index is None else int(sid[index]),
                    f'{model}_distance_m':None if index is None else float(d[index]),
                    f'{model}_target_quantity':None if index is None else float(local.target_quantity.iloc[index]),
                    f'{model}_valid':int(index is not None and local.target_quantity.iloc[index]>=1),
                    f'{model}_recommended':int(index is not None)})
            records.append(record)
    frame=pd.DataFrame(records);frame.to_parquet(out/'recommendation_queries.parquet',index=False)
    summaries={}
    for model in ['R0','R1','R2']:
        chosen=frame[f'{model}_recommended'].eq(1);good=frame[f'{model}_valid'].eq(1)
        common=chosen&frame.R0_recommended.eq(1)
        summaries[model]={'requests':len(frame),'recommended':int(chosen.sum()),'valid_recommendations':int(good.sum()),
            'effective_rate':float(good.mean()),'hit_rate':float(good[chosen].mean()) if chosen.any() else None,
            'coverage':float(chosen.mean()),'abstention_rate':float(1-chosen.mean()),
            'mean_distance_m':float(frame.loc[chosen,f'{model}_distance_m'].mean()) if chosen.any() else None,
            'paired_extra_distance_vs_R0_m':float((frame.loc[common,f'{model}_distance_m']-frame.loc[common,'R0_distance_m']).mean()) if common.any() else None}
    delta=(summaries['R2']['effective_rate']-summaries['R0']['effective_rate'])*100
    result={'protocol':rule,'attempted_queries':attempted,'common_eligible_queries':len(frame),'excluded_no_eligible_current_positive_candidate':skipped,
            'metrics':summaries,'r2_minus_r0_effective_pp':delta,'r2_minus_r1_effective_pp':100*(summaries['R2']['effective_rate']-summaries['R1']['effective_rate']),
            'goal_verdict':{'effective_5pp':delta>=5,'paired_extra_distance_100m':summaries['R2']['paired_extra_distance_vs_R0_m'] is not None and summaries['R2']['paired_extra_distance_vs_R0_m']<=100},
            'interpretation':'Constructed archived origin/time queries; available quantity at a later source record is a proxy, not observed rider rental success.'}
    (out/'recommendation_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    good=frame[frame.R2_valid.eq(1)&frame.R0_valid.eq(0)].head(5)
    bad=frame[frame.R2_valid.eq(0)&frame.R0_valid.eq(1)].head(5)
    (out/'recommendation_cases.json').write_text(json.dumps({'helped':json.loads(good.to_json(orient='records',force_ascii=False)),
                                                          'hurt':json.loads(bad.to_json(orient='records',force_ascii=False))},ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
