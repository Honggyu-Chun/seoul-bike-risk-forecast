"""따릉이 실시간 재고 수집기 (서울 열린데이터광장 bikeList). API 서버와 별도 프로세스로 돈다.

    cd backend
    .\\.venv\\Scripts\\python -m app.collectors.seoul_bike          # 2분마다 계속
    .\\.venv\\Scripts\\python -m app.collectors.seoul_bike --once   # 한 번만

수집 1회(capture)는 서울 전체를 끝 페이지까지 다 받은 경우에만 저장한다. 중간에 실패하면 그 회차 전체를 버린다.

응답 형식 (값이 모두 문자열)
    {"rentBikeStatus": {"list_total_count": 3, "RESULT": {"CODE": "INFO-000", ...},
     "row": [{"stationId": "ST-4", "stationName": "102. 망원역 1번출구 앞", "parkingBikeTotCnt": "4",
              "stationLatitude": "37.55564880", "stationLongitude": "126.91062927", "rackTotCnt": "15", "shared": "27"}]}}
- list_total_count는 서울 전체가 아니라 '이번 페이지' 개수다. 끝은 1,000건 미만 페이지 또는 INFO-200으로 판단한다.
- 관측 시각이 없어서 응답을 받은 시각을 received_at으로 쓴다.

응답 형식 자체가 깨진 경우는 따로 검사하지 않는다(KeyError 등으로 그 회차가 버려진다).
여기서 확인하는 것은 정상 응답을 처리하는 데 필요한 것과 DB 제약을 지키는 데 필요한 것뿐이다.

인증키가 URL 경로에 들어가므로 URL과 예외 메시지는 로그에 남기지 않는다.
"""
import argparse
import asyncio
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx

from app.core.config import Settings, get_settings
from app.core.time import utc_now

logger = logging.getLogger("app.collectors.seoul_bike")

PAGE_SIZE = 1000            # API 1회 최대 건수
MAX_PAGES = 20              # 끝 신호가 오지 않을 때를 위한 반복 상한 (현재 3페이지)
MIN_REQUEST_INTERVAL = 1.1  # 요청 사이 간격(초). 서울시 API에 부담을 주지 않도록


