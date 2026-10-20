"""Only small official metadata, public sample rows, and one archived bus-stop file.

No model training, no labels, no private credentials. All writes stay beside this script.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / 'evidence'
EVIDENCE.mkdir(parents=True, exist_ok=True)

PAGES = {
    'bus_catalog': 'https://data.seoul.go.kr/dataList/OA-15067/A/1/datasetView.do',
    'walk_catalog': 'https://data.seoul.go.kr/dataList/OA-21208/S/1/datasetView.do',
    'zoning_catalog': 'https://data.seoul.go.kr/dataList/OA-21136/S/1/datasetView.do',
    'park_catalog': 'https://data.seoul.go.kr/dataList/OA-394/S/1/datasetView.do',
    'parking_catalog': 'https://data.seoul.go.kr/dataList/OA-13122/S/1/datasetView.do',
    'attract_catalog': 'https://data.seoul.go.kr/dataList/OA-15580/S/1/datasetView.do',
    'jobs_catalog': 'https://data.seoul.go.kr/dataList/OA-22184/S/1/datasetView.do',
    'shops_catalog': 'https://data.seoul.go.kr/dataList/OA-22172/S/1/datasetView.do',
    'commercial_boundary_catalog': 'https://data.seoul.go.kr/dataList/OA-15560/S/1/datasetView.do',
    'commercial_release_policy': 'https://golmok.seoul.go.kr/introduce.do',
    'cycle_official_announcement': 'https://news.seoul.go.kr/traffic/archives/509372',
    'dem_catalog': 'https://www.data.go.kr/data/15059920/fileData.do',
    'dem_tile_metadata': 'https://www.data.go.kr/data/15067637/fileData.do',
    'subway_geom_schema': 'https://t-data.seoul.go.kr/category/dataviewopenapi.do?data_id=1036',
    'subway_geom_catalog': 'https://t-data.seoul.go.kr/dataprovide/trafficdataviewfile.do?data_id=36',
    'school_schema': 'https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17020190531110010104913&infSeq=2',
    'university_catalog': 'https://www.data.go.kr/data/15107736/standard.do',
    'tourism_catalog': 'https://www.data.go.kr/data/15021141/standard.do',
    'census_schema': 'https://sgis.mods.go.kr/developer/html/newOpenApi/api/dataApi/census.html',
    'ultrashort_catalog': 'https://www.data.go.kr/data/15084084/openapi.do',
    'ultrashort_official_explanation': 'https://data.kma.go.kr/community/nuriLovePopup.do',
    'warning_schema': 'https://apihub.kma.go.kr/apiList.do?seqApi=10&seqApiSub=288',
    'warning_catalog': 'https://www.data.go.kr/data/15000415/openapi.do',
    'radar_schema': 'https://apihub.kma.go.kr/apiList.do?seqApi=5',
    'flood_link_catalog': 'https://www.data.go.kr/data/15132903/openapi.do',
}
for code in ['OA-15067','OA-21208','OA-394','OA-13122','OA-15580','OA-22184','OA-22172','OA-21136']:
    PAGES[code + '_schema'] = 'https://data.seoul.go.kr/dataList/openApiView.do?' + urllib.parse.urlencode({'infId':code,'srvType':'A','infSeq':1})

manifest=[]
def fetch(name, url, suffix='.html', payload=None, cap=1_500_000):
    rec={'name':name,'url':url,'received_at':datetime.now(timezone.utc).isoformat(),'request_contains_private_key':False}
    try:
        req=urllib.request.Request(url,data=payload,headers={'User-Agent':'research-metadata-audit/1.0'})
        with urllib.request.urlopen(req,timeout=20) as response:
            raw=response.read(cap+1)
            if len(raw)>cap:
                raise ValueError('response exceeds small-download cap')
            rec.update(http_status=response.status,content_type=response.headers.get('Content-Type'),bytes=len(raw))
        out=EVIDENCE/(name+suffix)
        if out.exists():
            raise FileExistsError('refuse overwrite')
        out.write_bytes(raw)
        rec.update(path=str(out.relative_to(ROOT)),sha256=hashlib.sha256(raw).hexdigest(),success=True)
        return raw
    except Exception as exc:
        rec.update(success=False,error_type=type(exc).__name__,error=str(exc)[:200])
        return None
    finally:
        manifest.append(rec)

for name,url in PAGES.items():
    fetch(name,url)

# The public literal 'sample' is an official demonstration credential, not a user key.
for name,service in [('bus_sample','busStopLocationXyInfo'),('walk_sample','TbTraficWlkNet'),('park_sample','SearchParkInfoService')]:
    url='http://openapi.seoul.go.kr:8088/sample/json/'+service+'/1/1/'
    fetch(name,url,'.json',cap=50_000)
fetch('school_sample','https://open.neis.go.kr/hub/schoolInfo?Type=json&pIndex=1&pSize=1&ATPT_OFCDC_SC_CODE=B10','.json',cap=50_000)

# Catalog row: archive 20241209, sequence 37, displayed *modified* date 2024-12-10.
# It is not proof of first publication or historical receipt.
payload=urllib.parse.urlencode({'infId':'OA-15067','seqNo':'','seq':'37','infSeq':'1'}).encode()
fetch('bus_stops_20241209','https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false','.xlsx',payload,cap=10_000_000)
(ROOT/'small_fetch_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'requests':len(manifest),'successes':sum(x['success'] for x in manifest),'failures':[{k:r.get(k) for k in ['name','error_type','error']} for r in manifest if not r['success']]},ensure_ascii=False))
