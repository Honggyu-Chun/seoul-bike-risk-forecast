"""대여소 상세의 시간대별 평균 수량 (GET /api/stations/{station_id}/hourly-average).

최근 7일(오늘 포함 KST 달력 7일) 수집분으로 KST 0~23시마다 평균 수량을 낸다. 새 테이블 없이 observations를
바로 조회하고, AI 상태와 관계없이 동작한다.

계산 규칙
- 완료된 수집분의 quality='valid' 재고만 쓴다(repositories.stations.day_hour_means).
- 날짜·시간별 평균을 먼저 내고 그 평균을 낸다. 수집기가 잠깐 멈춘 날이 있어도 날마다 같은 무게가 되게.
- 평일·주말은 KST 요일로 나눈다(공휴일은 구분하지 않는다).
- 대여소가 100m 넘게 옮겨졌으면 현재 revision의 valid_from 이후만 쓴다. 이름 변경·좌표 미세 보정은 valid_from을
  그대로 두므로(repositories.inventory.revision_valid_from) 이력이 끊기지 않는다.
- 오늘 지금 이후의 시간은 아직 값이 없으므로 그날은 들어간 시간만 반영된다.
"""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import fmean

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import StationNotFound
from app.core.time import KST
from app.repositories import stations as repo
from app.schemas.public import DayType, HourlyAverage, HourlyAverageResponse

PERIOD_DAYS = 7


def day_type_of(day: date) -> DayType:
    return "weekend" if day.weekday() >= 5 else "weekday"


async def hourly_average(session: AsyncSession, now: datetime, station_id: str,
                         day_type: DayType | None) -> HourlyAverageResponse:
    """day_type을 빼면 오늘(KST)의 유형. 운영 중이 아닌 대여소는 404 station_not_found."""
    station = (await repo.stations_by_ids(session, [station_id])).get(station_id)
    if station is None or not repo.is_current(station):
        raise StationNotFound()
    today = now.astimezone(KST).date()
    day_type = day_type or day_type_of(today)
    start = max(datetime.combine(today - timedelta(days=PERIOD_DAYS - 1), time(), KST), station.valid_from)

    by_hour: dict[int, list[repo.DayHourMean]] = defaultdict(list)
    days = set()
    for cell in await repo.day_hour_means(session, station_id, start, now):
        if day_type == "all" or day_type_of(cell.day) == day_type:
            by_hour[cell.hour].append(cell)
            days.add(cell.day)
    hours = [HourlyAverage(hour=h, samples=sum(c.samples for c in by_hour[h]),
                           avg_inventory=round(fmean(c.mean for c in by_hour[h]), 1) if by_hour[h] else None)
             for h in range(24)]
    return HourlyAverageResponse(station_id=station.station_id, name=station.name, day_type=day_type,
                                 period_start=start, period_end=now, days=len(days), hours=hours)
