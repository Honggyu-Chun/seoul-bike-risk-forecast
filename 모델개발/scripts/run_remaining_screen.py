"""Resume only unfinished classical candidates using F1 checkpoint selection."""
from pathlib import Path
import sys,os
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['BIKE_F1_SCREEN']='1'
from bike_demo.research import run_search,candidates,dump
from bike_demo.research_features import OUT
names=[s['name'] for s in candidates() if not s['name'].startswith('causal_transition')]
dump(OUT/'screen/classical_worker.json',{'pid':os.getpid(),'started_at_utc':datetime.now(timezone.utc).isoformat(),'checkpoint_change':'Unfinished non-baseline LightGBM cases select by validation F1; completed trials remain unchanged. No new test used.'})
print('RESUME unfinished screening with validation-F1 checkpoints',os.getpid(),flush=True)
run_search('screen',names=names)
