"""Merge independently researched public-source catalogs into an offline report."""
from pathlib import Path
from collections import Counter
import csv
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DOMAINS = ['교통흐름', '공간시설', '인구행사']


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def esc(value):return html.escape(str(value), quote=True)


def source_group(row):
    found=re.findall(r'OA-\d+',row['official_url'])
    if found:return 'seoul:'+','.join(sorted(set(found)))
    address=urlsplit(row['official_url'])
    return address.netloc+address.path+('?' + address.query if address.query else '')


def main():
    rows=[];inputs=[]
    previous_text=''
    for previous in (ROOT.parent/'20261020_데이터방법론조사/sources.tsv',ROOT.parent/'20261020_후속실험_v4/API와추가데이터.md'):
        if previous.exists():previous_text+='\n'+previous.read_text(encoding='utf-8-sig')
    mentioned_ids=set(re.findall(r'OA-\d+',previous_text))
    for domain in DOMAINS:
        path=ROOT/domain/'후보목록.csv'
        if not path.exists():raise RuntimeError(f'Research not yet complete: {domain}')
        with path.open(encoding='utf-8-sig',newline='') as f:
            for source in csv.DictReader(f):
                row={k:(v or '').strip() for k,v in source.items() if k}
                for key in ('name','official_url','admissibility','next_action'):
                    if not row.get(key):raise RuntimeError(f'Missing {key}: {domain}/{row.get("name")}')
                if urlsplit(row['official_url']).scheme not in ('http','https'):
                    raise RuntimeError('Only official HTTP(S) reference links are accepted')
                row['research_domain']=domain
                row['agent_candidate_id']=row.get('candidate_id','')
                row['candidate_id']=domain+'-'+(row.get('candidate_id') or hashlib.sha256((row['name']+row['official_url']).encode()).hexdigest()[:8])
                row['source_group']=source_group(row)
                ids=set(re.findall(r'OA-\d+',row['official_url']))
                row['previous_catalog_mention']='이전 목록에서 언급함' if ids & mentioned_ids or row['official_url'] in previous_text else '이전 OA/URL 목록에서 일치 항목을 찾지 못함'
                rows.append(row)
        inputs.append(dict(path=str(path.relative_to(ROOT)),sha256=sha(path)))
    assert len({r['candidate_id'] for r in rows})==len(rows)
    columns=list(dict.fromkeys(k for row in rows for k in row))
    with (ROOT/'통합후보목록.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader();writer.writerows(rows)
    policy_path=ROOT/'통합선정.json'
    policy=json.loads(policy_path.read_text(encoding='utf-8')) if policy_path.exists() else {}
    groups=Counter(r['source_group'] for r in rows)
    summary=dict(generated_at_utc=datetime.now(timezone.utc).isoformat(),candidate_rows=len(rows),
                 unique_source_groups=len(groups),domain_rows=dict(Counter(r['research_domain'] for r in rows)),
                 shared_source_groups={k:n for k,n in groups.items() if n>1},inputs=inputs,
                 effect_proven_by_this_catalog=False,selection_policy_exists=bool(policy))
    (ROOT/'통합검수.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    opening=policy.get('overview','세 분야의 공공 원천을 병렬 조사한다. 영향 가능성은 가설이며 실제 개선은 동일 기간의 비교 실험으로 확인한다.')
    md=['# 공공데이터 추가 조사 통합보고서','',opening,'',
        f'교통·이동 {sum(r["research_domain"]=="교통흐름" for r in rows)}개, 공간·시설 {sum(r["research_domain"]=="공간시설" for r in rows)}개, 인구·행사 {sum(r["research_domain"]=="인구행사" for r in rows)}개로 총 {len(rows)}개 후보를 기록한다. 기존 자료의 재검토와 부적합 후보도 포함한다. 대표 출처 URL 묶음은 {len(groups)}개이며 서로 독립적인 신규 데이터의 개수를 뜻하지 않는다. 따릉이 OD·시간대·월별 집계와 같은 동일 원천 계열의 상관·중복을 별도 검토한다.','',
        '## 먼저 검토할 후보','']
    recommendations=policy.get('recommendations',[])
    top=[]
    for item in recommendations:
        md.extend(['### '+item['name'],'',item['reason'],'',item['next_action'],''])
        links=[]
        for i,url in enumerate(item.get('official_urls',[]),1):
            if urlsplit(url).scheme not in ('http','https'):raise RuntimeError('Invalid recommendation source')
            md.append(f'[공식 근거 {i}]({url})')
            links.append('<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">공식 근거 '+str(i)+'</a>')
        md.append('')
        top.append('<article class="recommend"><h3>'+esc(item['name'])+'</h3><p>'+esc(item['reason'])+'</p><p><b>다음 작업</b> '+esc(item['next_action'])+'</p><p>'+' · '.join(links)+'</p></article>')
    actions=policy.get('user_actions',[])
    md.extend(['## 키·신청·수집이 필요한 작업',''])
    for item in actions:md.append('- '+item)
    md.extend(['','## 후보 전체 목록',''])
    cards=[]
    labels=[('effect_hypothesis','영향 가설'),('evidence_level','근거 수준'),('temporal_resolution','시간 단위'),
            ('spatial_resolution','공간 단위'),('historical_coverage','과거 이력'),('live_access','현재 제공'),
            ('publication_or_update_lag','게시·갱신 지연'),('timestamps_available','확인 가능한 시각'),
            ('join_method','대여소 연결'),('key_or_approval','키·승인'),('cost_or_license','이용 조건'),('next_action','다음 작업')]
    for i,row in enumerate(rows):
        md.extend(['### '+row['name'],'',f'분야: {row["research_domain"]} / 판정: {row["admissibility"]} / 우선순위: {row.get("priority","")}',
                   '',f'공식 출처: [{row["name"]}]({row["official_url"]})',''])
        details=[]
        for key,label in labels:
            value=row.get(key,'')
            if not value:continue
            md.append('- '+label+': '+value)
            details.append('<dt>'+esc(label)+'</dt><dd>'+esc(value)+'</dd>')
        md.append('')
        search=' '.join(row.values()).lower()
        cards.append('<article class="candidate" data-domain="'+esc(row['research_domain'])+'" data-search="'+esc(search)+'"><header><span class="badge">'+esc(row['admissibility'])+'</span><span class="priority">'+esc(row.get('priority',''))+'</span><h3>'+esc(row['name'])+'</h3><p>'+esc(row.get('provider',''))+' · '+esc(row['research_domain'])+'</p></header><dl>'+''.join(details)+'</dl><a href="'+esc(row['official_url'])+'" target="_blank" rel="noopener noreferrer">공식 원천 확인</a></article>')
    (ROOT/'공공데이터_추가조사_통합보고서.md').write_text('\n'.join(md),encoding='utf-8')
    css='''body{margin:0;background:#f0f3f6;color:#24374a;font:16px/1.75 "Malgun Gothic",sans-serif}main{max-width:1120px;margin:30px auto;padding:34px;background:white}h1{font-size:30px}h2{margin-top:36px;border-top:1px solid #d6dfe7;padding-top:24px}h3{font-size:20px;margin:10px 0}p{margin:10px 0}.intro{max-width:1000px}.recommend{padding:15px 20px;background:#f0f6fb;border-left:4px solid #3d80ab;margin:15px 0}.toolbar{position:sticky;top:0;background:#fff;padding:15px 0;display:flex;gap:12px;flex-wrap:wrap;border-bottom:1px solid #d6dfe7}input,select{font:inherit;padding:9px;border:1px solid #a9bac9;border-radius:5px}input{flex:1;min-width:220px}.candidate{padding:24px 0;border-bottom:1px solid #d6dfe7}.badge{display:inline-block;padding:3px 9px;background:#edf2f7;font-size:14px}.priority{margin-left:12px;color:#5b7286;font-size:14px}dl{display:grid;grid-template-columns:145px 1fr;gap:8px 16px;margin:14px 0}dt{font-weight:bold;color:#486073}dd{margin:0;overflow-wrap:anywhere}a{color:#176c9d}.meta{color:#587084;font-size:14px}@media(max-width:600px){main{padding:20px;margin:0}dl{grid-template-columns:1fr;gap:3px}dd{margin-bottom:10px}}'''
    toolbar='<div class="toolbar"><input id="query" aria-label="후보 검색" placeholder="예: 교통량, 공개 지연, 초단기예보"><select id="domain" aria-label="분야 선택"><option value="">모든 분야</option>'+''.join('<option>'+d+'</option>' for d in DOMAINS)+'</select><span id="count" aria-live="polite"></span></div>'
    script='''const q=document.getElementById('query'),d=document.getElementById('domain'),cards=[...document.querySelectorAll('.candidate')];function filter(){let n=0;for(const c of cards){const show=(!d.value||c.dataset.domain===d.value)&&c.dataset.search.includes(q.value.trim().toLowerCase());c.hidden=!show;if(show)n++;}document.getElementById('count').textContent=n+'개 후보 표시';}q.addEventListener('input',filter);d.addEventListener('change',filter);filter();'''
    domain_text=' · '.join(d+' '+str(sum(r['research_domain']==d for r in rows))+'개' for d in DOMAINS)
    document='<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>따릉이 공공데이터 추가 조사</title><style>'+css+'</style><main><h1>따릉이 예측에 더할 공공데이터</h1><p class="intro">'+esc(opening)+'</p><p class="meta">'+esc(domain_text)+' · 총 '+str(len(rows))+'개 후보</p><p>기존 자료의 재검토와 부적합 후보도 포함한다. 대표 출처 URL 묶음 '+str(len(groups))+'개는 서로 독립적인 신규 데이터의 개수를 뜻하지 않는다. 따릉이 OD·시간대·월별 집계 등 같은 원천 계열의 중복을 별도 검토한다. 성능 개선 효과는 아직 가설로 구분한다.</p><h2>우선 검토할 후보</h2>'+''.join(top)+'<h2>키·신청·수집이 필요한 작업</h2><ul>'+''.join('<li>'+esc(a)+'</li>' for a in actions)+'</ul><h2>전체 후보 탐색</h2><p>영향 가능성이 있어도 과거 공개 시점이나 실제 제공 조건을 확인하기 전에는 모델에 자동 채택하지 않는다.</p>'+toolbar+''.join(cards)+'<p><a href="통합후보목록.csv">전체 후보 CSV</a> · <a href="공공데이터_추가조사_통합보고서.md">전체 보고서 원문</a></p></main><script>'+script+'</script></html>'
    (ROOT/'공공데이터_추가조사_통합보고서.html').write_text(document,encoding='utf-8')
    print('CATALOG_SAVED',len(rows),'rows',len(groups),'source groups')


if __name__=='__main__':main()
