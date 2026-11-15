"""예측 확인 기록 테이블 조회와 변경. 모든 조회는 user_id로 거른다(다른 사람의 기록에 닿지 않게). 커밋은 서비스가 한다."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PredictionView


async def get_view(session: AsyncSession, user_id: int, station_id: str, target_at: datetime) -> PredictionView | None:
    return await session.scalar(select(PredictionView).where(
        PredictionView.user_id == user_id, PredictionView.station_id == station_id,
        PredictionView.target_at == target_at))


async def insert_view(session: AsyncSession, values: dict) -> PredictionView | None:
    """새 기록을 넣는다. 같은 (회원, 대여소, 이용 시각)이 이미 있으면(동시 요청) 아무것도 하지 않고 None."""
    stmt = (insert(PredictionView).values(**values)
            .on_conflict_do_nothing(index_elements=["user_id", "station_id", "target_at"])
            .returning(PredictionView))
    return await session.scalar(stmt)
