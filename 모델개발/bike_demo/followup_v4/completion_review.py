"""Inventory the registered experiment scope; never marks the goal complete."""
import json
import re
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from .common import ROOT, OUT, RECORD, digest, save, now

CORE = ['persistence','seasonal_day','seasonal_week','smoothed_markov','logistic',
        'random_forest','lightgbm_basic','lightgbm_full','catboost_temporal',
        'xgboost_temporal','lightgbm_temporal','M2_no_weather']
PILOTS = ['lightgbm_full_112d','lightgbm_full_recency28','lightgbm_full_63',
          'lightgbm_ordinal','lightgbm_poisson','lightgbm_state_experts',
          'lightgbm_calendar','lightgbm_prior_year','lightgbm_kbo',
          'lightgbm_air_lag2','lightgbm_air_lag3','temporal_mlp24','temporal_mlp168',
          'tcn24','graph_tcn24_identity','graph_tcn24','lightgbm_gfs_prior_day']


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main():
    required = {(fold, name) for fold in 'ABC' for name in CORE}
    required.update(('A', name) for name in PILOTS)
    selection_path = RECORD/'confirmation_selection.json'
    selection = read(selection_path) if selection_path.exists() else None
    if selection:
        required.update((fold, item['name']) for item in selection['selected'] for fold in 'BC')
    results = {}
    for fold in 'ABC':
        for path in (OUT/fold).glob('*.json'):
            result = read(path)
            if result.get('status') == 'ok':results[(fold, result['name'])] = result
    missing = sorted(required-results.keys())
    artifact_issues = []
    protocol_hash = digest(RECORD/'protocol.json')
    for (fold, name), result in results.items():
        if result['protocol_sha256'] != protocol_hash:artifact_issues.append(f'{fold}/{name}: protocol hash')
        cal = OUT/fold/'models'/(name+'_calibrator.joblib')
        if not cal.exists():artifact_issues.append(f'{fold}/{name}: missing calibrator')
        kind = result['spec'].get('kind')
        suffixes = {'lgb':['.txt'],'ordinal':['.txt'],'poisson':['.txt'],
                    'experts':['_positive.txt','_zero.txt'],'cat':['.cbm'],
                    'xgb':['.ubj'],'neural':['.pt'],'linear':['.joblib'],'forest':['.joblib']}
        for suffix in suffixes.get(kind, []):
            if not (OUT/fold/'models'/(name+suffix)).exists():artifact_issues.append(f'{fold}/{name}: missing {suffix}')
        for stage in ('cal','policy','valid'):
            path = OUT/'predictions'/fold/(name+'_'+stage+'.npy')
            if not path.exists():artifact_issues.append(f'{fold}/{name}: missing {stage} predictions');continue
            pred = np.load(path, mmap_mode='r')
            if pred.shape != (result['stage_rows'][stage],):artifact_issues.append(f'{fold}/{name}: {stage} shape')
            if not np.isfinite(pred).all() or (pred<0).any() or (pred>1).any():artifact_issues.append(f'{fold}/{name}: {stage} invalid probability')
    evidence_paths = [
        'E2E/01_문제정의/문제정의.md','E2E/01_문제정의/정답정의_실제관측예시.csv',
        'E2E/02_탐색/탐색요약.json','E2E/02_탐색/일별관측과0대비율.csv',
        'E2E/03_큐레이션/원본월별검수.csv','E2E/04_전처리/학습과거표본_특징검수_10만행.csv',
        'E2E/04_전처리/시간분할.png','E2E/05_모델링/실행모델과학습조건.csv',
        'E2E/06_평가/각베이스라인대비변화.csv','E2E/06_평가/수량시간지역별_오류분석.csv',
        'E2E/06_평가/각베이스라인대비_날짜블록_차이범위.csv',
        'E2E/06_평가/기본과결합_판정사례.csv','E2E/06_평가/기본과결합_판정유형건수.csv','example_selection.json',
        'E2E/06_평가/확률구간별_실제위험과표본수.csv','E2E/06_평가/확률보정_전후와구간오차.csv',
        'E2E/06_평가/확률과실제위험_비교.png','E2E/06_평가/확률보정_전후_Brier.png',
        'calibration_diagnostic_plan.json','calibration_diagnostic_checks.json','calibration_bin_tests.xml',
        'E2E/07_보고와재현/report_snapshot.json','데이터처리_근거기록.md',
        '쉬운보고서.md','쉬운보고서.html','environment_versions.json','API와추가데이터.md',
        '추가데이터_병렬조사/보고서와재개방법.md','추가데이터_병렬조사/보존검수_최신_20261020.json']
    missing_evidence = [p for p in evidence_paths if not (RECORD/p).is_file()]
    coverage = {}
    for filename, check_key in [('roundtrip_checks.json','all_passed'),('uncertainty_checks.json','all_aggregate_matches')]:
        path = RECORD/filename
        if not path.exists():coverage[filename] = False;continue
        document = read(path)
        verified = {(x['fold'],x.get('model')):x for x in document['checks']}
        eligible = {key for key,r in results.items() if r['spec'].get('kind') not in (None,'rule','markov')} if filename.startswith('roundtrip') else set(results)
        coverage[filename] = bool(document[check_key] and eligible <= verified.keys() and all(
            verified[key]['result_sha256']==digest(OUT/key[0]/(key[1]+'.json')) for key in eligible))
    pairs_ok = False
    example_path=RECORD/'example_selection.json'
    coverage['example_selection.json']=False
    if example_path.exists():
        examples=read(example_path)
        coverage['example_selection.json']=bool(examples['net_TP_and_FP_differences_match_saved_metrics']
            and all(value==digest(OUT/path.split('/')[0]/(path.split('/')[1]+'.json'))
                    for path,value in examples['source_results'].items()))
    diagnostic_path=RECORD/'calibration_diagnostic_checks.json'
    coverage['calibration_diagnostic_checks.json']=False
    if diagnostic_path.exists():
        diagnostic=read(diagnostic_path)
        plan=read(RECORD/'calibration_diagnostic_plan.json')
        verified={(x['fold'],x['model'],x['group']):x for x in diagnostic['checks']}
        expected={(f,m,g) for f in plan['folds'] for m in plan['models'] for g in plan['groups']}
        coverage['calibration_diagnostic_checks.json']=bool(diagnostic['all_passed'] and expected<=verified.keys()
            and diagnostic['plan_sha256']==digest(RECORD/'calibration_diagnostic_plan.json')
            and all(verified[f,m,g]['result_sha256']==digest(OUT/f/(m+'.json')) for f,m,g in expected))
    comparison_path = RECORD/'E2E/06_평가/각베이스라인대비변화.csv'
    if comparison_path.exists():
        comparisons = pd.read_csv(comparison_path)
        available = set(zip(comparisons.fold,comparisons.model,comparisons.baseline))
        needed = {(fold,name,b) for fold,name in results for b in CORE[:7] if name!=b and (fold,b) in results}
        pairs_ok = needed <= available
    report = RECORD/'쉬운보고서.html'
    missing_images = []
    if report.exists():
        for source in re.findall(r'<img src="([^"]+)"',report.read_text(encoding='utf-8')):
            if not (RECORD/source).is_file():missing_images.append(source)
    original = read(ROOT/'experiments/20261020_데이터방법론조사/protected_fingerprints.json')
    changed = [p['path'] for p in original if digest(ROOT/p['path']) != p['sha256']]
    archive = RECORD/'체크포인트/코드와분석기록_20261020.zip'
    archive_ok = False
    if archive.exists():
        with zipfile.ZipFile(archive) as z:archive_ok = z.testzip() is None
    count_consistent = False
    report_snapshot = RECORD/'E2E/07_보고와재현/report_snapshot.json'
    if report_snapshot.exists():count_consistent = read(report_snapshot)['comparisons']==len(results)
    public_plan = ROOT/'experiments/20261020_추가공공데이터적용/후속비교_계획.json'
    public_missing = []
    public_required = 0
    public_report_verified = False
    if public_plan.exists():
        extension = read(public_plan)
        public_plan_hash = digest(public_plan)
        for fold in extension['folds']:
            for spec in extension['candidate_specs']:
                public_required += 1
                path = ROOT/'artifacts/followup_public_20261020'/fold/(spec['name']+'.json')
                if not path.exists():
                    public_missing.append(dict(fold=fold,name=spec['name'],reason='not yet run'))
                    continue
                result = read(path)
                if (result.get('status')!='ok' or result.get('public_extension_plan_sha256')!=public_plan_hash
                    or result.get('information_track')!=extension['information_track']):
                    public_missing.append(dict(fold=fold,name=spec['name'],reason='result/provenance not verified'))
        public_review = ROOT/'experiments/20261020_추가공공데이터적용/추가공공데이터_완료검수.json'
        if public_review.exists():
            checked = read(public_review)
            public_report_verified = bool(checked['ready_for_manual_review']
                and checked['public_extension_plan_sha256']==public_plan_hash
                and checked['completed']==public_required)
    ready = bool(not missing and not public_missing and (not public_required or public_report_verified) and selection is not None and not artifact_issues and not missing_evidence
                 and all(coverage.values()) and pairs_ok and not missing_images and not changed
                 and archive_ok and count_consistent)
    manual_path=RECORD/'final_manual_review.json'
    manual=read(manual_path) if manual_path.exists() else None
    manual_verified=bool(ready and manual and manual.get('analysis_scope_complete')
        and manual.get('report_sha256')==digest(report)
        and manual.get('public_report_sha256')==digest(ROOT/'experiments/20261020_추가공공데이터적용/추가공공데이터_비교보고서.html')
        and all(manual['checks'].values()))
    save(RECORD/'completion_review.json',dict(reviewed_at_utc=now(),
        registered_minimum_comparisons=53,required_including_confirmation=len(required),completed=len(results),
        public_extension_required=public_required,public_extension_missing=public_missing,
        public_extension_complete=bool(public_required and not public_missing),
        public_extension_report_verified=public_report_verified,
        missing_comparisons=[dict(fold=f,name=n) for f,n in missing],confirmation_selection_exists=selection is not None,
        artifact_issues=artifact_issues,missing_evidence=missing_evidence,verification_coverage=coverage,
        all_basic_baseline_pair_rows_exist=pairs_ok,missing_report_images=missing_images,
        protected_files_checked=len(original),protected_files_changed=changed,checkpoint_zip_crc_passed=archive_ok,
        report_count_matches=count_consistent,ready_for_manual_final_review=ready,
        goal_complete=False,analysis_scope_complete=manual_verified,manual_review_verified=manual_verified,
        manual_checks_remaining=[] if manual_verified else ['read final claims against saved metrics and source limitations',
        'inspect final plots and readable report layout','verify actual queue/process termination',
        'verify held-back Q3/Q4 scores remain unopened','refresh final report flag and checkpoint after review']))
    print('COMPLETION_REVIEW',len(results),'complete',len(missing),'missing comparisons',
          'ready_for_manual_review',ready,flush=True)


if __name__=='__main__':main()
