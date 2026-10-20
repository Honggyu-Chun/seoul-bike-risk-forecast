"""Point-in-time input safety; no model imports or training."""
import numpy as np
import pandas as pd
import pytest

from bike_demo.followup_v4.operational_inputs import select_observed_inputs, tag_archived_features


def snapshots(rows):
    return pd.DataFrame(rows, columns=["timestamp", "district", "received_at", "pm10", "pm25"])


def request(t="2025-05-01 10:00", district="종로구"):
    return pd.DataFrame({"input_time": [t], "district": [district]}, index=[17])


def select(frame, queries=None, **kwargs):
    return select_observed_inputs(frame, request() if queries is None else queries, ["pm10", "pm25"], **kwargs)


def test_filters_future_observation_and_future_reception():
    frame = snapshots([
        ["2025-05-01 07:00", "종로구", "2025-05-01 08:01", 10, 5],
        ["2025-05-01 08:00", "종로구", "2025-05-01 10:01", 80, 40],
        ["2025-05-01 09:00", "종로구", "2025-05-01 09:01", 90, 45],
    ])
    result = select(frame).iloc[0]
    assert result.pm10 == 10
    assert result.source_timestamp == pd.Timestamp("2025-05-01 07:00", tz="Asia/Seoul")
    assert result.operational_eligible


def test_future_revisions_cannot_change_an_earlier_input():
    old = snapshots([["2025-05-01 08:00", "종로구", "2025-05-01 09:00", 20, 8]])
    revision = snapshots([["2025-05-01 08:00", "종로구", "2025-05-01 11:00", 200, 80]])
    combined = pd.concat([old, revision], ignore_index=True)
    pd.testing.assert_frame_equal(select(old), select(combined))
    later = select(combined, request("2025-05-01 12:00")).iloc[0]
    assert later.pm10 == 200


def test_min_lag_and_reception_boundaries_are_inclusive():
    frame = snapshots([
        ["2025-05-01 07:00", "종로구", "2025-05-01 08:00", 7, 3],
        ["2025-05-01 08:00", "종로구", "2025-05-01 10:00", 8, 4],
    ])
    assert select(frame).iloc[0].pm10 == 8
    assert select(frame, min_observation_lag="3h").iloc[0].pm10 == 7


def test_maximum_age_stops_unbounded_carry_forward():
    frame = snapshots([["2025-05-01 03:00", "종로구", "2025-05-01 04:00", 12, 6]])
    row = select(frame).iloc[0]
    assert row.selection_status == "stale"
    assert not row.operational_eligible and np.isnan(row.pm10)
    assert select(frame, max_observation_age="7h").iloc[0].pm10 == 12


def test_qc_masks_each_field_without_silently_backfilling():
    frame = snapshots([
        ["2025-05-01 07:00", "종로구", "2025-05-01 08:00", 70, 35],
        ["2025-05-01 08:00", "종로구", "2025-05-01 09:00", 80, 40],
    ])
    frame["pm10_qc"] = [0, 1]
    row = select(frame).iloc[0]
    assert np.isnan(row.pm10) and not row.pm10_usable
    assert row.pm25 == 40 and row.pm25_usable
    assert row.selection_status == "selected_with_missing_values"
    assert row.timing_eligible and row.operational_eligible


@pytest.mark.parametrize("pm10,pm25", [(None, None), (-1, -99), (np.inf, -np.inf), ("bad", "bad")])
def test_all_unusable_values_are_not_marked_operational(pm10, pm25):
    row = select(snapshots([["2025-05-01 08:00", "종로구", "2025-05-01 09:00", pm10, pm25]])).iloc[0]
    assert row.timing_eligible
    assert not row.operational_eligible
    assert row.selection_status == "selected_all_values_unusable"
    assert np.isnan(row.pm10) and np.isnan(row.pm25)


def test_unknown_qc_does_not_become_zero():
    frame = snapshots([["2025-05-01 08:00", "종로구", "2025-05-01 09:00", 20, 10]])
    frame["pm10_qc"] = [None]
    row = select(frame).iloc[0]
    assert row.pm10_qc == 5 and not row.pm10_usable


