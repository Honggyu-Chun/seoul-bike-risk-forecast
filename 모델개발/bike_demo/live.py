"""Read the documented Seoul API. Never expose credentials or invent freshness."""
from datetime import datetime,timezone
import json
import copy
import os
import re
import threading
import time
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo
from .data import ROOT

_lock=threading.Lock();_cache=None;_cached_at=0.0


def fetch_live(force=False):
    global _cache,_cached_at
    with _lock:
        if _cache is not None and time.monotonic()-_cached_at<30:
            return {**copy.deepcopy(_cache),'reused_cache':True}
        key=os.environ.get('SEOUL_API_KEY','').strip()
        if key and (len(key)>120 or not re.fullmatch(r'[A-Za-z0-9_-]+',key)):
            raise RuntimeError('서버 인증키 형식을 확인해 주세요. 키 값은 공개하지 않습니다.')
        credential=key or 'sample'
        rows=[];pages=0
        started=datetime.now(timezone.utc)
        for start in range(1,10001,1000):
            end=start+999 if key else 5
            url=f'http://openapi.seoul.go.kr:8088/{credential}/json/bikeList/{start}/{end}/'
            try:
                request=urllib.request.Request(url,headers={'User-Agent':'SeoulBikeResearchDemo/1.0'})
                with urllib.request.urlopen(request,timeout=10) as response:
                    if response.status!=200:raise RuntimeError('공식 수량 API의 정상 응답을 받지 못했습니다.')
                    body=response.read(2_500_001)
                    if len(body)>2_500_000:raise RuntimeError('예상한 API 응답 크기를 초과했습니다.')
                parsed=json.loads(body)
            except urllib.error.HTTPError as error:
                raise RuntimeError(f'공식 수량 API 호출 실패(HTTP {error.code}). 과거 검증 모드를 이용할 수 있습니다.') from None
            except (urllib.error.URLError,TimeoutError,OSError,ValueError):
                raise RuntimeError('공식 수량 API에 연결하지 못했습니다. 네트워크·제공기관 응답을 확인해 주세요.') from None
            data=parsed.get('rentBikeStatus',{})
            result=data.get('RESULT',parsed.get('RESULT',{}))
            if result.get('CODE')!='INFO-000':
                raise RuntimeError('공식 API가 정상 데이터 코드를 반환하지 않았습니다. 인증·호출 조건을 확인해 주세요.')
            page=data.get('row',[]);pages+=1
            for row in page:
                match=re.match(r'^\s*(\d+)\.',str(row.get('stationName','')))
                try:
                    quantity=int(row['parkingBikeTotCnt']);lat=float(row['stationLatitude']);lon=float(row['stationLongitude'])
                except (ValueError,TypeError,KeyError):continue
                if quantity<0 or not (37<lat<38 and 126<lon<128):continue
                rows.append({'station_id':int(match.group(1)) if match else None,'api_station_id':str(row.get('stationId','')),
                             'name':str(row.get('stationName','')),'quantity':quantity,'latitude':lat,'longitude':lon})
            if not key or len(page)<1000:break
        received=datetime.now(timezone.utc)
        packet={'mode':'live','source_kind':'api_current_snapshot','scope':'production_key' if key else 'public_sample_5',
                'requested_at':started.isoformat(),'received_at':received.isoformat(),'received_kst':received.astimezone(ZoneInfo('Asia/Seoul')).isoformat(),
                'source_observed_at':None,'pages':pages,'rows':rows,'count':len(rows),'reused_cache':False,
                'source_url':'https://data.seoul.go.kr/dataList/OA-15493/A/1/datasetView.do',
                'limitations':['대여소별 원천 관측시각은 응답에 없어 수신시각과 구분합니다.',
                               '공개 샘플은 5곳만 제공하며 서울 전체로 해석하지 않습니다.',
                               '실시간 기본 모형의 입력 실행 시연이며 현재 계절·5~10분 뒤 정확도 검증이 아닙니다.']}
        folder=ROOT/'data/processed/live_snapshots';folder.mkdir(parents=True,exist_ok=True)
        (folder/(received.strftime('%Y%m%dT%H%M%S')+'.json')).write_text(json.dumps(packet,ensure_ascii=False,indent=2))
        _cache=packet;_cached_at=time.monotonic()
        return copy.deepcopy(packet)
