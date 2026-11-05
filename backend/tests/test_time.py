from datetime import datetime, timezone

import pytest

from app.core.errors import TargetNotSupported
from app.core.time import KST, base_at, horizon_for, iso_utc, supported_targets


def kst(*args):
    return datetime(*args, tzinfo=KST)


def test_base_is_kst_hour_floor():
    assert base_at(kst(2026, 11, 4, 14, 40)) == datetime(2026, 11, 4, 5, tzinfo=timezone.utc)


def test_supported_targets_are_next_three_hours():
    targets = supported_targets(base_at(kst(2026, 11, 4, 14, 40)))
    assert [t.astimezone(KST).hour for t in targets] == [15, 16, 17]


def test_targets_cross_midnight():
    targets = supported_targets(base_at(kst(2026, 11, 4, 23, 30)))
    assert [(t.astimezone(KST).day, t.astimezone(KST).hour) for t in targets] == [(5, 0), (5, 1), (5, 2)]


@pytest.mark.parametrize("target", [
    datetime(2026, 11, 4, 6, tzinfo=timezone.utc),  # Z
    kst(2026, 11, 4, 15),                            # +09:00
])
def test_offset_notation_does_not_matter(target):
    assert horizon_for(target, kst(2026, 11, 4, 14, 40)) == 1


def test_target_expires_after_hour_changes():
    with pytest.raises(TargetNotSupported) as raised:
        horizon_for(kst(2026, 11, 4, 15), kst(2026, 11, 4, 15, 0, 5))
    assert raised.value.status_code == 409
    assert raised.value.extra["supported_targets"][0] == "2026-11-04T07:00:00Z"


@pytest.mark.parametrize("target", [kst(2026, 11, 4, 14), kst(2026, 11, 4, 18), kst(2026, 11, 4, 15, 30)])
def test_unsupported_targets(target):
    with pytest.raises(TargetNotSupported):
        horizon_for(target, kst(2026, 11, 4, 14, 40))


def test_naive_time_is_rejected():
    with pytest.raises(ValueError):
        base_at(datetime(2026, 11, 4, 14, 40))


def test_iso_utc_uses_z():
    assert iso_utc(kst(2026, 11, 4, 15)) == "2026-11-04T06:00:00Z"
