"""Export a self-contained, finite archived demo that opens without Python or network."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import re
import pandas as pd
from bike_demo.api import overview,store,cases,quantity_history,research

root=Path(__file__).resolve().parents[1]
db=store();summary=overview();examples=cases()
timestamps={f'2025-03-{day}T{hour}:00:00' for day in ['20','21'] for hour in ['07','08','18','19']}
for group in examples.values():
    timestamps.update(row['input_time'].replace(' ','T') for row in group)
snapshots={}
histories={};panel=quantity_history()
for at in sorted(timestamps):
    t=pd.Timestamp(at)
    if t not in db['groups']:continue
    part=db['data'].iloc[db['groups'][t]]
    snapshots[t.isoformat()]=[[int(r.station_id),int(r.quantity),int(r.target_quantity),float(r.p_B1),float(r.p_M1)] for r in part.itertuples()]
    index=panel['times'].get_indexer([t])[0]
    start=max(0,index-23)
    values=panel['quantity'][start:index+1]
    histories[t.isoformat()]={'times':[x.isoformat() for x in panel['times'][start:index+1]],
                             'rows':[[None if not pd.notna(v) else int(v) for v in row] for row in values]}
summary['times']=sorted(snapshots)
stations=db['stations'][['station_id','name','district','address','latitude','longitude']].to_dict('records')
station_map={str(row['station_id']):row for row in stations}
bundle={'overview':summary,'stations':stations,'station_map':station_map,'cases':examples,'snapshots':snapshots,
        'research':research(),
        'histories':histories,'history_station_ids':[int(i) for i in panel['ids']],
        'boundary':json.loads((root/'bike_demo/static/seoul_districts.geojson').read_text()),
        'scope':'Finite archived snapshots; current API is available only from the Python server.'}
html=(root/'bike_demo/static/index.html').read_text()
css=(root/'bike_demo/static/style.css').read_text()
js=(root/'bike_demo/static/app.js').read_text()
payload=json.dumps(bundle,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('<','\\u003c')
html,n_css=re.subn(r'<link\b(?=[^>]*\bhref="/static/style\.css")[^>]*>',lambda _:f'<style>{css}</style>',html,flags=re.S)
html,n_js=re.subn(r'<script\b[^>]*\bsrc="/static/app\.js"[^>]*>\s*</script>','',html,flags=re.S)
assert n_css==1 and n_js==1,'Static entrypoints changed; cannot export an independent HTML.'
html=html.replace('</body>',f'<script>window.DEMO_BUNDLE={payload};</script><script>{js}</script></body>')
path=root/'deliverables/따릉이_시민용_오프라인데모.html'
path.write_text(html)
(root/'deliverables/따릉이_시민용_서비스데모_v2.html').write_text(html)
print(path,len(html.encode()),'bytes;',len(snapshots),'snapshots')
