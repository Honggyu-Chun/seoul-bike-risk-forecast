"""Finish the authorized research pipeline while preserving every test boundary.

Attach to already running workers, then use four CPU threads for classical
models plus one for neural/extra-feature work. No deployments or submissions.
"""
from pathlib import Path
import argparse,subprocess,os,time,json,sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from bike_demo.research import candidates,dump
BASE=ROOT/'artifacts/improvement';PY=str(ROOT/'.venv/bin/python')


def alive(pid):
    if not pid:return False
    try:return Path(f'/proc/{pid}/stat').read_text().split()[2]!='Z'
    except FileNotFoundError:return False


def launch(module,args,log,threads=4):
    handle=Path(log).open('a');env=os.environ.copy()
    env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS=str(threads),BIKE_RESEARCH_THREADS=str(threads),XDG_CACHE_HOME='/workspace/.cache')
    process=subprocess.Popen([PY,'-u','-m',module,*args],cwd=ROOT,env=env,stdout=handle,stderr=handle)
    return process,handle


def wait_process(process,handle):
    while process.poll() is None:time.sleep(5)
    handle.close()
    if process.returncode:raise RuntimeError(f'Worker exited {process.returncode}; inspect its log before retrying.')


def records(phase):
    rows=[]
    for path in (BASE/phase).glob('*.json'):
        value=json.loads(path.read_text())
        if isinstance(value,dict) and value.get('status') in ['ok','error']:rows.append(value)
    return rows


def verify_phase(phase):
    rows=records(phase);errors=[r for r in rows if r['status']=='error']
    if errors:raise RuntimeError('Candidate implementation errors need investigation before proceeding: '+', '.join(r['name'] for r in errors))
    rows.sort(key=lambda r:-r['rule']['f1']);dump(BASE/phase/'leaderboard.json',rows)
    print(phase,'completed',len(rows),'best validation F1',rows[0]['rule']['f1'],flush=True)


def run(module,args,log,threads=4):
    process,handle=launch(module,args,log,threads);wait_process(process,handle)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--classical-pid',type=int);parser.add_argument('--neural-pid',type=int);parser.add_argument('--causal-pid',type=int);parser.add_argument('--extra-forward-pid',type=int);parser.add_argument('--extra-screen-pid',type=int);parser.add_argument('--prefit-forward-pid',type=int);args=parser.parse_args()
    dump(BASE/'pipeline_worker.json',{'pid':os.getpid(),'workers':vars(args)})
    print('Waiting for the current neural worker before assigning its CPU to causal-transition trials.',flush=True)
    while alive(args.neural_pid):time.sleep(5)
    if args.causal_pid and alive(args.causal_pid):
        while alive(args.classical_pid) or alive(args.causal_pid):time.sleep(5)
    else:
        causal,log=launch('bike_demo.research',['--phase','screen','--names','causal_transition_63','causal_transition_127','causal_transition_spatial'],BASE/'screen/causal_search.log',threads=1)
        while alive(args.classical_pid) or causal.poll() is None:time.sleep(5)
        wait_process(causal,log)
    while alive(args.extra_screen_pid):time.sleep(5)
    expected={s['name'] for s in candidates()}|{'neural_mlp','neural_tcn','neural_gru','neural_tcn_weighted'}
    actual={r['name'] for r in records('screen')}
    if expected-actual:raise RuntimeError('Missing screening results: '+str(sorted(expected-actual)))
    verify_phase('screen')
    run('bike_demo.research',['--phase','screen','--refine-rules'],BASE/'screen/rule_refinement.log')
    while alive(args.prefit_forward_pid):time.sleep(5)
    (BASE/'forward').mkdir(exist_ok=True)
    classical,c_log=launch('bike_demo.research',['--phase','forward'],BASE/'forward/search.log',4)
    neural,n_log=launch('bike_demo.neural_research',['--phase','forward'],BASE/'forward/neural_search.log',1)
    wait_process(classical,c_log);wait_process(neural,n_log)
    while alive(args.extra_forward_pid):time.sleep(5)
    verify_phase('forward')
    run('bike_demo.research',['--phase','forward','--refine-rules'],BASE/'forward/rule_refinement.log')
    if not (BASE/'final/results.json').exists():
        run('bike_demo.evaluate_research',['--validate-predictors'],BASE/'forward/predictor_checks.log')
        print('All validation predictors verified. Freeze selection and evaluate untouched test once.',flush=True)
        run('bike_demo.evaluate_research',['--open-final-test'],BASE/'final_evaluation.log')
    report=subprocess.run([PY,'scripts/report_improvement.py'],cwd=ROOT,env={**os.environ,'XDG_CACHE_HOME':'/workspace/.cache'},capture_output=True,text=True)
    print(report.stdout,flush=True)
    if report.returncode:raise RuntimeError(report.stderr)
    print('Pipeline complete; inspect the actual goal flags before declaring success.',flush=True)


if __name__=='__main__':main()