def test_provider_maintenance_text_is_not_a_zero_or_a_numeric_observation():
    row = select(snapshots([["2025-05-01 08:00", "종로구", "2025-05-01 09:00", "점검중", "점검중"]])).iloc[0]
    assert row.pm10_qc == row.pm25_qc == 3
    assert not row.operational_eligible
    assert np.isnan(row.pm10) and np.isnan(row.pm25)


def test_archive_without_received_at_never_passes_operational_filter():
    frame = snapshots([["2025-05-01 08:00", "종로구", None, 20, 10]]).drop(columns="received_at")
    row = select(frame).iloc[0]
    assert row.reception_status == row.selection_status == "archived_reception_unknown"
    assert not row.timing_eligible and not row.operational_eligible
    archived = pd.DataFrame({"timestamp": ["2025-05-01 10:00"], "air_pm10_ug_m3_lag2h": [20.]})
    tagged = tag_archived_features(archived)
    assert "received_at" not in tagged
    pd.testing.assert_frame_equal(tagged[archived.columns], archived)
    assert tagged.reception_status.eq("archived_reception_unknown").all()
    assert not tagged.operational_eligible.any()


def test_missing_provider_observation_time_is_not_replaced_by_receive_time():
    # The bike API's local reception clock alone cannot establish its observation clock.
    frame = snapshots([[None, "종로구", "2025-05-01 07:00", 20, 10]]).drop(columns="timestamp")
    row = select(frame).iloc[0]
    assert row.selection_status == "observation_time_unknown"
    assert pd.isna(row.source_timestamp) and not row.operational_eligible


def test_clock_inconsistent_observation_is_rejected():
    row = select(snapshots([["2025-05-01 08:00", "종로구", "2025-05-01 07:59", 20, 10]])).iloc[0]
    assert row.selection_status == "invalid_observation_clock"
    assert not row.operational_eligible


def test_districts_and_requested_index_remain_separate():
    frame = snapshots([["2025-05-01 08:00", "중구", "2025-05-01 09:00", 20, 10]])
    result = select(frame)
    assert list(result.index) == [17]
    assert result.iloc[0].selection_status == "missing_district"


def test_aware_utc_and_naive_kst_represent_the_same_instant():
    local = snapshots([["2025-05-01 08:00", "종로구", "2025-05-01 09:00", 20, 10]])
    utc = snapshots([["2025-04-30 23:00+00:00", "종로구", "2025-05-01 00:00+00:00", 20, 10]])
    pd.testing.assert_frame_equal(select(local), select(utc, request("2025-05-01 01:00+00:00")))


def test_conflicting_identically_timed_snapshots_fail_independent_of_order():
    frame = snapshots([
        ["2025-05-01 08:00", "종로구", "2025-05-01 09:00", 20, 10],
        ["2025-05-01 08:00", "종로구", "2025-05-01 09:00", 21, 10],
    ])
    result = select(frame)
    assert result.iloc[0].selection_status == "conflicting_duplicate_snapshot"
    assert not result.iloc[0].operational_eligible
    pd.testing.assert_frame_equal(result, select(frame.iloc[::-1]))
    assert select(pd.concat([frame.iloc[:1], frame.iloc[:1]])).iloc[0].pm10 == 20


def test_unknown_input_and_empty_requests_are_safe():
    frame = snapshots([["2025-05-01 08:00", "종로구", "2025-05-01 09:00", 20, 10]])
    assert select(frame, request(None)).iloc[0].selection_status == "input_time_unknown"
    assert select(frame, request().iloc[:0]).empty


@pytest.mark.parametrize("kwargs", [{"min_observation_lag": "1h"}, {"min_observation_lag": "3h", "max_observation_age": "2h"}, {"max_observation_age": "NaT"}])
def test_unsafe_time_policies_are_explicit_errors(kwargs):
    frame = snapshots([["2025-05-01 08:00", "종로구", "2025-05-01 09:00", 20, 10]])
    with pytest.raises(ValueError):
        select(frame, **kwargs)
