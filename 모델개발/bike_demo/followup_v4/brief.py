"""Readable local HTML/Markdown report from saved analyses and real results."""
import argparse
import html
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import pandas as pd
from .common import ROOT,OUT,RECORD,save,now,digest

NAMES={
 'persistence':'현재 상태 유지','seasonal_day':'전일 규칙+상태별 판단','seasonal_week':'전주 규칙+상태별 판단',
 'smoothed_markov':'평활 전환 빈도','logistic':'선형 확률 모델','random_forest':'Random Forest',
 'lightgbm_basic':'기본 LightGBM','lightgbm_temporal':'시간 특징 LightGBM','lightgbm_full':'공간·전환 LightGBM',
 'catboost_temporal':'CatBoost','xgboost_temporal':'XGBoost','M2_no_weather':'기존 결합 구조(기상 제외)',
 'lightgbm_air_lag2':'대기질 2시간 전 추가','lightgbm_air_lag3':'대기질 3시간 전 추가',
 'lightgbm_full_112d':'학습 이력 112일로 확대','lightgbm_full_recency28':'최근 28일 중심 가중',
 'lightgbm_full_63':'트리 잎 63개로 축소','lightgbm_ordinal':'수량 구간별 위험 결합',
 'lightgbm_poisson':'수량 회귀로 위험 계산','lightgbm_state_experts':'현재 상태별 전문 모델',
 'lightgbm_calendar':'사전 발표 공휴일 추가','lightgbm_prior_year':'전년도 같은 요일 추가',
 'lightgbm_kbo':'원래 발표한 야구 일정 추가','temporal_mlp24':'24시간 입력 신경망',
 'temporal_mlp168':'168시간 입력 신경망','tcn24':'24시간 시간 합성곱',
 'graph_tcn24_identity':'자기 연결 시간 합성곱 대조군','graph_tcn24':'거리 이웃 시간 합성곱',
 'lightgbm_gfs_prior_day':'GFS 전일 예보 추가(조건부)'}

def markdown_table(frame):
    headers=[str(c) for c in frame.columns]
    lines=['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']
    lines.extend('| '+' | '.join(str(v) for v in row)+' |' for row in frame.itertuples(index=False,name=None))
    return '\n'.join(lines)

