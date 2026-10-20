"""Small static spatial join only; never reads inventory labels or training panels."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'feature_staging'
OUT.mkdir(exist_ok=True)
SOURCE=ROOT/'evidence'/'bus_stops_20241209.xlsx'
STATIONS=ROOT.parents[2]/'data'/'processed'/'rider_stations.parquet'
EARTH_RADIUS_M=6371008.8

bus=pd.read_excel(SOURCE,dtype=str)
stations=pd.read_parquet(STATIONS,columns=['station_id','latitude','longitude'])
assert len(stations)==2729 and stations.station_id.is_unique
assert bus.NODE_ID.notna().all() and bus.NODE_ID.is_unique
bus['longitude']=pd.to_numeric(bus['X좌표'],errors='raise')
bus['latitude']=pd.to_numeric(bus['Y좌표'],errors='raise')
for frame in [bus,stations]:
    assert np.isfinite(frame[['latitude','longitude']].to_numpy()).all()
    assert frame.latitude.between(-90,90).all() and frame.longitude.between(-180,180).all()
    # The actual values must be geographic degrees, not EPSG:5179 metre coordinates.
    assert frame.latitude.between(37,38).all() and frame.longitude.between(126,128).all()

def sphere(frame):
    lat,lon=np.radians(frame[['latitude','longitude']].to_numpy()).T
    return np.column_stack((np.cos(lat)*np.cos(lon),np.cos(lat)*np.sin(lon),np.sin(lat)))

def metrics(reference, suffix=''):
    tree=cKDTree(sphere(reference))
    points=sphere(stations)
    chord,index=tree.query(points,k=1,workers=1)
    columns={
        'nearest_bus_stop_distance_m'+suffix:2*EARTH_RADIUS_M*np.arcsin(np.clip(chord/2,0,1)),
        'nearest_bus_stop_node_id'+suffix:reference.iloc[index].NODE_ID.to_numpy(),
    }
    for radius in [300,500,1000]:
        limit=2*np.sin(radius/(2*EARTH_RADIUS_M))
        columns[f'bus_stop_count_{radius}m'+suffix]=tree.query_ball_point(points,limit,return_length=True,workers=1)
    return columns

# Primary facility counts omit the source's explicitly virtual stop category.
physical=bus[bus['정류소타입']!='가상정류장'].reset_index(drop=True)
features=stations.copy()
for key,value in {**metrics(physical),**metrics(bus,'_including_virtual')}.items():
    features[key]=value
features['bus_stop_snapshot_version']='2024-12-09'
features['catalog_file_modified_date']='2024-12-10'
features['first_publication_verified']=False
features['historical_received_at_verified']=False

assert len(features)==2729 and features.station_id.is_unique
assert (features.bus_stop_count_300m<=features.bus_stop_count_500m).all()
assert (features.bus_stop_count_500m<=features.bus_stop_count_1000m).all()
assert (features.nearest_bus_stop_distance_m>=0).all()

# Three independent great-circle calculations check KD-tree units/order.
for idx in [0,len(stations)//2,len(stations)-1]:
    slat,slon=np.radians(stations.iloc[idx][['latitude','longitude']].to_numpy(dtype=float))
    blat,blon=np.radians(physical[['latitude','longitude']].to_numpy()).T
    a=np.sin((blat-slat)/2)**2+np.cos(slat)*np.cos(blat)*np.sin((blon-slon)/2)**2
    distances=2*EARTH_RADIUS_M*np.arcsin(np.sqrt(np.clip(a,0,1)))
    assert np.isclose(distances.min(),features.iloc[idx].nearest_bus_stop_distance_m,atol=1e-6)
    for radius in [300,500,1000]:
        assert int((distances<=radius).sum())==int(features.iloc[idx][f'bus_stop_count_{radius}m'])

csv_path=OUT/'station_busstop_static_20241209.csv'
assert not csv_path.exists(), 'Refuse overwrite'
features.to_csv(csv_path,index=False,encoding='utf-8-sig')
audit={
    'source_url':'https://data.seoul.go.kr/dataList/OA-15067/A/1/datasetView.do',
    'source_file':'서울시버스정류소위치정보(20241209).xlsx',
    'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    'source_bytes':SOURCE.stat().st_size,
    'file_version_date':'2024-12-09',
    'catalog_date_column_name':'수정일',
    'catalog_file_modified_date':'2024-12-10',
    'historical_first_publication_verified':False,
    'historical_received_at_verified':False,
    'source_current_portal_snapshot_cannot_prove_unchanged_archived_file':True,
    'source_rows':len(bus),'primary_reference_rows':len(physical),
    'virtual_rows_excluded_from_primary':int((bus['정류소타입']=='가상정류장').sum()),
    'duplicate_node_ids':int(bus.NODE_ID.duplicated().sum()),
    'duplicate_ars_ids':int(bus.ARS_ID.duplicated().sum()),
    'duplicate_coordinate_rows':int(bus[['latitude','longitude']].duplicated().sum()),
    'id_semantics':'Counts distinct NODE_ID rows. Different co-located IDs remain separate. Leading-zero ARS_ID retained as text.',
    'source_columns':bus.columns[:6].tolist(),
    'coordinate_audit':{
        'portal_statement':'WGS84 (EPSG-5179)',
        'portal_statement_inconsistent':True,
        'interpretation':'Actual X values are longitude degrees and Y values latitude degrees. Treat as WGS84 geographic longitude/latitude, EPSG:4326; do not transform these numeric degrees from EPSG:5179.',
        'longitude_min':float(bus.longitude.min()),'longitude_max':float(bus.longitude.max()),
        'latitude_min':float(bus.latitude.min()),'latitude_max':float(bus.latitude.max()),
        'missing_or_nonfinite_coordinates':0,
        'out_of_geographic_range':0,
        'loose_seoul_bbox_note':'Seoul registry includes peripheral/outside-city stops; retained for border proximity. No claim that every point lies inside municipal boundary.'
    },
    'distance_method':'Great-circle distance on sphere radius 6371008.8m. KD-tree unit-sphere chord converted to arc; not walking/riding network distance.',
    'station_input':str(STATIONS),'station_rows':len(stations),'station_id_duplicates':0,
    'output_rows':len(features),'output_file':str(csv_path),
    'output_sha256':hashlib.sha256(csv_path.read_bytes()).hexdigest(),
    'metric_summary':features[['nearest_bus_stop_distance_m','bus_stop_count_300m','bus_stop_count_500m','bus_stop_count_1000m']].describe().to_dict(),
    'verification':'Shape and unique IDs; numeric geographic ranges; monotonic radius counts; three brute-force Haversine checks passed.',
    'meaning':'정류소 위치와 주변 정류소 엔터티 수이다. 노선수·배차·승객수·정류소 혼잡도·실시간 환승수요가 아니다.',
    'admissibility':'Conditional static exploratory feature; snapshot filename/portal modified date predate 2025. First publication, historical receipt, unchanged archive, and all 2025 opening/closure changes are not proven. Do not call retrospective real-time performance verified.',
    'labels_read':False,'model_training_run':False,'existing_queue_or_v4_changed':False,
}
(OUT/'bus_static_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:audit[k] for k in ['source_rows','primary_reference_rows','virtual_rows_excluded_from_primary','duplicate_node_ids','duplicate_ars_ids','duplicate_coordinate_rows','output_rows']},ensure_ascii=False))
