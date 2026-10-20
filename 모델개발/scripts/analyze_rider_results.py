"""Turn frozen evaluation outputs into readable, evidence-backed analysis and figures."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import os
os.environ.setdefault('MPLCONFIGDIR',str(Path(__file__).resolve().parents[1]/'.cache/matplotlib'))
Path(os.environ['MPLCONFIGDIR']).mkdir(parents=True,exist_ok=True)
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from bike_demo.data import ROOT

OUT=ROOT/'artifacts/evaluation';FIG=OUT/'figures';FIG.mkdir(exist_ok=True)
plt.rcParams.update({'font.family':'Noto Sans CJK KR','axes.unicode_minus':False,'figure.facecolor':'white','axes.spines.top':False,'axes.spines.right':False,'font.size':10})
read=lambda name:json.loads((OUT/name).read_text())
m=read('model_results.json');r=read('recommendation_results.json');sub=read('subgroup_metrics.json');quality=read('data_quality.json');split=read('split_manifest.json')
metrics=m['metrics'];b=metrics['B1'];p=metrics['M1'];r0=r['metrics']['R0'];r1=r['metrics']['R1'];r2=r['metrics']['R2']
importance=read('feature_importance.json')['M1'];total_gain=sum(x['gain'] for x in importance)
neighbor_gain=sum(x['gain'] for x in importance if x['feature'].startswith('neighbor') or x['feature']=='own_minus_neighbor')
insights={
 'model_f1_delta_pp':100*(p['f1']-b['f1']),
 'recall_delta_pp':100*(p['recall']-b['recall']),
 'precision_delta_pp':100*(p['precision']-b['precision']),
 'false_negatives_reduced':b['fn']-p['fn'], 'false_positives_added':p['fp']-b['fp'],
 'effective_recommendations_added_vs_distance':r2['valid_recommendations']-r0['valid_recommendations'],
 'effective_recommendations_added_vs_basic_ai':r2['valid_recommendations']-r1['valid_recommendations'],
 'distance_baseline_ceiling_headroom_pp':100*(1-r0['effective_rate']),
 'original_5pp_service_goal_exceeds_mathematical_ceiling':r0['effective_rate']+.05>1,
 'current_positive_transition_recall':sub['current_positive']['M1']['recall'],
 'current_positive_transition_f1_delta_pp':100*(sub['current_positive']['M1']['f1']-sub['current_positive']['B1']['f1']),
 'neighbor_feature_gain_share':neighbor_gain/total_gain,
 'current_quantity_gain_share':next(x['gain'] for x in importance if x['feature']=='quantity')/total_gain,
 'f1_delta_bootstrap_interval_contains_zero':m['day_block_bootstrap_f1_delta_pp']['lower_2_5']<=0<=m['day_block_bootstrap_f1_delta_pp']['upper_97_5'],
 'field_success_verified':False,
 'conclusion':'Prototype works and comparison is reproducible. Spatial-model superiority and practical rider benefit are not established; transition detection needs substantial improvement.'}
(OUT/'analysis_summary.json').write_text(json.dumps(insights,ensure_ascii=False,indent=2))

def save(name):
 plt.tight_layout();plt.savefig(FIG/name,dpi=160,bbox_inches='tight');plt.close()

names=['B0','B1S','B1','M1'];colors=['#b9bebb','#c6ccc9','#74817b','#237b62']
fig,ax=plt.subplots(figsize=(8,4))
bars=ax.bar(names,[100*metrics[n]['f1'] for n in names],color=colors,width=.6)
ax.set_ylim(0,100);ax.set_ylabel('F1 (%)');ax.set_title('전체 F1: 제안 모델의 추가 이득은 작음')
for bar,n in zip(bars,names):ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+1,f'{metrics[n]["f1"]*100:.3f}',ha='center',fontsize=10)
ax.text(.02,.07,f'M1 − B1 = {insights["model_f1_delta_pp"]:+.3f}%p  |  당초 목표 +5%p 미달',transform=ax.transAxes,fontsize=10)
save('model_f1.png')
labels=['전체','현재 1대 이상','현재 1~3대','출퇴근 기준 시각'];groups=[None,'current_positive','current_1_to_3','commute']
fig,ax=plt.subplots(figsize=(8.2,4));x=np.arange(4)
for offset,name,color in [(-.18,'B1','#85928b'),(.18,'M1','#237b62')]:
 values=[100*(metrics[name] if group is None else sub[group][name])['f1'] for group in groups]
 bars=ax.bar(x+offset,values,.34,label=name,color=color)
 for bar,v in zip(bars,values):ax.text(bar.get_x()+bar.get_width()/2,v+1,f'{v:.2f}',ha='center',fontsize=8)
ax.set_xticks(x,labels);ax.set_ylim(0,100);ax.set_ylabel('F1 (%)');ax.legend(frameon=False);ax.set_title('전체 점수만으로는 대여자에게 중요한 전환을 설명할 수 없음');save('subgroup_f1.png')
fig,ax=plt.subplots(figsize=(8,4));bars=ax.bar(['R0 거리 기준','R1 기본 AI','R2 제안 AI'],[r['metrics'][n]['requests']-r['metrics'][n]['valid_recommendations'] for n in ['R0','R1','R2']],color=['#b9bebb','#74817b','#237b62'])
ax.set_ylim(0,310);ax.set_ylabel('유효 추천을 제공하지 못한 요청 수');ax.set_title(f'같은 {r["common_eligible_queries"]:,}개 요청에서 비교')
for bar in bars:ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+5,f'{int(bar.get_height())}개',ha='center')
save('recommendation_failures.png')
ci=m['day_block_bootstrap_f1_delta_pp'];mid=insights['model_f1_delta_pp']
fig,ax=plt.subplots(figsize=(8,2.8));ax.axvline(0,color='#8e9892',linestyle='--');ax.errorbar(mid,0,xerr=[[mid-ci['lower_2_5']],[ci['upper_97_5']-mid]],fmt='o',color='#237b62',capsize=9,markersize=8)
ax.set_yticks([]);ax.set_xlabel('M1 − B1 F1 변화 (%p)');ax.set_title('일 단위 재표집의 탐색적 95% 범위에 0이 포함됨');ax.set_ylim(-.8,.8);save('delta_uncertainty.png')
fig,ax=plt.subplots(figsize=(8,3.4));ss=split['splits'];bars=ax.barh(['학습 1~2월','검증 3/1~14','시험 3/15~31'],[ss[n]['rows']/1e6 for n in ['train','validation','test']],color=['#b8c4bd','#87998d','#237b62'])
for bar,n in zip(bars,['train','validation','test']):ax.text(bar.get_width()+.04,bar.get_y()+bar.get_height()/2,f'{ss[n]["rows"]:,}건',va='center')
ax.set_xlim(0,4.7);ax.set_xlabel('평가 가능한 대여소×다음 한 시간 자료 (백만 건)');ax.invert_yaxis();ax.set_title('모델 선택은 검증기간에서, 최종 결과는 뒤의 기간에서');save('time_split.png')
fig,ax=plt.subplots(figsize=(8,4.5));top=importance[:10][::-1];ax.barh([x['feature'] for x in top],[100*x['gain']/total_gain for x in top],color=['#a6b5ab' if not x['feature'].startswith('neighbor') else '#237b62' for x in top]);ax.set_xlabel('전체 gain에서 차지하는 비율 (%)');ax.set_title('현재 수량의 비중이 큼 · 중요도는 인과효과가 아님');save('feature_importance.png')
curves=read('learning_curves.json');selection=read('selection_frozen_before_test.json')['models']
fig,ax=plt.subplots(figsize=(8,3.5))
for name,color in [('B1','#74817b'),('M1','#237b62')]:
 history=curves[f'{name}_{selection[name]["candidate"]}']['validation']['binary_logloss'];ax.plot(np.arange(len(history))+1,history,label=name,color=color)
ax.set_xlabel('학습 반복');ax.set_ylabel('검증 로그 손실 · 작을수록 좋음');ax.set_title('모델 선택에 사용한 검증기간 학습 곡선');ax.legend(frameon=False);save('validation_curves.png')

pct=lambda v:f'{100*v:.3f}%'
lines=['# 따릉이 시민용 데모: 실제 실험 분석', '', '## 판단',
 '웹 데모·공식 실시간 입력·실제 모델 비교는 구현했다. 그러나 주변 정보를 더한 M1의 우수성과 시민의 헛걸음 감소는 입증하지 못했다. 전체 F1의 작은 증가와 사용자 후보 구간의 낮은 전환 탐지율을 분리해서 해석해야 한다.', '',
 '## 데이터와 정답',f'- 서울시 2025년 1분기 수량 원본 {quality["source_rows"]:,}행, 원본 번호 {quality["source_station_ids"]:,}개. 월별로는 각각 {quality["monthly"][0]["rows"]:,}/{quality["monthly"][1]["rows"]:,}/{quality["monthly"][2]["rows"]:,}행.',
 f'- 학습기간만으로 정한 변화·양수 관측·좌표 조건의 {quality["cohort_station_ids"]:,}개 대여소. 제외 {quality["excluded_station_ids"]}개. 공식 운영 상태가 확인되었다는 뜻은 아니다.',
 f'- 분기 전체 도시 공통 누락 {quality["missing_city_hours"]}시간. 누락은 0이 아니다. 같은 대여소의 다음 격자 시각에 실제 기록이 있을 때만 다음 0대 여부의 정답으로 사용했다.',
 '- 원본 열 거치대수량과 공식 페이지의 대여가능 수량 설명 사이의 집계시점·운영상태 의미는 완전히 확정되지 않았다. 실제 대여 실패 정답으로 해석하지 않는다.', '',
 '## 기간과 모델 선택']
for n in ['train','validation','test']:
 row=ss[n];lines.append(f'- {n}: {row["target_start"]}~{row["target_end"]}, {row["rows"]:,}건, 0대 {row["zero_cases"]:,}건.')
lines += ['- 선택 설정·threshold·모델 해시는 selection_frozen_before_test.json에 최종 시험 예측 전 기록했다. B1/M1은 2개 후보, 240회 상한과 같은 조정 예산을 사용했다. 날씨는 공개시점 미확인으로 제외했다.',
 '- B0는 현재 0대 여부 유지. B1은 자기 정보 16개 특징. M1은 같은 LightGBM에 주변 특징 11개를 추가해 총 27개. B1S는 현재 수량·시간 등의 6개 특징으로 별도 학습한 실시간 입력 참고 모형이며 B1을 대체하지 않는다.', '',
 '## 실제 모델 결과', '|모델|F1|정밀도|재현율|놓친 0대|', '|---|---:|---:|---:|---:|']
for n in names:
 x=metrics[n];lines.append(f'|{n}|{pct(x["f1"])}|{pct(x["precision"])}|{pct(x["recall"])}|{x["fn"]:,}|')
lines += [f'- M1−B1 F1 {insights["model_f1_delta_pp"]:+.4f}%p, 재현율 {insights["recall_delta_pp"]:+.4f}%p, 정밀도 {insights["precision_delta_pp"]:+.4f}%p.',
 f'- 놓친 0대는 {insights["false_negatives_reduced"]:,}건 줄었지만 잘못된 위험 알림은 {insights["false_positives_added"]:,}건 늘었다. +5%p 목표 미달이다.',
 f'- 일 전체를 묶어 1,000번 짝지어 재표집한 F1 차이의 탐색적 95% 범위는 {ci["lower_2_5"]:.4f}~{ci["upper_97_5"]:.4f}%p. 0이 포함되므로 확실한 개선이라고 결론 내리지 않는다. 시험은 17일로 짧고 장기 시간상관·계절까지 입증하지 않는다.', '',
 '## 시민에게 중요한 구간',
 f'- 현재 양수인 곳의 다음 0대 전환: M1 재현율 {pct(sub["current_positive"]["M1"]["recall"])}, F1 {pct(sub["current_positive"]["M1"]["f1"])}. B1 F1 {pct(sub["current_positive"]["B1"]["f1"])}보다 낮았다.',
 '- 전체 0대 위험은 현재도 0대인 상태의 지속을 많이 포함한다. 시민은 현재 자전거가 있는 곳을 고르므로 전체 F1 75%만으로 서비스가 충분하다고 말할 수 없다.', '',
 '## 실제 추천 결과', '|방법|유효추천 수/전체|유효추천율|보류율|짝지은 추가 직선거리|', '|---|---:|---:|---:|---:|']
for n in ['R0','R1','R2']:
 x=r['metrics'][n];lines.append(f'|{n}|{x["valid_recommendations"]:,}/{x["requests"]:,}|{pct(x["effective_rate"])}|{pct(x["abstention_rate"])}|{x["paired_extra_distance_vs_R0_m"]:.2f}m|')
lines += [f'- 출발지 120개는 학습 코호트 좌표에서 고정 난수로 만든 가상 위치다. 고정 평일 7·8·18·19시 총 5,280개 요청 중 현재 양수·목표 정답 후보가 없는 56개를 공통 제외해 5,224개를 평가했다.',
 f'- R2는 R0보다 {insights["effective_recommendations_added_vs_distance"]}개 더 유효하지만 R1보다 {-insights["effective_recommendations_added_vs_basic_ai"]}개 적었다. 공간 정보로 추천이 더 좋아졌다고 말할 수 없다.',
 f'- R0가 이미 {pct(r0["effective_rate"])}여서 최대 추가 개선 여지는 {insights["distance_baseline_ceiling_headroom_pp"]:.3f}%p다. 초기 +5%p 서비스 목표는 이 시험에서 수학적으로 도달 불가능했다. 목표는 소급 수정하지 않았으며 후속 실험의 목표는 새 검증자료와 미사용 시험자료로 다시 설계해야 한다.',
 '- 유효추천율은 보류를 분모에 남긴다. 추천 적중률만 제시해 보류를 숨기지 않았다. 실제 사용자 이동·대여·고장·반납은 관측하지 않아 현장 성공률과 다르다.', '',
 '## 중요도와 실패',f'- 현재 수량의 gain 비중은 {100*insights["current_quantity_gain_share"]:.2f}%, 주변 특징 전체는 {100*insights["neighbor_feature_gain_share"]:.2f}%. gain은 모델 내부 분기 기여도이며 인과적 영향이나 실제 도로 연결을 뜻하지 않는다.',
 '- 실패 사례·대여소별 점수·일별 점수·추천 요청별 기록을 각각 false_negative_cases.csv, station_metrics.parquet, daily_metrics.csv, recommendation_queries.parquet에 보관한다.', '',
 '## 데모와 실시간 경계',
 '- 과거 화면은 실제 M1/B1의 고정 시험 결과를 재생한다. 성공 사례와 불리한 사례를 함께 고를 수 있다. 좌표 위치도는 실제 보행지도·경로가 아니다.',
 '- 실시간 화면은 공식 API의 현재 5곳 수량에 별도의 B1S를 실행한다. 현재 계절의 실제 미래 정답이 없으므로 정확도가 검증된 실서비스가 아니다. M1의 실시간 전체·과거 이력이 충족되었다고 표시하지 않는다.',
 '- 발급키 전체 조회, 데이터 갱신 지연, 안정적 수집, 짧은 간격 정답 생성과 새로운 기간별 검증은 후속 과제다.', '',
 '## 과제 평가에 대한 정직한 대응',
 '- 문제 정의·실제 데이터·구현·객관적 Baseline 비교·재현 문서는 준비했다. 반면 “벤치마크 대비 우수성”을 강하게 뒷받침할 결과는 아직 부족하다. 이 약점을 발표에서 감추지 않는다.',
 '- 후속 연구는 현재 양수·낮은 수량의 0대 전환 탐지와 사용자 선택의 비용에 맞춘 평가를 우선한다. 새 목표는 기존 시험 점수에 맞춰 소급 달성으로 바꾸지 않는다. 사용자 수요·기존 앱 기능·허용 추가 이동을 인터뷰로 확인한다.', '',
 '## 재현', '```bash', 'uv sync --frozen', 'uv run python -m bike_demo.train', 'uv run python -m bike_demo.evaluate_recommendations', 'uv run python scripts/analyze_rider_results.py', 'uv run python scripts/export_offline_demo.py', 'uv run pytest -q', 'bash scripts/run_demo.sh', '```',
 '모델 선택·시간 분할·raw 파일 확보는 README와 config/experiment_v1.json을 함께 확인한다. 이미 본 시험기간은 후속 개선의 새 독립 시험집합으로 재사용하지 않는다.']
(ROOT/'reports/rider_demo_analysis.md').write_text('\n'.join(lines)+'\n')
pd.DataFrame([{'model':n,**metrics[n]} for n in names]).to_csv(ROOT/'deliverables/실제_모델비교_결과.csv',index=False,encoding='utf-8-sig')
pd.DataFrame([{'method':n,**r['metrics'][n]} for n in ['R0','R1','R2']]).to_csv(ROOT/'deliverables/실제_추천비교_결과.csv',index=False,encoding='utf-8-sig')
print(json.dumps(insights,ensure_ascii=False,indent=2))
