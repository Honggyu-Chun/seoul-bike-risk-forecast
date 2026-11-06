"""내부 데이터 API 응답 (AI → 백엔드 동기화). 필드는 모델개발/docs/cycleon_backend_openapi.json과 같다.

AI는 record를 '모르는 필드 거부' 모델로 검증하므로, 여기 없는 필드를 record에 추가하면 동기화가 깨진다.
필드를 바꿀 때는 모델 담당과 먼저 맞춘다.
"""
from pydantic import BaseModel, ConfigDict

from app.schemas.public import UtcDatetime


class Record(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # ORM 행에서 같은 이름의 컬럼을 그대로 읽는다


class StationRecord(Record):
    station_id: str
    source_station_id: str
    name: str
    latitude: float | None
    longitude: float | None
    valid_from: UtcDatetime
    valid_to: UtcDatetime | None
    received_at: UtcDatetime
    source: str
    revision: str
    identity_status: str


class ObservationRecord(Record):
    source: str
    entity_id: str
    event_key: str
    variable: str
    value: float | None
    unit: str
    raw_value: str | None
    received_at: UtcDatetime
    observed_at: UtcDatetime | None
    issued_at: UtcDatetime | None
    valid_at: UtcDatetime | None
    revision: str
    quality: str
    timing_basis: str


class CaptureMeta(Record):
    capture_id: str
    source: str
    started_at: UtcDatetime
    completed_at: UtcDatetime
    expected_rows: int
    actual_rows: int
    state: str
    summary_json: str


class PageItem[R](BaseModel):
    cursor: int                    # 커밋 순서 번호 (cursor_seq)
    conflict: bool
    record: R
    capture_id: str | None = None  # 재고면 소속 capture, 대여소면 null


class Page[R](BaseModel):
    items: list[PageItem[R]]
    next_cursor: int   # 다음 요청의 cursor. has_more=false면 watermark와 같다
    watermark: int     # 이번 동기화의 상한. AI가 다음 페이지에 그대로 다시 보낸다
    has_more: bool
    captures: list[CaptureMeta]  # 이 페이지의 재고가 속한 capture들. AI가 수집분 완료를 판단하는 데 쓴다
