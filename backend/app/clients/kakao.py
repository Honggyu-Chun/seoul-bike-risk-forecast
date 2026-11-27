"""카카오 로컬 API(키워드 장소 검색, 주소 검색) 호출.

REST API 키는 서버에만 둔다. 브라우저가 직접 부르면 키가 노출되므로 백엔드가 중계한다.
검색어는 사용자의 위치 정보일 수 있어("우리 집 주소") 저장하지도, 로그에 남기지도 않는다.
로그에는 경로와 상태 코드만 남긴다.

결과 → 이 모듈이 내는 것
- 200 + documents 목록        → 문서(dict) 목록
- 키 없음·연결 실패·200 아님·모양 다름 → PlaceSearchUnavailable(503 place_search_unavailable)
"""
import logging

import httpx

from app.core.config import Settings
from app.core.errors import PlaceSearchUnavailable

logger = logging.getLogger(__name__)

KEYWORD_PATH = "/v2/local/search/keyword.json"
ADDRESS_PATH = "/v2/local/search/address.json"


class KakaoLocalClient:
    """앱이 뜰 때 하나 만들어 app.state.kakao에 두고 같이 쓴다. transport는 테스트의 가짜 카카오용."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._has_key = bool(settings.kakao_rest_api_key.get_secret_value())
        self._client = httpx.AsyncClient(
            base_url=settings.kakao_base_url, timeout=settings.kakao_timeout_seconds, transport=transport,
            headers={"Authorization": f"KakaoAK {settings.kakao_rest_api_key.get_secret_value()}"})

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _documents(self, path: str, params: dict) -> list[dict]:
        if not self._has_key:
            logger.error("place search rejected: KAKAO_REST_API_KEY is not set")
            raise PlaceSearchUnavailable()
        try:
            response = await self._client.get(path, params=params)
        except httpx.TransportError as exc:
            logger.warning("kakao local %s unreachable: %s", path, type(exc).__name__)
            raise PlaceSearchUnavailable() from None
        if response.status_code != 200:
            # 401·403이면 키 문제, 429면 하루 호출 한도 초과
            logger.error("kakao local %s returned %s", path, response.status_code)
            raise PlaceSearchUnavailable()
        try:
            documents = response.json()["documents"]
            if not isinstance(documents, list):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            logger.error("kakao local %s returned an unexpected body", path)
            raise PlaceSearchUnavailable() from None
        return [d for d in documents if isinstance(d, dict)]

    async def keyword(self, query: str, size: int = 15) -> list[dict]:
        """장소 이름으로 검색("가천대학교", "강남역"). 정확도순."""
        return await self._documents(KEYWORD_PATH, {"query": query, "size": size})

    async def address(self, query: str, size: int = 10) -> list[dict]:
        """주소로 검색("강남대로 396"). 키워드 검색에 결과가 없을 때 쓴다."""
        return await self._documents(ADDRESS_PATH, {"query": query, "size": size})