class CollectionError(Exception):
    """이번 회차만 버리고 다음 회차에 다시 시도할 오류 (서버 오류 코드 등)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ProviderStopped(CollectionError):
    """인증키 오류·요청 한도 초과. 다시 시도해도 같으므로 수집기를 멈춘다(한도를 계속 두드리지 않는다)."""


@dataclass(frozen=True)
class BikeRow:
    """대여소 1곳의 수집 결과."""
    station_id: str
    name: str
    latitude: float | None     # 서울 범위 밖이면 None (DB 제약을 지키기 위해)
    longitude: float | None
    verified: bool             # 좌표가 서울 범위 안이면 True. AI는 verified 대여소만 예측에 쓴다
    value: int | None          # 거치 자전거 수. 0 이상 정수가 아니면 None (0으로 채우지 않음)
    quality: str               # valid / invalid
    raw_value: str | None      # 원본 문자열
    received_at: datetime


@dataclass(frozen=True)
class CaptureData:
    """완전 수집분 1회. 저장소(repositories/inventory.py)가 이 값을 한 트랜잭션으로 저장한다."""
    capture_id: str
    started_at: datetime
    completed_at: datetime
    rows: list[BikeRow]
    pages: list[dict] = field(default_factory=list)  # 진단용 (페이지별 건수·받은 시각)


def _to_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_count(value) -> int | None:
    """거치 수 문자열 → 0 이상 정수. 아니면 None.

    한 곳의 값이 이상하다고 예외를 내면 서울 전체 회차가 버려지므로, 그 행만 None으로 둔다.
    음수도 None으로 둔다(DB 제약이 음수를 거부해 저장 전체가 실패하지 않도록).
    """
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def parse_row(raw: dict, received_at: datetime) -> BikeRow:
    """응답의 row 1개를 BikeRow로 바꾼다.

    이름 끝에 공백이 붙어 오는 대여소가 있어서(예: "946. 독바위역 ") 앞뒤 공백을 지운다.
    지우지 않으면 공백 차이만으로 새 revision이 생긴다.
    """
    lat, lon = _to_float(raw.get("stationLatitude")), _to_float(raw.get("stationLongitude"))
    in_seoul = lat is not None and lon is not None and 37 <= lat <= 38 and 126 <= lon <= 128
    value = _to_count(raw.get("parkingBikeTotCnt"))
    return BikeRow(station_id=raw["stationId"].strip(), name=raw["stationName"].strip(),
                   latitude=lat if in_seoul else None, longitude=lon if in_seoul else None,
                   verified=in_seoul, value=value, quality="valid" if value is not None else "invalid",
                   raw_value=raw.get("parkingBikeTotCnt"), received_at=received_at)


def parse_page(payload: dict) -> list[dict]:
    """응답 JSON 1개 → row 목록. 서울시 API는 오류도 HTTP 200으로 주고 본문의 CODE로 알린다.

    - INFO-000: 정상 → row 목록
    - INFO-200: 해당 범위에 데이터 없음 → 빈 목록 (마지막 페이지 다음)
    - INFO-100(인증키 오류), ERROR-3xx(요청 오류·한도 초과) → ProviderStopped
    - 그 밖(ERROR-5xx 서버 오류 등) → CollectionError, 다음 회차에 재시도
    인증키 오류 응답은 rentBikeStatus 없이 최상위에 RESULT만 오므로 두 곳을 모두 본다.
    """
    body = payload.get("rentBikeStatus") or {}
    code = (body.get("RESULT") or payload.get("RESULT") or {}).get("CODE", "missing")
    if code == "INFO-000":
        return body["row"]
    if code == "INFO-200":
        return []
    if code == "INFO-100" or code.startswith("ERROR-3"):
        raise ProviderStopped(f"provider_{code}")
    raise CollectionError(f"provider_{code}")


async def fetch_page(http: httpx.AsyncClient, api_key: str, first: int) -> list[dict]:
    """first번째부터 1,000건을 요청한다. 네트워크·HTTP 오류는 httpx 예외 그대로 올라간다."""
    response = await http.get(f"/{api_key}/json/bikeList/{first}/{first + PAGE_SIZE - 1}/")
    response.raise_for_status()
    return parse_page(response.json())


async def fetch_capture(http: httpx.AsyncClient, api_key: str, *,
                        clock: Callable[[], datetime] = utc_now,
                        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                        min_interval: float = MIN_REQUEST_INTERVAL) -> CaptureData:
    """서울 전체를 끝 페이지까지 받아 CaptureData를 만든다. 중간에 실패하면 예외(저장하지 않음).

    - 1,000건 미만 페이지가 오면 끝이다.
    - 1,000건이 꽉 찬 페이지 다음에 INFO-200이 오면 끝이다. 첫 페이지부터 INFO-200이면 오류.
    - 각 행의 received_at은 그 페이지를 받은 시각이라 항상 started_at~completed_at 안에 있다.
    clock·sleep은 테스트에서 바꿔 넣는다.
    """
    capture_id, started = str(uuid.uuid4()), clock()
    rows: list[BikeRow] = []
    pages: list[dict] = []
    for first in range(1, MAX_PAGES * PAGE_SIZE + 1, PAGE_SIZE):
        if pages:
            await sleep(min_interval)
        raw_rows = await fetch_page(http, api_key, first)
        received = clock()
        if not raw_rows:
            if not pages:
                raise CollectionError("empty_first_page")
            break
        rows.extend(parse_row(raw, received) for raw in raw_rows)
        pages.append({"first": first, "rows": len(raw_rows), "received_at": received.isoformat()})
        if len(raw_rows) < PAGE_SIZE:
            break
    else:
        raise CollectionError("too_many_pages")
    return CaptureData(capture_id=capture_id, started_at=started, completed_at=clock(), rows=rows, pages=pages)


async def run(settings: Settings, *, once: bool = False) -> int:
    """수집 → 저장 → 오래된 재고 삭제를 interval마다 반복한다. 종료 코드를 돌려준다.

    - ProviderStopped: 즉시 종료 (키·한도 문제는 사람이 확인해야 함)
    - CollectionError: 경고만 남기고 다음 회차로
    - 그 밖(네트워크, 응답 형식, DB 오류 등): 오류 종류만 남기고 다음 회차로.
      예외 메시지에는 인증키가 든 URL이 있을 수 있어 남기지 않는다.
    """
    from app.db.session import make_engine, make_sessionmaker
    from app.repositories.inventory import prune, save_capture

    api_key = settings.seoul_api_key.get_secret_value()
    if not api_key:
        logger.error("SEOUL_COLLECTION_API_KEY is not set (backend/.env)")
        return 2
    if not settings.database_url:
        logger.error("DATABASE_URL is not set (backend/.env)")
        return 2
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    try:
        async with httpx.AsyncClient(base_url=settings.seoul_api_base_url, timeout=10) as http:
            while True:
                tick = time.monotonic()
                try:
                    capture = await fetch_capture(http, api_key)
                    async with sessions() as session:
                        saved = await save_capture(session, capture)
                        removed = await prune(session, utc_now() - timedelta(hours=settings.retention_hours))
                    logger.info("capture saved: %d stations (%d changed), %d pages, %.1fs, pruned %d",
                                len(capture.rows), saved.station_revisions, len(capture.pages),
                                (capture.completed_at - capture.started_at).total_seconds(), removed)
                except ProviderStopped as exc:
                    logger.error("collector stopped: %s", exc.reason)
                    return 1
                except CollectionError as exc:
                    logger.warning("capture skipped: %s", exc.reason)
                except Exception as exc:  # noqa: BLE001  수집기는 한 번의 오류로 죽지 않는다
                    logger.error("capture failed: %s", type(exc).__name__)
                if once:
                    return 0
                await asyncio.sleep(max(0.0, settings.collection_interval_seconds - (time.monotonic() - tick)))
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="따릉이 실시간 재고 수집기")
    parser.add_argument("--once", action="store_true", help="한 번만 수집하고 종료")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx INFO 로그는 인증키가 든 URL을 찍는다
    sys.exit(asyncio.run(run(get_settings(), once=args.once)))


if __name__ == "__main__":
    main()
