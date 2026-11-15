"""예측 확인 기록 API 요청과 응답 (/api/me/views...). 마이 리포트의 재료다.

기록 요청에는 대여소와 이용 시각만 받는다. 확률은 백엔드가 AI에서 직접 받고, 사용자 위치는 받지 않는다.
"""
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from app.schemas.favorites import StationId
from app.schemas.public import UtcDatetime


class ViewCreate(BaseModel):
    station_id: StationId
    target_at: AwareDatetime   # GET /api/status의 supported_targets 중 하나


class ViewItem(BaseModel):
    """리포트 카드 한 장."""
    id: int
    station_id: str
    station_name: str                                   # 확인한 때의 이름
    target_at: UtcDatetime
    horizon_hours: int                                  # 확인한 때 이용 시각이 몇 시간 뒤였는지(1·2·3)
    risk_probability: float
    is_risk: bool
    outcome: Literal["hit", "miss", "unknown"] | None   # 맞음·틀림·판정 불가. 판정 전이면 null
    actual_inventory: int | None                        # 판정에 쓴 실제 수량. 판정 전·판정 불가면 null
    rented: bool | None                                 # 사용자가 직접 표시한 대여 여부


class ViewRecordResponse(BaseModel):
    """recorded=false면 기록하지 않았다는 뜻이고 reason에 이유가 있다(AI가 꺼짐, 확률 없음 등)."""
    recorded: bool
    reason: str | None = None
    item: ViewItem | None = None
