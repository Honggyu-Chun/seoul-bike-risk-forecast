"""대여소 공개 API 로직: DB의 대여소 정보(이름·좌표)·수량과 AI 예측(확률)을 합친다.

수량 출처는 AI가 켜져 있든 꺼져 있든 항상 백엔드 DB의 가장 최근 완료 수집분이다.
모델(연구실 API)은 재고를 따로 수집하므로 수신 시각이 우리 DB와 조금씩 다르다. DB 값을 쓰면 지도(/api/stations)와
출처가 같고, 모델이 모르는 대여소도 수량을 보여 줄 수 있다. AI에서는 확률·위험 판정·보류 이유만 받는다.
(추천 결과만 예외로 모델 값을 쓴다. 추천 판단에 쓴 값이기 때문이다. services/ai_relay.py)

AI가 꺼져 있을 때(AIUnavailable)
- 오류가 아니라 200 + ai_available=false
- 확률·위험 판정은 null, reason="ai_unavailable"
AI 401·5xx는 잡지 않는다(502로 드러나게).
"""
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.ai import AIClient
from app.core.config import Settings
from app.core.errors import AIUnavailable, InvalidRequest
from app.core.geo import distance_m
from app.core.time import HORIZONS, base_at, horizon_for
from app.repositories import stations as repo
from app.schemas.ai import AIPrediction
from app.schemas.public import (NearbyItem, NearbyResponse, PredictionDebug, PredictionItem, PredictionRequest,
                                PredictionsResponse, StationItem, StationsResponse)
from app.services.ai_relay import check_same_base

FRESH_SECONDS = 300  # 추천 자격과 같은 재고 신선도 기준
AI_UNAVAILABLE = "ai_unavailable"


def _sort_key(station_id: str) -> tuple[int, str]:
    """ST-2가 ST-10보다 앞에 오도록 (길이, 문자열) 순서."""
    return len(station_id), station_id


def _fresh(received_at: datetime | None, now: datetime) -> bool:
    """수량이 300초 안의 값인지. AI는 나이가 아무리 커도 재고를 주므로 백엔드가 판정한다."""
    return received_at is not None and 0 <= (now - received_at).total_seconds() < FRESH_SECONDS


async def _predict(ai: AIClient, settings: Settings, station_ids: list[str], horizons: list[int],
                   base: datetime) -> dict[tuple[str, int], AIPrediction] | None:
    """AI 예측을 (대여소, 간격)별로 돌려준다. AI 접속 실패면 None.

    응답 항목의 target_at - 간격으로 AI의 기준 정각을 구해 백엔드 기준 정각과 비교한다(정각 경계 409).
    """
    if not station_ids:
        return {}
    try:
        items = await ai.predict(station_ids, horizons)
    except AIUnavailable:
        return None
    for ai_base in {item.target_at - timedelta(hours=item.horizon_hours) for item in items}:
        check_same_base(ai_base, base, settings)
    return {(item.station_id, item.horizon_hours): item for item in items}


def _prediction_fields(prediction: AIPrediction | None, ai_available: bool, inventory: repo.Inventory | None,
                       now: datetime, debug: bool) -> dict:
    """예측·재고 관련 공통 필드. 수량은 DB(inventory), 확률은 AI(prediction).

    ai_available=False면 AI 접속 실패라 reason="ai_unavailable".
    """
    received = inventory.received_at if inventory else None
    fields = {"latest_inventory": inventory.value if inventory else None,
              "latest_inventory_received_at": received, "inventory_fresh": _fresh(received, now),
              "risk_probability": prediction.risk_probability if prediction else None,
              "available_probability": prediction.available_probability if prediction else None,
              "is_risk": prediction.is_risk if prediction else None,
              "reason": prediction.reason if prediction else (None if ai_available else AI_UNAVAILABLE)}
    if debug:
        age = (now - received).total_seconds() if received else None
        fields["debug"] = PredictionDebug(model_id=prediction.model_id if prediction else None,
                                          input_group=prediction.input_group if prediction else None,
                                          latest_inventory_age_seconds=age)
    return fields


def _station_item(station, distance: float | None, inventory: repo.Inventory | None,
                  now: datetime) -> StationItem:
    received = inventory.received_at if inventory else None
    return StationItem(station_id=station.station_id, name=station.name, latitude=station.latitude,
                       longitude=station.longitude, distance_m=distance,
                       latest_inventory=inventory.value if inventory else None,
                       latest_inventory_received_at=received, inventory_fresh=_fresh(received, now))


