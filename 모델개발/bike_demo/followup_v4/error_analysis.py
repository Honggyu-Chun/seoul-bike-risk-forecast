"""Descriptive development errors from frozen decisions, with aggregate checks."""
import json
import joblib
import numpy as np
import pandas as pd
from scipy.special import logit
import matplotlib.pyplot as plt
from .common import ROOT,OUT,RECORD,save,digest,now,protocol
from .runner import Inputs
from ..research import decisions
from .analysis import setup,export,frame_csv,E2E,STAGES

def counts(y,pred,group):
    frame=pd.DataFrame({'group':group,'n':1,'positive':y.astype(np.int64),'tp':(y&pred).astype(np.int64),'fp':(~y&pred).astype(np.int64),'fn':(y&~pred).astype(np.int64)})
    summary=frame.groupby('group',dropna=False,sort=True).sum().reset_index()
    summary['precision']=summary.tp/(summary.tp+summary.fp).replace(0,np.nan)
    summary['recall']=summary.tp/summary.positive.replace(0,np.nan)
    return summary

def main():
    setup();data=Inputs();stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    folder=E2E/STAGES[5];checks=[];pieces=[];complement=[];examples=[];case_counts=[]
    for fold in ('A','B','C'):
        split=protocol()['development_folds'][fold];index=data.indices(split['valid_start'],split['valid_end'])
        y=data.target[index]==0;q=data.q.ravel()[index];positive=q>0;hour=data.times.hour.to_numpy()[index//data.n]
        district=stations.district.to_numpy()[index%data.n]
        quantity=pd.cut(q,bins=[-1,0,1,2,3,5,np.inf],labels=['0','1','2','3','4~5','6+']).astype(str)
        predictions={};example_probabilities={}
        for name in ('persistence','lightgbm_basic','lightgbm_full','catboost_temporal','xgboost_temporal','M2_no_weather'):
            path=OUT/fold/(name+'.json')
            if not path.exists():continue
            r=json.loads(path.read_text(encoding='utf-8'))
            if r.get('status')!='ok':continue
            raw=np.load(OUT/'predictions'/fold/(name+'_valid.npy'))
            kind=r['spec'].get('kind')
            if kind in ('lgb','cat','linear','forest','poisson','ordinal','experts'):raw=raw.astype(np.float64)
            cal=joblib.load(OUT/fold/'models'/(name+'_calibrator.joblib'))
            p=cal.predict_proba(logit(np.clip(raw,1e-6,1-1e-6)).reshape(-1,1))[:,1].astype(np.float32)
            pred=decisions(p,q,r['rule']);expected=r['validation']['overall']
            actual=dict(tp=int((y&pred).sum()),fp=int((~y&pred).sum()),fn=int((y&~pred).sum()))
            matches=all(actual[key]==expected[key] for key in actual)
            checks.append(dict(fold=fold,name=name,aggregate_confusion_matches=matches,actual=actual,expected={key:expected[key] for key in actual},raw_cache_precision='float32',result_sha256=digest(path)))
            if not matches:
                # Do not silently publish incompatible subgroup counts.
                print('SKIP_ERROR_GROUPS aggregate mismatch',fold,name,flush=True);continue
            predictions[name]=pred
            if name in ('lightgbm_basic','M2_no_weather'):example_probabilities[name]=p
            for label,groups in [('current_quantity',quantity),('input_hour',hour),('district',district)]:
                part=counts(y,pred,groups);part.insert(0,'dimension',label);part.insert(0,'model',name);part.insert(0,'fold',fold);pieces.append(part)
        for left,lp in predictions.items():
            for right,rp in predictions.items():
                if left>=right:continue
                lf=y&~lp&positive;rf=y&~rp&positive;union=(lf|rf).sum()
                complement.append(dict(fold=fold,left=left,right=right,transition_FN_left=int(lf.sum()),transition_FN_right=int(rf.sum()),both_miss=int((lf&rf).sum()),FN_jaccard=float((lf&rf).sum()/union) if union else None,left_finds_right_misses=int((y&lp&~rp&positive).sum()),right_finds_left_misses=int((y&rp&~lp&positive).sum())))
        if {'lightgbm_basic','M2_no_weather'}<=set(predictions):
            basic=predictions['lightgbm_basic'];blend=predictions['M2_no_weather']
            categories={
                '기본 놓침-결합 포착':positive&y&~basic&blend,
                '기본 포착-결합 놓침':positive&y&basic&~blend,
                '기본 오경보-결합 정상':positive&~y&basic&~blend,
                '기본 정상-결합 오경보':positive&~y&~basic&blend,
                '두 모델 모두 놓침':positive&y&~basic&~blend,
                '두 모델 모두 포착':positive&y&basic&blend}
            totals={label:int(mask.sum()) for label,mask in categories.items()}
            for label,mask in categories.items():
                case_counts.append(dict(fold=fold,case_type=label,n=totals[label]))
                positions=np.flatnonzero(mask)
                if not len(positions):continue
                k=int(positions[0]);flat=int(index[k]);station=stations.iloc[flat%data.n]
                examples.append(dict(fold=fold,case_type=label,available_cases=totals[label],
                    input_time=str(data.times[flat//data.n]),target_time=str(pd.Timestamp(data.tt[flat])),
                    station_id=int(station.station_id),station_name=station['name'],district=station.district,
                    current_quantity=float(q[k]),next_quantity=float(data.target[flat]),
                    basic_probability=float(example_probabilities['lightgbm_basic'][k]),
                    blend_probability=float(example_probabilities['M2_no_weather'][k]),
                    basic_warning=bool(basic[k]),blend_warning=bool(blend[k])))
            base_record=json.loads((OUT/fold/'lightgbm_basic.json').read_text(encoding='utf-8'))['validation']['current_positive']
            blend_record=json.loads((OUT/fold/'M2_no_weather.json').read_text(encoding='utf-8'))['validation']['current_positive']
            assert totals['기본 놓침-결합 포착']-totals['기본 포착-결합 놓침']==blend_record['tp']-base_record['tp']
            assert totals['기본 정상-결합 오경보']-totals['기본 오경보-결합 정상']==blend_record['fp']-base_record['fp']
    save(RECORD/'subgroup_cache_checks.json',dict(checked_at_utc=now(),checks=checks,all_aggregate_matches=all(c['aggregate_confusion_matches'] for c in checks),note='Only matching records enter descriptive groups. float32 probability cache may differ at numerical thresholds; this is checked, not assumed.'))
    if not pieces:raise RuntimeError('No matching descriptive scores')
    summary=pd.concat(pieces,ignore_index=True);frame_csv(summary,folder,'수량시간지역별_오류분석');frame_csv(pd.DataFrame(complement),folder,'모델오류상보성')
    if examples:
        frame_csv(pd.DataFrame(examples),folder,'기본과결합_판정사례')
        frame_csv(pd.DataFrame(case_counts),folder,'기본과결합_판정유형건수')
        save(RECORD/'example_selection.json',dict(created_at_utc=now(),selection='For each fold and six declared outcome types, first chronological valid current-positive row in preserved cohort order',
            examples=len(examples),case_type_rows=len(case_counts),post_result_illustration=True,
            model_or_threshold_changed=False,classification_only_not_observed_rental_success=True,
            source_results={f+'/'+m:digest(OUT/f/(m+'.json')) for f in 'ABC' for m in ('lightgbm_basic','M2_no_weather')},
            net_TP_and_FP_differences_match_saved_metrics=True))
    selection=summary[(summary.fold=='A')&(summary.dimension=='current_quantity')&(summary.model.isin(['persistence','lightgbm_basic','lightgbm_full','catboost_temporal','xgboost_temporal']))]
    pivot=selection.pivot(index='group',columns='model',values='recall').reindex(['0','1','2','3','4~5','6+'])
    fig,ax=plt.subplots(figsize=(10,4.2));pivot.mul(100).plot.bar(ax=ax,width=.8);ax.set(xlabel='입력 시각의 수량(대)',ylabel='다음 0대 재현율(%)',title='현재 0대 맞추기와 새로 비는 대여소 찾기를 구분한다');ax.legend(fontsize=7,ncol=2);ax.tick_params(axis='x',rotation=0);export(fig,folder,'현재수량별_재현율')
    notes=['# 오류 분석의 해석','','정해진 개발 구간의 보조 분석으로 사용한다. 이 표를 보고 임계값을 바꾼 뒤 같은 구간을 새 독립 시험으로 부르지 않는다.','',
        '현재 0대인 대여소가 계속 0대인 경우는 상태 유지 규칙도 잘 찾을 수 있다. 시민이 현재 양수 대여소 중 선택할 때의 문제는 현재 1대 이상에서 다음 0대로 바뀌는 부분집합으로 따로 해석한다.','',
        '캐시 확률로 복원한 전체 TP·FP·FN이 저장된 원래 지표와 맞는 경우만 세부 분석에 포함한다. 불일치가 있으면 임의 보정하지 않고 검수 JSON에 남긴다.','',
        '오류 상보성의 낮은 겹침은 결합 후보의 탐색 근거일 수 있으나 결합 성능 향상을 보장하지 않는다. M2의 50:50 가중치는 이 분석 전에 고정한 대조 구조를 유지한다.','']
    (folder/'오류분석_읽는법.md').write_text('\n'.join(notes),encoding='utf-8')
    print('ERROR_ANALYSIS_SAVED',len(checks),'checked results',flush=True)

if __name__=='__main__':main()
