"""Describe fitted split use and prediction equality without changing any fit."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RECORD = Path(__file__).resolve().parent
OUT = ROOT / 'artifacts/followup_public_20261020'
BASE = ROOT / 'artifacts/followup_v4_20261020'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

rows, comparisons, sources = [], [], {}
for fold in 'ABC':
    for name in ('lightgbm_public_bus', 'lightgbm_public_od', 'lightgbm_public_bus_od'):
        result_path = OUT/fold/(name+'.json')
        result = json.loads(result_path.read_text(encoding='utf-8'))
        assert result['status'] == 'ok' and result['public_checks']['all_passed']
        sources[f'{fold}/{name}'] = sha(result_path)
        model_path = OUT/fold/'models'/(name+'.txt')
        model = lgb.Booster(model_str=model_path.read_text(encoding='utf-8'))
        for feature, count in zip(model.feature_name()[105:], model.feature_importance('split')[105:]):
            rows.append(dict(fold=fold, model=name, feature=feature, splits=int(count)))
    for candidate, control, control_root in (
        ('lightgbm_public_bus', 'lightgbm_full', BASE),
        ('lightgbm_public_bus_od', 'lightgbm_public_od', OUT),
    ):
        for stage in ('cal', 'policy', 'valid'):
            a = np.load(OUT/'predictions'/fold/(candidate+'_'+stage+'.npy'), mmap_mode='r')
            b = np.load(control_root/'predictions'/fold/(control+'_'+stage+'.npy'), mmap_mode='r')
            comparisons.append(dict(fold=fold, model=candidate, control=control,
                stage=stage, rows=len(a), raw_float32_predictions_equal=bool(np.array_equal(a,b))))

pd.DataFrame(rows).to_csv(RECORD/'추가특징_분할횟수.csv', index=False, encoding='utf-8-sig')
document = dict(checked_at_utc=datetime.now(timezone.utc).isoformat(),
    result_sha256=sources, extra_feature_rows=len(rows), comparisons=comparisons,
    scope='Post-fit descriptive inspection only; split use does not prove causality or general usefulness.',
    training_or_tuning_performed=False)
(RECORD/'추가특징_실제사용검수.json').write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('EXTRA_FEATURE_USE_CHECKED', len(rows), 'feature rows', len(comparisons), 'prediction comparisons')
