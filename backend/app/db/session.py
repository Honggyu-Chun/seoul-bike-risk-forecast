"""DB 엔진과 세션.

드라이버는 asyncpg(postgresql+asyncpg://...)를 쓴다. psycopg의 async 모드는 Windows 기본
이벤트 루프(Proactor)에서 동작하지 않아서, Windows 개발 환경과 Linux 서버에서 똑같이 도는 asyncpg를 골랐다.
"""
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def make_engine(database_url: str) -> AsyncEngine:
    """엔진(연결 풀)을 만든다. 실제 접속은 첫 쿼리 때 일어나므로 DB가 꺼져 있어도 앱은 뜬다.

    pool_pre_ping: 풀에서 꺼낸 연결이 끊겨 있으면(DB 재시작 등) 버리고 새로 연결한다.
    """
    return create_async_engine(database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """세션 공장. expire_on_commit=False: 커밋 뒤에도 객체 속성을 다시 조회하지 않고 읽을 수 있게 한다."""
    return async_sessionmaker(engine, expire_on_commit=False)
