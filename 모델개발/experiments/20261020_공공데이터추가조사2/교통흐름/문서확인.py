import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import pathlib
import re
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).parent
OUT = ROOT / '공식문서'
OUT.mkdir(exist_ok=True)
SOURCES = [
 ('vol_meta','https://data.seoul.go.kr/dataList/OA-13316/A/1/datasetView.do'),
 ('vol_api','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-13316&srvType=A&infSeq=1'),
 ('vol_spot_api','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-13314&srvType=A&infSeq=1'),
 ('speed_api','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-13291&srvType=A&infSeq=1'),
 ('incident_api','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-13315&srvType=A&infSeq=1'),
 ('sub_arrival_api','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-12764&srvType=A&infSeq=1'),
 ('sub_position_api','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-12601&srvType=A&infSeq=1'),
 ('sub_month_api','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-12252&srvType=A&infSeq=1'),
 ('bus_month_api','https://data.seoul.go.kr/dataList/openApiView.do?infId=OA-12913&srvType=A&infSeq=1'),
 ('sub_congestion','https://data.seoul.go.kr/dataList/OA-12928/F/1/datasetView.do'),
 ('metro9_congestion','https://data.seoul.go.kr/dataList/OA-22197/F/1/datasetView.do'),
 ('bus_arrival','https://www.data.go.kr/data/15000314/openapi.do'),
 ('bus_position','https://www.data.go.kr/data/15000332/openapi.do'),
 ('metro_new_ridership','https://www.data.go.kr/data/15143845/openapi.do'),
 ('metro_timetable','https://www.data.go.kr/data/15143847/openapi.do'),
 ('bike_counter','https://data.seoul.go.kr/dataList/OA-23091/S/1/datasetView.do'),
 ('smart_crosswalk','https://data.seoul.go.kr/dataList/OA-15986/S/1/datasetView.do'),
 ('its_cctv','https://its.go.kr/opendata/opendataList?service=cctv'),
 ('topis_api','https://topis.seoul.go.kr/refRoom/openRefRoom_4.do'),
 ('tdata_home','https://t-data.seoul.go.kr/'),
]

def fetch(item):
    name,url=item
    stamp=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()
    rec={'source_id':name,'url':url,'checked_at':stamp,'method':'urllib.request GET; single attempt; public documentation'}
    try:
        with urllib.request.urlopen(url,timeout=20) as r:
            b=r.read(2_000_000)
            rec.update(http_status=r.status,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),final_url=r.url)
        text=b.decode('utf-8',errors='replace')
        text=re.sub(r'<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>','',text,flags=re.I|re.S)
        text=html.unescape(re.sub('<[^>]+>',' ',text))
        text=re.sub(r'[\t\r\f\v ]+',' ',text)
        text=re.sub(r'\n\s*\n','\n',text).strip()
        (OUT/(name+'.txt')).write_text(text,encoding='utf-8')
        rec.update(access_result='success',text_file=str(OUT/(name+'.txt')))
        return rec, text
    except Exception as e:
        rec.update(access_result='failed; no retry or bypass',error_type=type(e).__name__)
        if isinstance(e,urllib.error.HTTPError):rec['http_status']=e.code
        return rec,''

if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(fetch,SOURCES))
    (ROOT/'출처검증_문서원본.json').write_text(json.dumps([r for r,t in results],ensure_ascii=False,indent=2),encoding='utf-8')
    for r,t in results:
        print(r['source_id'],r['access_result'],r.get('http_status'),len(t))
        if r['source_id'].endswith('_api'):
            print(re.sub(r'\s+',' ',t)[:12000])
