"""장소·주소 검색. 키워드로 먼저 찾고, 없으면 주소로 찾는다. 서울 안의 결과만 최대 5개."""
from app.clients.kakao import KakaoLocalClient
from app.schemas.places import PlaceItem, PlacesResponse

MAX_RESULTS = 5
# 서울 범위(대략). 주소가 "서울"로 시작하고 좌표도 이 안이어야 한다.
SEOUL_LAT = (37.40, 37.72)
SEOUL_LON = (126.76, 127.19)


def _coords(document: dict) -> tuple[float, float] | None:
    """카카오는 x(경도)·y(위도)를 문자열로 준다. 숫자가 아니면 그 결과는 버린다."""
    try:
        return float(document["y"]), float(document["x"])
    except (KeyError, TypeError, ValueError):
        return None


def _from_keyword(document: dict) -> PlaceItem | None:
    coords = _coords(document)
    if coords is None or not document.get("place_name") or not document.get("address_name"):
        return None
    return PlaceItem(name=document["place_name"], address=document["address_name"],
                     road_address=document.get("road_address_name") or None, latitude=coords[0],
                     longitude=coords[1], category=document.get("category_name") or None)


def _from_address(document: dict) -> PlaceItem | None:
    coords = _coords(document)
    if coords is None or not document.get("address_name"):
        return None
    road = document.get("road_address") or {}
    jibun = document.get("address") or {}
    address = jibun.get("address_name") or document["address_name"]
    return PlaceItem(name=document["address_name"], address=address,
                     road_address=road.get("address_name") or None, latitude=coords[0], longitude=coords[1],
                     category=None)


def _in_seoul(place: PlaceItem) -> bool:
    return (place.address.startswith("서울") and SEOUL_LAT[0] <= place.latitude <= SEOUL_LAT[1]
            and SEOUL_LON[0] <= place.longitude <= SEOUL_LON[1])


async def search(client: KakaoLocalClient, query: str) -> PlacesResponse:
    """키워드 검색에 결과가 하나도 없을 때만 주소 검색을 한다(호출 수 절약)."""
    documents = await client.keyword(query)
    places = [p for p in map(_from_keyword, documents) if p]
    if not documents:
        places = [p for p in map(_from_address, await client.address(query)) if p]
    seoul = [p for p in places if _in_seoul(p)]
    return PlacesResponse(count=min(len(seoul), MAX_RESULTS), outside_seoul=bool(places) and not seoul,
                          items=seoul[:MAX_RESULTS])
