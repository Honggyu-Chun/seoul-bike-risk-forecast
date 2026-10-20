"""Package only the rider demo, its measured evidence, and final deliverables.

The archive includes trained models and replay predictions, so raw-source
downloads and training are unnecessary for a first demo. No keys or raw personal
trip records are included. Raw quantity ZIP can be downloaded for retraining.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import platform
import importlib.metadata
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'deliverables'
names=[
 '따릉이_시민용_오프라인데모.html',
 '따릉이_시민용_서비스데모_v2.html',
 '따릉이_시민용서비스_과제계획서_실험결과반영.docx',
 '따릉이_시민용서비스_과제계획서_실험결과반영.pdf',
 '따릉이_데모와실험_팀원용_쉬운상세설명서.docx',
 '따릉이_데모와실험_팀원용_쉬운상세설명서.pdf',
 '실제_모델비교_결과.csv','실제_추천비교_결과.csv',
 '따릉이_성능개선_실제실험결과_쉬운보고서.docx',
 '따릉이_성능개선_실제실험결과_쉬운보고서.pdf',
 '성능개선_새시험_모델비교.csv','성능개선_새시험_추천비교.csv',
 '성능개선_screen_검증비교.csv','성능개선_forward_검증비교.csv',
]
files=[ROOT/n for n in ['README.md','pyproject.toml','uv.lock','.gitignore','data/downloads.json','data/processed/rider_stations.parquet','data/processed/rider_q1_panel.npz','reports/rider_demo_analysis.md','docs/map_sources.md','docs/demo_v2_guide.md','docs/templates/MAP_APACHE_2_LICENSE.txt']]
for folder,pattern in [('bike_demo','*.py'),('bike_demo/static','*'),('scripts','*.py'),('tests','*.py'),('config','*.json'),('docs/templates','*.docx'),('artifacts/models','*'),('artifacts/evaluation','*.json'),('artifacts/evaluation','*.csv'),('artifacts/evaluation','*.parquet'),('artifacts/evaluation/figures','*.png')]:
 files.extend((ROOT/folder).glob(pattern))
files.append(ROOT/'scripts/run_demo.sh')
files.append(ROOT/'docs/local_transfer_guide.md')
files.extend((ROOT/'artifacts/browser/v2').glob('*.png'))
files.extend((ROOT/'artifacts/browser/v2').glob('*.json'))
for relative in ['data/improvement_downloads.json','data/weather_research_downloads.json',
 'artifacts/improvement/source_audit.json','artifacts/improvement/weather_audit.json',
 'data/raw/improvement/availability_2025q2.zip.meta.json',
 'data/raw/citywide_review/kma_seoul_2025.zip.meta.json',
 'artifacts/improvement/final/results.json','artifacts/improvement/final/selection_frozen_before_test.json',
 'artifacts/improvement/forward/predictor_roundtrip_checks.json',
 'reports/improvement_results.md','reports/method_search/research_design.md',
 'reports/method_search/sources.json','reports/method_search/primary_sources.json',
 'reports/method_search/original_service_upper_bound.json']:
 files.append(ROOT/relative)
for phase in ['screen','forward']:
 for path in (ROOT/'artifacts/improvement'/phase).glob('*.json'):
  value=json.loads(path.read_text())
  if (isinstance(value,dict) and value.get('status') in ['ok','error']) or path.name in ['manifest.json','leaderboard.json','ensembles.json','recall_constraint.json','predictor_roundtrip_checks.json']:
   files.append(path)
files.extend(OUT/n for n in names)
for name in ['helped_cards.png','hurt_cards.png','workspace_detail.png','browser_checks.json']:
 files.append(ROOT/'artifacts/browser'/name)
files=sorted({p for p in files if p.is_file()})
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
frozen=json.loads((ROOT/'artifacts/evaluation/selection_frozen_before_test.json').read_text())
for name,info in frozen['models'].items():
 assert sha(ROOT/f'artifacts/models/{name}.txt')==info['model_sha256'],name
manifest={
 'packaged_at_utc':datetime.now(timezone.utc).isoformat(),
 'python':platform.python_version(),
 'packages':{n:importlib.metadata.version(n) for n in ['pandas','numpy','scikit-learn','lightgbm','fastapi','uvicorn','pyarrow','python-docx']},
 'scope':'Rider demo runtime and measured archived evaluation. Raw sources must be fetched separately for retraining.',
 'not_verified':['field rental success','full-key whole-city live queries','live M1/M2 accuracy','historical weather vintage equivalence and live publication latency','5-minute or 10-minute forecasting accuracy','fresh cloud snapshot restore'],
 'files':{str(p.relative_to(ROOT)):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in files},
}
mp=ROOT/'reports/rider_delivery_manifest.json'
mp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
files.append(mp)
target=OUT/'따릉이_데모_실행패키지.zip'
with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
 for p in files:z.write(p,'P_Project/'+str(p.relative_to(ROOT)))
with zipfile.ZipFile(target) as z:assert z.testzip() is None
print(target, target.stat().st_size, 'bytes;',len(files),'files; model hashes verified')
