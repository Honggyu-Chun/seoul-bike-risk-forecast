"""Independent aggregate checks and readable saved research report; no model fitting."""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import csv
import hashlib
import html
import io
import json
import zipfile
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def main():
    audit = json.loads((ROOT / '202411_OD_전수검수.json').read_text(encoding='utf-8'))
    receipt = json.loads((ROOT / '202411_OD_수신기록.json').read_text(encoding='utf-8'))
    for item in audit['outputs']:
        assert sha(ROOT / item['path']) == item['sha256']
    profile = pd.read_parquet(ROOT / '정제본/202411_ST-ID_요일시간_수록프로필.parquet')
    edges = pd.read_parquet(ROOT / '정제본/202411_출발기준_이동관계.parquet')
    slots = pd.read_parquet(ROOT / '정제본/202411_코호트_요일시간_조건부프로필.parquet')
    daily = pd.read_csv(ROOT / '정제본/202411_일별검수.csv', dtype={'date': str})
    mapping = pd.read_csv(ROOT / '정제본/대여소_ST-ID_현재교차표_과거미검증.csv')
    expected = audit['basis_count_sums']
    assert profile.groupby('basis').reported_count.sum().to_dict() == expected
    assert int(edges.departure_basis_reported_count.sum()) == expected['departure']
    assert profile.reported_days.between(1, profile.nonempty_archive_days).all()
    np.testing.assert_allclose(profile.mean_per_reported_day * profile.reported_days,
                               profile.reported_count, rtol=1e-12, atol=1e-9)
    np.testing.assert_allclose(profile.reported_day_fraction * profile.nonempty_archive_days,
                               profile.reported_days, rtol=1e-12, atol=1e-9)
    missing = daily[daily.data_status.eq('header_only_missing_not_zero')]
    assert missing.date.tolist() == ['20241127']
    assert missing[['departure_reported_count', 'arrival_reported_count']].isna().all().all()
    assert len(slots) == 2729 * 7 * 24
    assert not slots.duplicated(['station_id', 'weekday', 'hour']).any()
    unlinked = slots[~slots.conditional_link_allowed]
    flow_columns = [c for c in slots if c.startswith('departure_') or c.startswith('arrival_')]
    assert unlinked[flow_columns].isna().all().all()
    for basis in ('departure', 'arrival'):
        absent = slots[slots.conditional_link_allowed & slots[basis + '_reported_days'].eq(0)]
        assert absent[basis + '_mean_per_reported_day'].isna().all()
    # Use the standard-library CSV reader, independently of the pandas/groupby builder.
    counts = Counter()
    archive_path = ROOT / receipt['archive']
    with zipfile.ZipFile(archive_path) as archive:
        with archive.open('tpss_bcycl_od_statnhm_20241101.csv') as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding='cp949'))
            for row in reader:
                counts[row['집계_기준']] += int(row['전체_건수'])
    first = daily[daily.date.eq('20241101')].iloc[0]
    assert counts['출발시간'] == first.departure_reported_count
    assert counts['도착시간'] == first.arrival_reported_count
    checks = dict(checked_at_utc=datetime.now(timezone.utc).isoformat(),
                  all_prepared_hashes_match=True, profile_mass_conservation=True,
                  departure_graph_mass_conservation=True, observed_day_denominators_valid=True,
                  header_only_day_not_filled_zero=True, fixed_2729_cohort_preserved=True,
                  unlinked_values_remain_missing=True, unreported_means_remain_missing=True,
                  independent_csv_reader_first_day_sums_match=True,
                  cross_midnight_source_date_semantics_verified=False,
                  new_model_performance_verified=False)
    save(ROOT / '정제본_독립검수.json', checks)
    plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False})
    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.plot(range(1, 31), daily.departure_reported_count, label='출발시간 기준')
    ax.plot(range(1, 31), daily.arrival_reported_count, label='도착시간 기준', linestyle='--')
    ax.axvspan(26.7, 27.3, alpha=.2, color='#999999')
    ax.text(27, .97, '27일 자료 없음', transform=ax.get_xaxis_transform(), ha='center', va='top', fontsize=10)
    ax.set(xlabel='2024년 11월 날짜', ylabel='원천 수록 건수 합', title='출발·도착 시간 기준별 수록 건수(각각 집계)')
    ax.legend(loc='upper center');fig.tight_layout()
    for ext in ('png', 'svg'):fig.savefig(ROOT / ('시각화/01_일별수록흐름.' + ext), dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    m = audit['mapping']
    labels = ['고정 코호트', '현재 번호 일치', '이름·50m 조건', '조건 충족·과거 ID 존재']
    values = [m['cohort_rows'], m['current_number_matches'], m['exact_name_and_within_50m_matches'], m['allowed_and_present_in_prior_archive']]
    axes[0].barh(labels, values, color=['#607d8b', '#4f81a8', '#327d75', '#327d75'])
    for i, n in enumerate(values):axes[0].text(n + 10, i, str(n), va='center')
    axes[0].set_xlim(0, m['cohort_rows'] * 1.18);axes[0].set_title('현재 교차표의 조건부 연결', fontsize=12)
    distance = mapping.coordinate_difference_m.dropna()
    axes[1].hist(np.log1p(distance), bins=30, color='#4f81a8')
    axes[1].axvline(np.log1p(50), color='#a65c2c', linestyle='--', label='50m 기준')
    axes[1].set(xlabel='log(1+좌표 차이 m)', ylabel='대여소 수')
    axes[1].set_title('과거 ST-ID 동일성은 별도 미검증', fontsize=12)
    axes[1].legend();fig.tight_layout()
    for ext in ('png', 'svg'):fig.savefig(ROOT / ('시각화/02_현재ID연결검수.' + ext), dpi=160)
    plt.close(fig)
    e = html.escape
    comparison_review=ROOT/'추가공공데이터_완료검수.json'
    compared=json.loads(comparison_review.read_text(encoding='utf-8'))['completed'] if comparison_review.exists() else 0
    model_status=f'추가 자료 {compared}/9개 비교의 계산을 검수한다.' if compared else '아직 추가 자료 모델을 학습하지 않음'
    facts = [('원천 수록 행', f'{audit["source_rows"]:,}행(두 시간 기준 포함)'),
             ('일파일', '30개 중 실제 행이 있는 파일 29개'),
             ('출발시간 기준 수록 건수 합', f'{expected["departure"]:,}건'),
             ('도착시간 기준 수록 건수 합', f'{expected["arrival"]:,}건(출발 합계와 더하지 않음)'),
             ('원천 ST-ID', f'{audit["unique_source_ST_IDs"]:,}개'),
             ('고정 코호트·요일·시간 셀', f'{len(slots):,}개'),
             ('조건 충족·과거 ID 존재', f'{m["allowed_and_present_in_prior_archive"]:,}/{m["cohort_rows"]:,}곳({100*m["allowed_and_present_in_prior_archive"]/m["cohort_rows"]:.2f}%)'),
             ('모델 비교 진행', model_status)]
    table = '<table>' + ''.join('<tr><th>' + e(k) + '</th><td>' + e(v) + '</td></tr>' for k, v in facts) + '</table>'
    body = '''<h1>따릉이 대여·반납 자료를 실제 입력으로 준비한다</h1>
<p>현재 수량만으로 부족 위험을 설명하기 어려운 곳에 과거 출발·도착 패턴이 도움이 되는지 확인하기 위해 선행월 자료를 준비한다. 2025년 개발 실험보다 앞서 종료된 2024년 11월 자료를 고정하고, 미래 실제 이동을 입력하지 않는다.</p>'''+table+'''
<h2>먼저 확인한 품질 문제를 처리한다</h2>
<p>11월 27일 파일은 UTF-8 헤더만 있고 데이터 행이 없다. 다른 29개 파일은 CP949로 읽는다. 엄격한 해독과 열 검사를 통과한 파일만 처리하며, 27일을 실제 이용 0으로 바꾸지 않는다. 같은 요일의 비어 있지 않은 일파일을 수록률 분모로 사용한다.</p>
<p>출발시간 기준과 도착시간 기준을 따로 집계한다. 한 이동의 서로 다른 시간 기준을 합산해 두 배로 세지 않는다. 대여소·시각에 행이 없으면 평균은 결측으로 남기고 수록 일수와 기록 존재 마스크를 함께 둔다. 이용분·거리의 결측 때문에 유효한 건수까지 삭제하지 않는다.</p>
<figure><img src="시각화/01_일별수록흐름.png" alt="날짜별 출발 및 도착 기준 수록 건수와 27일 자료 누락"><figcaption>회색 영역은 실제 0건이 아닌 헤더만 있는 날짜를 표시한다. 행이 있는 날짜도 전체 이동의 완전 수록을 인증한 것은 아니다.</figcaption></figure>
<h2>연결 범위를 보수적으로 제한한다</h2>
<p>현재 bikeList의 ST-ID와 시민용 대여소 번호를 연결한다. 2024 코호트와 현재 번호가 같고 공백·유니코드만 정규화한 이름이 일치하며 좌표 차이가 50m 이내인 곳을 조건부 연결한다. 과거 ST-ID의 실제 동일성은 별도 미검증으로 유지한다. 고정 코호트를 줄이지 않고 미연결 입력을 결측으로 둔다.</p>
<figure><img src="시각화/02_현재ID연결검수.png" alt="고정 코호트와 조건부 ID 연결 수"><figcaption>현재 교차표의 연결 조건을 통과한 수와 과거 흐름에서 실제 ID가 나타난 수를 구분한다.</figcaption></figure>
<h2>다음 모델에서 비교할 입력을 정리한다</h2>
<p>버스정류소 거리·반경별 개수 4개, 선행 흐름의 평균·수록률·결측 마스크 7개를 작은 입력 배열로 준비한다. 흐름은 입력 t의 다음 시간 t+1에 해당하는 요일·시각을 조회한다. 공개 전 입력 차단, 자정 요일 전환, 입력 순서·중복, 미연결 결측을 검사한다.</p>
<p>같은 105개 특징 모델에 버스, 흐름, 둘을 각각 더하는 A/B/C 총 9개 비교를 계획으로 고정한다. 기존 v4 큐 다음에 순차 학습하고 진행 상태와 성과는 <a href="추가공공데이터_비교보고서.html">추가 공공데이터 비교보고서</a>에서 확인한다. 이미 확인한 개발 기간의 추가 비교이며 독립 최종 시험으로 제시하지 않는다.</p>
<h2>해석 범위와 남은 확인을 기록한다</h2>
<p>공식 목록의 수정일은 2024-12-08이다. 목록에는 125.93MB가 표시되지만 실제 응답은 123,792,524바이트를 받는다. 날짜·구성원·압축검사·해시로 받은 본문을 확인하며 이 크기 차이를 임의로 해소했다고 주장하지 않는다. 최초 공개·당시 수신·원본 불변·과거 마스터는 미검증이다.</p>
<p>단일 11월의 패턴이 봄·여름에도 맞는지는 실험해야 한다. 현재 교차표와 선행 자료의 한계가 남아 있으므로 추가 결과는 조건부 자료 비교로 제시한다. 준비만으로 성능 개선이나 실제 대여 성공을 확인한 것으로 표현하지 않는다.</p>
<p>각 일파일의 출발·도착 기준 수록 합계가 같다는 것은 확인한다. 자정을 넘는 이동에서 기준 날짜가 실제 도착 날짜와 어떻게 연결되는지는 원천 명세만으로 확정하지 못한다. 프로필은 원천이 표시한 날짜·시간 기준의 과거 집계로 사용하며 실제 미래 유입량으로 해석하지 않는다.</p>
<p><a href="https://data.seoul.go.kr/dataList/OA-21229/F/1/datasetView.do">서울시 공식 원천</a> · <a href="준비계획.md">자료 준비 기준</a> · <a href="특징정의와근거.md">특징 정의와 근거</a> · <a href="202411_OD_전수검수.json">원천 전수검수</a> · <a href="정제본_독립검수.json">독립 집계 검사</a></p>'''
    css = 'body{background:#edf1f5;color:#203246;margin:0;font:16px/1.8 "Malgun Gothic",sans-serif}main{max-width:1000px;margin:30px auto;padding:32px;background:white}h1{font-size:28px}h2{font-size:22px;margin-top:32px}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;border-bottom:1px solid #dce4eb}th{width:40%;background:#edf4f9}figure{margin:24px 0}img{width:100%;height:auto}figcaption{font-size:14px;color:#53677b}a{color:#176c9d}@media(max-width:600px){main{margin:0;padding:18px}th{width:38%}}'
    (ROOT / '데이터준비_쉬운설명.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>추가 흐름 데이터 준비</title><style>'+css+'</style><main>'+body+'</main></html>', encoding='utf-8')
    base = PROJECT / 'experiments/20261020_후속실험_v4/protocol.json'
    meta_path = PROJECT / 'artifacts/followup_public_features_20261020/metadata.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    plan_path = ROOT / '후속비교_계획.json'
    if not plan_path.exists():
        baseline = json.loads(base.read_text(encoding='utf-8'))
        specs = [dict(name='lightgbm_public_'+name, extra_features=count, base_features=105,
                      group=name, kind='lgb', leaves=127, rounds=900, window=56, seed=20261020)
                 for name, count in [('bus', 4), ('od', 7), ('bus_od', 11)]]
        save(plan_path, dict(created_at_utc=datetime.now(timezone.utc).isoformat(),
                             status='after_partial_v4_development_results_before_public_extension_training',
                             v4_protocol_sha256=sha(base), feature_metadata_sha256=sha(meta_path),
                             definition_document_sha256=sha(ROOT / '특징정의와근거.md'),
                             information_track=meta['information_track'], development_folds=baseline['development_folds'],
                             candidate_specs=specs, folds=['A','B','C'], registered_comparisons=9,
                             primary_control='lightgbm_full', additional_controls=baseline['basic_baselines'],
                             calibration_and_threshold_policy='same time windows and v4 procedures; never outside validation tuning',
                             parallel_training_jobs=1, threads=4, run_only_after_v4_queue_terminates=True,
                             unopened_Q3_Q4_scores_remain_unopened=True, independent_final_test=False,
                             historical_reception_verified=False, historical_station_ID_identity_verified=False,
                             adoption='conditional data experiment; not eligible as proven historical live-input winner'))
    print('PUBLIC_DATA_CHECKS_AND_REPORT_SAVED', json.dumps(checks, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
