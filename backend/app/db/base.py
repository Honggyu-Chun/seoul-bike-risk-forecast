"""모든 테이블 모델의 부모 클래스."""
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 제약 조건 이름 규칙. 이름이 정해져 있어야 Alembic이 나중에 제약을 찾아 지우거나 바꿀 수 있다.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
