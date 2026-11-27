"""GET /api/places 응답. 장소 후보를 좌표와 함께 준다. 프론트는 고른 후보의 좌표로 추천 결과 화면을 부른다."""
from pydantic import BaseModel


class PlaceItem(BaseModel):
    name: str                    # 장소 이름. 주소 검색 결과면 주소
    address: str                 # 지번 주소 ("서울 강남구 역삼동 ...")
    road_address: str | None     # 도로명 주소. 없으면 null
    latitude: float
    longitude: float
    category: str | None         # "교통,수송 > 지하철,전철 > ..." 같은 분류. 주소 검색 결과면 null


class PlacesResponse(BaseModel):
    count: int
    outside_seoul: bool          # 결과가 있었지만 모두 서울 밖이라 뺐으면 true ("서울 안의 장소를 검색하세요")
    items: list[PlaceItem]
