"""Compare verified public-data ablations with every frozen basic/strong control."""
import argparse
import html
import json
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy.special import logit
from .common import ROOT, OUT as BASE_OUT, RECORD as BASE_RECORD, save, digest, now
from .public_runner import OUT, RECORD, PLAN, plan
from .runner import Inputs
from .uncertainty import daily_confusions, moving_blocks, paired_ranges, FIELDS
from ..research import decisions
from .brief import NAMES

NAMES = {**NAMES, 'lightgbm_public_bus': '버스정류소 4개 추가',
         'lightgbm_public_od': '선행 대여·반납 7개 추가',
         'lightgbm_public_bus_od': '버스·선행 흐름 11개 추가'}
GROUPS = ('overall', 'current_positive', 'current_1_to_3')


def build(require_complete=False):
    config = plan()
    plan_hash = digest(PLAN)
    expected = [(f, s['name']) for f in config['folds'] for s in config['candidate_specs']]
    found, missing = {}, []
    for fold, name in expected:
        path = OUT / fold / (name + '.json')
        if not path.exists():
            missing.append(dict(fold=fold, model=name, reason='not run'))
            continue
        r = json.loads(path.read_text(encoding='utf-8'))
        if r.get('status') != 'ok' or not r.get('public_checks', {}).get('all_passed'):
            missing.append(dict(fold=fold, model=name, reason='model verification incomplete'))
            continue
        assert r['public_extension_plan_sha256'] == plan_hash
        assert r['information_track'] == config['information_track'] and r['independent_final_test'] is False
        for item in r['public_checks']['artifact_hashes']:
            assert digest(OUT / item['path']) == item['sha256'], 'Saved model/prediction artifact changed'
        for suffix in ('.txt', '_calibrator.joblib'):
            assert (OUT / fold / 'models' / (name + suffix)).exists()
        for stage in ('cal','policy','valid'):
            assert (OUT / 'predictions' / fold / (name + '_' + stage + '_exact.npy')).exists()
        found[fold, name] = r
    if require_complete:
        assert not missing and len(found) == 9, 'Nine verified public-data fits are required'
    controls = config['additional_controls'] + [config['primary_control']]
    optional = ['lightgbm_temporal','catboost_temporal','xgboost_temporal','M2_no_weather']
    controls += [name for name in optional if all((BASE_OUT / f / (name + '.json')).exists() for f in config['folds'])]
    comparisons, coverage = [], []
    for (fold, name), record in found.items():
        for control in controls:
            base_path = BASE_OUT / fold / (control + '.json')
            base = json.loads(base_path.read_text(encoding='utf-8'))
            assert base['status'] == 'ok' and base['protocol_sha256'] == config['v4_protocol_sha256']
            assert base['fold'] == fold
            if 'data_sha256' in base:
                assert base['data_sha256'] == record['data_sha256']
            else:
                # The frozen 50:50 blend records its component identities rather
                # than duplicating a panel hash or stop-window row count.
                assert base['spec'].get('components')
                for component, expected_hash in base['component_result_sha256'].items():
                    component_path = BASE_OUT / fold / (component + '.json')
                    assert digest(component_path) == expected_hash
                    source = json.loads(component_path.read_text(encoding='utf-8'))
                    assert source['data_sha256'] == record['data_sha256']
                    assert source['protocol_sha256'] == config['v4_protocol_sha256']
            for stage in ('cal','policy','valid'):
                assert base['stage_rows'][stage] == record['stage_rows'][stage]
            for group in GROUPS:
                left, right = record['validation'][group], base['validation'][group]
                assert left['n'] == right['n'] and left['zero_cases'] == right['zero_cases']
                row = dict(fold=fold, model=name, baseline=control, group=group, n=left['n'], zero_cases=left['zero_cases'],
                           information_track=record['information_track'], model_result_sha256=digest(OUT / fold / (name + '.json')),
                           baseline_result_sha256=digest(base_path))
                for metric in ('f1','precision','recall','average_precision','brier'):
                    row[metric] = left[metric]
                    row['baseline_' + metric] = right[metric]
                    if metric == 'brier':
                        row['brier_difference'] = left[metric] - right[metric]
                    else:
                        row[metric + '_difference_pp'] = 100 * (left[metric] - right[metric])
                for field in ('tp','fp','fn','tn'):
                    row[field] = left[field]
                    row['baseline_' + field] = right[field]
                    row[field + '_difference'] = left[field] - right[field]
                comparisons.append(row)
        coverage.append(dict(fold=fold, model=name, result_sha256=digest(OUT / fold / (name + '.json')), all_controls_matched=True))
    frame = pd.DataFrame(comparisons)
    if not frame.empty:
        frame.to_csv(RECORD / '추가자료_각기준모델대비.csv', index=False, encoding='utf-8-sig')
    daily_rows, ranges, decision_checks = [], [], []
    if found:
        source_daily = pd.read_csv(BASE_RECORD / 'E2E/06_평가/일별_고정판정_오류집계.csv')
        source_checks = json.loads((BASE_RECORD / 'uncertainty_checks.json').read_text(encoding='utf-8'))['checks']
        checked = {(r['fold'], r['model'], r['group']): r for r in source_checks}
        data = Inputs()
        for fold in config['folds']:
            split = config['development_folds'][fold]
            index = data.indices(split['valid_start'], split['valid_end'])
            dates, codes = np.unique(data.tt[index].astype('datetime64[D]'), return_inverse=True)
            y, quantity = data.target[index] == 0, data.q.ravel()[index]
            for (record_fold, name), record in found.items():
                if record_fold != fold:
                    continue
                exact = np.load(OUT / 'predictions' / fold / (name + '_valid_exact.npy'))
                cal = joblib.load(OUT / fold / 'models' / (name + '_calibrator.joblib'))
                p = cal.predict_proba(logit(np.clip(exact, 1e-6, 1-1e-6)).reshape(-1, 1))[:, 1].astype(np.float32)
                prediction = decisions(p, quantity, record['rule'])
                for group, mask in (('overall', np.ones(len(y), dtype=bool)), ('current_positive', quantity > 0)):
                    daily = daily_confusions(y, prediction, codes, len(dates), mask)
                    assert np.array_equal(daily.sum(axis=0), [record['validation'][group][key] for key in FIELDS])
                    decision_checks.append(dict(fold=fold, model=name, group=group, exact_counts_match=True,
                                                result_sha256=digest(OUT / fold / (name + '.json'))))
                    for date, values in zip(dates, daily):
                        daily_rows.append(dict(fold=fold, model=name, group=group, target_date=str(date), **dict(zip(FIELDS, map(int, values)))))
                    for control in controls:
                        key = fold, control, group
                        assert checked[key]['aggregate_matches']
                        assert checked[key]['result_sha256'] == digest(BASE_OUT / fold / (control + '.json'))
                        base = source_daily[(source_daily.fold == fold) & (source_daily.model == control) & (source_daily.group == group)].sort_values('target_date')
                        assert np.array_equal(base.target_date.to_numpy(dtype='datetime64[D]'), dates)
                        right = base[list(FIELDS)].to_numpy(np.int64)
                        for block_days in (1,7):
                            draws = moving_blocks(len(dates), block_days, repeats=2000, seed=20261020)
                            values = paired_ranges(daily, right, draws)
                            for metric, value in values.items():
                                ranges.append(dict(fold=fold, model=name, baseline=control, group=group,
                                                   metric=metric, block_days=block_days, **value))
        pd.DataFrame(daily_rows).to_csv(RECORD / '추가자료_일별오류.csv', index=False, encoding='utf-8-sig')
        pd.DataFrame(ranges).to_csv(RECORD / '추가자료_날짜블록_차이범위.csv', index=False, encoding='utf-8-sig')
    figures = []
    if not frame.empty:
        plt.rcParams.update({'font.family':'Malgun Gothic','axes.unicode_minus':False})
        plot_dir = RECORD / '시각화'
        plot_dir.mkdir(exist_ok=True)
        selected = frame[(frame.baseline == config['primary_control']) & (frame.group == 'overall')]
        fig, ax = plt.subplots(figsize=(10,5.5))
        labels = [r.fold + ' · ' + NAMES[r.model] for r in selected.itertuples()]
        values = selected.f1_difference_pp.to_numpy()
        ax.barh(labels, values, color=['#3d7d9f' if n >= 0 else '#b8743e' for n in values])
        ax.axvline(0,color='#555555',linewidth=1)
        for i, value in enumerate(values):
            ax.annotate(f'{value:+.3f}', (value, i), xytext=(5 if value >= 0 else -5, 0),
                        textcoords='offset points', va='center', ha='left' if value >= 0 else 'right')
        ax.margins(x=.25);ax.set(xlabel='같은 기간 공간·전환 LightGBM 대비 F1 차이(%p)', title='추가 공공데이터의 조건부 비교')
        fig.tight_layout()
        for ext in ('png','svg'):fig.savefig(plot_dir / ('03_추가자료_F1차이.' + ext), dpi=160)
        plt.close(fig);figures.append('시각화/03_추가자료_F1차이.png')
    status = '9개 비교의 계산 검수를 완료한다.' if len(found) == 9 and not missing else f'9개 계획 중 {len(found)}개 결과를 검수한다. 나머지 비교를 기다린다.'
    parts = ['<h1>추가 공공데이터가 예측에 도움이 되는가</h1><p>'+html.escape(status)+'</p>',
             '<p>버스정류소 4개, 선행 대여·반납 7개, 결합 11개를 같은 105개 특징 LightGBM에 각각 추가한다. 같은 A/B/C 개발 기간·정답·코호트·학습 설정·종료·보정·정책 선택 절차를 사용한다.</p>',
             '<p>역사 수신·원본 불변·과거 ST-ID 동일성·자정을 넘는 이동의 기준 날짜는 미검증이다. 이 결과는 조건부 자료 비교이며 운영 성능이나 독립 최종 시험 우위로 바꾸어 쓰지 않는다.</p>']
    if not frame.empty:
        view=[]
        for (fold,name),record in found.items():
            overall = frame[(frame.fold == fold)&(frame.model == name)&(frame.baseline == config['primary_control'])&(frame.group == 'overall')].iloc[0]
            positive = frame[(frame.fold == fold)&(frame.model == name)&(frame.baseline == config['primary_control'])&(frame.group == 'current_positive')].iloc[0]
            view.append({'기간':fold,'추가 자료':NAMES[name],'전체 F1(%)':f'{100*overall.f1:.3f}',
                         '공간·전환 기준 대비(%p)':f'{overall.f1_difference_pp:+.3f}',
                         '전환 재현율(%)':f'{100*positive.recall:.2f}',
                         '전환 오경보 차이(건)':f'{int(positive.fp_difference):+,}'})
        parts.append(pd.DataFrame(view).to_html(index=False,escape=True,border=0))
        for source in figures:parts.append('<figure><img src="'+html.escape(source,quote=True)+'" alt="추가자료의 기간별 F1 차이"></figure>')
        parts.append('<p>양수인 F1 차이는 점수 증가, 음수인 오류 건수 차이는 오류 감소를 뜻한다. 전체 F1·전환 재현율·오경보를 함께 확인한다. 전체·현재 양수·현재 1~3대 구간을 각 기준 모델과 비교한 CSV를 저장한다.</p>')
        parts.append('<p>날짜별 모든 대여소를 함께 묶은 짝지은 1일·7일 블록을 각각 2,000회 다시 뽑는다. 짧은 개발 기간·단일 초기값·여러 후보 비교·선택 편향이 남아 재표집 범위를 보장된 95% 신뢰수준이나 독립 우월성 증명으로 표현하지 않는다.</p>')
        parts.append('<p><a href="추가자료_각기준모델대비.csv">각 기준 모델 대비 전체 수치</a> · <a href="추가자료_날짜블록_차이범위.csv">날짜별 변동 범위</a></p>')
    else:
        parts.append('<p>아직 추가 자료 학습을 시작하지 않는다. 현재 실행 중인 v4 큐가 끝난 뒤 기존 결과를 검수하고, 버스→선행 흐름→결합 순서로 한 번에 하나씩 학습한다. 준비된 입력과 통과한 연결 검사를 성능 개선 결과로 제시하지 않는다.</p>')
    parts.append('<p><a href="데이터준비_쉬운설명.html">원천·정제·누락·연결 설명</a> · <a href="특징정의와근거.md">처리 근거와 정확한 특징 정의</a></p>')
    css='body{background:#edf1f5;color:#203246;margin:0;font:16px/1.8 "Malgun Gothic",sans-serif}main{max-width:1120px;margin:30px auto;padding:32px;background:white}h1{font-size:28px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:10px;border-bottom:1px solid #dce4eb}th{background:#edf4f9}img{width:100%;height:auto}figure{margin:24px 0}a{color:#176c9d}'
    (RECORD / '추가공공데이터_비교보고서.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>추가 공공데이터 모델 비교</title><style>'+css+'</style><main>'+''.join(parts)+'</main></html>',encoding='utf-8')
    review = dict(reviewed_at_utc=now(), public_extension_plan_sha256=plan_hash,
                  report_code_sha256=digest(__file__),
                  required=9, completed=len(found), missing=missing, matched_control_names=controls,
                  comparison_rows=len(comparisons), day_block_range_rows=len(ranges),
                  result_coverage=coverage, exact_decision_checks=decision_checks,
                  all_controls_and_frozen_validation_match=bool(found and len(decision_checks)==2*len(found)),
                  ready_for_manual_review=bool(len(found)==9 and not missing and len(decision_checks)==18),
                  goal_complete=False, information_track=config['information_track'])
    save(RECORD / '추가공공데이터_완료검수.json', review)
    print('PUBLIC_REPORT_SAVED',len(found),'of 9 fits',len(comparisons),'control/group pairs',len(ranges),'day-block ranges',flush=True)


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--require-complete',action='store_true');args=p.parse_args();build(args.require_complete)
