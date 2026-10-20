"""Exploratory paired moving-day-block ranges from frozen development decisions."""
import json
import joblib
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.special import logit
from .common import OUT, RECORD, save, now, digest, protocol
from .runner import Inputs
from ..research import decisions
from .analysis import setup, export, frame_csv, E2E, STAGES

BASELINES = ('persistence', 'seasonal_day', 'seasonal_week', 'smoothed_markov',
             'logistic', 'random_forest', 'lightgbm_basic')
FIELDS = ('n', 'zero_cases', 'tp', 'fp', 'fn', 'tn')


def moving_blocks(days, length, repeats=2000, seed=20261020):
    if not 1 <= length <= days:
        raise ValueError('Block length must fit the observed days')
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, days - length + 1,
                          size=(repeats, int(np.ceil(days / length))))
    return (starts[:, :, None] + np.arange(length)).reshape(repeats, -1)[:, :days]


def metrics(confusion):
    c = np.asarray(confusion, dtype=float)
    tp, fp, fn = c[..., 2], c[..., 3], c[..., 4]
    def ratio(n, d):
        return np.divide(n, d, out=np.zeros_like(n), where=d > 0)
    return dict(f1=ratio(2 * tp, 2 * tp + fp + fn),
                precision=ratio(tp, tp + fp), recall=ratio(tp, tp + fn))


def daily_confusions(y, pred, codes, number_days, mask):
    y, pred, group = y[mask], pred[mask], codes[mask]
    values = [np.ones(len(y), dtype=bool), y, y & pred, ~y & pred,
              y & ~pred, ~y & ~pred]
    return np.column_stack([np.bincount(group, weights=v.astype(np.int64),
                                       minlength=number_days) for v in values]).astype(np.int64)


def paired_ranges(left, right, draws):
    point_l, point_r = metrics(left.sum(axis=0)), metrics(right.sum(axis=0))
    sample_l, sample_r = metrics(left[draws].sum(axis=1)), metrics(right[draws].sum(axis=1))
    return {name: dict(difference_pp=float(100 * (point_l[name] - point_r[name])),
                       lower_pp=float(np.quantile(100 * (sample_l[name] - sample_r[name]), .025)),
                       upper_pp=float(np.quantile(100 * (sample_l[name] - sample_r[name]), .975)))
            for name in point_l}


