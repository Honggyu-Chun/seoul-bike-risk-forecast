import numpy as np
import pandas as pd
from bike_demo.followup_v4.public_features import select, BUS_COLUMNS, OD_COLUMNS


def arrays():
    bus = np.arange(2 * 4, dtype=np.float32).reshape(2, 4)
    weekday, hour, station, feature = np.indices((7, 24, 2, 7))
    od = (weekday * 1000 + hour * 10 + station + feature * .01).astype(np.float32)
    return bus, od


def test_target_calendar_rolls_over_midnight_and_preserves_repeated_index_order():
    bus, od = arrays()
    times = pd.DatetimeIndex(['2025-06-01 23:00', '2025-06-02 00:00'])
    columns = [OD_COLUMNS[0], BUS_COLUMNS[2], OD_COLUMNS[3]]
    got = select([3, 0, 3, 1], times, 2, bus, od, columns)
    assert list(got.columns) == columns
    np.testing.assert_array_equal(got.iloc[:, 0], [11, 0, 11, 1])
    np.testing.assert_array_equal(got.iloc[:, 1], [6, 2, 6, 6])
    np.testing.assert_allclose(got.iloc[:, 2], [11.03, .03, 11.03, 1.03], rtol=1e-6)


def test_availability_proxy_uses_input_time_not_future_target_time():
    bus, od = arrays()
    times = pd.DatetimeIndex(['2024-12-08 23:00', '2024-12-09 00:00', '2024-12-11 00:00'])
    got = select([0, 2, 4], times, 2, bus, od, [OD_COLUMNS[0], BUS_COLUMNS[0]])
    assert np.isnan(got.iloc[0]).all()
    assert np.isfinite(got.iloc[1, 0]) and np.isnan(got.iloc[1, 1])
    assert np.isfinite(got.iloc[2]).all()


def test_unlinked_profile_remains_missing_with_explicit_link_mask():
    bus, od = arrays()
    od[:, :, 1, :6] = np.nan
    od[:, :, 1, 6] = 0
    got = select([1], pd.DatetimeIndex(['2025-01-01']), 2, bus, od, OD_COLUMNS)
    assert np.isnan(got.iloc[0, :6]).all()
    assert got.iloc[0, 6] == 0
