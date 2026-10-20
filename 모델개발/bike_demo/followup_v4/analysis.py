"""Persist the seven E2E stages, analysis tables and exportable figures."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from .common import ROOT,OUT,RECORD,save,now,digest

E2E=RECORD/'E2E'
STAGES=['01_문제정의','02_탐색','03_큐레이션','04_전처리','05_모델링','06_평가','07_보고와재현']

def setup():
    plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False,'figure.dpi':130,'savefig.dpi':180,'axes.spines.top':False,'axes.spines.right':False})
    for stage in STAGES:(E2E/stage).mkdir(parents=True,exist_ok=True)

def export(fig,folder,name):
    fig.savefig(folder/(name+'.png'),bbox_inches='tight',facecolor='white')
    fig.savefig(folder/(name+'.svg'),bbox_inches='tight',facecolor='white')
    plt.close(fig)

def frame_csv(frame,folder,name):frame.to_csv(folder/(name+'.csv'),index=False,encoding='utf-8-sig')

def preparation():
    setup()
    with np.load(OUT/'data/panel_2025.npz') as p:q=p['quantity'];times=pd.DatetimeIndex(p['times']);ids=p['station_ids']
    stations=pd.read_parquet(ROOT/'data/processed/rider_stations.parquet')
    known=np.isfinite(q);target=np.vstack([q[1:],np.full((1,q.shape[1]),np.nan,np.float32)])
    valid=known&np.isfinite(target);y=target==0
    folder=E2E/STAGES[0]
    (folder/'문제정의.md').write_text('# 문제 정의\n\n현재 수량이 있는 대여소도 다음 시간에 비어 있을 수 있다. 현재 수량과 과거 변화로 다음 정시 격자에 0대가 될 위험을 예측한다. 시민의 대여소 선택을 돕는 기초 예측을 목적으로 한다. 실제 대여 실패·한 시간 내내 빈 상태·5분 뒤 도착 성공은 관측하지 않는다.\n\n정답 시각으로 기간을 나누고 전체 F1과 현재 양수의 전환 재현율·오경보를 함께 확인한다. 현재 상태 유지와 기존 M2 구조를 기준으로 사용한다.\n',encoding='utf-8')
    examples=[]
    for ti in range(120*24,120*24+5):
        for si in (0,1,2):examples.append(dict(station_id=int(ids[si]),input_time=str(times[ti]),quantity=q[ti,si],target_time=str(times[ti]+pd.Timedelta(hours=1)),next_quantity=target[ti,si],eligible=bool(valid[ti,si]),next_zero=int(y[ti,si]) if valid[ti,si] else None))
    frame_csv(pd.DataFrame(examples),folder,'정답정의_실제관측예시')
    fig,ax=plt.subplots(figsize=(11,2.3));ax.axis('off')
    labels=['문제 정의','탐색','큐레이션','전처리','모델링','평가','보고·재현']
    for i,label in enumerate(labels):
        x=(i+.5)/7;ax.text(x,.6,f'{i+1}\n{label}',ha='center',va='center',transform=ax.transAxes,bbox=dict(boxstyle='round,pad=.55',fc='#e9f2ff',ec='#4383bd'),fontsize=11)
        if i<6:ax.annotate('',xy=((i+1.12)/7,.6),xytext=((i+.89)/7,.6),xycoords='axes fraction',arrowprops=dict(arrowstyle='->',color='#627586'))
    ax.text(.5,.08,'모든 단계의 표·그림·조건을 PC에 저장하고 같은 명령으로 다시 생성한다.',ha='center',transform=ax.transAxes)
    export(fig,folder,'E2E_7단계')
    folder=E2E/STAGES[1]
    daily=pd.DataFrame(dict(input_time=times,observed=known.sum(axis=1),zero=(q==0).sum(axis=1),mean_quantity=np.nanmean(q,axis=1))).set_index('input_time').resample('D').agg({'observed':'sum','zero':'sum','mean_quantity':'mean'}).reset_index()
    daily['coverage']=daily.observed/(24*q.shape[1]);daily['zero_rate']=daily.zero/daily.observed
    frame_csv(daily,folder,'일별관측과0대비율')
    hourly=[]
    for h in range(24):
        m=times.hour==h;v=valid[m];positive=(q[m]>0)&v
        hourly.append(dict(hour=h,eligible_rows=int(v.sum()),next_zero_rate=float((y[m]&v).sum()/v.sum()),current_positive_rows=int(positive.sum()),transition_rate=float((y[m]&positive).sum()/positive.sum())))
    hourly=pd.DataFrame(hourly);frame_csv(hourly,folder,'시간대별위험')
    profile=stations[['station_id','name','district','latitude','longitude']].copy();profile['observed_hours']=known.sum(axis=0);profile['coverage']=known.mean(axis=0);profile['zero_fraction']=np.divide((q==0).sum(axis=0),known.sum(axis=0),out=np.zeros(q.shape[1]),where=known.sum(axis=0)>0)
    frame_csv(profile,folder,'대여소별관측과0대비율')
    labels=['0','1','2','3','4~5','6~10','11~20','21+'];bounds=[0,1,2,3,4,6,11,21,np.inf];observed=q[known]
    counts=np.histogram(observed,bins=bounds)[0];dist=pd.DataFrame({'quantity':labels,'rows':counts,'fraction':counts/counts.sum()});frame_csv(dist,folder,'수량분포')
    fig,axes=plt.subplots(2,1,figsize=(10,5.7),sharex=True);axes[0].plot(daily.input_time,100*daily.coverage,color='#2571a9');axes[0].set(ylabel='유효 관측 비율(%)',title='2025년 1~6월: 관측 누락과 0대 비율');axes[0].set_ylim(0,103)
    axes[1].plot(daily.input_time,100*daily.zero_rate,color='#d47134');axes[1].set(ylabel='관측 중 0대(%)',xlabel='날짜');fig.autofmt_xdate();export(fig,folder,'일별자료품질과0대')
    fig,axes=plt.subplots(1,2,figsize=(11,3.5));axes[0].bar(labels,100*dist.fraction,color='#408dc3');axes[0].set(title='시간별 관측 수량의 분포',xlabel='자전거 수량(대)',ylabel='관측 비율(%)')
    axes[1].plot(hourly.hour,100*hourly.next_zero_rate,label='전체 다음 시간 0대');axes[1].plot(hourly.hour,100*hourly.transition_rate,label='현재 양수 → 다음 0대');axes[1].set(title='같은 정답도 분모에 따라 위험이 다르다',xlabel='입력 시각(시)',ylabel='발생 비율(%)');axes[1].legend(fontsize=8);export(fig,folder,'수량분포와시간대위험')
    fig,ax=plt.subplots(figsize=(6,6));points=ax.scatter(profile.longitude,profile.latitude,c=100*profile.zero_fraction,s=7,cmap='YlOrRd',vmin=0,vmax=50,alpha=.7);fig.colorbar(points,ax=ax,label='관측 중 0대 비율(%)');ax.set(title='대여소별 빈 대여소 관측 비율',xlabel='경도',ylabel='위도');export(fig,folder,'대여소위치와0대비율')
    summary=dict(observed_hours=int(known.sum()),eligible_next_hour_rows=int(valid.sum()),next_zero_cases=int((y&valid).sum()),next_zero_rate=float((y&valid).sum()/valid.sum()),current_positive_zero_cases=int((y&valid&(q>0)).sum()),stations=q.shape[1],start=str(times[0]),end=str(times[-1]),new_test_quarters_read=False)
    save(folder/'탐색요약.json',summary)
    folder=E2E/STAGES[2];audit=[]
    for year in (2024,2025):
        a=json.loads((OUT/f'data/audit_{year}.json').read_text(encoding='utf-8'))
        for month in a['monthly']:audit.append(dict(year=year,**month))
    frame_csv(pd.DataFrame(audit),folder,'원본월별검수')
    yearly=[json.loads((OUT/f'data/audit_{year}.json').read_text(encoding='utf-8')) for year in (2024,2025)]
    fig,ax=plt.subplots(figsize=(8,3.7));x=np.arange(2);bottom=np.zeros(2)
    for field,label,color in [('observed_cells','유효 관측','#3a8ac0'),('conflicting_keys_excluded','서로 다른 중복값 제외','#dd7c3e')]:
        values=np.array([a[field] for a in yearly])/1e6;ax.bar(x,values,bottom=bottom,label=label,color=color);bottom+=values
    remaining=np.array([a['missing_cells']-a['conflicting_keys_excluded'] for a in yearly])/1e6
    ax.bar(x,remaining,bottom=bottom,label='그 외 결측',color='#cfd7df')
    ax.set(xticks=x,xticklabels=['2024년 전체','2025년 1~6월'],ylabel='대여소×시간 격자(백만)',title='원본 검수: 결측과 충돌을 0대로 채우지 않는다')
    ax.legend(fontsize=8);export(fig,folder,'자료선별결과')
    (folder/'선별원칙.md').write_text('# 자료 선별\n\n같은 대여소·시각에 같은 값이 반복되면 한 관측으로 합친다. 값이 서로 다르면 원인을 입증하기 전까지 해당 키 전체를 제외한다. 값들을 더하거나 마지막 행을 정답으로 고르지 않는다. 기존 대여소 코호트와 좌표를 유지한다. 2024년 7월 표본에서는 같은 이름·시각에 7·8·9대가 함께 나타나는 사례를 확인한다. 세부 원인은 확인하지 못한다.\n\n공개 달력·기록 시각에 맞는 수량과, 사후 수정 가능성이 남은 기상·행사 스냅샷을 분리한다. 원본 파일·해시·API 상태는 상위 기록과 audit JSON에 보존한다.\n',encoding='utf-8')
    folder=E2E/STAGES[3];meta=json.loads((OUT/'data/features_2025.json').read_text(encoding='utf-8'));matrix=np.load(OUT/'data/features_2025.npy',mmap_mode='r')
    train=np.flatnonzero(valid.ravel()&(np.repeat((times+pd.Timedelta(hours=1)).to_numpy(),q.shape[1])<np.datetime64('2025-05-01')))
    sample=np.sort(np.random.default_rng(20261020).choice(train,min(100000,len(train)),replace=False));stats=[]
    for j,name in enumerate(meta['columns']):
        values=np.asarray(matrix[sample,j]);good=values[np.isfinite(values)]
        stats.append(dict(feature=name,sample_rows=len(values),missing_rate=float(1-len(good)/len(values)),min=float(good.min()) if len(good) else None,median=float(np.median(good)) if len(good) else None,max=float(good.max()) if len(good) else None))
    frame_csv(pd.DataFrame(stats),folder,'학습과거표본_특징검수_10만행')
    frame_csv(pd.DataFrame([{'group':k,'features':len(v),'names':' / '.join(v)} for k,v in meta['groups'].items()]),folder,'특징집합')
    fig,ax=plt.subplots(figsize=(10,3.7));cfg=json.loads((RECORD/'protocol.json').read_text(encoding='utf-8'))
    colors=['#d5dce3','#94bddb','#8dc6b6','#e3b768','#377db5'];legend={}
    for i,(fold,s) in enumerate(cfg['development_folds'].items()):
        spans=[(pd.Timestamp(s['train_end'])-pd.Timedelta(days=56),pd.Timestamp(s['train_end']),'학습')]+[(pd.Timestamp(s[k+'_start']),pd.Timestamp(s[k+'_end']),label) for k,label in [('stop','조기 종료'),('cal','확률 보정'),('policy','임계값'),('valid','바깥 개발 검증')]]
        for color,(start,end,label) in zip(colors,spans):
            bar=ax.barh(i,(end-start).days,left=matplotlib.dates.date2num(start),color=color,label=label);legend[label]=bar
    ax.set_yticks(range(3),list('ABC'));ax.xaxis_date();ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter('%m-%d'));ax.set(title='정답 시각으로 다섯 구간을 나눈다',ylabel='개발 폴드');ax.legend(legend.values(),legend.keys(),ncol=5,fontsize=8,loc='upper center',bbox_to_anchor=(.5,-.14));export(fig,folder,'시간분할')
    (folder/'전처리설명.md').write_text('# 전처리\n\n현재 및 다음 격자가 관측된 행만 정답을 만든다. 과거 시차·최근 평균·상태 지속·주변 수량·과거 전환 빈도를 구성한다. 미래 수량을 바꿔도 앞선 특징이 바뀌지 않는 검사를 수행한다. 청크 경계에서 긴 상태 지속과 전환 통계를 초기화하지 않는다.\n\n트리의 결측 처리는 해당 학습기에 맡기고 선형·RF의 대치값과 선형 표준화는 학습 행에서만 적합한다. 확률 보정·임계값 선택은 각각 별도 기간에서 수행한다. 특징 검수 CSV는 5월 1일 전 과거의 고정 무작위 10만행 표본으로 작성하며 전체 자료 통계로 주장하지 않는다.\n',encoding='utf-8')
    save(E2E/'preparation_complete.json',dict(updated_at_utc=now(),stages_completed=STAGES[:4],source_hashes={f'panel_{year}':digest(OUT/f'data/panel_{year}.npz') for year in (2024,2025)},analysis_code_sha256=digest(__file__),sample_seed=20261020))
    print('E2E_PREPARATION_COMPLETE',flush=True)

def evaluation():
    setup();from .runner import refresh
    from .brief import NAMES
    def display(name,track):
        label=NAMES.get(name,name)
        if any(s in str(track) for s in ('archive','sensitivity')) and not label.endswith('(조건부)'):
            label+=' (조건부)'
        return label
    refresh();df=pd.read_csv(RECORD/'leaderboard.csv');folder=E2E/STAGES[5]
    frame_csv(df,folder,'완료한모든실험')
    baselines=['persistence','seasonal_day','seasonal_week','smoothed_markov','logistic','random_forest','lightgbm_basic','lightgbm_temporal','lightgbm_full','catboost_temporal','xgboost_temporal','M2_no_weather','M2_refit']
    differences=[]
    for fold,g in df.groupby('fold'):
        for baseline in baselines:
            b=g[g.model==baseline]
            if b.empty:continue
            b=b.iloc[0]
            for _,r in g.iterrows():
                if r.model==baseline:continue
                differences.append(dict(fold=fold,model=r.model,baseline=baseline,F1=r.F1,baseline_F1=b.F1,delta_F1_pp=100*(r.F1-b.F1),relative_F1_change_percent=100*(r.F1/b.F1-1),delta_transition_recall_pp=100*(r.transition_recall-b.transition_recall),FP_change=int(r.FP-b.FP),FN_change=int(r.FN-b.FN),same_information_track=r.track==b.track))
    frame_csv(pd.DataFrame(differences),folder,'각베이스라인대비변화')
    complete=df.groupby('model').filter(lambda x:set(x.fold)==set('ABC'))
    if not complete.empty:
        pivot=complete.pivot(index='model',columns='fold',values='F1');pivot=pivot.loc[pivot.mean(axis=1).sort_values().index]
        tracks=complete.groupby('model').track.first()
        pivot.index=[display(name,tracks[name]) for name in pivot.index]
        fig,ax=plt.subplots(figsize=(11,max(4.5,len(pivot)*.43)))
        pivot.mul(100).plot.barh(ax=ax,width=.8,color=['#b1cfe5','#648fb7','#204b75'])
        ax.set(xlabel='F1(%)',ylabel='',title='같은 세 개발 기간의 비교')
        ax.legend(title='검증 기간',loc='upper center',bbox_to_anchor=(.5,-.13),ncol=3,fontsize=9)
        export(fig,folder,'세기간_F1비교')
    a=df[df.fold=='A'].copy();a=a.sort_values('F1')
    fig,ax=plt.subplots(figsize=(11,max(4,len(a)*.34)));colors=['#d68847' if any(s in str(t) for s in ('archive','sensitivity')) else '#3b80b3' for t in a.track]
    labels=[display(r.model,r.track) for r in a.itertuples()]
    ax.barh(labels,100*a.F1,color=colors);ax.set(xlabel='A 개발 검증 F1(%)',title='A 예비 비교: 파랑=수량·달력 / 주황=아카이브 보조');export(fig,folder,'A_전체후보_F1')
    fig,ax=plt.subplots(figsize=(9,5))
    representative={'persistence':'현재 상태 유지','lightgbm_basic':'기본 LightGBM','lightgbm_temporal':'시간 특징 LightGBM','lightgbm_full':'공간·전환 LightGBM','catboost_temporal':'CatBoost','xgboost_temporal':'XGBoost','M2_no_weather':'50:50 결합'}
    for name,label in representative.items():
        selected=a[a.model==name]
        if selected.empty:continue
        r=selected.iloc[0];ax.scatter(100*r.transition_recall,100*r.transition_precision,s=40,label=label)
    ax.legend(loc='upper left',bbox_to_anchor=(1,1),fontsize=8)
    ax.set(xlabel='현재 양수의 0대 전환 재현율(%)',ylabel='현재 양수의 0대 전환 정밀도(%)',title='대표 모델의 전환 위험 포착과 오경보를 함께 확인한다');export(fig,folder,'전환위험_정밀도재현율')
    models=[]
    for f in ('A','B','C'):
        for path in (OUT/f).glob('*.json'):
            r=json.loads(path.read_text(encoding='utf-8'))
            if r.get('status')=='ok':models.append(dict(fold=f,name=r['name'],kind=r['spec'].get('kind','blend'),train_rows=r['actual_train_rows'],features=len(r.get('features',[])),seconds=r['seconds'],peak_process_gib=r.get('memory',{}).get('peak_rss_gib'),spec=json.dumps(r['spec'],ensure_ascii=False)))
    frame_csv(pd.DataFrame(models),E2E/STAGES[4],'실행모델과학습조건')
    save(E2E/'evaluation_snapshot.json',dict(updated_at_utc=now(),completed=len(models),independent_final_test=False,analysis_code_sha256=digest(__file__)))
    print('E2E_EVALUATION_SAVED',len(models),flush=True)

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=['prepare','evaluate'],required=True);args=parser.parse_args()
    preparation() if args.phase=='prepare' else evaluation()
