"""Select already-received observations without future values or future revisions.

This module consumes a saved snapshot cache; it never fetches, imputes, trains, or
invents a provider observation timestamp. Forecasts need a separate valid-time /
issued-time contract and must not be passed to this observation selector.
"""
from collections.abc import Sequence

import numpy as np
import pandas as pd


def _utc(value, naive_timezone):
    """Interpret explicitly configured naive local times, retaining unknowns."""
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            return pd.NaT
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(naive_timezone, ambiguous="raise", nonexistent="raise")
        return stamp.tz_convert("UTC")
    except (TypeError, ValueError, OverflowError):
        return pd.NaT


def tag_archived_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Label historical features whose reception history was not recorded.

    Lagged archive features may be used in a clearly labelled conditional model
    experiment. A lag does not prove that their historical values were received
    before the prediction time. Do not insert a fabricated received_at column.
    """
    result = frame.copy()
    result["reception_status"] = "archived_reception_unknown"
    result["timing_eligible"] = False
    result["operational_eligible"] = False
    return result


def select_observed_inputs(
    snapshots: pd.DataFrame,
    requests: pd.DataFrame,
    value_columns: Sequence[str],
    *,
    min_observation_lag="2h",
    max_observation_age="6h",
    naive_timezone="Asia/Seoul",
) -> pd.DataFrame:
    """Return conservative observations for each (input_time, district) request.

    snapshots: timestamp is the PROVIDER OBSERVATION time, received_at is the
    recorded local reception time, and district identifies the observation area.
    Missing timestamps/reception times remain unknown, including a missing column.
    requests: input_time and district; request order and index are preserved.
    value_columns: numeric nonnegative pollutants, in their documented units;
    optional <value>_qc must equal 0 to permit a value. An absent QC column means
    only local finite/nonnegative validation, never provider-certified quality.

    Pick the newest event in [t-max_age, t-min_lag] received no later than t. For
    the same event pick the last revision received by t. Reject clock-inconsistent
    observations (event later than reception). Conflicting equal-time snapshots
    are rejected, not resolved by file order. Select provenance first, then mask
    unusable values; do not backfill an older value after a QC failure.

    Output times are UTC-aware. timing_eligible means both time gates passed;
    operational_eligible additionally requires at least one usable requested value.
    Each <value>_usable flag determines whether that particular value is usable.
    These flags concern this saved cache/query, not historical live-model accuracy.
    """
    values = list(value_columns)
    if not values or len(values) != len(set(values)):
        raise ValueError("value_columns must be a nonempty unique sequence")
    reserved = {"timestamp", "received_at", "district", "input_time"}
    if reserved.intersection(values):
        raise ValueError("Time and district keys cannot be value columns")
    for name, frame, needed in (("snapshots", snapshots, {"district"}),
                                ("requests", requests, {"input_time", "district"})):
        missing = needed - set(frame)
        if missing:
            raise ValueError(f"{name} is missing required keys: {sorted(missing)}")
    if any(c not in snapshots for c in values):
        raise ValueError("A requested numeric value column is absent from snapshots")
    try:
        lag = pd.Timedelta(min_observation_lag)
        maximum_age = pd.Timedelta(max_observation_age)
    except (TypeError, ValueError) as error:
        raise ValueError("Observation lag and maximum age must be durations") from error
    if pd.isna(lag) or pd.isna(maximum_age) or lag < pd.Timedelta("2h") or maximum_age < lag:
        raise ValueError("Require min_observation_lag >= 2h and max_observation_age >= lag")

    source = snapshots.copy().reset_index(drop=True)
    for key in ("timestamp", "received_at"):
        raw = source[key] if key in source else pd.Series(pd.NaT, index=source.index)
        source["__" + key] = pd.to_datetime([_utc(v, naive_timezone) for v in raw], utc=True)
    # Mask invalid values before duplicate checks, while retaining quality codes.
    for col in values:
        number = pd.to_numeric(source[col], errors="coerce")
        finite = np.isfinite(number.to_numpy(dtype=float, na_value=np.nan))
        raw_text = source[col].astype("string").str.strip().str.lower()
        missing = source[col].isna() | raw_text.isin(["", "nan", "null", "none", "-", "<na>"])
        nonparseable = number.isna() & ~missing
        invalid = ~finite | number.lt(0).fillna(False).to_numpy(dtype=bool)
        qc = pd.Series(np.select([missing.to_numpy(dtype=bool), nonparseable.to_numpy(dtype=bool), invalid],
                                 [1, 3, 2], default=0), index=source.index)
        if col + "_qc" in source:
            supplied = pd.to_numeric(source[col + "_qc"], errors="coerce")
            supplied = supplied.where(supplied.notna() & supplied.mod(1).eq(0), 5)
            qc = supplied.where(qc.eq(0), qc)
        source[col + "_qc"] = qc.astype("int64")
        source[col] = number.where(qc.eq(0) & finite & number.ge(0)).astype(float)
    groups = {key: group.sort_values(["__timestamp", "__received_at"], kind="stable")
              for key, group in source.groupby("district", sort=False, dropna=True)}
    lag_ns, max_ns = lag.value, maximum_age.value
    prepared = {}
    for key, group in groups.items():
        observed = group[group.__timestamp.notna()]
        clocks = observed[observed.__received_at.notna() & (observed.__timestamp <= observed.__received_at)]
        prepared[key] = (group, observed, clocks, clocks.__timestamp.astype("int64").to_numpy())

    result_rows = []
    compare_columns = [c for col in values for c in (col, col + "_qc")]
    for request_time, district in requests[["input_time", "district"]].itertuples(index=False, name=None):
        t = _utc(request_time, naive_timezone)
        row = dict(input_time=t, district=district, source_timestamp=pd.NaT,
                   source_received_at=pd.NaT, source_age_hours=np.nan,
                   timing_eligible=False, operational_eligible=False,
                   reception_status="no_eligible_received_observation", selection_status="missing_district")
        for col in values:
            row.update({col: np.nan, col + "_qc": 1, col + "_usable": False})
        if pd.isna(t):
            row["selection_status"] = "input_time_unknown"
            result_rows.append(row)
            continue
        try:
            data = prepared.get(district)
        except TypeError:
            data = None
        if data is None:
            result_rows.append(row)
            continue
        group, observed, clocks, event_ns = data
        if observed.empty:
            row["selection_status"] = "observation_time_unknown"
        elif observed.__received_at.isna().all():
            row.update(selection_status="archived_reception_unknown", reception_status="archived_reception_unknown")
        elif clocks.empty:
            row["selection_status"] = "invalid_observation_clock"
        else:
            start = np.searchsorted(event_ns, t.value - max_ns, side="left")
            stop = np.searchsorted(event_ns, t.value - lag_ns, side="right")
            candidates = clocks.iloc[start:stop]
            candidates = candidates[candidates.__received_at <= t]
            if candidates.empty:
                known = clocks[clocks.__received_at <= t]
                if known.empty:
                    row["selection_status"] = "not_yet_received"
                elif (known.__timestamp.astype("int64") < t.value - max_ns).all():
                    row["selection_status"] = "stale"
                elif (known.__timestamp.astype("int64") > t.value - lag_ns).all():
                    row["selection_status"] = "observation_too_recent"
                else:
                    row["selection_status"] = "no_observation_in_age_window"
            else:
                newest_event = candidates.__timestamp.max()
                versions = candidates[candidates.__timestamp == newest_event]
                last_reception = versions.__received_at.max()
                tied = versions[versions.__received_at == last_reception]
                if len(tied[compare_columns].drop_duplicates()) != 1:
                    row["selection_status"] = "conflicting_duplicate_snapshot"
                else:
                    selected = tied.iloc[0]
                    row.update(source_timestamp=newest_event, source_received_at=last_reception,
                               source_age_hours=(t.value-newest_event.value)/3_600_000_000_000,
                               timing_eligible=True, reception_status="received_before_input")
                    usable = 0
                    for col in values:
                        ok = bool(selected[col + "_qc"] == 0 and pd.notna(selected[col]))
                        usable += int(ok)
                        row.update({col: selected[col], col + "_qc": int(selected[col + "_qc"]), col + "_usable": ok})
                    row["operational_eligible"] = usable > 0
                    row["selection_status"] = ("selected" if usable == len(values) else
                                               "selected_with_missing_values" if usable else "selected_all_values_unusable")
        result_rows.append(row)
    if not result_rows:
        columns = ["input_time", "district", "source_timestamp", "source_received_at", "source_age_hours",
                   "timing_eligible", "operational_eligible", "reception_status", "selection_status"]
        columns += [c for col in values for c in (col, col + "_qc", col + "_usable")]
        return pd.DataFrame(columns=columns, index=requests.index)
    result = pd.DataFrame(result_rows, index=requests.index)
    for key in ("input_time", "source_timestamp", "source_received_at"):
        result[key] = pd.to_datetime(result[key], utc=True)
    return result
