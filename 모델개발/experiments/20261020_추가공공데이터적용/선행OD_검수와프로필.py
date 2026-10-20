"""Inspect every 2024-11 OD member and prepare explicitly conditional prior profiles."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import calendar
import csv
import hashlib
import json
import re
import unicodedata
import zipfile
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
SOURCE = ROOT / '원본/tpss_bcycl_od_statnhm_202411.zip'
PREP = ROOT / '정제본'
FIG = ROOT / '시각화'
KEY = ['기준_날짜', '집계_기준', '기준_시간대', '시작_대여소_ID', '종료_대여소_ID']
COLS = ['기준_날짜', '집계_기준', '기준_시간대', '시작_대여소_ID', '시작_대여소명',
        '종료_대여소_ID', '종료_대여소명', '전체_건수', '전체_이용_분', '전체_이용_거리']


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def clean_name(value):
    if pd.isna(value):
        return ''
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value)))


def crosswalk(source_ids):
    cohort = pd.read_parquet(PROJECT / 'data/processed/rider_stations.parquet')
    live_path = PROJECT / 'data/raw/followup_v4_external/live_bikes_latest.csv'
    live = pd.read_csv(live_path)
    assert not cohort.station_id.duplicated().any()
    assert not live.station_id.duplicated().any() and not live.stationId.duplicated().any()
    keep = live[['station_id', 'stationId', 'stationName', 'stationLatitude', 'stationLongitude']].copy()
    frame = cohort[['station_id', 'name', 'latitude', 'longitude']].merge(keep, on='station_id', how='left', validate='one_to_one')
    lat1 = np.deg2rad(frame.latitude.to_numpy(float))
    lat2 = np.deg2rad(pd.to_numeric(frame.stationLatitude).to_numpy(float))
    dlat = lat2 - lat1
    dlon = np.deg2rad(pd.to_numeric(frame.stationLongitude).to_numpy(float) - frame.longitude.to_numpy(float))
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    frame['coordinate_difference_m'] = 2 * 6371008.8 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    live_names = frame.stationName.str.replace(r'^\s*\d+\.\s*', '', regex=True)
    frame['normalized_name_exact'] = [clean_name(x) == clean_name(y) and bool(clean_name(y))
                                      for x, y in zip(frame.name, live_names)]
    frame['current_number_match'] = frame.stationId.notna()
    frame['prior_archive_contains_ST_ID'] = frame.stationId.isin(source_ids)
    frame['conditional_link_allowed'] = (frame.current_number_match & frame.normalized_name_exact
                                         & frame.coordinate_difference_m.le(50))
    frame['historical_ST_ID_identity_proven'] = False
    frame.to_csv(PREP / '대여소_ST-ID_현재교차표_과거미검증.csv', index=False, encoding='utf-8-sig')
    return frame, {'current_crosswalk_path': str(live_path.relative_to(PROJECT)),
                   'current_crosswalk_sha256': digest(live_path), 'cohort_rows': len(frame),
                   'current_number_matches': int(frame.current_number_match.sum()),
                   'exact_name_and_within_50m_matches': int(frame.conditional_link_allowed.sum()),
                   'allowed_and_present_in_prior_archive': int((frame.conditional_link_allowed & frame.prior_archive_contains_ST_ID).sum()),
                   'historical_identity_proven': False}


def main():
    PREP.mkdir(exist_ok=True)
    FIG.mkdir(exist_ok=True)
    receipt = json.loads((ROOT / '202411_OD_수신기록.json').read_text(encoding='utf-8'))
    assert digest(SOURCE) == receipt['sha256']
    exposure = Counter(datetime(2024, 11, day).weekday() for day in range(1, 31))
    nonempty_exposure = Counter()
    sums, reported_days, edges = Counter(), Counter(), Counter()
    all_ids = set()
    daily = []
    with zipfile.ZipFile(SOURCE) as archive:
        expected = {f'tpss_bcycl_od_statnhm_202411{day:02}.csv' for day in range(1, 31)}
        assert set(archive.namelist()) == expected
        for member in sorted(archive.namelist()):
            date = member[-12:-4]
            with archive.open(member) as stream:
                header = stream.readline()
            encoding = None
            for candidate in ('utf-8-sig', 'cp949'):
                try:
                    fields = next(csv.reader([header.decode(candidate).strip()]))
                except UnicodeDecodeError:
                    continue
                if fields == COLS:
                    encoding = candidate
                    break
            assert encoding is not None, 'Unrecognized strict CSV header encoding'
            with archive.open(member) as stream:
                data = pd.read_csv(stream, encoding=encoding, dtype=str)
            assert list(data.columns) == COLS, member
            weekday = datetime.strptime(date, '%Y%m%d').weekday()
            if data.empty:
                daily.append({'date': date, 'rows': 0, 'zero_count_rows': 0, 'encoding': encoding,
                              'data_status': 'header_only_missing_not_zero',
                              'missing_start_name': 0, 'missing_end_name': 0,
                              'missing_duration': 0, 'missing_distance': 0,
                              'departure_rows': 0, 'arrival_rows': 0,
                              'departure_reported_count': np.nan, 'arrival_reported_count': np.nan})
                print('OD_HEADER_ONLY_MISSING', date, flush=True)
                continue
            nonempty_exposure[weekday] += 1
            assert data['기준_날짜'].eq(date).all(), member
            assert set(data['집계_기준']) == {'출발시간', '도착시간'}, member
            assert data[KEY].notna().all().all(), member
            assert not data.duplicated(KEY).any(), 'Duplicate OD aggregation keys require review'
            times = data['기준_시간대']
            assert times.str.fullmatch(r'\d{4}').all()
            hours = times.str[:2].astype(int)
            minutes = times.str[2:].astype(int)
            assert hours.between(0, 23).all() and minutes.between(0, 59).all() and minutes.mod(5).eq(0).all()
            data['hour'] = hours
            quantity = pd.to_numeric(data['전체_건수'], errors='raise')
            assert quantity.notna().all() and quantity.ge(0).all() and quantity.mod(1).eq(0).all()
            data['count'] = quantity.astype('int64')
            all_ids.update(data['시작_대여소_ID'])
            all_ids.update(data['종료_대여소_ID'])
            stat = {'date': date, 'rows': len(data), 'zero_count_rows': int(quantity.eq(0).sum()),
                    'encoding': encoding, 'data_status': 'rows_present_completeness_unknown',
                    'missing_start_name': int(data['시작_대여소명'].isna().sum()),
                    'missing_end_name': int(data['종료_대여소명'].isna().sum()),
                    'missing_duration': int(data['전체_이용_분'].isna().sum()),
                    'missing_distance': int(data['전체_이용_거리'].isna().sum())}
            for basis, side, label in [('출발시간', '시작_대여소_ID', 'departure'), ('도착시간', '종료_대여소_ID', 'arrival')]:
                subset = data[data['집계_기준'].eq(basis)]
                stat[label + '_rows'] = len(subset)
                stat[label + '_reported_count'] = int(subset['count'].sum())
                values = subset.groupby([side, 'hour'], sort=False)['count'].sum()
                for (station, hour), count in values.items():
                    key = (station, weekday, hour, label)
                    sums[key] += int(count)
                    reported_days[key] += 1
                if label == 'departure':
                    pairs = subset.groupby(['시작_대여소_ID', '종료_대여소_ID'], sort=False)['count'].sum()
                    for pair, count in pairs.items():
                        edges[pair] += int(count)
            daily.append(stat)
            print('OD_DAY_AUDITED', date, len(data), flush=True)
    records = []
    for (station, weekday, hour, basis), count in sums.items():
        n = reported_days[(station, weekday, hour, basis)]
        records.append((station, weekday, hour, basis, count, n, exposure[weekday],
                        nonempty_exposure[weekday], count / n,
                        n / nonempty_exposure[weekday], n / exposure[weekday]))
    profile = pd.DataFrame(records, columns=['ST_ID', 'weekday', 'hour', 'basis', 'reported_count',
                                            'reported_days', 'calendar_days', 'nonempty_archive_days',
                                            'mean_per_reported_day', 'reported_day_fraction', 'calendar_reported_day_fraction'])
    profile.to_parquet(PREP / '202411_ST-ID_요일시간_수록프로필.parquet', index=False)
    edge_frame = pd.DataFrame([(a, b, count) for (a, b), count in edges.items()],
                              columns=['from_ST_ID', 'to_ST_ID', 'departure_basis_reported_count'])
    edge_frame.to_parquet(PREP / '202411_출발기준_이동관계.parquet', index=False)
    daily_frame = pd.DataFrame(daily)
    daily_frame.to_csv(PREP / '202411_일별검수.csv', index=False, encoding='utf-8-sig')
    mapping, mapping_audit = crosswalk(all_ids)
    slots = pd.MultiIndex.from_product([mapping.station_id, range(7), range(24)], names=['station_id', 'weekday', 'hour']).to_frame(index=False)
    slots = slots.merge(mapping[['station_id', 'stationId', 'conditional_link_allowed']], on='station_id', validate='many_to_one')
    for basis in ('departure', 'arrival'):
        part = profile[profile.basis.eq(basis)][['ST_ID', 'weekday', 'hour', 'mean_per_reported_day', 'reported_days', 'reported_day_fraction']]
        part = part.rename(columns={name: basis + '_' + name for name in ('mean_per_reported_day', 'reported_days', 'reported_day_fraction')})
        slots = slots.merge(part, left_on=['stationId', 'weekday', 'hour'], right_on=['ST_ID', 'weekday', 'hour'], how='left', validate='many_to_one').drop(columns='ST_ID')
        allowed = slots.conditional_link_allowed
        for name in ('reported_days', 'reported_day_fraction'):
            col = basis + '_' + name
            slots.loc[allowed, col] = slots.loc[allowed, col].fillna(0)
        for col in (basis + '_' + n for n in ('mean_per_reported_day', 'reported_days', 'reported_day_fraction')):
            slots.loc[~allowed, col] = np.nan
    assert len(slots) == len(mapping) * 7 * 24
    slots.to_parquet(PREP / '202411_코호트_요일시간_조건부프로필.parquet', index=False)
    plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False})
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.plot(range(1, 31), daily_frame.departure_reported_count, label='출발시간 기준')
    ax.plot(range(1, 31), daily_frame.arrival_reported_count, label='도착시간 기준', linestyle='--')
    ax.set(xlabel='2024년 11월 날짜', ylabel='원천 수록 건수 합', title='두 시간 기준을 합쳐 이동 총건수로 세지 않는다')
    ax.legend();fig.tight_layout()
    for ext in ('png', 'svg'): fig.savefig(FIG / ('01_일별수록흐름.' + ext), dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))
    values = [mapping_audit['cohort_rows'], mapping_audit['current_number_matches'], mapping_audit['exact_name_and_within_50m_matches'], mapping_audit['allowed_and_present_in_prior_archive']]
    axes[0].barh(['고정 코호트', '현재 번호 일치', '이름·50m 조건', '조건 충족·과거 ID 존재'], values, color=['#607d8b', '#4f81a8', '#327d75', '#327d75'])
    for i, n in enumerate(values): axes[0].text(n + 10, i, str(n), va='center')
    axes[0].set_xlim(0, len(mapping) * 1.18);axes[0].set_title('현재 교차표의 조건부 연결')
    distance = mapping.coordinate_difference_m.dropna()
    axes[1].hist(np.log1p(distance), bins=30, color='#4f81a8')
    axes[1].axvline(np.log1p(50), color='#a65c2c', linestyle='--', label='50m 기준')
    axes[1].set(xlabel='log(1+좌표 차이 m)', ylabel='대여소 수', title='과거 ST-ID 동일성은 별도 미검증')
    axes[1].legend();fig.tight_layout()
    for ext in ('png', 'svg'): fig.savefig(FIG / ('02_현재ID연결검수.' + ext), dpi=160)
    plt.close(fig)
    outputs = [dict(path=str(p.relative_to(ROOT)), bytes=p.stat().st_size, sha256=digest(p)) for p in sorted(PREP.glob('*'))]
    audit = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'source_archive_sha256': receipt['sha256'],
             'preprocessing_plan_sha256': digest(ROOT / '준비계획.md'), 'csv_members': 30,
             'every_member_read_with_zip_crc_validation': True, 'source_rows': int(daily_frame.rows.sum()),
             'date_range': ['2024-11-01', '2024-11-30'], 'basis_count_sums': {basis: int(daily_frame[basis + '_reported_count'].sum()) for basis in ('departure', 'arrival')},
             'header_only_missing_dates': daily_frame.loc[daily_frame.data_status.eq('header_only_missing_not_zero'), 'date'].tolist(),
             'encoding_member_counts': daily_frame.encoding.value_counts().to_dict(),
             'nonempty_archive_days_by_weekday': dict(nonempty_exposure),
             'basis_added_together_as_trips': False, 'unique_source_ST_IDs': len(all_ids),
             'duplicate_aggregation_keys': 0, 'invalid_date_time_count_rows': 0,
             'missing_auxiliary_values': {k: int(daily_frame[k].sum()) for k in ('missing_start_name', 'missing_end_name', 'missing_duration', 'missing_distance')},
             'profile_rows': len(profile), 'departure_pair_rows': len(edge_frame),
             'conditional_cohort_slot_rows': len(slots), 'mapping': mapping_audit,
             'unreported_flow_filled_as_zero': False, 'unreported_flow_mean': 'NaN; reported-day coverage is separate',
             'historical_first_publication_and_reception_proven': False, 'model_training_performed': False,
             'new_2025_targets_or_held_back_labels_read': False, 'outputs': outputs}
    save(ROOT / '202411_OD_전수검수.json', audit)
    print('OD_PROFILE_PREPARED', json.dumps({k: audit[k] for k in ('source_rows', 'profile_rows', 'departure_pair_rows', 'conditional_cohort_slot_rows', 'mapping')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