async def list_stations(session: AsyncSession, now: datetime, lat: float | None, lon: float | None,
                        radius_m: float | None) -> StationsResponse:
    """GET /api/stations. 지도 마커용 현재 운영 대여소와 지금 수량(마커 색). AI를 부르지 않는다.

    수량은 DB의 가장 최근 완료 수집분이다. 캐시 헤더 없이 매번 새로 준다(응답은 gzip 압축).

    lat·lon을 주면 거리를 계산해 가까운 순으로, radius_m까지 주면 반경 안만. 안 주면 ID 순.
    lat·lon 중 하나만 주거나, 위치 없이 radius_m만 주면 422.
    """
    if (lat is None) != (lon is None):
        raise InvalidRequest(["lon" if lon is None else "lat"])
    if radius_m is not None and lat is None:
        raise InvalidRequest(["lat", "lon"])
    stations = await repo.current_stations(session)
    inventory = await repo.latest_inventory(session, None)
    if lat is None:
        items = [_station_item(s, None, inventory.get(s.station_id), now)
                 for s in sorted(stations, key=lambda s: _sort_key(s.station_id))]
    else:
        measured = [(distance_m(lat, lon, s.latitude, s.longitude), s) for s in stations]
        items = [_station_item(s, d, inventory.get(s.station_id), now)
                 for d, s in sorted(measured, key=lambda pair: (pair[0], _sort_key(pair[1].station_id)))
                 if radius_m is None or d <= radius_m]
    return StationsResponse(count=len(items), items=items)


async def nearby(session: AsyncSession, ai: AIClient, settings: Settings, now: datetime, *, lat: float,
                 lon: float, target_at: datetime, radius_m: float, debug: bool) -> NearbyResponse:
    """GET /api/stations/nearby. 메인 화면.

    1. target_at → 간격(h). 지원하지 않으면 409 (DB·AI 조회 전에)
    2. DB에서 반경 안 대여소를 직선거리로 고름
    3. AI 예측(해당 h 하나)을 500개씩 나눠 받음. 수량은 DB 최신 수집분
    4. 이름·좌표·거리와 합쳐 거리순 정렬
    반경 안에 대여소가 없으면 AI를 부르지 않고 count=0 (ai_available은 true로 둔다)
    """
    horizon = horizon_for(target_at, now)
    base = base_at(now)
    measured = sorted(((distance_m(lat, lon, s.latitude, s.longitude), s)
                       for s in await repo.current_stations(session)),
                      key=lambda pair: (pair[0], _sort_key(pair[1].station_id)))
    inside = [(d, s) for d, s in measured if d <= radius_m]
    ids = [s.station_id for _, s in inside]

    predictions = await _predict(ai, settings, ids, [horizon], base)
    inventory = await repo.latest_inventory(session, ids)
    available = predictions is not None
    items = [NearbyItem(station_id=s.station_id, name=s.name, latitude=s.latitude, longitude=s.longitude,
                        distance_m=d,
                        **_prediction_fields((predictions or {}).get((s.station_id, horizon)), available,
                                             inventory.get(s.station_id), now, debug))
             for d, s in inside]
    return NearbyResponse(base_at=base, target_at=target_at, radius_m=radius_m,
                          ai_available=predictions is not None, count=len(items), items=items)


async def predict(session: AsyncSession, ai: AIClient, settings: Settings, now: datetime,
                  request: PredictionRequest, debug: bool) -> PredictionsResponse:
    """POST /api/predictions. 즐겨찾기·상세 화면용.

    - target_at 하나 또는 all_targets=true(세 이용 시각 모두) 중 하나만. 둘 다거나 둘 다 없으면 422
    - 중복 ID는 한 번만. 순서는 요청한 대여소 순서 × 이용 시각 순서
    - DB에 없는 ID는 이름·좌표 null, AI의 station_unknown_or_ineligible을 그대로 둔다
    """
    if (request.target_at is None) != request.all_targets:
        raise InvalidRequest(["target_at", "all_targets"])
    horizons = list(HORIZONS) if request.all_targets else [horizon_for(request.target_at, now)]
    base = base_at(now)
    ids = list(dict.fromkeys(request.station_ids))
    known = await repo.stations_by_ids(session, ids)

    predictions = await _predict(ai, settings, ids, horizons, base)
    inventory = await repo.latest_inventory(session, ids)
    available = predictions is not None
    items = []
    for sid in ids:
        station = known.get(sid)
        for h in horizons:
            items.append(PredictionItem(
                station_id=sid, target_at=base + timedelta(hours=h),
                name=station.name if station else None,
                latitude=station.latitude if station else None, longitude=station.longitude if station else None,
                **_prediction_fields((predictions or {}).get((sid, h)), available, inventory.get(sid), now, debug)))
    return PredictionsResponse(base_at=base, ai_available=predictions is not None, items=items)
