"""Offline power-loss checkpoint. No secrets or large data copies in the zip."""
import json
from pathlib import Path
import zipfile
import pandas as pd
from .common import ROOT,OUT,RECORD,save,digest,now

def worker_pid_alive(pid):
    if __import__('os').name!='nt':return None
    import ctypes
    kernel=ctypes.windll.kernel32
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
    handle=kernel.OpenProcess(0x1000,False,int(pid))
    if not handle:return False
    try:
        code=ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code)) and code.value==259)
    finally:kernel.CloseHandle(handle)

def main():
    dest=RECORD/'체크포인트';dest.mkdir(exist_ok=True)
    results=[];artifacts=[]
    for fold in ('A','B','C'):
        for path in sorted((OUT/fold).glob('*.json')):
            r=json.loads(path.read_text(encoding='utf-8'))
            if r.get('status')=='ok':
                results.append(dict(fold=fold,name=r['name'],f1=r['validation']['overall']['f1'],result_sha256=digest(path)))
                save(dest/'완료결과'/fold/path.name,r)
        for path in sorted((OUT/fold/'models').glob('*')):
            if path.is_file():artifacts.append(dict(path=str(path.relative_to(ROOT)),bytes=path.stat().st_size,sha256=digest(path)))
    for folder in (OUT/'data',OUT/'predictions'):
        for path in sorted(folder.rglob('*')):
            if path.is_file():artifacts.append(dict(path=str(path.relative_to(ROOT)),bytes=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns))
    worker=json.loads((OUT/'worker.json').read_text(encoding='utf-8'))
    live=worker_pid_alive(worker['pid'])
    worker_snapshot=OUT/worker.get('fold','_')/'snapshots'/worker.get('candidate','_')/'state.cbsnapshot'
    snapshots=[dict(path=str(p.relative_to(ROOT)),bytes=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns) for p in OUT.glob('*/snapshots/*/state.cbsnapshot')]
    figures=list((RECORD/'E2E').rglob('*.png'))
    public_out=ROOT/'artifacts/followup_public_20261020'
    public_state=json.loads((public_out/'queue_state.json').read_text(encoding='utf-8-sig')) if (public_out/'queue_state.json').exists() else None
    public_worker=json.loads((public_out/'worker.json').read_text(encoding='utf-8-sig')) if (public_out/'worker.json').exists() else None
    public_review_path=ROOT/'experiments/20261020_추가공공데이터적용/추가공공데이터_완료검수.json'
    public_review=json.loads(public_review_path.read_text(encoding='utf-8')) if public_review_path.exists() else None
    manual_path=RECORD/'final_manual_review.json'
    manual=json.loads(manual_path.read_text(encoding='utf-8')) if manual_path.exists() else None
    verified_complete=bool(manual and manual.get('analysis_scope_complete')
        and manual.get('report_sha256')==digest(RECORD/'쉬운보고서.html')
        and public_review and public_review.get('ready_for_manual_review')
        and public_review['completed']==public_review['required'])
    snapshot=dict(saved_at_utc=now(),goal_complete=False,analysis_scope_complete=verified_complete,
        manual_review=str(manual_path) if manual else None,
        goal_status_note='Goal tool status is recorded separately; this snapshot does not change it.',
        successful_comparisons=results,successful_comparison_count=len(results),
        current_worker=worker,current_worker_pid_alive=live,worker_record_is_stale=live is False,
        current_in_memory_candidate_has_midfit_snapshot=bool(live and worker_snapshot.exists()),
        catboost_snapshot_policy='new fits save every 60 seconds and on exit; resume only with matching exact input hash',
        snapshot_inventory=snapshots,
        resume='D:/P실무/모델개발/experiments/20261020_후속실험_v4/재개.ps1',
        handoff='D:/P실무/이어서_작업하기.md',e2e_png_figures=len(figures),artifact_inventory=artifacts,
        extra_data_checkpoint=str(RECORD/'추가데이터_병렬조사/보존검수_최신_20261020.json'),
        public_data_research_report=str(ROOT/'experiments/20261020_공공데이터추가조사2/공공데이터_추가조사_통합보고서.html'),
        public_data_research_separate_backup=str(ROOT/'experiments/20261020_공공데이터추가조사2/백업/최신백업.json'),
        public_data_preparation_report=str(ROOT/'experiments/20261020_추가공공데이터적용/데이터준비_쉬운설명.html'),
        public_data_preparation_separate_backup=str(ROOT/'experiments/20261020_추가공공데이터적용/백업/최신백업.json'),
        public_feature_metadata=str(ROOT/'artifacts/followup_public_features_20261020/metadata.json'),
        public_extension_queue_state=public_state,
        public_extension_queue_pid_alive=worker_pid_alive(public_state['pid']) if public_state else False,
        public_extension_worker=public_worker,
        public_extension_worker_pid_alive=worker_pid_alive(public_worker['pid']) if public_worker else False,
        public_extension_completed=public_review['completed'] if public_review else 0,
        public_extension_required=public_review['required'] if public_review else 0,
        env_local_exists=(ROOT/'.env').exists(),env_excluded_from_archive=True,
        completed_stages=['problem_definition','exploration','curation','preprocessing']+(['modeling','evaluation','reporting_and_reproducibility'] if verified_complete else []),
        incomplete_stages=[] if verified_complete else ['complete model comparisons','evaluation and adoption across folds','easy final report'],
        latest_constraints=['real-time available inputs only for main results','explain and cite reasons for data processing','no Q3/Q4 scoring before selection freeze'],
        remaining=[] if verified_complete else ['finish registered comparisons and verify actual queue state','complete public-data comparisons and automated checks','finish manual claims/layout review and final checkpoint'])
    save(dest/'최신상태.json',snapshot)
    candidates=[]
    for folder in (ROOT/'bike_demo',ROOT/'tests',ROOT/'config'):
        candidates.extend(p for p in folder.rglob('*') if p.is_file() and p.suffix in ('.py','.json','.toml','.txt','.yml','.yaml'))
    candidates.extend(p for p in RECORD.rglob('*') if p.is_file() and dest not in p.parents and p.suffix in ('.py','.ps1','.md','.json','.csv','.png','.svg','.html','.xml'))
    candidates.extend([ROOT.parent/'이어서_작업하기.md',ROOT.parent/'AGENTS.md'])
    candidates.extend(p for p in (ROOT.parent/'README.md',ROOT/'README.md',ROOT/'experiments/README.md') if p.exists())
    candidates.extend(ROOT.glob('requirements*.txt'))
    candidates=[p for p in candidates if p.name!='.env' and not p.name.startswith('.env.') and '__pycache__' not in p.parts]
    from .external_data import keys
    secrets=[v.encode() for v in keys().values() if v]
    for path in candidates:
        body=path.read_bytes()
        if any(value in body for value in secrets):raise RuntimeError('Credential found in proposed source checkpoint; archive creation stopped')
    archive=dest/'코드와분석기록_20261020.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in sorted(set(candidates)):
            z.write(path,str(path.relative_to(ROOT.parent)))
        for path in dest.rglob('*.json'):z.write(path,str(path.relative_to(ROOT.parent)))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None;count=len(z.infolist())
    save(dest/'압축검수.json',dict(saved_at_utc=now(),archive=str(archive),bytes=archive.stat().st_size,sha256=digest(archive),members=count,crc_passed=True,credentials_scanned=True,large_models_and_data_stay_in_place=True))
    print('CHECKPOINT_SAVED',len(results),'comparisons',len(figures),'figures',count,'source/archive members',flush=True)

if __name__=='__main__':main()
