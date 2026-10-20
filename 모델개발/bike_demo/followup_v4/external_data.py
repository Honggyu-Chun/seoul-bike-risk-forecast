"""Bounded public-data ingestion. Credentials never enter logs or manifests."""
import argparse
import calendar
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from .common import ROOT,OUT,RECORD,save,now,digest

RAW=ROOT/'data/raw/followup_v4_external'

def keys():
    values={}
    path=ROOT/'.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1);values[k.strip()]=v.strip().strip('"').strip("'")
    return values

class Client:
    def __init__(self,budget=400):
        self.values=keys();self.budget=budget;self.calls=0;RAW.mkdir(parents=True,exist_ok=True)
        self.manifest=json.loads((RAW/'requests.json').read_text(encoding='utf-8')) if (RAW/'requests.json').exists() else []
    def request(self,url,label,params=None):
        if self.calls>=self.budget:raise RuntimeError('Per-run request budget reached; resume from saved files')
        self.calls+=1
        public_params={k:v for k,v in (params or {}).items() if k not in ('service','key','ServiceKey','serviceKey')}
        if params:url+='?'+urllib.parse.urlencode(params)
        request=urllib.request.Request(url,headers={'User-Agent':'PProjectAcademicResearch/1.0'})
        try:
            with urllib.request.urlopen(request,timeout=30) as response:
                body=response.read(12*1024*1024+1)
                if len(body)>12*1024*1024:raise RuntimeError('Response size limit exceeded')
                status=response.status
        except urllib.error.HTTPError as exc:
            error_body=exc.read(16384)
            for value in self.values.values():
                if value:error_body=error_body.replace(value.encode(),b'[REDACTED]')
            (RAW/(label+'_request_error.txt')).write_bytes(error_body)
            save(RAW/(label+'_request_error.json'),dict(status=exc.code,params=public_params,credential_values_omitted=True))
            raise RuntimeError(f'Public provider request failed with HTTP {exc.code}') from None
        except Exception:
            raise RuntimeError('Provider connection failed; URL and credentials omitted') from None
        for value in self.values.values():
            if value:body=body.replace(value.encode(),b'[REDACTED]')
        self.manifest.append(dict(label=label,status=status,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                                  params=public_params,received_at_utc=now(),snapshot_vintage='2026-10-20; not historical-as-issued'))
        save(RAW/'requests.json',self.manifest)
        time.sleep(.15)
        return body

def culture(client):
    key=client.values.get('SEOUL_API_KEY')
    if not key:raise RuntimeError('SEOUL_API_KEY is not configured')
    records=[];total=None
    for start in range(1,30001,1000):
        label=f'culture_{start:05d}'
        path=RAW/(label+'.json')
        if path.exists():j=json.loads(path.read_text(encoding='utf-8'))
        else:
            url=f'http://openapi.seoul.go.kr:8088/{urllib.parse.quote(key,safe="")}/json/aiViewCulture/{start}/{start+999}/'
            body=client.request(url,label);j=json.loads(body)
            path.write_bytes(body)
        service=j.get('aiViewCulture')
        if not service:
            code=j.get('RESULT',{}).get('CODE','unknown')
            raise RuntimeError(f'Seoul culture response code {code}; credential omitted')
        total=int(service['list_total_count']);rows=service.get('row',[]);records.extend(rows)
        print('CULTURE_PAGE',start,'rows',len(rows),'total',total,flush=True)
        if start+len(rows)>total or not rows:break
    unique={json.dumps([r.get(k) for k in ('TITLE','PLACE','STRTDATE','END_DATE','RGSTDATE')],ensure_ascii=False):r for r in records}
    save(RAW/'culture_all.json',list(unique.values()))
    save(RECORD/'culture_audit.json',dict(total_reported=total,rows_downloaded=len(records),unique_rows=len(unique),
         fields=list(records[0]) if records else [],snapshot_received_at_utc=now(),service='aiViewCulture',
         credential_values_omitted=True,historical_issuance_version_verified=False))

