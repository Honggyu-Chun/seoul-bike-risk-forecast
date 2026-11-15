"""마이 리포트 기록 관리 테스트: "대여함" 표시(PATCH), 지우기(DELETE), 보관 기간 정리(prune_views).

현재 2026-11-04(수) 14:40 KST, 이용 시각 H1 = 15:00 KST. 기본 보관 2주라 10-26(월) 0시 KST(10-25T15:00Z)부터 남는다.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import make_engine, make_sessionmaker
from app.services.views import prune_views
from tests.test_favorites_api import ORIGIN, client, make_client, signup  # noqa: F401 (픽스처)
from tests.test_report_api import at, execute, report
from tests.test_stations_api import H1, H2, NOW
from tests.test_views_api import count_views, db_scalar, record

T1 = datetime(2026, 11, 4, 6, 0, tzinfo=timezone.utc)   # H1


def view_id(client, station_id="ST-3", target_at=H1) -> int:
    return record(client, station_id, target_at).json()["item"]["id"]


def mark(client, vid, rented, headers=ORIGIN):
    return client.patch(f"/api/me/views/{vid}", json={"rented": rented}, headers=headers)


def remove(client, vid, headers=ORIGIN):
    return client.delete(f"/api/me/views/{vid}", headers=headers)


def remove_all(client, headers=ORIGIN):
    return client.delete("/api/me/views", headers=headers)


def test_requires_login(make_client):
    guest = make_client(login=False)
    assert mark(guest, 1, True).status_code == 401
    assert remove(guest, 1).status_code == 401
    assert remove_all(guest).status_code == 401


def test_foreign_origin_is_rejected(client, test_database_url):
    vid = view_id(client)
    at(client, T1)
    evil = {"Origin": "https://evil.example"}
    for response in (mark(client, vid, True, headers=evil), remove(client, vid, headers=evil),
                     remove_all(client, headers=evil)):
        assert response.status_code == 403 and response.json()["error"]["code"] == "origin_not_allowed"
    assert count_views(test_database_url) == 1


def test_rented_only_after_target(client):
    vid = view_id(client)
    response = mark(client, vid, True)            # 14:40, 이용 시각 전
    assert response.status_code == 409 and response.json()["error"]["code"] == "target_not_passed"
    assert mark(client, vid, None).status_code == 200   # 표시 지우기는 언제든

    at(client, T1)                                  # 이용 시각 정각부터
    response = mark(client, vid, True)
    assert response.status_code == 200
    assert response.json()["rented"] is True and response.json()["id"] == vid
    assert report(client).json()["summary"]["rented"] == 1

    assert mark(client, vid, False).json()["rented"] is False
    assert report(client).json()["summary"]["rented"] == 0
    assert mark(client, vid, None).json()["rented"] is None
    assert report(client).json()["items"][0]["rented"] is None


@pytest.mark.parametrize("body", [{}, {"rented": "yes"}, {"rented": 1}])
def test_rented_body_is_validated(client, body):
    vid = view_id(client)
    at(client, T1)
    response = client.patch(f"/api/me/views/{vid}", json=body, headers=ORIGIN)
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["rented"]


@pytest.mark.parametrize("vid", ["0", "-1", str(2**63), "abc"])
def test_view_id_is_validated(client, vid):
    assert mark(client, vid, None).status_code == 422
    assert remove(client, vid).status_code == 422


def test_other_members_record_is_untouchable(client, test_database_url):
    vid = view_id(client)
    client.cookies.clear()
    signup(client, "rider_02")
    at(client, T1)
    response = mark(client, vid, True)
    assert response.status_code == 404 and response.json()["error"]["code"] == "view_not_found"
    assert mark(client, 999_999, True).status_code == 404
    assert remove(client, vid).status_code == 204          # 남의 기록은 지워지지 않지만 있는지 알려 주지 않는다
    assert remove_all(client).status_code == 204
    assert db_scalar(test_database_url, f"SELECT rented IS NULL FROM prediction_views WHERE id = {vid}") is True
    assert count_views(test_database_url) == 1


def test_delete_one(client):
    first, second = view_id(client, "ST-3"), view_id(client, "ST-2")
    assert remove(client, first).status_code == 204
    assert [i["id"] for i in report(client).json()["items"]] == [second]
    assert remove(client, first).status_code == 204         # 이미 없어도 204


def test_delete_all_leaves_other_members(client, test_database_url):
    view_id(client, "ST-3")
    view_id(client, "ST-2", H2)
    client.cookies.clear()
    signup(client, "rider_02")
    view_id(client, "ST-3")
    assert remove_all(client).status_code == 204
    assert report(client).json()["summary"]["viewed"] == 0 and report(client).json()["items"] == []
    assert count_views(test_database_url) == 2                # 첫 회원의 기록은 남는다


def test_prune_removes_records_before_oldest_week(client, test_database_url):
    view_id(client)   # H1, 이번 주
    before_week, week_start = datetime(2026, 10, 25, 14, tzinfo=timezone.utc), datetime(2026, 10, 25, 15, tzinfo=timezone.utc)
    for target in (before_week, week_start):    # 10-25(일) 23시, 10-26(월) 0시 KST
        execute(test_database_url,
                "INSERT INTO prediction_views (user_id, station_id, station_name, base_at, target_at, viewed_at, "
                "risk_probability, is_risk) SELECT user_id, 'ST-2', 'ST-2 대여소', :base, :target, :base, 0.1, false "
                "FROM prediction_views LIMIT 1", base=target - timedelta(hours=1), target=target)

    async def go(weeks):
        engine = make_engine(test_database_url)
        try:
            async with make_sessionmaker(engine)() as session:
                return await prune_views(session, NOW, weeks)
        finally:
            await engine.dispose()

    assert asyncio.run(go(2)) == 1             # 지난 주(10-26~)까지 남긴다
    assert [i["target_at"] for i in report(client, week="2026-10-26").json()["items"]] == ["2026-10-25T15:00:00Z"]
    assert asyncio.run(go(2)) == 0
    assert asyncio.run(go(1)) == 1             # 보관 1주면 이번 주(11-02~)만
    assert [i["target_at"] for i in report(client).json()["items"]] == [H1]
