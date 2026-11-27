"""공통 테스트 설정.

DB 테스트는 TEST_DATABASE_URL(환경변수 또는 backend/.env)이 있을 때만 돈다. 없거나 DB가 꺼져 있으면 건너뛴다.
단, CYCLEON_REQUIRE_TEST_DB=1이면(CI) 건너뛰지 않고 실패로 처리한다. DB 테스트가 안 돌았는데 통과로 보이지 않게.
개발 DB(DATABASE_URL)는 절대 쓰지 않는다. 테스트가 테이블을 지우고 다시 만들기 때문이다.
"""
import os
import subprocess
import sys

import pytest
from dotenv import dotenv_values

from app.core.config import BACKEND_DIR

REQUIRE_DB = os.environ.get("CYCLEON_REQUIRE_TEST_DB") == "1"


def _test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get("TEST_DATABASE_URL") or ""


def _unavailable(reason: str):
    """DB를 쓸 수 없을 때: 로컬에서는 건너뛰고, CI에서는 실패시킨다."""
    if REQUIRE_DB:
        pytest.fail(f"test database required but unavailable: {reason}", pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """마이그레이션을 끝까지 적용한 테스트 DB 주소. 준비할 수 없으면 건너뛴다(CI에서는 실패)."""
    url = _test_database_url()
    if not url:
        _unavailable("TEST_DATABASE_URL is not set")
    result = subprocess.run([sys.executable, "-m", "alembic", "-x", f"url={url}", "upgrade", "head"],
                            cwd=BACKEND_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        _unavailable(f"test database unavailable: {result.stderr.strip().splitlines()[-1:]}")
    return url
