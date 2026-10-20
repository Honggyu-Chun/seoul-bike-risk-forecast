"""Publish only measured research results; requires the frozen new test output."""
from pathlib import Path
import sys,json,csv,os
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.cache/matplotlib'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from make_final_rider_documents import Paper

BASE=ROOT/'artifacts/improvement';DEST=ROOT/'deliverables'
result=json.loads((BASE/'final/results.json').read_text())
freeze=json.loads((BASE/'final/selection_frozen_before_test.json').read_text())
upper=json.loads((ROOT/'reports/method_search/original_service_upper_bound.json').read_text())
fmt=lambda v:f'{100*v:.3f}%'
M=result['metrics'];R=result['recommendation']['metrics']
model_labels={'B0':'B0 · 현재 유지','B1':'B1 · 기본 모델','M2':'M2 · 제안 모델','B1_tuned':'B1+ · 추가 조정'}
records={}
for phase in ['screen','forward']:
 rows=[]
 for path in (BASE/phase).glob('*.json'):
  value=json.loads(path.read_text())
  if isinstance(value,dict) and value.get('status') in ['ok','error']:rows.append(value)
 records[phase]=rows
 with (DEST/f'성능개선_{phase}_검증비교.csv').open('w',encoding='utf-8-sig',newline='') as stream:
  writer=csv.writer(stream);writer.writerow(['모델','종류','상태','입력수','원래검증F1','재현율조건부검증F1','정밀도','재현율','소요초','학습표본수','오류','검증F1체크포인트사용','설정JSON','학습결과JSON'])
  for row in sorted(rows,key=lambda r:-r.get('selection_rule',r.get('rule',{})).get('f1',-1)):
   m=row.get('selection_validation',row.get('validation',{}))
   inputs='24시간×4채널+임베딩' if row['kind']=='neural' else len(row.get('features',[]))
   writer.writerow([row['name'],row['kind'],row['status'],inputs,row.get('rule',{}).get('f1'),row.get('selection_rule',{}).get('f1'),m.get('precision'),m.get('recall'),row.get('seconds'),row.get('fit',{}).get('sampled_train_rows','전체'),row.get('error',''),row['kind']=='neural' or row.get('spec',{}).get('select_f1',False),json.dumps(row.get('spec',{}),ensure_ascii=False),json.dumps(row.get('fit',{}),ensure_ascii=False)])
