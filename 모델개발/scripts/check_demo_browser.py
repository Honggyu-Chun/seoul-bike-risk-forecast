"""Exercise the citizen demo in Chromium, including faults and offline parity.

Run against the local server. The live test requests the official public sample
once; fault cases are deliberately injected by Playwright and logged as such.
"""
import argparse
import json
import urllib.request
import os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser()
parser.add_argument('--base-url',default='http://127.0.0.1:8000')
parser.add_argument('--browser-executable',default=os.environ.get('BIKE_BROWSER_EXECUTABLE','/usr/bin/chromium'))
parser.add_argument('--output-dir',type=Path,default=None)
args=parser.parse_args()
ROOT=Path(__file__).resolve().parents[1];OUT=args.output_dir or ROOT/'artifacts/browser/v2';OUT.mkdir(parents=True,exist_ok=True)
passed=[];errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=args.browser_executable,args=['--no-sandbox','--disable-dev-shm-usage'])
    context=browser.new_context(viewport={'width':1512,'height':1080},accept_downloads=True)
    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(args.base_url,wait_until='networkidle')
    expect(page.locator('.stock-hero strong')).to_be_visible()
    assert page.locator('.station-row').count()==8
    assert page.locator('.history-chart circle').count()==24
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    assert page.evaluate("risk({quantity:1,score_M1:.35,risk_M1:false})") is False
    page.screenshot(path=str(OUT/'desktop.png'),full_page=True)
    passed.append('initial real replay, 24 observed hours, precision-safe risk flag, bounded candidate list')

    page.locator('[data-action="load-more"]').click();assert page.locator('.station-row').count()==16
    page.locator('#station-search').fill('102');page.locator('#station-search').press('ArrowDown');page.locator('#station-search').press('Enter')
    expect(page.locator('.detail-heading h2')).to_contain_text('망원역')
    first_id=page.evaluate('state.activeStation')
    page.locator('#station-detail [data-favorite]').click();assert page.locator('#favorite-count').inner_text()=='1'
    page.reload(wait_until='networkidle');expect(page.locator('#favorite-count')).to_have_text('1')
    page.locator('#favorites-open').click();expect(page.locator('#favorites-list')).to_contain_text('망원역')
    page.locator('[data-visit]').click();expect(page.locator('.detail-heading h2')).to_contain_text('망원역')
    passed.append('keyboard search, favorite persistence, saved-station navigation')

    page.locator('[data-filter="available"]').click()
    quantities=page.locator('.quantity-bubble').all_text_contents();assert all(int(x)>0 for x in quantities)
    page.locator('#sort').select_option('quantity')
    quantities=[int(x) for x in page.locator('.quantity-bubble').all_text_contents()];assert quantities==sorted(quantities,reverse=True)
    with page.expect_download() as download_info:page.locator('#download-csv').click()
    download=download_info.value;download.save_as(str(OUT/'candidates.csv'));assert (OUT/'candidates.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    page.locator('#share-view').click();shared=page.locator('#share-url').input_value();assert 'lat=' in shared and 'at=' in shared
    page.locator('[data-close="share-dialog"]').click()
    shared_page=context.new_page();shared_page.goto(shared,wait_until='networkidle')
    expect(shared_page.locator('.detail-heading h2')).to_contain_text('망원역');shared_page.close()
    passed.append('quantity filter, ordering, CSV download, shared settings restore')

    page.locator('#map-city').click();expect(page.locator('#map-subtitle')).to_contain_text('2013')
    assert page.locator('#map path').count()==25
    expect(page.locator('#map-legend')).to_contain_text('위험 판정 아님')
    page.screenshot(path=str(OUT/'city.png'),full_page=True)
    page.locator('#map-near').click()
    old=page.evaluate('state.at');page.locator('#time-next').click();page.wait_for_function('state.snapshot.input_time===state.at')
    assert page.evaluate('state.at')!=old
    page.locator('#time-play').click();page.wait_for_timeout(1500);page.locator('#time-play').click();assert not page.evaluate('state.playing')
    passed.append('25 real district polygons, city clusters, hourly step and playback pause')

    previous=page.evaluate('state.snapshot.input_time')
    page.route('**/api/snapshot?*',lambda route:route.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'검증용 과거 조회 장애'})))
    page.locator('#time-next').click();expect(page.locator('#alert')).to_contain_text('직전 조회에 성공한 조건')
    assert page.evaluate('state.at')==previous
    page.unroute('**/api/snapshot?*')
    passed.append('injected replay failure restores matching prior controls instead of mislabeling old data')

    page.locator('#scenarios-open').click();page.locator('[data-scenario="0"]').click()
    expect(page.locator('#recommendation-cards')).to_contain_text('다음 기록 0대')
    assert page.locator('#show-truth').is_checked()
    page.locator('#station-detail [data-model="B1"]').click();assert page.evaluate('state.model')=='B1'
    page.locator('#scenarios-open').click();page.locator('[data-case-kind="hurt"]').click();page.locator('[data-scenario="0"]').click()
    page.screenshot(path=str(OUT/'failure_case.png'),full_page=True)
    passed.append('helped and harmed actual cases, outcome switch, B1/M1 inspection')

    page.locator('#tour-start').click()
    for _ in range(4):page.locator('#tour-next').click();page.wait_for_timeout(250)
    expect(page.locator('#results')).to_be_visible()
    expect(page.locator('#recommendation-note')).to_contain_text('5건 적었습니다')
    with urllib.request.urlopen(args.base_url+'/api/research',timeout=15) as response:research=json.load(response)
    if research['status']=='evaluated':
        expect(page.locator('#research-results')).to_contain_text('2025년 4월 15일~5월 14일')
        expect(page.locator('#research-results')).to_contain_text('Rq · 수량과 거리')
        expect(page.locator('#research-results')).to_contain_text('R1u · 기본 AI와 거리')
        assert page.locator('#research-results table').count()==2
        expected=page.evaluate('(value)=>pct(value)',research['results']['metrics']['M2']['f1'])
        expect(page.locator('#research-results table').first).to_contain_text(expected)
        page.locator('.research-details summary').click()
        expect(page.locator('.research-details')).to_contain_text('학교가 정한 합격선이 아닙니다')
        if research['report_ready']:
            with page.expect_download() as evidence:page.locator('a[href="/api/research/report"]').click()
            evidence.value.save_as(str(OUT/'research_report.docx'))
            assert (OUT/'research_report.docx').read_bytes().startswith(b'PK')
        passed.append('new-period model and service tables, measured F1 matches API, explicit team-goal scope, real Word download')
    elif research['status']=='search_in_progress':
        expect(page.locator('#research-results')).to_contain_text('개발 검증 점수')
        passed.append('unfinished research is explicitly development validation, not new final test accuracy')
    page.screenshot(path=str(OUT/'results.png'),full_page=True)
    page.locator('#tour-next').click();expect(page.locator('#tour-bar')).to_be_hidden()
    passed.append('five-stage presentation walkthrough and honest aggregate evidence')

    page.locator('[data-tab="explore"]').click()
    page.locator('#live-mode').click();expect(page.locator('#live-status')).to_contain_text('수신 완료',timeout=30000)
    assert page.locator('.station-row').count()==5
    expect(page.locator('#station-detail')).to_contain_text('B1S')
    expect(page.locator('#mode-caption')).to_contain_text('미검증')
    assert page.locator('[data-radius="500"]').is_disabled()
    page.locator('#auto-live').check();assert page.evaluate('liveTimer!==null')
    page.locator('[data-tab="results"]').click();assert page.evaluate('liveTimer===null')
    page.locator('[data-tab="explore"]').click();assert page.evaluate('liveTimer!==null')
    page.locator('#auto-live').uncheck();assert page.evaluate('liveTimer===null')
    page.evaluate('''() => { const realNow=Date.now; Date.now=()=>realNow()+125000; updateReceiptAge(); Date.now=realNow; }''')
    expect(page.locator('#live-status')).to_contain_text('재조회 권장')
    page.evaluate('renderLive()')
    page.screenshot(path=str(OUT/'live.png'),full_page=True)
    received=page.evaluate('state.live.received_kst')
    page.route('**/api/live',lambda route:route.fulfill(status=502,content_type='application/json',body=json.dumps({'detail':'검증용 주입: 일시적 API 장애'})))
    page.locator('#refresh-live').click();expect(page.locator('#live-status')).to_contain_text('갱신 실패')
    assert page.evaluate('state.live.received_kst')==received
    expect(page.locator('.stock-hero')).to_have_class(__import__('re').compile('live-stale'))
    page.unroute('**/api/live')
    page.locator('#replay-mode').click();expect(page.locator('.history-chart')).to_be_visible()
    passed.append('official five-row live input, auto-refresh pauses away from tab, simulated receipt aging, injected failure retains visibly stale receipt')

    mobile=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
    phone=mobile.new_page();phone.on('pageerror',lambda e:errors.append(str(e)));phone.goto(args.base_url,wait_until='networkidle')
    assert not phone.evaluate('document.documentElement.scrollWidth>innerWidth')
    phone.locator('#scenarios-open').click();phone.locator('[data-scenario="0"]').click()
    phone.wait_for_function('state.snapshot.origin.latitude===state.origin.latitude')
    assert not phone.evaluate('document.documentElement.scrollWidth>innerWidth')
    phone.screenshot(path=str(OUT/'mobile.png'),full_page=True)
    phone.locator('[data-tab="results"]').click()
    if research['status']=='evaluated':expect(phone.locator('#research-results')).to_contain_text('M2 · 후속 제안 모델')
    assert not phone.evaluate('document.documentElement.scrollWidth>innerWidth')
    mobile.close();passed.append('390px touch layout, case selection, no horizontal overflow')

    # Fresh context, no warm online cache: the exact exported HTML is the only
    # supplied resource, and all real network is blocked before first render.
    portable=(ROOT/'deliverables/따릉이_시민용_서비스데모_v2.html').read_text()
    offline_context=browser.new_context(viewport={'width':1512,'height':1080},accept_downloads=True)
    offline=offline_context.new_page();offline.on('pageerror',lambda e:errors.append(str(e)))
    virtual_origin='https://offline-demo.invalid/'
    offline.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html; charset=utf-8',body=portable) if route.request.url==virtual_origin and route.request.is_navigation_request() else route.abort())
    offline.goto(virtual_origin,wait_until='networkidle')
    expect(offline.locator('.history-chart')).to_be_visible()
    offline.route('**/*',lambda route:route.abort())
    offline.locator('#station-search').fill('102');offline.locator('#station-search').press('Enter')
    expect(offline.locator('.detail-heading h2')).to_contain_text('망원역')
    assert offline.locator('.history-chart circle').count()==24
    offline.locator('#map-city').click();assert offline.locator('#map path').count()==25
    offline.locator('#scenarios-open').click();offline.locator('[data-scenario="0"]').click()
    expect(offline.locator('#recommendation-cards')).to_contain_text('다음 기록 0대')
    offline.screenshot(path=str(OUT/'offline.png'),full_page=True)
    assert offline.locator('#live-mode').is_disabled()
    for lat,lon,radius in [(37.5665,126.978,500),(37.55565,126.91063,1000),(37.4979,127.0276,1500)]:
        path=f'/api/snapshot?at=2025-03-20T07:00&lat={lat}&lon={lon}&radius={radius}'
        with urllib.request.urlopen(args.base_url+path,timeout=15) as response:online=json.load(response)
        local=offline.evaluate('(path)=>offlineApi(path)',path)
        assert [r['station_id'] for r in local['stations']]==[r['station_id'] for r in online['stations']]
        for method in ['R0','R1','R2']:
            assert (local['recommendations'][method] or {}).get('station_id')==(online['recommendations'][method] or {}).get('station_id')
    passed.append('offline/server exact candidate ordering and all three recommendations agree at three regions and radii')
    passed.append('exact exported HTML in a fresh context: first render, search, 24h context, city geometry and cases without any real network')
    if research['status']=='evaluated':
        offline.locator('[data-tab="results"]').click()
        expect(offline.locator('#research-results')).to_contain_text('Rq · 수량과 거리')
        assert offline.locator('a[href="/api/research/report"]').count()==0
        passed.append('new-period measured tables embedded offline without a nonworking server download link')
    offline_context.close();context.close();browser.close()
assert not errors,errors
report={'passed':passed,'javascript_errors':errors,'faults':'replay 503 and live 502 intentionally injected; a temporary client clock shift tests receipt aging; official sample was received separately','offline_scope':'exact disk HTML supplied at a reserved virtual origin in a fresh context; all other network blocked before initial rendering. Actual file-protocol navigation not verified in managed browser.','fresh_machine_install':'not tested; current pinned environment used'}
(OUT/'checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))