def build(final=False):
    display_time=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M (한국 시각)')
    df=pd.read_csv(RECORD/'leaderboard.csv')
    record=json.loads((RECORD/'E2E/02_탐색/탐색요약.json').read_text(encoding='utf-8'))
    sections=[]
    def section(title,paragraphs,images=(),table=None,links=()):sections.append(dict(title=title,paragraphs=paragraphs,images=images,table=table,links=links))
    def image(stage,name,label):return (f'E2E/{stage}/{name}.png',label)
    main=df[~df.track.str.contains('archive|sensitivity',case=False,na=False)]
    full=main.groupby('model').filter(lambda g:set(g.fold)==set('ABC'))
    averages=full.groupby('model',sort=False).agg(F1=('F1','mean'),transition_recall=('transition_recall','mean'),transition_precision=('transition_precision','mean'),FP=('FP','sum'),FN=('FN','sum'),seconds=('seconds','sum'))
    best=averages.F1.idxmax();best_row=averages.loc[best]
    reference=averages.loc['persistence'];gain=100*(best_row.F1-reference.F1)
    stage='완료 보고서' if final else '진행 중 보고서'
    overview=[f'{stage}로 작성한다. 실제 성공 결과 {len(df)}건과 후보 이름 {df.model.nunique()}개를 집계한다. 규칙·앙상블·변형을 모두 포함하며 독립적인 새 AI 모델 수로 표현하지 않는다.',
        f'같은 세 기간을 모두 확인한 후보 중 현재 평균 F1이 가장 높은 것은 {NAMES.get(best,best)}로 확인한다. 현재 상태 유지 {100*reference.F1:.2f}%에서 {100*best_row.F1:.2f}%로 {gain:.2f}%p 차이를 기록한다. 이 값은 개발 검증 성능이며 실제 대여 성공률이나 독립 최종 시험 성능으로 바꾸지 않는다.',
        'F1은 위험을 놓치는 정도와 오경보를 함께 고려하는 점수로 사용한다. 재현율은 실제 위험 중 찾은 비율, 정밀도는 위험 경보 중 실제 위험의 비율로 설명한다.']
    section('먼저 확인한 결과',overview)
    section('1. 문제 정의: 다음 정각에 비게 되는가',[
        '시민이 도착할 대여소를 고를 때 현재 수량만으로 다음 시간의 부족을 판단하기 어렵다는 문제에서 시작한다. 현재 수량과 과거 변화를 이용해 정확히 다음 1시간 격자의 관측 수량이 0인지 예측한다.',
        '실제 대여 실패, 한 시간 내내 비어 있는 상태, 5분 뒤 도착 성공은 이 정답으로 측정하지 않는다. 현재 양수인 대여소가 0대로 바뀌는 구간을 별도로 확인한다.'
    ],[image('01_문제정의','E2E_7단계','일곱 단계의 분석 흐름을 보여준다.')])
    section('2. 탐색: 자료가 어떤 모습을 갖는가',[
        f'2025년 1~6월 {record["stations"]:,}개 대여소에서 유효 관측 {record["observed_hours"]:,}건을 확인한다. 현재·다음 관측이 모두 있는 행은 {record["eligible_next_hour_rows"]:,}건이며 다음 0대 비율은 {100*record["next_zero_rate"]:.2f}%로 확인한다.',
        '1월 9~10일에는 전체 관측 누락을 확인한다. 이 날짜를 실제로 모든 대여소가 0대였다고 처리하지 않는다. 시간대별 전환 위험이 다른 것도 확인하며, 이 탐색 관계를 원인으로 단정하지 않는다.'
    ],[image('02_탐색','일별자료품질과0대','위는 관측이 존재하는 비율, 아래는 존재한 관측 중 0대 비율로 설명한다.'),image('02_탐색','수량분포와시간대위험','전체 위험과 현재 양수의 전환 위험을 다른 분모로 표시한다.'),image('02_탐색','대여소위치와0대비율','위치별 차이를 보여주며 새 대여소까지 검증했다고 해석하지 않는다.')])
    section('3. 큐레이션: 어떤 기록을 믿고 쓸 것인가',[
        '같은 대여소·시각에 같은 수량이 반복되면 한 관측으로 합친다. 서로 다른 수량이 충돌하면 자료 의미를 확인하기 전 해당 키 전체를 제외한다. 2024년에는 약 268만 충돌 키를 제외한다. 임의의 합계·평균·마지막 값을 선택하지 않는다.',
        '기존 학습에서 정한 대여소와 2024-12 위치를 유지해 모델마다 다른 표본으로 비교하지 않는다. 새 대여소 일반화는 별도 과제로 남긴다. 모든 처리의 이유·조사 근거·검사·대안을 데이터처리_근거기록.md에 남긴다.'
    ],[image('03_큐레이션','자료선별결과','파랑은 유효 관측, 주황은 충돌로 제외한 격자, 회색은 다른 결측으로 구분한다.')])
    section('4. 전처리: 미래를 보지 않고 입력을 만든다',[
        '과거 수량·증감·지속 시간·주변 수량·과거 전환 빈도를 구성한다. 미래 값을 바꿔도 앞선 특징이 바뀌지 않는 검사와 청크 계산 동등성 검사를 수행한다. 결측과 실제 0대를 별도 마스크로 구분한다.',
        '학습, 학습 종료 시점 선택, 확률 보정, 경보 기준 선택, 바깥 개발 검증을 시간 순서로 나눈다. 대치값·표준화는 학습에서만 적합한다. 3·4분기 성능은 아직 열지 않는다.'
    ],[image('04_전처리','시간분할','A/B/C에서 정답 시각 기준으로 다섯 구간을 분리한다.')])
    model_table=averages.reset_index().rename(columns={'model':'후보'})
    model_table['후보']=model_table['후보'].map(lambda n:NAMES.get(n,n))
    model_table=pd.DataFrame({'후보':model_table['후보'],'평균 F1(%)':model_table.F1.map(lambda x:f'{100*x:.3f}'),'전환 재현율(%)':model_table.transition_recall.map(lambda x:f'{100*x:.2f}'),'전환 정밀도(%)':model_table.transition_precision.map(lambda x:f'{100*x:.2f}'),'학습·평가 합계(분)':model_table.seconds.map(lambda x:f'{x/60:.1f}')})
    section('5. 모델링: 같은 문제를 여러 방법으로 푼다',[
        '상태 유지·계절 규칙·평활 Markov·선형·Random Forest·부스팅을 기준으로 비교한다. 시간 특징 73개를 동일하게 쓰는 LightGBM·CatBoost·XGBoost로 학습기 차이를 확인한다. 공간·전환 특징을 더한 비교는 정보 추가 효과를 포함한다.',
        '학습 기간·최근성·공휴일·전년도·분포 가정·시간 입력 길이·이웃 신호를 별도로 비교한다. RF 50만, 선형 100만, 신경망 30만 학습 표본 제한을 기록한다. 같은 검증 전체 행을 평가하지만 동일한 계산 예산이라고 주장하지 않는다.',
        '아래 표는 세 기간 모두 성공한 후보만 집계한다. 한 기간만 실행한 탐색 후보는 별도 그림과 상세 CSV에서 확인한다. 시간은 학습·보정·평가를 포함한 프로세스 경과시간으로 기록하며 PC 절전·대기 영향을 포함할 수 있어 순수 CPU 학습시간으로 해석하지 않는다.'
    ],table=model_table)
    if {'M2_no_weather','lightgbm_full','lightgbm_temporal','catboost_temporal','xgboost_temporal'}<=set(averages.index):
        temporal=averages.loc['lightgbm_temporal'];spatial=averages.loc['lightgbm_full'];blend=averages.loc['M2_no_weather']
        pilot_names=['lightgbm_full_112d','lightgbm_full_recency28','lightgbm_full_63','lightgbm_ordinal',
                     'lightgbm_poisson','lightgbm_state_experts','lightgbm_calendar','lightgbm_prior_year','lightgbm_kbo',
                     'temporal_mlp24','temporal_mlp168','tcn24','graph_tcn24_identity','graph_tcn24','lightgbm_air_lag2','lightgbm_air_lag3','lightgbm_gfs_prior_day']
        pilot=df[(df.fold=='A')&df.model.isin(pilot_names)]
        pilot_base=df[(df.fold=='A')&(df.model=='lightgbm_full')].F1.iloc[0]
        notes=[f'같은 LightGBM 설정에서 시간 특징 73개와 공간·전환 특징 105개를 비교하면 세 기간 평균 F1 차이를 {100*(spatial.F1-temporal.F1):+.3f}%p로 확인한다. 입력 정보의 추가를 학습기 이름 변경과 구분한다.',
               f'고정 50:50 결합은 공간·전환 LightGBM보다 평균 F1 차이 {100*(blend.F1-spatial.F1):+.3f}%p를 기록한다. 결합 비율을 이 세 검증 성과에 맞춰 다시 조정하지 않는다.']
        if len(pilot)==17:
            strongest=pilot.loc[pilot.F1.idxmax()]
            notes.append(f'A에서만 실행한 17개 예비 비교 중 전체 F1이 가장 높은 것은 {NAMES.get(strongest.model,strongest.model)}이며 공간·전환 기준 대비 {100*(strongest.F1-pilot_base):+.3f}%p로 확인한다. 사전에 정한 +0.2%p 기준을 넘는 추가 확인 대상이 없어 B/C 조건부 추가 학습을 실시하지 않는다. 50:50 결합은 처음부터 세 기간 필수 대조군에 속하므로 이 예비 후보 선별과 구분한다.')
        neural=pilot[pilot.model.isin(['temporal_mlp24','temporal_mlp168','tcn24','graph_tcn24_identity','graph_tcn24'])]
        if len(neural)==5:
            notes.append(f'신경망 5개 예비 비교는 A의 공간·전환 기준보다 {100*(neural.F1.min()-pilot_base):+.3f}~{100*(neural.F1.max()-pilot_base):+.3f}%p 낮은 F1을 기록한다. CPU·30만 학습 표본·최대 20회 학습이라는 이번 범위의 결과로 한정하며 모든 신경망이나 원논문보다 트리가 우월하다고 일반화하지 않는다.')
        section('방법론 비교에서 확인한 의미',notes)
        counts=[]
        for name in ('lightgbm_basic','lightgbm_full','M2_no_weather'):
            collected=[json.loads((OUT/f/(name+'.json')).read_text(encoding='utf-8'))['validation']['current_positive'] for f in 'ABC']
            total={key:sum(r[key] for r in collected) for key in ('n','zero_cases','tp','fp','fn')}
            counts.append({'모델':NAMES[name],'전환 포착(건)':total['tp'],'전환 놓침(건)':total['fn'],
                           '전환 오경보(건)':total['fp'],'합산 전환 재현율(%)':f'{100*total["tp"]/total["zero_cases"]:.2f}',
                           '합산 전환 정밀도(%)':f'{100*total["tp"]/(total["tp"]+total["fp"]):.2f}'})
        first,last=counts[0],counts[-1]
        section('시민이 고를 현재 양수 대여소를 따로 평가한다',[
            f'현재 자전거가 있는 세 기간의 기록을 합산해 비교한다. 50:50 결합은 기본 LightGBM보다 새 0대 전환을 {last["전환 포착(건)"]-first["전환 포착(건)"]:,}건 더 찾지만, 같은 구간의 오경보도 {last["전환 오경보(건)"]-first["전환 오경보(건)"]:,}건 늘어난다. 전체 오경보 감소를 이 구간의 오경보 감소로 바꾸어 설명하지 않는다.',
            '아래 재현율·정밀도는 세 구간의 오류 건수를 먼저 합친 뒤 계산한다. 앞 표의 기간별 비율 단순 평균과 분모가 다름을 명시한다. 전환 놓침이 여전히 많으며 실제 시민의 대여 성공·추가 이동 감소를 검증한 것은 아니다.'
        ],table=pd.DataFrame(counts))
    comparisons=[]
    for name,r in averages.iterrows():
        comparisons.append({'비교 기준':NAMES.get(name,name),'기준 F1(%)':f'{100*r.F1:.3f}','현재 우세 후보 차이(%p)':f'{100*(best_row.F1-r.F1):+.3f}','전체 FP 차이':f'{int(best_row.FP-r.FP):+,}','전체 FN 차이':f'{int(best_row.FN-r.FN):+,}'})
    section('6. 평가: 각 기준보다 얼마나 달라졌는가',[
        f'같은 세 기간에서 {NAMES.get(best,best)}를 각 비교 기준과 대조한다. F1은 기간별 단순 평균이며 FP·FN은 세 구간 합계로 계산한다. FP·FN 차이에서 음수는 오류 감소를 의미한다.',
        '단일 기간의 최고 점수를 최종 채택으로 바꾸지 않는다. A에서 기준보다 0.2%p 이상 높은 사전 지정 후보 중 최대 두 개를 B/C에서 확인하는 규칙을 기록한다. 예측 확률 품질(AP·Brier), 전환 재현율·정밀도, 현재 1~3대 구간도 원본 JSON에 보관한다.',
        '더 많이 찾으면서 오경보가 늘 수 있다. 낮은 절대 재현율과 악화된 구간도 성과 옆에 제시한다. 최종 선택 후의 독립 검증과 운영 효과 검증은 현재 개발 비교와 구분한다.'
    ],[image('06_평가','세기간_F1비교','같은 세 기간의 F1을 함께 보여준다.'),image('06_평가','A_전체후보_F1','A만 실행한 후보는 예비 비교로 표시한다.'),image('06_평가','전환위험_정밀도재현율','전환을 더 찾는 효과와 오경보의 균형을 확인한다.')],pd.DataFrame(comparisons))
    strong=[]
    for fold in 'ABC':
        base_path=OUT/fold/'lightgbm_basic.json'
        if not base_path.exists():continue
        baseline=json.loads(base_path.read_text(encoding='utf-8'))['validation']
        for name in ('lightgbm_full','catboost_temporal','xgboost_temporal','M2_no_weather'):
            path=OUT/fold/(name+'.json')
            if not path.exists():continue
            result=json.loads(path.read_text(encoding='utf-8'))
            if result.get('status')!='ok':continue
            value=result['validation'];transition=value['current_positive']
            strong.append({'기간':fold,'추가 후보':NAMES.get(name,name),'전체 F1(%)':f'{100*value["overall"]["f1"]:.3f}',
                '기본 대비 차이(%p)':f'{100*(value["overall"]["f1"]-baseline["overall"]["f1"]):+.3f}',
                '전환 재현율(%)':f'{100*transition["recall"]:.2f}',
                '전환 오경보 차이(건)':f'{transition["fp"]-baseline["current_positive"]["fp"]:+,}'})
    if strong:
        section('추가 AI의 현재 성과와 비용',[
            '완료한 기간만 아래 표에 싣는다. A는 5월 15~31일, B는 6월 1~14일, C는 6월 15~30일로 구분한다. 모든 차이는 같은 기간의 기본 LightGBM을 기준으로 계산한다.',
            '전환 오경보 차이의 양수는 현재 자전거가 있는 대여소에 잘못 보낸 위험 경보가 늘었다는 뜻으로 설명한다. 전체 F1 향상과 시민이 선택할 양수 대여소의 오경보를 함께 읽는다. 각 모델의 입력 특징 수가 달라 학습기만 바꾼 효과로 단정하지 않는다.'
        ],[image('06_평가','현재수량별_재현율','현재 0대 지속과 현재 양수의 새 전환을 구분한다.')],pd.DataFrame(strong))
    example_path=RECORD/'E2E/06_평가/기본과결합_판정사례.csv'
    if example_path.exists():
        examples=pd.read_csv(example_path)
        chosen=examples[(examples.fold=='A')&examples.case_type.isin(['기본 놓침-결합 포착','기본 포착-결합 놓침','기본 정상-결합 오경보','두 모델 모두 놓침'])]
        rows=[{'판정 유형':r.case_type,'입력 시각':str(r.input_time)[:16],'대여소':r.station_name,
               '현재→다음 수량':f'{r.current_quantity:g}→{r.next_quantity:g}',
               '기본 경보':'있음' if r.basic_warning else '없음','결합 경보':'있음' if r.blend_warning else '없음'} for r in chosen.itertuples()]
        section('실제 관측 사례로 판정 차이를 확인한다',[
            '2025년 A 개발 기간에서 각 판정 유형의 첫 관측을 시간·고정 대여소 순서로 고른다. 좋은 사례와 불리한 사례, 두 모델이 함께 놓친 사례를 같은 기준으로 제시한다. 전체 A/B/C의 여섯 유형 사례와 건수는 CSV에 저장한다.',
            '두 모델은 각각 앞선 정책 구간에서 고정한 경보 기준을 적용한다. 표는 다음 한 시간 격자의 관측 수량과 분류 경보를 비교하며 실제 시민의 대여 성공·실패나 이동 감소를 측정한 것으로 설명하지 않는다. 사례 수를 전체 성능의 분모로 사용하지 않는다.'
        ],table=pd.DataFrame(rows),links=[('전체 판정 사례와 예측 확률','E2E/06_평가/기본과결합_판정사례.csv'),('선정한 유형별 전체 건수','E2E/06_평가/기본과결합_판정유형건수.csv')])
    range_path=RECORD/'E2E/06_평가/각베이스라인대비_날짜블록_차이범위.csv'
    if range_path.exists():
        intervals=pd.read_csv(range_path)
        selected=intervals[(intervals.baseline=='lightgbm_basic')&(intervals.group=='overall')&(intervals.metric=='f1')&(intervals.block_days==7)&intervals.model.isin(['lightgbm_full','catboost_temporal','xgboost_temporal','M2_no_weather'])]
        rows=[{'기간':r.fold,'추가 후보':NAMES.get(r.model,r.model),'전체 F1 차이(%p)':f'{r.difference_pp:+.3f}',
               '7일 묶음 재표집 범위(%p)':f'{r.lower_pp:+.3f} ~ {r.upper_pp:+.3f}'} for r in selected.itertuples()]
        section('날짜가 달라도 차이가 유지되는지 살펴본다',[
            '하루의 모든 대여소·시간을 함께 묶고 같은 날짜 묶음을 두 모델에 적용한다. 1일과 연속 7일 묶음을 각각 2,000회 다시 뽑아 차이의 변동을 확인한다. 표에는 7일 묶음의 결과를 싣고 그림에는 두 방식을 함께 표시한다.',
            '이미 성과를 확인한 개발 기간의 탐색적 분석으로 사용한다. 짧은 기간·주간 묶음 수·여러 후보 선택·단일 초기값·학습 기간 중첩이 남아 있으므로 이 범위를 독립 최종 시험의 우월성 증명이나 보장된 95% 신뢰수준으로 바꾸지 않는다.'
        ],[image('06_평가','날짜블록_성능차이범위','점은 원래 성능 차이, 선은 재표집 차이의 2.5~97.5% 범위로 표시한다.')],pd.DataFrame(rows))
    diagnostic_path=RECORD/'E2E/06_평가/확률보정_전후와구간오차.csv'
    if diagnostic_path.exists():
        diagnostics=pd.read_csv(diagnostic_path)
        worsened=int((diagnostics.brier_change>0).sum())
        rows=[]
        for (name,group),g in diagnostics.groupby(['model','group']):
            rows.append({'모델':NAMES.get(name,name),'기록 범위':'전체' if group=='overall' else '현재 양수',
                         '보정 전 평균 Brier':f'{g.raw_brier.mean():.5f}',
                         '보정 후 평균 Brier':f'{g.calibrated_brier.mean():.5f}',
                         '차이(낮을수록 개선)':f'{g.brier_change.mean():+.5f}'})
        section('예측 확률이 실제 위험과 얼마나 맞는지 확인한다',[
            '예측 위험이 비슷한 기록을 10개 확률 구간으로 묶고 평균 예측값과 실제 0대 비율을 비교한다. 대각선에 가까울수록 이 구간의 확률과 관측 비율이 비슷함을 뜻한다. 빈 구간은 실제 위험 0%로 바꾸지 않으며 구간별 표본 수를 CSV에 저장한다.',
            f'모델·기간·기록 범위 조합 {len(diagnostics)}개 중 {worsened}개에서는 확률 보정 뒤 Brier가 증가한다. 보정을 수행했다는 사실을 확률 품질이 항상 좋아졌다는 주장으로 바꾸지 않는다. 아래 수치는 기간별 점수의 단순 평균이며 Brier는 확률의 제곱 오차로 설명한다.',
            '낮은 Brier는 확률 보정과 구분 능력을 함께 반영하므로 보정의 정확성만 증명하지 않는다. 이미 확인한 개발 기간의 사후 진단이며 이 정답으로 모델·보정기·임계값을 다시 맞추지 않는다. 미래 운영에서 같은 확률 품질이 유지된다는 보장으로 해석하지 않는다.'
        ],[image('06_평가','확률과실제위험_비교','각 점의 표본 수는 확률구간별_실제위험과표본수.csv에서 확인한다. 전체와 현재 양수 구간을 분리한다.'),
           image('06_평가','확률보정_전후_Brier','보정 전후의 좋은 변화와 나쁜 변화를 같은 척도로 제시한다.')],pd.DataFrame(rows))
    air=df[df.model.str.startswith('lightgbm_air_')]
    air_note='대기질 추가 학습은 아직 완료 결과가 없다.' if air.empty else f'대기질 추가 비교 {len(air)}건의 실제 결과를 상세 표에 기록한다. 역사 수신 이력 미상이라는 조건을 붙여 해석한다.'
    section('추가 데이터: 실제로 제때 받을 수 있는가',[
        '실시간 따릉이 조회에서 2,716개 대여소와 기존 코호트 2,618개의 연결을 확인한다. API에 공급자 관측 시각이 없어 수신 시각을 측정 시각으로 꾸미지 않는다. 과거 시간 격자와 현재 API의 동등성·지연은 추가 운영 검증으로 남긴다.',
        '서울 대기질은 과거 상반기 108,600행과 현재 25개 구 API 연결을 확보한다. 한 번의 응답에서 관측→수신 약 60분 차이를 확인한다. 현재 3개 구의 점검중 수치를 결측으로 거부한다. 수신≤입력, 관측≤입력-2/3시간, 자료 나이·QC 규칙을 코드로 검사한다. '+air_note,
        '역사 자료의 공개·수정·수신 이력은 입증하지 못했으므로 단지 시차를 줬다는 이유로 실제 과거 실시간 성능이라고 부르지 않는다. 24시간 전 자료를 쓰려면 실시간 스냅샷 캐시가 필요하다.',
        'GFS 전일 예보 상품은 미래 유효 시각의 현재 수신 경로와 현재 실행의 게시 메타데이터를 확인한다. day1 표기를 행마다 정확히 24시간 전 발표했다는 의미로 해석하지 않는다. 과거 실행별 발표·수신 시각은 미검증이므로 별도 조건부 비교로 진행한다. 문화행사 19,642건과 KOPIS 부분 목록 3,273개를 확보하며, 수정 이력·좌표·서버 차단 조건이 남은 자료는 주 입력에 자동 채택하지 않는다. 추가 키가 필요한 기상청 자료의 발급 대상과 필요성을 별도 조사 보고서에 기록한다.'
    ])
    public_dir=ROOT/'experiments/20261020_공공데이터추가조사2'
    public_audit=public_dir/'최종통합검수.json'
    if public_audit.exists():
        public=json.loads(public_audit.read_text(encoding='utf-8'))
        domains=public['domain_rows']
        section('추가 공공데이터를 넓게 조사한다',[
            f'세 조사 작업으로 교통·이동 {domains["교통흐름"]}개, 공간·시설 {domains["공간시설"]}개, 인구·행사 {domains["인구행사"]}개로 총 {public["candidate_rows"]}개 후보를 정리한다. 기존 원천의 재검토와 부적합 후보도 포함하며 모두 새 독립 원천으로 세지 않는다.',
            '후보마다 영향 가설, 공식 출처, 과거 이력, 공개 지연, 대여소 연결, 이용조건, 필요한 키와 다음 검수를 기록한다. 실시간으로 조회되는 자료와 과거 검증 기간에 사용할 수 있는 자료를 구분한다.',
            '버스정류소 위치는 고정 2,729개 대여소의 거리와 반경별 정류소 수를 구성하고 별도 거리 계산으로 대조한다. 과거 최초 공개·수신 미검증 조건은 유지한다. 실시간 주차·지하철은 현재 응답만으로 2025년 이력을 만들지 않는다.'
        ],links=[(f'{public["candidate_rows"]}개 후보를 검색하는 통합보고서','../20261020_공공데이터추가조사2/공공데이터_추가조사_통합보고서.html'),
                  ('전체 후보 CSV','../20261020_공공데이터추가조사2/통합후보목록.csv')])
    od_dir=ROOT/'experiments/20261020_추가공공데이터적용'
    od_audit=od_dir/'202411_OD_전수검수.json'
    if od_audit.exists():
        od=json.loads(od_audit.read_text(encoding='utf-8'))
        section('새 자료를 실제 입력으로 준비한다',[
            f'2024년 11월 대여·반납 집계의 일파일 {od["csv_members"]}개, 수록 {od["source_rows"]:,}행을 전수 검수한다. 출발시간과 도착시간 기준을 따로 집계하며 같은 이동을 두 번 더하지 않는다. 행이 없는 시간의 이용을 실제 0으로 확정하지 않고 수록 일수와 결측을 보존한다.',
            f'현재 번호·이름·50m 위치 조건을 충족하고 과거 흐름에 ID가 있는 대여소 {od["mapping"]["allowed_and_present_in_prior_archive"]:,}곳을 조건부 연결한다. 고정 코호트 2,729곳을 유지하며 미연결 입력을 결측으로 남긴다. 현재 교차표가 과거 ST-ID 동일성을 입증하지는 않는다.',
            '선행 흐름 프로필과 이동 관계의 정제본을 저장한다. 처리 근거와 검수는 모델 학습 전에 남기며, 이 준비만으로 추가 자료의 성능 개선을 확인한 것으로 표현하지 않는다. v4 고정 실험과 구분한 후속 비교로 검토한다.'
        ],links=[('추가 자료 준비와 품질 검수 설명','../20261020_추가공공데이터적용/데이터준비_쉬운설명.html'),
                  ('추가 입력의 정의와 처리 근거','../20261020_추가공공데이터적용/특징정의와근거.md')])
    public_review=od_dir/'추가공공데이터_완료검수.json'
    if public_review.exists():
        review=json.loads(public_review.read_text(encoding='utf-8'))
        public_table=None
        comparison_path=od_dir/'추가자료_각기준모델대비.csv'
        if comparison_path.exists():
            pairs=pd.read_csv(comparison_path)
            overall=pairs[(pairs.baseline=='lightgbm_full')&(pairs.group=='overall')]
            rows=[]
            for name,g in overall.groupby('model'):
                if set(g.fold)!=set('ABC'):continue
                positive=pairs[(pairs.model==name)&(pairs.baseline=='lightgbm_full')&(pairs.group=='current_positive')]
                label={'lightgbm_public_bus':'버스정류소 추가','lightgbm_public_od':'선행 대여·반납 추가','lightgbm_public_bus_od':'버스·선행 흐름 결합'}[name]
                rows.append({'추가 자료':label,'세 기간 평균 F1(%)':f'{100*g.f1.mean():.3f}',
                             '공간·전환 기준 대비(%p)':f'{g.f1_difference_pp.mean():+.3f}',
                             '전환 재현율 차이(%p)':f'{positive.recall_difference_pp.mean():+.2f}',
                             '전환 오경보 차이 합계':f'{int(positive.fp_difference.sum()):+,}'})
            if rows:public_table=pd.DataFrame(rows)
        section('추가 공공데이터를 붙여 따로 비교한다',[
            f'추가 자료 비교 {review["completed"]}/9개의 계산을 검수한다. 버스정류소 4개, 선행 흐름 7개, 결합 11개를 같은 105개 특징 모델에 각각 더한다. 같은 A/B/C에서 비교하며 v4 학습이 끝난 뒤 한 번에 하나씩 실행한다.',
            '모형 재로딩과 저장 확률, 전체 오류 건수, 각 기준 모델 대비 차이, 날짜별 변동을 확인한다. 역사 수신·과거 ID·원천 날짜 의미의 미검증이 남으므로 이 추가 자료의 결과를 주 운영 우세 모델로 자동 채택하지 않는다.',
            '표가 있는 경우 F1과 재현율 차이는 세 기간 단순 평균, 오경보 차이는 세 구간 합계로 계산한다. 자세한 기간별 성과와 불리한 결과도 별도 보고서에서 함께 확인한다.'
        ],table=public_table,links=[('추가 공공데이터 모델 비교보고서','../20261020_추가공공데이터적용/추가공공데이터_비교보고서.html')])
        if review['completed']==9:
            od=overall[overall.model=='lightgbm_public_od']
            bus=overall[overall.model=='lightgbm_public_bus']
            section('추가 자료의 채택 판단',[
                f'버스정류소 특징의 공간·전환 기준 대비 평균 F1 변화는 {bus.f1_difference_pp.mean():+.4f}%p로 확인한다. 이번 특징과 모형 설정에서 얻은 결과로 한정하며 버스 이용과 따릉이가 무관하다고 일반화하지 않는다.',
                f'선행 대여·반납 특징의 평균 F1 변화는 공간·전환 기준 대비 {od.f1_difference_pp.mean():+.4f}%p이며, 현재 주 비교의 결합 모델 대비 {100*(od.f1.mean()-best_row.F1):+.4f}%p로 확인한다. 평균 변화만으로 채택하지 않고 기간별 악화와 전환 오경보, 역사 연결 조건을 함께 판단한다.',
                '현재 다음 검증의 우선 후보는 기존 50:50 결합으로 유지한다. 추가 자료와 더 긴 학습 창은 재검토 후보로 보존한다. 기상청 발표본·교통량·생활인구 프로필은 공개 시점과 인증·품질을 먼저 확인할 후속 후보로 정리하며, 이번에 모두 학습했다고 표현하지 않는다.',
                '새 운영 자료로 검증할 때는 원천 측정·발표·수신 시각과 버전을 함께 쌓는다. 3·4분기 자료는 새 선택 절차를 고정한 뒤 다룰 후속 검증으로 남기며 이번 개발 비교의 독립 시험으로 주장하지 않는다.'
            ])
    section('7. 보고·재현: 어디에서 다시 확인하는가',[
        '이 보고서, 단계별 CSV·JSON·PNG·SVG, 학습 설정, 모델·예측·보정기, 처리 근거, 검수 기록을 PC에 저장한다. 큰 원본과 모델은 원래 위치를 유지하고 코드·분석 기록을 별도 압축한다. 키는 로컬 .env에서만 읽고 압축·보고서에서 제외한다.',
        'leaderboard.csv에서 전체 수치를 확인하고 E2E/06_평가/각베이스라인대비변화.csv에서 후보별 차이를 확인한다. 데이터처리_근거기록.md, 추가데이터_병렬조사/보고서와재개방법.md, roundtrip_checks.json, tests_latest.json으로 근거와 검사를 추적한다.',
        '재개.ps1은 완료 후보를 건너뛰며 미완료 후보를 이어간다. 최종 결과 해석은 개발 기간의 선택 편향, 단일 초기값, 입력 제공 지연, 실제 대여 성공을 측정하지 않았다는 한계를 포함한다.'
    ])
    md=['# 따릉이 부족 위험 예측: 쉬운 E2E 보고서','',f'작성 시각: {display_time}','']
    body=[]
    for i,s in enumerate(sections):
        md.extend(['## '+s['title'],'']);body.append('<section id="section-'+str(i)+'"><h2>'+html.escape(s['title'])+'</h2>')
        for p in s['paragraphs']:md.extend([p,'']);body.append('<p>'+html.escape(p)+'</p>')
        for label,href in s['links']:
            md.extend([f'[{label}]({href})',''])
            body.append('<p><a href="'+html.escape(href,quote=True)+'">'+html.escape(label)+'</a></p>')
        if s['table'] is not None:
            md.extend([markdown_table(s['table']),'']);body.append('<div class="table-wrap">'+s['table'].to_html(index=False,escape=True,border=0)+'</div>')
        for path,caption in s['images']:
            if not (RECORD/path).exists():continue
            md.extend([f'![{caption}]({path})','']);body.append('<figure><img src="'+html.escape(path,quote=True)+'" alt="'+html.escape(caption,quote=True)+'"><figcaption>'+html.escape(caption)+'</figcaption></figure>')
        body.append('</section>')
    css='''body{margin:0;background:#edf1f5;color:#203246;font:16px/1.8 "Malgun Gothic",sans-serif}main{max-width:1080px;margin:36px auto;padding:40px 46px;background:white;border-radius:12px}h1{font-size:30px;line-height:1.4;color:#143b61}h2{font-size:23px;line-height:1.5;margin-top:36px;padding-top:20px;border-top:1px solid #d6e1eb}p{max-width:960px}img{width:100%;height:auto}figure{margin:24px 0}figcaption{font-size:14px;color:#53677b}table{width:100%;border-collapse:collapse;font-size:13px}td,th{text-align:left;padding:10px;border-bottom:1px solid #dce4eb}th{background:#eaf2f9}.table-wrap{overflow:auto}.meta{color:#53677b;font-size:14px}@media print{body{background:white}main{margin:0;padding:10px;max-width:none}section{break-inside:auto}figure,table{break-inside:avoid}h2{break-after:avoid}a{color:inherit}}'''
    toc='<nav aria-label="보고서 목차"><details><summary>보고서 목차</summary><ol>'+''.join('<li><a href="#section-'+str(i)+'">'+html.escape(s['title'])+'</a></li>' for i,s in enumerate(sections))+'</ol></details></nav>'
    document='<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>따릉이 E2E 쉬운 보고서</title><style>'+css+'</style><main><h1>따릉이 부족 위험 예측<br>쉬운 E2E 분석 보고서</h1><p class="meta">'+html.escape(stage+' · '+display_time)+'</p>'+toc+''.join(body)+'</main></html>'
    (RECORD/'쉬운보고서.md').write_text('\n'.join(md),encoding='utf-8')
    (RECORD/'쉬운보고서.html').write_text(document,encoding='utf-8')
    save(RECORD/'E2E/07_보고와재현/report_snapshot.json',dict(created_at_utc=now(),final=final,comparisons=len(df),best_three_fold_main_candidate=best,report_code_sha256=digest(__file__),main_comparison_excludes_archive_tracks=True))
    print('EASY_REPORT_SAVED',len(df),'comparisons',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--final',action='store_true');args=p.parse_args();build(args.final)