with (DEST/'성능개선_새시험_모델비교.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 writer=csv.writer(stream);writer.writerow(['모델','F1','정밀도','재현율','오경보','놓친위험','평가건수'])
 for name,m in M.items():writer.writerow([name,m['f1'],m['precision'],m['recall'],m['fp'],m['fn'],m['n']])
with (DEST/'성능개선_새시험_추천비교.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 writer=csv.writer(stream);writer.writerow(['방식','유효추천율','유효추천수','전체요청','보류율','추가직선거리m'])
 for name,m in R.items():writer.writerow([name,m['effective_rate'],m['valid_recommendations'],m['requests'],m['abstention_rate'],m['extra_distance_m']])
plt.rcParams.update({'font.family':'Noto Sans CJK KR','axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False,'font.size':10})
fig,ax=plt.subplots(figsize=(8,4));bars=ax.bar([model_labels.get(name,name) for name in M],[100*m['f1'] for m in M.values()],color=['#c4c4c4','#979797','#424242','#b4b4b4'][:len(M)])
ax.set_ylim(0,100);ax.set_ylabel('F1 (%)');ax.set_title('새로운 4/15~5/14 시험 · 서로 같은 자료에서 비교')
for bar,m in zip(bars,M.values()):ax.text(bar.get_x()+bar.get_width()/2,bar.get_height()+1,f'{100*m["f1"]:.3f}',ha='center')
fig.tight_layout();fig.savefig(BASE/'final/model_comparison.png',dpi=160);plt.close(fig)
successes=[r for r in records['screen'] if r['status']=='ok'];failures=[r for r in records['screen'] if r['status']=='error']
def friendly(name):
 row=next(r for r in records['forward'] if r['name']==name)
 learner={'lgb':'LightGBM','catboost':'CatBoost','xgboost':'XGBoost','experts':'현재 상태별 분리 모델','neural':'작은 신경망','poisson':'수량 회귀','quantile':'하위 수량 회귀','linear':'선형 모델','extra':'ExtraTrees','forest':'랜덤포레스트','markov':'전환 확률'}.get(row['kind'],row['kind'])
 inputs={'basic':'자기 정보','temporal':'장단기 수량 흐름','full':'시간·주변 정보','spatial_v1':'자기·주변500m 정보','hazard':'시간·전환 이력','hazard_spatial':'시간·전환·주변 정보','weather_own':'시간·전환·과거 날씨','weather_spatial':'시간·전환·주변·과거 날씨','sequence':'지난24시간의 수량·주변 값'}.get(row['spec']['group'],row['spec']['group'])
 return learner+' · '+inputs
lines=['# 성능 개선 연구의 실제 결과','',f'- 성공적으로 측정한 1차 후보: {len(successes)}개. 구현·실행 오류: {len(failures)}개. 같은 모델의 threshold 조합은 별도 모델 수로 부풀리지 않았다.',
f'- 새로운 시험: 2025-04-15~2025-05-14, {result["test_rows"]:,}건. 1~3월 학습 → 4/1~14 선택 → 선택·모델 해시 저장 후 새 시험 평가.',
f'- 기본 B1 F1 {fmt(M["B1"]["f1"])} → 제안 M2 {fmt(M["M2"]["f1"])}. 변화 {result["deltas"]["f1_pp"]:+.4f}%p.',
f'- 초기 목표 판정: {json.dumps(result["goals"],ensure_ascii=False)}.',
f'- 제안 구성: {json.dumps(result["proposal"],ensure_ascii=False)}.',
f'- 원래 추천 시험의 이상적 미래 정답 상한: {fmt(upper["maximum_rate_even_with_perfect_future_labels"])}. R0 대비 최대 {upper["oracle_improvement_pp_over_R0"]:.3f}%p로 +5%p보다 낮다.',
'','## 새 시험 모델 결과','|방법|F1|정밀도|재현율|놓친 위험|','|---|---:|---:|---:|---:|']
for name,m in M.items():lines.append(f'|{name}|{fmt(m["f1"])}|{fmt(m["precision"])}|{fmt(m["recall"])}|{m["fn"]:,}|')
lines+=['','## 추천 결과','|방식|유효추천율|유효 추천/전체|추가 직선거리|','|---|---:|---:|---:|']
for name,m in R.items():lines.append(f'|{name}|{fmt(m["effective_rate"])}|{m["valid_recommendations"]:,}/{m["requests"]:,}|{m["extra_distance_m"]:.2f}m|')
lines+=['','## 해석','F1 향상을 추천 효과나 실제 시민의 대여 성공과 동일시하지 않는다. R1u는 기본 B1에도 같은 보정·위험과거리 규칙을 쓴 대조군이고, Rq는 현재 수량만 활용하는 단순한 대조군이다. 이들과의 비교 없이 M2의 추가 가치를 입증했다고 표현하지 않는다.',
'광범위한 탐색을 했지만 가능한 모든 알고리즘을 소진했다는 뜻은 아니다. 신경망의 표본·계층·에포크 예산은 제한되어 있고, 원래 그래프 논문 전체를 재현했다고 주장하지 않는다.',
'','## 증빙','- reports/method_search/research_design.md: 방법의 출처·채택·보류와 실제 실행의 구분.',
'- artifacts/improvement/{screen,forward}: 모든 후보·검증 점수·모델·오류·소요 시간.',
'- artifacts/improvement/final/selection_frozen_before_test.json: 새 시험 전 선택·모델 해시.',
'- artifacts/improvement/final/results.json: 새 시험·구간별·추천·목표 판정·불확실성.',
'- config/improvement_v3.json: 목표·분할·제약 조건.','']
(ROOT/'reports/improvement_results.md').write_text('\n'.join(lines))

p=Paper()
p.page('01  성능 개선 연구 · 먼저 볼 결론',first=True)
p.para('더 많은 방법을 실제로 비교한 결과',True,21)
p.box('이번 작업',f'공식 구현·문서 16개를 확인하고, 1차 후보 {len(successes)}개를 실제 측정했습니다. 원본을 추가 확보하여 이미 본 3월 대신 4/15~5/14를 새 시험으로 사용했습니다.')
p.table(['지표','같은 새 시험에서의 결과'],[
 ('기본 모델 B1',fmt(M['B1']['f1'])+' F1'),('제안 모델 M2',fmt(M['M2']['f1'])+' F1'),('개선 폭',f'{result["deltas"]["f1_pp"]:+.4f}%p'),('F1 +5%p 목표','충족' if result['goals']['f1_5pp'] else '미달'),('재현율 비악화','충족' if result['goals']['recall_no_worse'] else '미충족')],[6,11.4])
p.note('새 시험 점수와 이전 3월 시험 점수를 섞어 개선 폭을 계산하지 않았습니다. 목표를 낮춰 달성으로 바꾸지 않았습니다.')
p.note('+5%p는 초기 팀 목표이며 학교가 정한 합격선은 아닙니다. 학교의 객관적 Baseline 비교 요구와 목표 달성 여부를 구분합니다.')
p.page('02  무엇을 달라지게 했나')
p.table(['방법 묶음','실제로 비교한 내용'],[
 ('시간·수량 흐름','다양한 과거 시차, 평균·변동, 주기, 품절·저수량 지속시간'),
 ('주변 흐름','250/500/1000m와 가까운5곳의 수량·부족·변화'),
 ('전환 이력','관측된 과거 전환만으로 대여소·시간·현재 상태별 경향 추정'),
 ('과거 관측 날씨','기상청 서울108 지점. 1·24시간 전 기온·강수·바람 등 추가 비교'),
 ('다른 학습기','LightGBM·CatBoost·XGBoost·선형·두 종류의 숲'),
 ('다른 목적·손실','불균형 가중치, Focal, Poisson·분위 수량 회귀, 상태별 전문가'),
 ('작은 신경망','MLP·시계열CNN·GRU·가중CNN. 원논문 전체 재현은 아님'),
 ('결정·추천','검증 기준 선택, 재현율 조건, 앙상블, 보정·위험과거리의 비용 비교'),
 ],[4.4,13])
p.note('많이 조사한 방법과 실제 실행한 방법을 구분했습니다. 후보별 입력·표본·학습 예산이 다르므로 알고리즘 자체의 보편적 우열을 주장하지 않습니다.')
p.note('일부 미완료 시도는 F1 기준으로 재개하기 위해 중단했고 로그를 보존했습니다. 후보 수·오류 수는 완료한 결과 파일 기준이며, 소요시간은 병행 학습 중 각 완료 실행의 벽시계 시간입니다.')
screen_by_name={r['name']:r for r in records['screen'] if r['status']=='ok'}
if all(name in screen_by_name for name in ['causal_weather_own','causal_weather_spatial']):
 a=screen_by_name['causal_weather_own'];b=screen_by_name['causal_weather_spatial']
 p.note(f'같은127개 잎·최소150표본·1,000회 상한·F1 선택 조건에서, 과거 날씨·전환 모델에 주변수량25개 특징을 더한 개발 검증 F1 차이는 {100*(b["rule"]["f1"]-a["rule"]["f1"]):+.3f}%p였습니다. 이는 개발 검증의 입력 비교이며 새 시험의 독립적인 공간 효과 입증과는 구분합니다.')
p.page('03  새 시험을 어떻게 지켰나')
p.box('시간의 순서','1~2월 학습·3/1~14 후보 탐색 → 1~3월로 다시 학습·4/1~14 선택 → 설정·파일 해시 저장 → 4/15~5/14 최종 확인')
p.bullet('대상 유지','기존 학습기간으로 정한2,729개 대여소를 유지했습니다. 점수를 올리려고 어려운 시험 대여소를 골라 빼지 않았습니다.')
p.bullet('미래 차단','전환 경향도 현재까지 관측한 전환만 누적합니다. 내일 결과로 오늘 입력을 만들지 않습니다. 기록 없음은0대로 바꾸지 않습니다.')
p.bullet('데이터 추가','서울시 공식2025년2분기 ZIP을 확보했습니다. 원본·변환·모델의 해시와 분할을 기록했습니다.')
p.bullet('날씨의 확인 범위','기상청2025년 서울 시간자료8,760행에서 관측시각이 입력보다 이른 값만 사용했습니다. 강수·적설의 빈칸은0으로 바꾸지 않았습니다. 사후 품질수정·실시간 게시 지연은 미확인이라 과거 자료의 민감도 비교로 해석합니다.')
p.bullet('이미 본 시험','3월15~31일은 이제 개발·학습 자료입니다. 새 독립 시험이라고 다시 부르지 않습니다.')
p.note('정답은 다음 시간 격자, 즉1시간 뒤의 실제 기록 수량0입니다. 그 시각에 기록이 없으면0대로 간주하지 않습니다. 원본의 시간 내 집계 의미·실제 대여 성공·고장 여부까지 검증한 결과는 아닙니다.')
p.page('04  최종 모델의 실제 성능')
p.picture(BASE/'final/model_comparison.png','같은 새 시험의 비교. 높은 점수를 실제 대여 성공률로 해석하지 않습니다.')
p.table(['방법','F1','정밀도','재현율'],[(model_labels.get(name,name),fmt(m['f1']),fmt(m['precision']),fmt(m['recall'])) for name,m in M.items()],[4.8,4.2,4.2,4.2])
p.note('정밀도는0대라고 경고한 것 중 맞은 비율, 재현율은 실제0대 중 경고로 잡은 비율입니다. F1은 두 비율의 균형입니다. 이번 정답은0대 여부이므로 F1을 주지표로 삼으며, 수량 차이를 재는L2·RMSE를 F1처럼 해석하지 않습니다.')
p.note('B1+는 같은 자기 정보16개로 더 조정한 기본 모델입니다. CSV·JSON의 B1_tuned와 같습니다. M2의 차이가 주변 정보 때문인지, 탐색 예산이 늘었기 때문인지 구분할 때 함께 봅니다.')
ci=result['f1_day_bootstrap']
p.note(f'목표 날짜별 짝지은 재표집의 탐색적95% 범위: {ci["lower_pp"]:+.4f}~{ci["upper_pp"]:+.4f}%p. 짧은 계절·과거 자료의 범위이며 연중·현장 효과의 보장은 아닙니다.')
p.page('05  실제 대여 후보 구간도 따로 본다')
group=result['subgroups']['current_positive']
p.box('왜 따로 보나','지금0대인 곳이 다음에도0대인 상황보다, 지금은 있지만 다음에 비어 버리는 상황이 대여자의 선택에 더 중요합니다.')
p.table(['현재 양수 구간','F1','재현율','놓친0대'],[(model_labels.get(name,name),fmt(m['f1']),fmt(m['recall']),f'{m["fn"]:,}') for name,m in group.items()],[5.4,4,4,4])
p.note(f'이 구간의 F1 변화는 {100*(group["M2"]["f1"]-group["B1"]["f1"]):+.3f}%p, 재현율 변화는 {100*(group["M2"]["recall"]-group["B1"]["recall"]):+.3f}%p입니다. 전체 F1 +5%p 목표의 판정을 이 구간 점수로 대신하지 않습니다.')
p.bullet('해석','이 구간이 충분하지 않으면 전체 F1이 높아도 헛걸음을 잘 막는 서비스라고 말할 수 없습니다. 실제 이동·대여 성공은 별도 사용자 시험이 필요합니다.')
p.bullet('제안 모델의 구성',' / '.join(f'{friendly(name)} ({100*weight:.0f}%)' for name,weight in zip(result['proposal']['members'],result['proposal']['weights'])))
p.note('두 모델을 섞을 경우 각각 얼마나 반영할지, 위험 경고를 띄울 기준과 추천의 거리 비용은 검증자료에서 정했습니다. 시험 결과를 보고 바꾸지 않았습니다.')
if result.get('weather_used_by_proposal'):p.note('최종 M2는 과거 관측 날씨를 포함합니다. 실제 서비스에서 같은 시점에 같은 품질의 날씨를 얻을 수 있는지는 아직 검증하지 않았습니다.')
p.page('06  추천은 더 강한 단순 기준과도 비교')
p.table(['방식','유효추천율','유효 건수/전체','추가 거리'],[(name,fmt(m['effective_rate']),f'{m["valid_recommendations"]:,}/{m["requests"]:,}',f'{m["extra_distance_m"]:.2f}m') for name,m in R.items()],[2,4.5,6.3,4.6])
p.bullet('R0','현재 양수인 가장 가까운 곳입니다.')
p.bullet('R1','기본 B1이 위험으로 판단한 곳을 제외하고 가까운 곳입니다.')
p.bullet('R1u','B1의 점수를 실제0대 비율에 맞춘 뒤 R2와 같은 위험·거리 기준으로 고릅니다. 추천 규칙의 영향과 모델의 영향을 구분하는 대조군입니다.')
p.bullet('Rq','현재 수량과거리만 활용하는 단순한 비교 기준입니다. AI가 아니어도 개선할 수 있는 부분을 확인합니다.')
p.bullet('R2','M2의 위험 점수와 추가 직선거리를 함께 따져 고릅니다. 점수를 실제 비율에 맞추는 작업은4월 첫주, 선택 기준을 정하는 작업은 둘째주에서 했습니다.')
p.note('가상 출발지120개·출퇴근 시각의 과거 대리평가입니다. 보류는 유효추천율의 분모에 남깁니다. 추가 거리는 추천한 요청에서 R0와 짝지은 평균 직선거리입니다. 실제 위치분포·보행 경로 효과는 미검증입니다.')
service_ci=result['recommendation']['day_bootstrap']['comparisons_R2_minus_baseline']
p.note('날짜별 짝지은 재표집의 탐색적95% 차이 범위: '+', '.join(f'R2−{name} {service_ci[name]["lower_pp"]:+.3f}~{service_ci[name]["upper_pp"]:+.3f}%p' for name in ['R0','R1u','Rq'])+'. 범위에0이 들어가면 차이의 방향을 확정하지 않습니다.')
stock=result['recommendation'].get('subgroups',{}).get('nearest_current_1_to_3',{})
if stock and stock['R0']['requests']:
 p.page('07  어디에 쓰나 · 가까운 곳에1~3대일 때')
 p.box('시민이 판단할 순간','가까운 대여소에1~3대만 보이면, 다른 후보의 현재 수량·다음 시간대0대 위험·추가 직선거리를 함께 비교하는 용도입니다. 실제 도착 몇 분 뒤를 맞혔다는 뜻은 아닙니다.')
 p.para(f'새 시험의 가상 요청 {R["R0"]["requests"]:,}건 중 {stock["R0"]["requests"]:,}건이 이 상황이었습니다. 시험 전에 정한 보조 구간이며 추천 기준은 전체 검증에서 정한 그대로 사용했습니다.')
 p.table(['방식','유효추천율','추가 직선거리'],[(name,fmt(stock[name]['effective_rate']),f'{stock[name]["extra_distance_m"]:.2f}m') for name in ['R0','R1u','Rq','R2']],[4.4,6.5,6.5])
 p.bullet('관측된 변화',f'이 구간의 R0 대비 R2 유효추천율 차이는 {100*(stock["R2"]["effective_rate"]-stock["R0"]["effective_rate"]):+.3f}%p였습니다. 수량 기준 Rq와 R2는 표의 비율을 보였으며, R2의 추가 직선거리는 더 짧았습니다.')
 p.bullet('주장의 범위','실제 시민의 대여 성공이나 보행 거리 절감을 입증한 결과는 아닙니다. 비슷해 보이는 비율이 통계적으로 동등함을 증명한 것도 아닙니다. 다음 현장 검증의 가설로 사용합니다.')
 p.note(f'R2의 이 구간 추가 직선거리는 평균 {stock["R2"]["extra_distance_m"]:.2f}m입니다. 전체 평균100m 이하 조건의 판정을 이 구간으로 바꾸지 않았고, 전체 F1·유효추천율 +5%p 목표도 그대로 유지합니다.')
p.page('08  도달할 수 없는 원래 목표도 있다')
p.para('기존3월 시험의 +5%p 추천 목표는\n어떤 모델로도 도달할 수 없습니다.',True,19)
p.table(['기존3월 시험','유효추천율'],[
 ('거리 기준 R0',fmt(upper['reproduced_R0']['effective_rate'])),
 ('미래 정답을 다 아는 이상적인 선택',fmt(upper['maximum_rate_even_with_perfect_future_labels'])),
 ('이상적 선택의 최대 개선 폭',f'+{upper["oracle_improvement_pp_over_R0"]:.3f}%p'),
 ('원래 요구한 개선 폭','+5.000%p'),
 ],[11,6.4])
p.para('일부 요청은 범위 안의 현재 양수 후보가 다음 시각에 모두0대가 됩니다. 완벽한 예측도 그 요청에 유효한 추천을 만들 수 없습니다. 후보·거리·분모를 바꾸면 다른 실험이 되므로, 그렇게 점수를 바꿔 기존 목표를 달성했다고 하지 않습니다.')
p.note('이는 해당 고정 시험·후보 조건의 수학적 상한입니다. 다른 기간의 모든 서비스 효과까지 불가능하다는 뜻은 아닙니다.')
new_oracle=result['recommendation']['perfect_future_oracle_rate']
new_headroom=100*(new_oracle-R['R0']['effective_rate'])
p.note(f'새4/15~5/14 시험도 별도로 계산했습니다. R0 {fmt(R["R0"]["effective_rate"])}, 미래 정답 참고 상한 {fmt(new_oracle)}, 같은 후보 안의 최대 개선 폭 {new_headroom:+.3f}%p입니다. '+('이 상한도 +5%p 미만입니다.' if new_headroom<5 else '새 시험의 +5%p는 이 상한 안에 있으나 이번 모델은 미달했습니다. 불가능함을 입증한 것은 기존3월 조건입니다.'))
p.page('09  목표 판정과 다음 결정')
goal_names={'f1_5pp':'기본 모델 대비 F1 +5%p','recall_no_worse':'기본 모델보다 재현율이 낮아지지 않음','effective_recommendation_5pp':'거리 기준 대비 유효추천율 +5%p','extra_distance_100m':'추가 직선거리 평균100m 이하'}
p.table(['초기 목표','판정'],[(goal_names.get(key,key),'충족' if value else '미충족') for key,value in result['goals'].items()],[12,5.4])
p.bullet('충족한 부분과 미달한 부분','실제 측정값과 목표 판정을 함께 남겼습니다. 개선이 있더라도 +5%p가 아니면 초기 목표 달성이라고 표시하지 않습니다.')
p.bullet('다음에 필요한 것','기존3월 시험의 불가능한 추천 목표와 새 시험의 목표 미달을 구분해야 합니다. 실제 대여자의 선택에 맞춘 우선 지표를 정리하고, 후속 실험에는 별도의 미사용 시험을 씁니다. 실시간5~10분 정확도에는 더 짧은 간격의 이력이 필요합니다.')
p.bullet('발표에서의 주장','동일 자료의 Baseline 비교, 실패·상한 분석, 별도 시험과 재현 증빙은 제시할 수 있습니다. 실제 우수성은 측정된 차이·불확실성·더 강한 단순 기준과의 비교 범위에서 설명합니다.')
p.note('본 연구가 가능한 모든 알고리즘을 소진했다거나 F1 향상 자체가 불가능함을 입증한 것은 아닙니다. 원래 추천 목표의 불가능성과 이번 탐색의 목표 미달을 구분합니다.')
p.page('10  학교 평가기준과 연결되는 증거')
p.note('초기 +5%p 목표를 학교의 합격선으로 바꾸지 않습니다. 평가에 제출할 증거와 실제로 입증한 범위를 구분합니다.')
p.table(['평가 항목','이번에 보강한 증거','주장할 수 있는 범위'],[
 ('모델 성능','새 기간 F1·정밀도·재현율, 현재 양수 구간의 별도 결과','전체 점수와 대여자에게 중요한 전환 탐지를 함께 설명'),
 ('Baseline 대비 우수성','같은 기간 B0·B1·조정한 B1+·M2와 날짜 재표집','개선 폭·불확실성 제시. +5%p 미달을 달성으로 표현하지 않음'),
 ('객관적인 검증','시간순 분할, 새 시험 전 모델·설정 해시, 복원 예측 일치 검사','이미 본3월과 새4~5월 시험을 명확히 구분'),
 ('구현 완성도','검색·이력·추천 비교·오프라인 재생, 결과 화면과 Word 다운로드','실제로 동작하는 연구 데모. 서울 전체 실시간 운영은 미검증'),
 ('적용 가능성','거리 기준·수량 기준·AI 추천의 유효율과 추가 거리','과거의 가상 요청 대리평가. 현장 대여 성공을 보장하지 않음'),
 ('문서화·발표','모든 후보CSV, 실험설정·코드·Word·데모·출처 기록','제출 자료 준비. 실제 발표·GitHub 공개·팀 기여 평가는 별도'),
 ],[3.1,7.1,7.2],size=9.7)
p.note('4인 분담 제안: A는 모델·오류 분석, B는 원본·수집·백엔드, C는 평가 재현·문서, D는 화면·사용자 확인을 맡습니다. 실제 담당·기여로 기록한 내용이 아닙니다. 상세 산출물은 기존 팀원용 설명서의 역할 분담표와 함께 확정합니다.')
p.page('11  증빙과 재현 자료')
for text in ['reports/method_search/research_design.md · 공식 출처와 실제 적용 구분','reports/improvement_results.md · 전체 결과 해석','성능개선_screen_검증비교.csv / 성능개선_forward_검증비교.csv · 모든 후보','성능개선_새시험_모델비교.csv / 성능개선_새시험_추천비교.csv · 최종 숫자','artifacts/improvement/final/selection_frozen_before_test.json · 시험 전 선택','artifacts/improvement/final/results.json · 실제 시험·구간·추천·목표·불확실성','config/improvement_v3.json · 기간·목표·제약','원출처: 서울시 OA-22382 / OA-13252; LightGBM·CatBoost·XGBoost·scikit-learn·PyTorch와 각 논문의 공식 구현 문서']:p.para(text,size=10.5)
p.note('서울시 시간별 수량: https://data.seoul.go.kr/dataList/OA-22382/F/1/datasetView.do')
p.note('서울시 대여소 위치: https://data.seoul.go.kr/dataList/OA-13252/F/1/datasetView.do')
p.note('기상청 종관관측 시간자료: https://data.kma.go.kr/data/grnd/selectAsosList.do?pgmNo=36 · 다운로드 재현 설정: data/weather_research_downloads.json')
p.note('방법론별 공식 원문 주소·응답·해시는 reports/method_search/primary_sources.json과 sources.json에 보관했습니다. 접근 실패와 실제 확인한 자료를 구분합니다.')
p.note('연구 의존성 설치는 uv sync --frozen --group research. 모델·배열은Git 제외 대상이며 원본 파일과hash를 확인합니다. 열린 최종 시험은 새 독립 시험으로 재사용하지 않습니다.')
p.save('따릉이_성능개선_실제실험결과_쉬운보고서.docx')
print('Measured research report and CSVs created')