def xml_rows(body):
    root=ET.fromstring(body)
    if root.tag!='dbs':
        raise RuntimeError('KOPIS returned an unexpected XML service envelope; not treated as an empty dataset')
    return [{child.tag:child.text or '' for child in db if len(child)==0} for db in root.findall('.//db')]

def kopis(client,detail_limit=220):
    key=client.values.get('KOPIS_API_KEY')
    if not key:raise RuntimeError('KOPIS_API_KEY is not configured')
    unique={};base='https://kopis.or.kr/openApi/restful/'
    for month in range(1,7):
        last=calendar.monthrange(2025,month)[1]
        for page in range(1,21):
            label=f'kopis_list_2025{month:02d}_{page:02d}';path=RAW/(label+'.xml')
            params=dict(service=key,stdate=f'2025{month:02d}01',eddate=f'2025{month:02d}{last:02d}',cpage=page,rows=100,signgucode='11')
            if path.exists():body=path.read_bytes()
            else:body=client.request(base+'pblprfr',label,params);path.write_bytes(body)
            rows=xml_rows(body)
            for row in rows:
                if row.get('mt20id'):unique[row['mt20id']]=row
            print('KOPIS_LIST',month,page,'rows',len(rows),'unique',len(unique),flush=True)
            if len(rows)<100:break
    save(RAW/'kopis_list_2025h1.json',list(unique.values()))
    detail_dir=RAW/'kopis_details';detail_dir.mkdir(exist_ok=True)
    details=[];venue_ids=set()
    # Fixed chronological date/ID ordering; never select by target outcomes.
    # Round-robin start-date months, including ongoing older performances.
    buckets={}
    for pid in sorted(unique):
        date=unique[pid].get('prfpdfrom','').replace('.','-')
        bucket=date[:7] if date.startswith('2025') else 'ongoing_earlier'
        buckets.setdefault(bucket,[]).append(pid)
    ids=[]
    while len(ids)<min(detail_limit,len(unique)):
        for bucket in sorted(buckets):
            if buckets[bucket] and len(ids)<detail_limit:ids.append(buckets[bucket].pop(0))
    for i,pid in enumerate(ids):
        path=detail_dir/(pid+'.xml')
        if path.exists():body=path.read_bytes()
        else:body=client.request(base+'pblprfr/'+pid,'detail_'+pid,dict(service=key));path.write_bytes(body)
        rows=xml_rows(body)
        if rows:
            details.append(rows[0])
            if rows[0].get('mt10id'):venue_ids.add(rows[0]['mt10id'])
        if (i+1)%25==0:print('KOPIS_DETAILS',i+1,'/',len(ids),flush=True)
    venues=[];venue_dir=RAW/'kopis_venues';venue_dir.mkdir(exist_ok=True)
    for vid in sorted(venue_ids):
        path=venue_dir/(vid+'.xml')
        if path.exists():body=path.read_bytes()
        else:body=client.request(base+'prfplc/'+vid,'venue_'+vid,dict(service=key));path.write_bytes(body)
        rows=xml_rows(body)
        if rows:venues.append(rows[0])
    save(RAW/'kopis_details.json',details);save(RAW/'kopis_venues.json',venues)
    save(RECORD/'kopis_audit.json',dict(unique_performances=len(unique),details_received=len(details),venues_received=len(venues),
         detail_selection='round-robin scheduled-start months, IDs sorted within month; cap fixed before target comparison',detail_limit=detail_limit,
         snapshot_received_at_utc=now(),historical_issuance_version_verified=False,credential_values_omitted=True,
         schedule_fields=['prfpdfrom','prfpdto','dtguidance','prfruntime','frstregdt','updatedate','mt10id','mt13id'],
         venue_fields=['la','lo','seatscale','adres'],actual_attendance_used=False))
    print('KOPIS_COMPLETE',len(unique),len(details),len(venues),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--provider',choices=['culture','kopis'],required=True);p.add_argument('--detail-limit',type=int,default=220);args=p.parse_args()
    client=Client()
    if args.provider=='culture':culture(client)
    else:kopis(client,args.detail_limit)
