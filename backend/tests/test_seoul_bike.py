"""따릉이 수집기 테스트 (DB 없음). 응답 모양은 2026-11-06 sample 키로 받은 실제 응답을 따른다."""
import asyncio
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.collectors.seoul_bike import (CollectionError, ProviderStopped, fetch_capture, parse_page, parse_row)

T0 = datetime(2026, 11, 6, 5, 0, tzinfo=timezone.utc)
KEY = "test-key-abc"


def raw(i: int, count="4", lat="37.55564880", lon="126.91062927", station_id=None) -> dict:
    """sample 응답과 같은 모양의 row. 값은 모두 문자열."""
    return {"rackTotCnt": "15", "stationName": f"{100 + i}. 테스트 대여소 {i}", "parkingBikeTotCnt": count,
            "shared": "27", "stationLatitude": lat, "stationLongitude": lon,
            "stationId": station_id or f"ST-{i}"}


def page(rows):
    return {"rentBikeStatus": {"list_total_count": len(rows),
                               "RESULT": {"CODE": "INFO-000", "MESSAGE": "정상 처리되었습니다."}, "row": rows}}


def result(code):
    return {"RESULT": {"CODE": code, "MESSAGE": "..."}}


class FakeSeoul:
    """전체 대여소 목록을 들고 있다가 /{key}/json/bikeList/{a}/{b}/ 요청에 맞는 범위를 준다."""

    def __init__(self, rows, override=None):
        self.rows, self.override, self.paths = rows, override or {}, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        first, last = map(int, re.search(r"/bikeList/(\d+)/(\d+)/", request.url.path).groups())
        if first in self.override:
            return self.override[first]
        chunk = self.rows[first - 1:last]
        return httpx.Response(200, json=page(chunk) if chunk else result("INFO-200"))


def capture(fake: FakeSeoul, **kwargs):
    ticks = iter(T0 + timedelta(seconds=s) for s in range(1000))

    async def go():
        async with httpx.AsyncClient(base_url="http://seoul.test", transport=httpx.MockTransport(fake)) as http:
            async def no_sleep(_):
                return None
            return await fetch_capture(http, KEY, clock=lambda: next(ticks), sleep=no_sleep, **kwargs)
    return asyncio.run(go())


# parse_row

def test_parse_row_strips_trailing_spaces():
    # 실제 응답에 이름 끝 공백이 있는 대여소가 있다 ("946. 독바위역 ")
    assert parse_row(raw(1) | {"stationName": "946. 독바위역 "}, T0).name == "946. 독바위역"


def test_parse_row_converts_strings():
    row = parse_row(raw(4), T0)
    assert (row.station_id, row.name, row.value, row.quality) == ("ST-4", "104. 테스트 대여소 4", 4, "valid")
    assert row.latitude == pytest.approx(37.5556488) and row.verified


@pytest.mark.parametrize("count, value, quality", [
    ("0", 0, "valid"),          # 실제 0대
    ("abc", None, "invalid"),
    ("-1", None, "invalid"),
    ("1.5", None, "invalid"),
    ("nan", None, "invalid"),
    (None, None, "invalid"),
])
def test_parse_row_inventory_value(count, value, quality):
    row = parse_row(raw(1, count=count), T0)
    assert (row.value, row.quality) == (value, quality)


@pytest.mark.parametrize("overrides", [
    {"lat": "0", "lon": "0"}, {"lat": "", "lon": ""}, {"lat": "35.1", "lon": "129.0"},
])
def test_parse_row_bad_coordinates_are_unverified(overrides):
    row = parse_row(raw(1, **overrides), T0)
    assert (row.latitude, row.longitude, row.verified) == (None, None, False)


# parse_page

def test_parse_page_codes():
    assert parse_page(result("INFO-200")) == []
    with pytest.raises(ProviderStopped):
        parse_page(result("INFO-100"))       # 인증키 오류 (최상위 RESULT)
    with pytest.raises(ProviderStopped):
        parse_page(result("ERROR-337"))
    with pytest.raises(CollectionError) as server:
        parse_page(result("ERROR-500"))
    assert not isinstance(server.value, ProviderStopped)


# fetch_capture

def test_capture_reads_all_pages():
    fake = FakeSeoul([raw(i) for i in range(1, 2346)])
    data = capture(fake)
    assert len(data.rows) == 2345 and [p["rows"] for p in data.pages] == [1000, 1000, 345]
    assert all(data.started_at <= row.received_at <= data.completed_at for row in data.rows)
    assert fake.paths[0] == f"/{KEY}/json/bikeList/1/1000/"


def test_capture_full_last_page_ends_with_info_200():
    fake = FakeSeoul([raw(i) for i in range(1, 2001)])
    assert len(capture(fake).rows) == 2000 and len(fake.paths) == 3


@pytest.mark.parametrize("fake, error", [
    (FakeSeoul([]), CollectionError),                                                         # 첫 페이지부터 데이터 없음
    (FakeSeoul([raw(i) for i in range(1, 1500)], {1001: httpx.Response(500)}), httpx.HTTPStatusError),
    (FakeSeoul([raw(i) for i in range(1, 1500)], {1001: httpx.Response(200, json=result("ERROR-500"))}),
     CollectionError),
])
def test_capture_failures_discard_whole_capture(fake, error):
    # 두 번째 페이지에서 실패해도 첫 페이지 결과를 반쪽짜리로 돌려주지 않는다
    with pytest.raises(error):
        capture(fake)


def test_capture_stops_on_quota_error():
    fake = FakeSeoul([raw(1)], {1: httpx.Response(200, json=result("ERROR-337"))})
    with pytest.raises(ProviderStopped):
        capture(fake)
