"""즐겨찾기 API 요청과 응답 (/api/me/favorites...).

목록 항목은 /api/predictions 항목과 같은 모양에 alias·position이 더해진다.
target_at 없이 부르면 예측 없이 목록만 주고, 이때 예측 필드(target_at, latest_inventory 등)는 응답에서 빠진다.
"""
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.schemas.public import PredictionDebug, UtcDatetime

FAVORITES_LIMIT = 20
ALIAS_MAX = 20

StationId = Annotated[str, Field(min_length=1, max_length=80)]


def _alias(value: str | None) -> str | None:
    """앞뒤 공백 제거, 빈 문자열은 별칭 없음(null)"""
    if value is None:
        return None
    value = value.strip()
    if len(value) > ALIAS_MAX:
        raise ValueError(f"{ALIAS_MAX}자 이하")
    return value or None


class FavoriteAliasUpdate(BaseModel):
    alias: str | None = None

    @field_validator("alias")
    @classmethod
    def _check_alias(cls, value: str | None) -> str | None:
        return _alias(value)


class FavoriteCreate(FavoriteAliasUpdate):
    station_id: StationId


class FavoriteOrder(BaseModel):
    """지금 즐겨찾기 ID 전체를 원하는 순서대로. 빠지거나 더해진 ID가 있으면 422."""
    station_ids: list[StationId] = Field(max_length=FAVORITES_LIMIT)


class FavoriteImport(BaseModel):
    """게스트(브라우저)에 있던 즐겨찾기 ID 목록. 앞에서부터 차례로 넣는다."""
    station_ids: list[StationId] = Field(max_length=100)


class FavoriteItem(BaseModel):
    station_id: str
    alias: str | None
    position: int
    name: str | None               # 폐소 등으로 DB에서 찾을 수 없으면 null
    latitude: float | None
    longitude: float | None
    # 아래는 target_at을 줬을 때만 응답에 들어간다(exclude_unset)
    target_at: UtcDatetime | None = None
    latest_inventory: int | None = None
    latest_inventory_received_at: UtcDatetime | None = None
    inventory_fresh: bool | None = None
    risk_probability: float | None = None
    is_risk: bool | None = None
    reason: str | None = None
    debug: PredictionDebug | None = None


class FavoritesResponse(BaseModel):
    base_at: UtcDatetime | None = None    # target_at을 줬을 때만
    ai_available: bool | None = None      # target_at을 줬을 때만
    count: int
    limit: int                            # 한 사람당 최대 개수(20)
    items: list[FavoriteItem]


class FavoriteImportResponse(BaseModel):
    """옮긴 결과. 이미 있던 것, 운영 중이 아닌 것, 20개를 넘는 것은 건너뛰고 ID를 알려 준다."""
    added: list[str]
    skipped_existing: list[str]
    skipped_unknown: list[str]
    skipped_over_limit: list[str]
    count: int
