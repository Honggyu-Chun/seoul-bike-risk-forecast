"""Alembic 마이그레이션 실행 환경.

DB 주소는 alembic.ini가 아니라 앱 설정(DATABASE_URL, backend/.env)에서 읽는다.
테이블 정의는 app.db.models의 Base.metadata를 기준으로 autogenerate한다.
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db import models  # noqa: F401  모든 테이블을 metadata에 등록
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """-x url=...로 넘기면 그 값을(테스트 DB용), 아니면 DATABASE_URL을 쓴다."""
    url = context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set (backend/.env)")
    return url


def run_migrations_offline() -> None:
    """DB에 접속하지 않고 SQL만 출력한다 (alembic upgrade head --sql)."""
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """DB에 접속해 마이그레이션을 적용한다."""
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
