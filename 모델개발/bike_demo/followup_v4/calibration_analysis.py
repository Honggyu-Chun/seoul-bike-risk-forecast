"""Descriptive reliability diagnostics from frozen development predictions only."""
import json
import joblib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.special import logit
from .common import OUT, RECORD, save, digest, now, protocol
from .runner import Inputs
from .analysis import setup, export, E2E, STAGES
from .brief import NAMES


def bin_summary(y, probability, bins=10):
    y = np.asarray(y, dtype=np.uint8)
    p = np.asarray(probability, dtype=np.float64)
    if y.ndim != 1 or p.shape != y.shape or not len(y) or not np.isin(y, [0,1]).all():
        raise ValueError('Require nonempty, matching binary target and probability vectors')
    if not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
        raise ValueError('Probability must be finite and between zero and one')
    edges = np.linspace(0,1,bins+1)
    codes = np.searchsorted(edges[1:-1], p)
    count = np.bincount(codes, minlength=bins)
    positive = np.bincount(codes, weights=y, minlength=bins)
    sums = np.bincount(codes, weights=p, minlength=bins)
    mean = np.divide(sums,count,out=np.full(bins,np.nan),where=count>0)
    fraction = np.divide(positive,count,out=np.full(bins,np.nan),where=count>0)
    table = pd.DataFrame(dict(bin=np.arange(bins), lower=edges[:-1], upper=edges[1:],
                              n=count, positive=positive.astype(np.int64), mean_predicted=mean,
                              observed_fraction=fraction))
    ece = float(np.nansum(count * np.abs(fraction-mean)) / len(y))
    return table, ece


