"""예측 기준 정각과 이용 시각(target_at) 처리.

기준 정각 = 현재 시각을 KST 정각으로 내린 값. AI 서비스와 같은 규칙이다.
target_at은 문자열이 아니라 시각 값으로 비교한다(+09:00과 Z 표기를 같게 처리).
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.errors import TargetNotSupported

KST = ZoneInfo("Asia/Seoul")
HORIZONS = (1, 2, 3)


def utc_now() -> datetime:
    """현재 UTC 시각"""
    return datetime.now(timezone.utc)


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone offset is required")
    return value


def base_at(now: datetime) -> datetime:
    """현재 시각을 KST 정각으로 내림"""
    local = _require_aware(now).astimezone(KST)
    return local.replace(minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def supported_targets(base: datetime) -> list[datetime]:
    """기준 정각 +1, 2, 3시간 리스트 반환"""
    return [base + timedelta(hours=h) for h in HORIZONS]


def horizon_for(target_at: datetime, now: datetime) -> int:
    """target_at을 현재 기준 정각 대비 간격(1~3시간)으로 바꾼다. 아니면 409."""
    base = base_at(now)
    seconds = (_require_aware(target_at) - base).total_seconds()
    hours, remainder = divmod(seconds, 3600)
    if remainder or int(hours) not in HORIZONS:
        raise TargetNotSupported(supported_targets(base))
    return int(hours)


def iso_utc(value: datetime) -> str:
    """시각을 문자열 형태로 반환"""
    return _require_aware(value).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
