"""Build a reviewable report from successful result files, never guessed scores."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .common import ROOT,OUT,RECORD,save,now,digest
from .runner import refresh,specs

def table(rows,headers):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(str(x) for x in row)+' |' for row in rows])

def main():
    refresh();df=pd.read_csv(RECORD/'leaderboard.csv');completed=[]
    for fold in ('A','B','C'):
        for path in (OUT/fold).glob('*.json'):
            item=json.loads(path.read_text(encoding='utf-8'))
            if item.get('status')=='ok':completed.append(item)
    lines=['# 후속 실제 실험 결과','','자동 집계 시각(UTC): '+now(),'',
        '성공 JSON이 있는 실제 결과만 집계한다. 2025년 A/B/C 바깥 구간도 개발 검증으로 사용하며, 최종 독립 시험 성능으로 표현하지 않는다.',
        '',f'완료한 후보×폴드 비교는 {len(completed)}건, 서로 다른 후보 이름은 {df.model.nunique()}개로 집계한다. 규칙·앙상블·변형을 모두 포함하는 수이며 독립적인 새 AI 모델 수로 바꾸지 않는다.','',
        '## 세 기간 기준 모델 비교','',
        'A는 5월 15~31일, B는 6월 1~14일, C는 6월 15~30일로 평가한다. 아래 평균은 세 폴드의 단순 평균으로 계산한다. 모든 학습기는 정답·기간·보정·정책 선택을 공유하지만 RF·선형·신경망의 학습 표본과 계산 예산은 다르다.','']
    rows=[]
    for model,g in df.groupby('model',sort=False):
        if set(g.fold)!=set('ABC'):continue
        row=[model]+[f'{100*g.loc[g.fold==f,"F1"].iloc[0]:.3f}' for f in 'ABC']
        row += [f'{100*g.F1.mean():.3f}',f'{100*g.transition_recall.mean():.2f}',f'{100*g.transition_precision.mean():.2f}',f'{g.Brier.mean():.4f}',f'{g.seconds.sum()/60:.1f}',g.track.iloc[0]]
        rows.append(row)
    lines.append(table(rows,['후보','A F1(%)','B F1(%)','C F1(%)','평균 F1(%)','전환 재현율(%)','전환 정밀도(%)','Brier','합계 분','정보 조건']))
    lines.extend(['','전환은 현재 양수에서 다음 시간 0대로 바뀌는 부분집합으로 정의한다. 전환 재현율의 분모를 전체 양성 분모와 혼동하지 않는다. 상세 분모·TP·FP·FN·AP는 각 결과 JSON에 기록한다.','',
        '계절 규칙도 공통된 확률 보정과 현재 0대/양수별 임계값 선택을 거친다. 검증에서 계절 신호를 사용하지 않는 현재 상태와 같은 판단을 고를 수 있다. 같은 F1은 같은 확률 품질을 뜻하지 않으므로 AP와 Brier를 함께 확인한다.','',
        '## A 우선 비교와 실제 예산','',
        '아래는 동일한 A 검증 1개 기간의 탐색 결과로 제시한다. 최고값만으로 채택하거나 세 기간의 우위를 주장하지 않는다. 예측 변수 수가 다른 비교는 정보 추가 효과이며 순수 학습기 교체 효과와 구분한다.',''])
    subset=df[df.fold=='A'].sort_values('F1',ascending=False);rows=[]
    for _,r in subset.iterrows():
        item=next(x for x in completed if x['fold']=='A' and x['name']==r.model)
        rows.append([r.model,f'{100*r.F1:.3f}',f'{100*r.transition_recall:.2f}',f'{100*r.transition_precision:.2f}',int(r.FP),int(r.FN),f'{r.seconds/60:.1f}',int(r.train_rows),f'{item.get("memory",{}).get("peak_rss_gib",0):.2f}',r.track])
    lines.append(table(rows,['후보','F1(%)','전환 재현율(%)','전환 정밀도(%)','전체 FP','전체 FN','분','학습 행','프로세스 최고 GiB','정보 조건']))
    lines.extend(['','최고 메모리는 프로세스 누적 최고 작업집합으로 기록한다. 한 프로세스 안의 앞선 후보가 남긴 최고치일 수 있으므로 후보 단독 피크라고 해석하지 않는다. 시간은 학습·보정·평가를 포함한 경과시간이며 PC 절전·대기 영향을 포함할 수 있다. 앙상블 시간은 저장 확률을 결합·보정·평가한 추가 시간으로 기록하며 구성 모델 학습 시간은 별도로 합산한다.','',
        '## 해석과 다음 확인','',
        '- 같은 73개 시간 특징의 LightGBM·CatBoost·XGBoost를 학습기 비교로 확인한다. 공간·전환 특징을 포함한 LightGBM과의 차이는 정보 구성 효과를 포함한다.',
        '- `M2_no_weather`는 공간·전환 LightGBM과 시간 특징 CatBoost의 예측을 50:50으로 결합한 대조군으로 사용한다. 기존 날씨 포함 M2의 고정 시험 점수와 직접 비교하지 않는다.',
        '- 112일 창·최근성·순서형·Poisson·상태 전문가·달력·전년 정보는 각각 기준 LightGBM과 비교한다. A 개선 후보가 있으면 다른 기간에서도 확인한다.',
        '- 공휴일·전년도 수량·공식 KBO 원일정의 추가 효과를 비교한다. 과거 수정 이력이 확인되지 않은 문화행사 스냅샷과 실시간 경로가 확인되지 않은 ASOS는 주 학습 큐에서 제외한다.',
        '- 대기질은 2·3시간 전 관측, GFS는 다음 시간에 해당하는 전일 예보를 조건부 보조 비교로 사용한다. 현재 제공 경로를 확인했지만 당시 수신 이력은 미검증이므로 운영 성능을 입증했다고 해석하지 않는다.',
        '- 신경망은 30만 학습 표본·최대 20에포크·100,000행 조기 종료 표본을 사용한다. 검증 전체 행을 평가하며 논문 재현이나 동일 계산 예산을 주장하지 않는다.',
        '- 대규모 Transformer·기반 모델·새 OD 그래프는 계산 자원 또는 적격한 원천자료 확인을 요구하는 조건부 항목으로 남긴다. 그래프 신경망은 동일 구조의 자기 자신만 보는 대조군과 비교한다.',
        '- 3·4분기 점수는 아직 열지 않는다. 최종 선택·여러 초기값 재현·이후 기간 검증을 완료한 결과와 현재 개발 탐색을 구분한다.','',
        '## 검수·자료 상태',''])
    protected=json.loads((ROOT/'experiments/20261020_데이터방법론조사/protected_fingerprints.json').read_text(encoding='utf-8'))
    mismatches=[p['path'] for p in protected if digest(ROOT/p['path'])!=p['sha256']]
    save(RECORD/'preservation_check.json',dict(checked_at_utc=now(),files=len(protected),mismatches=mismatches))
    lines.append(f'기존 보호 대상 {len(protected)}개 파일의 해시를 대조하며 불일치는 {len(mismatches)}개로 확인한다. 새 결과는 별도 폴더에 저장한다.')
    lines.extend(['','정의·시점·행렬 청크·API 오류·신경망 출력 검사는 `tests/test_followup_v4*.py`로 수행한다. 저장 모델의 예측 재현 결과는 `roundtrip_checks.json`이 생성되면 확인한다. API 수집과 원천자료 제약은 `API와추가데이터.md`에서 확인한다.',''])
    (RECORD/'실험결과.md').write_text('\n'.join(lines),encoding='utf-8')
    save(RECORD/'progress.json',dict(updated_at_utc=now(),successful_fold_candidates=len(completed),unique_candidate_names=int(df.model.nunique()),three_fold_candidates=[m for m,g in df.groupby('model') if set(g.fold)==set('ABC')],
         independent_final_test_opened=False,old_artifacts_changed=mismatches))
    print('REPORT',len(completed),'comparisons',flush=True)

if __name__=='__main__':main()