def main():
    setup()
    path = RECORD/'calibration_diagnostic_plan.json'
    plan = json.loads(path.read_text(encoding='utf-8'))
    if not all((OUT/f/(m+'.json')).exists() for f in plan['folds'] for m in plan['models']):
        print('CALIBRATION_DIAGNOSTICS_WAIT_FOR_COMPLETE_THREE_FOLD_CONTROLS',flush=True)
        return
    data = Inputs()
    tables, scores, checks = [], [], []
    folder = E2E/STAGES[5]
    fig, axes = plt.subplots(2,3,figsize=(12.5,8),sharex=True,sharey=True)
    colors = ['#3d7d9f','#b8743e','#39836f']
    for column,fold in enumerate(plan['folds']):
        split = protocol()['development_folds'][fold]
        index = data.indices(split['valid_start'],split['valid_end'])
        y = (data.target[index]==0).astype(np.uint8)
        quantity = data.q.ravel()[index]
        for name,color in zip(plan['models'],colors):
            result_path = OUT/fold/(name+'.json')
            record = json.loads(result_path.read_text(encoding='utf-8'))
            raw = np.load(OUT/'predictions'/fold/(name+'_valid.npy'))
            if record['spec'].get('kind') in ('lgb','cat','linear','forest','poisson','ordinal','experts'):
                raw = raw.astype(np.float64)
            cal = joblib.load(OUT/fold/'models'/(name+'_calibrator.joblib'))
            p = cal.predict_proba(logit(np.clip(raw,1e-6,1-1e-6)).reshape(-1,1))[:,1].astype(np.float32)
            for row,group in enumerate(plan['groups']):
                mask = np.ones(len(y),dtype=bool) if group=='overall' else quantity>0
                yy, pp, rr = y[mask],p[mask],raw[mask]
                table,ece = bin_summary(yy,pp,plan['uniform_bins'])
                table.insert(0,'group',group);table.insert(0,'model',name);table.insert(0,'fold',fold)
                tables.append(table)
                brier = float(np.mean((pp.astype(np.float64)-yy)**2))
                raw_brier = float(np.mean((rr.astype(np.float64)-yy)**2))
                expected = record['validation'][group]
                raw_expected = record['raw_validation'][group]
                assert len(yy)==expected['n'] and int(yy.sum())==expected['zero_cases']
                error = abs(brier-expected['brier']);raw_error = abs(raw_brier-raw_expected['brier'])
                assert error < plan['saved_score_tolerance'] and raw_error < plan['saved_score_tolerance']
                assert int(table.n.sum())==len(yy) and int(table.positive.sum())==int(yy.sum())
                scores.append(dict(fold=fold,model=name,group=group,n=len(yy),zero_cases=int(yy.sum()),
                                   raw_brier=raw_brier,calibrated_brier=brier,brier_change=brier-raw_brier,
                                   descriptive_10bin_ece=ece,mean_predicted=float(pp.mean()),observed_rate=float(yy.mean())))
                checks.append(dict(fold=fold,model=name,group=group,brier_difference_from_saved=error,
                                   raw_brier_difference_from_saved=raw_error,result_sha256=digest(result_path),passed=True))
                filled=table[table.n>0]
                axes[row,column].plot(100*filled.mean_predicted,100*filled.observed_fraction,'o-',color=color,markersize=3,label=NAMES[name])
            print('CALIBRATION_DIAGNOSED',fold,name,flush=True)
        for row in range(2):
            axes[row,column].plot([0,100],[0,100],'--',color='#999999',linewidth=1)
            axes[row,column].set(xlim=(-2,102),ylim=(-2,102),title=f'{fold} · '+('전체 기록' if row==0 else '현재 양수 기록'))
            if column==0:axes[row,column].set_ylabel('실제 다음 0대 비율(%)')
            if row==1:axes[row,column].set_xlabel('평균 예측 위험(%)')
    handles,labels=axes[0,0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',ncol=3,bbox_to_anchor=(.5,0),fontsize=9)
    fig.suptitle('위험 확률과 실제 0대 비율: 개발 기간의 보정 후 진단',fontsize=14)
    fig.tight_layout(rect=(0,.05,1,.96));export(fig,folder,'확률과실제위험_비교')
    bins = pd.concat(tables,ignore_index=True)
    frame = pd.DataFrame(scores)
    bins.to_csv(folder/'확률구간별_실제위험과표본수.csv',index=False,encoding='utf-8-sig')
    frame.to_csv(folder/'확률보정_전후와구간오차.csv',index=False,encoding='utf-8-sig')
    fig,axes=plt.subplots(1,2,figsize=(12,5.5))
    for ax,group,title in zip(axes,plan['groups'],['전체 기록','현재 양수 기록']):
        subset=frame[frame.group==group]
        y=np.arange(len(subset));labels=[r.fold+' · '+NAMES[r.model] for r in subset.itertuples()]
        changes=subset.brier_change.to_numpy()
        ax.barh(y,changes,color=['#b8743e' if v>0 else '#397d9f' for v in changes])
        ax.axvline(0,color='#777777',linewidth=1)
        pad=max(1e-6,float(np.max(np.abs(changes)))*.04)
        for i,value in enumerate(changes):
            ax.text(value+(pad if value>=0 else -pad),i,f'{value:+.6f}',va='center',ha='left' if value>=0 else 'right',fontsize=7)
        ax.margins(x=.3);ax.ticklabel_format(axis='x',style='plain',useOffset=False)
        ax.set_yticks(y,labels,fontsize=8);ax.set(title=title,xlabel='Brier 점수(낮을수록 예측 오차가 작음)')
        ax.set_xlabel('Brier 변화(보정 후 - 보정 전, 음수면 개선)')
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color='#397d9f',label='점수 감소(개선)'),Patch(color='#b8743e',label='점수 증가(악화)')],loc='lower center',ncol=2,bbox_to_anchor=(.5,0),fontsize=9)
    fig.tight_layout(rect=(0,.05,1,1));export(fig,folder,'확률보정_전후_Brier')
    save(RECORD/'calibration_diagnostic_checks.json',dict(checked_at_utc=now(),plan_sha256=digest(path),
         code_sha256=digest(__file__),checks=checks,all_passed=True,bin_rows=len(bins),score_rows=len(frame),
         training_or_recalibration_performed=False,independent_final_test=False,
         note='Stored raw probabilities are float32; reconstructed Brier matches frozen scores within declared tolerance.'))
    print('CALIBRATION_DIAGNOSTICS_SAVED',len(bins),'bin rows',len(frame),'score rows',flush=True)


if __name__=='__main__':main()
