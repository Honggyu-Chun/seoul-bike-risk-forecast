"""DB 연결 확인. 마이그레이션 적용(conftest)과 요청별 세션 의존성이 실제 PostgreSQL에서 동작하는지 본다."""
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.core.config import Settings
from app.main import create_app


def test_session_dependency_reaches_database(test_database_url):
    app = create_app(Settings(_env_file=None, DATABASE_URL=test_database_url))

    @app.get("/_test/db")
    async def db_check(session: AsyncSession = Depends(get_session)):
        return {"one": await session.scalar(text("select 1"))}

    with TestClient(app) as client:  # with: 종료 시 lifespan이 엔진을 닫는지도 함께 확인
        assert client.get("/_test/db").json()["one"] == 1


def test_missing_database_url_fails_only_db_routes():
    app = create_app(Settings(_env_file=None))

    @app.get("/_test/db")
    async def db_check(session: AsyncSession = Depends(get_session)):
        return {}

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/_test/db").status_code == 500
    assert client.get("/health").status_code == 200
