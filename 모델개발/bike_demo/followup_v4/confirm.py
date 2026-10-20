"""Confirm predeclared eligible pilot gains on two other development folds."""
import json
from .common import OUT,RECORD,save,digest,now
from . import runner
from .advanced import extra_specs
from .air import AirInputs,air_specs

def main():
    plan=json.loads((RECORD/'supplemental_specs.json').read_text(encoding='utf-8'))['confirmation']
    forecast_plan=RECORD/'forecast_experiment_plan.json'
    if forecast_plan.exists():
        from .forecast import run_pilot,forecast_spec
        run_pilot();plan['eligible']=plan['eligible']+[forecast_spec()['name']]
    base=json.loads((OUT/'A/lightgbm_full.json').read_text(encoding='utf-8'))
    candidates=[]
    for name in plan['eligible']:
        path=OUT/'A'/(name+'.json')
        if not path.exists():continue
        record=json.loads(path.read_text(encoding='utf-8'))
        if record.get('status')!='ok':continue
        gain=record['validation']['overall']['f1']-base['validation']['overall']['f1']
        if gain>=plan['minimum_A_F1_gain_absolute']:candidates.append(dict(name=name,gain=gain))
    selected=sorted(candidates,key=lambda x:(-x['gain'],x['name']))[:plan['maximum_candidates']]
    save(RECORD/'confirmation_selection.json',dict(selected_at_utc=now(),selected=selected,qualifying=candidates,plan_sha256=digest(RECORD/'supplemental_specs.json'),forecast_plan_sha256=digest(forecast_plan) if forecast_plan.exists() else None,criterion=plan,independent_final_test=False))
    specs={s['name']:s for s in runner.specs()+extra_specs()+air_specs()}
    if forecast_plan.exists():specs[forecast_spec()['name']]=forecast_spec()
    data=None
    for item in selected:
        name=item['name']
        if name in specs:
            if data is None:
                if forecast_plan.exists():
                    from .forecast import ForecastInputs
                    data=ForecastInputs()
                else:data=AirInputs()
            for fold in ('B','C'):
                runner.run_one(data,fold,specs[name])
                path=OUT/fold/(name+'.json');record=json.loads(path.read_text(encoding='utf-8'))
                if name.startswith('lightgbm_air_'):
                    record.update(information_track='live_source_backed_historical_archive',historical_received_at_verified=False,air_metadata_sha256=digest(OUT/'data/air_features_2025.json'))
                record['confirmation_plan_sha256']=digest(RECORD/'supplemental_specs.json');save(path,record)
                if name=='lightgbm_gfs_prior_day':
                    from .forecast import decorate
                    decorate(fold)
        else:
            import subprocess,sys
            subprocess.run([sys.executable,'-X','utf8','-m','bike_demo.followup_v4.neural','--folds','B','C','--names',name],check=True)
    runner.refresh();print('CONFIRMATION_COMPLETE',len(selected),flush=True)

if __name__=='__main__':main()
