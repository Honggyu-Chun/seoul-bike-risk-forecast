"""Verify the exact portable HTML opened by file URI with network blocked."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/local_runs/windows_20261020/offline_file';OUT.mkdir(parents=True,exist_ok=True)
errors=[];requests=[]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
    context=browser.new_context(viewport={'width':1440,'height':1000})
    context.route('**/*',lambda route:route.continue_() if route.request.url.startswith('file:') else route.abort())
    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
    page.goto((ROOT/'deliverables/따릉이_시민용_서비스데모_v2.html').as_uri(),wait_until='networkidle')
    expect(page.locator('.stock-hero strong')).to_be_visible()
    assert page.locator('.history-chart circle').count()==24
    page.locator('#station-search').fill('102');page.locator('#station-search').press('Enter')
    expect(page.locator('.detail-heading h2')).to_contain_text('망원역')
    page.locator('#map-city').click();assert page.locator('#map path').count()==25
    page.locator('[data-tab="results"]').click();expect(page.locator('#research-results')).to_contain_text('M2')
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    page.screenshot(path=str(OUT/'file_uri.png'),full_page=True)
    browser.close()
assert not errors,errors
(OUT/'checks.json').write_text(json.dumps({'file_uri_passed':True,'checks':['initial render','24 observed hours','station search','25 district polygons','M2 result table','no horizontal overflow'],'javascript_errors':errors,'network':'all non-file requests blocked','requested_urls':requests},ensure_ascii=False,indent=2),encoding='utf-8')
print('Offline file URI: 6 checks passed, no JavaScript errors.')
