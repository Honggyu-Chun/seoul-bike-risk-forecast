"""장소·주소 검색 API 테스트 (GET /api/places). 카카오 로컬 API는 가짜(httpx.MockTransport)를 쓴다."""
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.clients.kakao import KakaoLocalClient
from app.core.config import Settings
from app.core.log_filters import HideQueryString
from app.main import create_app


def place(name, address, lat, lon, road=None, category="교통,수송 > 지하철,전철"):
    return {"place_name": name, "address_name": address, "road_address_name": road or "", "y": str(lat),
            "x": str(lon), "category_name": category, "id": "1"}


GANGNAM = place("강남역 2호선", "서울 강남구 역삼동 858", 37.4979, 127.0276, road="서울 강남구 강남대로 지하 396")
PANGYO = place("판교역 신분당선", "경기 성남시 분당구 백현동 537", 37.3948, 127.1111)


class FakeKakao:
    """경로별로 정해 둔 documents를 준다. status·error로 장애를 흉내 낸다."""

    def __init__(self, keyword=(), address=(), status=200, error=None, body=None):
        self.documents = {"/v2/local/search/keyword.json": list(keyword), "/v2/local/search/address.json": list(address)}
        self.status, self.error, self.body, self.requests = status, error, body, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        if self.body is not None:
            return httpx.Response(self.status, content=self.body)
        return httpx.Response(self.status, json={"meta": {}, "documents": self.documents[request.url.path]})


def make_client(fake, key="test-key"):
    settings = Settings(_env_file=None, KAKAO_REST_API_KEY=key)
    app = create_app(settings, kakao_client=KakaoLocalClient(settings, transport=httpx.MockTransport(fake)))
    return TestClient(app, raise_server_exceptions=False)


def search(client, query):
    return client.get("/api/places", params={"query": query})


def test_keyword_results_in_seoul_only():
    fake = FakeKakao(keyword=[GANGNAM, PANGYO])
    body = search(make_client(fake), "  강남역 ").json()
    assert body == {"count": 1, "outside_seoul": False, "items": [{
        "name": "강남역 2호선", "address": "서울 강남구 역삼동 858", "road_address": "서울 강남구 강남대로 지하 396",
        "latitude": 37.4979, "longitude": 127.0276, "category": "교통,수송 > 지하철,전철"}]}
    request = fake.requests[0]
    assert request.headers["authorization"] == "KakaoAK test-key"
    assert request.url.params["query"] == "강남역" and len(fake.requests) == 1   # 결과가 있으면 주소 검색 안 함


def test_at_most_five_results():
    many = [place(f"카페 {i}", "서울 중구 명동 1", 37.56, 126.98) for i in range(9)]
    body = search(make_client(FakeKakao(keyword=many)), "카페").json()
    assert body["count"] == 5 and len(body["items"]) == 5


def test_falls_back_to_address_search():
    address_doc = {"address_name": "서울 강남구 강남대로 396", "x": "127.0276", "y": "37.4979",
                   "address": {"address_name": "서울 강남구 역삼동 858"},
                   "road_address": {"address_name": "서울 강남구 강남대로 396"}}
    fake = FakeKakao(keyword=[], address=[address_doc])
    item = search(make_client(fake), "강남대로 396").json()["items"][0]
    assert item == {"name": "서울 강남구 강남대로 396", "address": "서울 강남구 역삼동 858",
                    "road_address": "서울 강남구 강남대로 396", "latitude": 37.4979, "longitude": 127.0276,
                    "category": None}
    assert [r.url.path for r in fake.requests] == ["/v2/local/search/keyword.json", "/v2/local/search/address.json"]


def test_only_outside_seoul():
    body = search(make_client(FakeKakao(keyword=[PANGYO])), "판교역").json()
    assert body == {"count": 0, "outside_seoul": True, "items": []}


def test_no_results():
    body = search(make_client(FakeKakao()), "없는장소이름").json()
    assert body == {"count": 0, "outside_seoul": False, "items": []}


def test_bad_coordinates_are_skipped():
    broken = {**GANGNAM, "x": "not-a-number"}
    assert search(make_client(FakeKakao(keyword=[broken, GANGNAM])), "강남역").json()["count"] == 1


@pytest.mark.parametrize("query", ["   ", "가" * 51])
def test_invalid_query(query):
    response = search(make_client(FakeKakao()), query)
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["query"]


def test_missing_query():
    response = make_client(FakeKakao()).get("/api/places")
    assert response.status_code == 422 and response.json()["error"]["fields"] == ["query"]


@pytest.mark.parametrize("fake", [
    FakeKakao(status=401), FakeKakao(status=429), FakeKakao(status=500),
    FakeKakao(error=httpx.ConnectTimeout("timeout")), FakeKakao(body=b"not json"), FakeKakao(body=b'{"meta": {}}'),
])
def test_kakao_failures_are_503_without_logging_query(fake, caplog):
    with caplog.at_level(logging.DEBUG):
        response = search(make_client(fake), "우리집주소")
    assert response.status_code == 503 and response.json()["error"]["code"] == "place_search_unavailable"
    assert "우리집주소" not in caplog.text and "test-key" not in caplog.text


def test_missing_key_is_503_without_calling_kakao():
    fake = FakeKakao(keyword=[GANGNAM])
    response = search(make_client(fake, key=""), "강남역")
    assert response.status_code == 503 and fake.requests == []


# 접속 로그에서 쿼리(위치·검색어) 지우기

def test_access_log_hides_query_string():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1:5000", "GET", "/api/places?query=우리집", "1.1", 200), None)
    HideQueryString().filter(record)
    assert record.getMessage() == '127.0.0.1:5000 - "GET /api/places HTTP/1.1" 200'


def test_privacy_filter_installed_once():
    make_client(FakeKakao())
    make_client(FakeKakao())
    filters = [f for f in logging.getLogger("uvicorn.access").filters if isinstance(f, HideQueryString)]
    assert len(filters) == 1
    assert logging.getLogger("httpx").level == logging.WARNING


# 응답 압축

def test_large_responses_are_gzipped():
    client = make_client(FakeKakao())
    big = client.get("/openapi.json", headers={"Accept-Encoding": "gzip"})
    small = client.get("/health", headers={"Accept-Encoding": "gzip"})
    assert big.headers.get("content-encoding") == "gzip" and big.json()["info"]["title"] == "CycleOn backend"
    assert "content-encoding" not in small.headers