def main():
    setup()
    plan_path = RECORD / 'uncertainty_plan.json'
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    data = Inputs()
    rows, checks, intervals = [], [], []
    for fold in 'ABC':
        split = protocol()['development_folds'][fold]
        index = data.indices(split['valid_start'], split['valid_end'])
        dates, codes = np.unique(data.tt[index].astype('datetime64[D]'), return_inverse=True)
        y, q = data.target[index] == 0, data.q.ravel()[index]
        by_model, tracks = {}, {}
        for path in sorted((OUT / fold).glob('*.json')):
            record = json.loads(path.read_text(encoding='utf-8'))
            if record.get('status') != 'ok':
                continue
            name = record['name']
            raw = np.load(OUT / 'predictions' / fold / (name + '_valid.npy'))
            if record['spec'].get('kind') in ('lgb', 'cat', 'linear', 'forest', 'poisson', 'ordinal', 'experts'):
                raw = raw.astype(np.float64)
            assert raw.shape == y.shape and np.isfinite(raw).all()
            cal = joblib.load(OUT / fold / 'models' / (name + '_calibrator.joblib'))
            p = cal.predict_proba(logit(np.clip(raw, 1e-6, 1-1e-6)).reshape(-1, 1))[:, 1].astype(np.float32)
            pred = decisions(p, q, record['rule'])
            groups = {}
            for group, mask in (('overall', np.ones(len(y), dtype=bool)), ('current_positive', q > 0)):
                daily = daily_confusions(y, pred, codes, len(dates), mask)
                expected = record['validation'][group]
                matches = all(int(daily[:, k].sum()) == expected[key] for k, key in enumerate(FIELDS))
                checks.append(dict(fold=fold, model=name, group=group,
                                   aggregate_matches=matches, result_sha256=digest(path)))
                if not matches:
                    raise RuntimeError(f'Cached decisions differ from frozen counts: {fold}/{name}/{group}')
                groups[group] = daily
                for date, counts in zip(dates, daily):
                    rows.append(dict(fold=fold, model=name, group=group, target_date=str(date),
                                     **dict(zip(FIELDS, counts.tolist()))))
            by_model[name], tracks[name] = groups, record['information_track']
        for length in plan['block_lengths_days']:
            draws = moving_blocks(len(dates), length, plan['repeats'], plan['seed'])
            for name, groups in by_model.items():
                for baseline in BASELINES:
                    if name == baseline or baseline not in by_model:
                        continue
                    for group, daily in groups.items():
                        ranges = paired_ranges(daily, by_model[baseline][group], draws)
                        for metric, values in ranges.items():
                            intervals.append(dict(fold=fold, model=name, baseline=baseline,
                                                  group=group, metric=metric, block_days=length,
                                                  days=len(dates), track=tracks[name], **values))
    folder = E2E / STAGES[5]
    frame_csv(pd.DataFrame(rows), folder, '일별_고정판정_오류집계')
    interval_frame = pd.DataFrame(intervals)
    frame_csv(interval_frame, folder, '각베이스라인대비_날짜블록_차이범위')
    save(RECORD / 'uncertainty_checks.json', dict(checked_at_utc=now(), checks=checks,
         all_aggregate_matches=all(r['aggregate_matches'] for r in checks),
         compared_result_count=len(checks)//2, interval_rows=len(intervals),
         plan_sha256=digest(plan_path), code_sha256=digest(__file__),
         interpretation='exploratory development sensitivity; no selection-adjusted superiority claim'))
    selected = interval_frame[(interval_frame.baseline == 'lightgbm_basic') &
                              (interval_frame.group == 'overall') & (interval_frame.metric == 'f1') &
                              interval_frame.model.isin(['lightgbm_full', 'catboost_temporal', 'xgboost_temporal', 'M2_no_weather'])]
    if not selected.empty:
        fig, ax = plt.subplots(figsize=(10, max(4, len(selected) * .31)))
        for j, row in enumerate(selected.itertuples()):
            color = '#287ca8' if row.block_days == 1 else '#d47732'
            ax.hlines(j, row.lower_pp, row.upper_pp, color=color)
            ax.plot(row.difference_pp, j, 'o', color=color, markersize=4)
        names={'lightgbm_full':'공간·전환 LightGBM','catboost_temporal':'CatBoost','xgboost_temporal':'XGBoost','M2_no_weather':'50:50 결합'}
        ax.set_yticks(np.arange(len(selected)), [f'{r.fold} · {names.get(r.model,r.model)} · {r.block_days}일 블록' for r in selected.itertuples()], fontsize=8)
        ax.axvline(0, color='#778899', linestyle='--')
        ax.set(xlabel='기본 LightGBM 대비 F1 차이(%p) · 재표집 2.5~97.5% 범위',
               title='날짜를 함께 다시 뽑아 개발 성능 차이의 변동을 확인한다')
        export(fig, folder, '날짜블록_성능차이범위')
    (folder / '날짜블록_읽는법.md').write_text(
        '# 날짜 블록 비교를 읽는 방법\n\n'
        '한 날짜의 모든 대여소·시간을 함께 뽑고, 같은 날짜 묶음을 두 모델에 적용한다. '
        '개별 행 백만 개를 서로 독립인 표본으로 다루지 않는다. 1일 묶음과 연속 7일 묶음을 각각 2,000회 뽑는다. '
        '각 재표집의 TP·FP·FN을 합쳐 F1을 다시 계산한다. 날짜별 F1을 단순 평균하지 않는다.\n\n'
        '점은 원래 기간의 차이이며 선은 재표집 차이의 2.5~97.5% 범위로 표시한다. '
        '점이 재표집 범위 바깥에 있을 수도 있다. 짧은 기간·끝부분 가중·주간 묶음 수가 결과에 영향을 준다. '
        '정상성·장기 의존·계절 일반화·모델 초기값 변동을 입증한 검사가 아니다.\n\n'
        '이미 성과를 본 개발 구간의 탐색적 보조 분석으로만 사용한다. '
        '다중 후보 선택·여러 비교의 편향을 보정하지 않았으며 독립 최종 시험의 우월성 검정이나 보장된 95% 신뢰수준으로 표현하지 않는다. '
        '기간별 학습 자료도 겹치므로 A/B/C를 서로 독립적인 반복 실험으로 취급하지 않는다.\n\n'
        '원래 성과 JSON과 전체 혼동행렬이 일치한 경우만 표에 포함한다. '
        '아카이브 조건부 모델은 정보 조건 열과 함께 해석한다. '
        '블록 재표집의 문헌 근거·프로젝트 선택·한계는 상위 데이터처리_근거기록.md에 남긴다.\n', encoding='utf-8')
    print('UNCERTAINTY_SAVED', len(checks)//2, 'frozen results', len(intervals), 'comparison ranges', flush=True)


if __name__ == '__main__':
    main()
