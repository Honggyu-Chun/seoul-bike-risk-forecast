"""공통 테스트 설정.

DB 테스트는 TEST_DATABASE_URL(환경변수 또는 backend/.env)이 있을 때만 돈다. 없거나 DB가 꺼져 있으면 건너뛴다.
개발 DB(DATABASE_URL)는 절대 쓰지 않는다. 테스트가 테이블을 지우고 다시 만들기 때문이다.
"""
import os
import subprocess
import sys

import pytest
from dotenv import dotenv_values

from app.core.config import BACKEND_DIR


def _test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or dotenv_values(BACKEND_DIR / ".env").get("TEST_DATABASE_URL") or ""


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """마이그레이션을 끝까지 적용한 테스트 DB 주소. 준비할 수 없으면 해당 테스트를 건너뛴다."""
    url = _test_database_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    result = subprocess.run([sys.executable, "-m", "alembic", "-x", f"url={url}", "upgrade", "head"],
                            cwd=BACKEND_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"test database unavailable: {result.stderr.strip().splitlines()[-1:]}")
    return url
