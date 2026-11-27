from fastapi import APIRouter, Depends, Query, Request

from app.clients.kakao import KakaoLocalClient
from app.core.errors import InvalidRequest
from app.schemas.places import PlacesResponse, ReverseResponse
from app.services import places as service

router = APIRouter(tags=["places"])


def get_kakao(request: Request) -> KakaoLocalClient:
    return request.app.state.kakao


@router.get("/places", response_model=PlacesResponse)
async def get_places(query: str = Query(max_length=50, description="장소 이름 또는 주소. 예: 가천대학교, 강남대로 396"),
                     kakao: KakaoLocalClient = Depends(get_kakao)):
    """장소·주소 검색 → 좌표. 서울 안의 후보 최대 5개. 카카오 장애·키 없음이면 503 place_search_unavailable.

    고른 후보의 좌표로 POST /api/recommendations(추천 결과 화면)를 부른다. 검색어는 저장·로그하지 않는다.
    """
    query = query.strip()
    if not query:
        raise InvalidRequest(["query"])
    return await service.search(kakao, query)


@router.get("/places/reverse", response_model=ReverseResponse)
async def get_place_reverse(lat: float = Query(ge=33, le=39), lon: float = Query(ge=124, le=132),
                            kakao: KakaoLocalClient = Depends(get_kakao)):
    """좌표 → 주소(지번·도로명)와 서울 안인지. 추천 결과 화면의 "현재 위치: ..." 문구용.

    주소가 없는 곳(한강 위 등)은 200에 두 주소 모두 null. 카카오 장애·키 없음이면 503 place_search_unavailable.
    좌표는 사용자 위치라 저장·로그하지 않는다. 프론트는 현재 위치를 정하거나 추천 결과를 열 때 한 번만 부른다.
    """
    return await service.reverse(kakao, lat, lon)
