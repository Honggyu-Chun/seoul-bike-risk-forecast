"""비밀번호 규칙과 bcrypt 해시.

- 원문 비밀번호는 저장하지도, 로그에 남기지도 않는다. DB에는 bcrypt 해시(60자)만 둔다.
- bcrypt 계산은 일부러 느리다(비용 12에서 약 0.2~0.3초). 이벤트 루프에서 돌리면 그동안 다른 API가 모두
  멈추므로 스레드풀에서 실행한다.
- 규칙은 NIST SP 800-63B 권고를 따른다. 조합 규칙(특수문자 필수 등) 대신 길이와 흔한 비밀번호 차단.
"""
from functools import lru_cache

import bcrypt
from starlette.concurrency import run_in_threadpool

MIN_LENGTH = 8
MAX_BYTES = 72  # bcrypt는 72바이트까지만 쓴다. 한글은 한 글자가 3바이트

# 유출 사례에서 가장 흔한 비밀번호 일부(8자 이상만 의미가 있다). 소문자로 비교한다.
COMMON_PASSWORDS = frozenset({
    "password", "password1", "password12", "password123", "passw0rd", "p@ssw0rd", "12345678", "123456789",
    "1234567890", "0123456789", "87654321", "11111111", "00000000", "88888888", "12341234", "11223344",
    "qwertyui", "qwerty12", "qwerty123", "qwer1234", "1q2w3e4r", "1q2w3e4r5t", "1qaz2wsx", "q1w2e3r4",
    "asdf1234", "asdfasdf", "asdfghjk", "zxcvbnm1", "abcd1234", "abc12345", "aa123456", "a1234567",
    "iloveyou", "sunshine", "princess", "football", "baseball", "superman", "trustno1", "welcome1",
    "letmein1", "dragon12", "computer", "internet", "whatever", "starwars", "admin123", "administrator",
    "changeme", "test1234", "testtest", "minecraft", "cycleon1", "cycleon123", "ddareungi",
})


def password_is_acceptable(password: str, login_id: str) -> bool:
    """가입·변경 때 새 비밀번호 검사. 틀리면 422 invalid_request(fields: 해당 칸)로 낸다."""
    if len(password) < MIN_LENGTH or len(password.encode()) > MAX_BYTES:
        return False
    lowered = password.lower()
    return lowered != login_id and lowered not in COMMON_PASSWORDS


async def hash_password(password: str, rounds: int) -> str:
    return await run_in_threadpool(lambda: bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds)).decode())


async def verify_password(password: str, password_hash: str) -> bool:
    """bcrypt 비교. 72바이트를 넘는 입력은 가입 때 막았으므로 맞을 수 없다(시간은 똑같이 쓴다)."""
    raw = password.encode()
    matched = await run_in_threadpool(bcrypt.checkpw, raw[:MAX_BYTES], password_hash.encode())
    return matched and len(raw) <= MAX_BYTES


@lru_cache
def _dummy_hash(rounds: int) -> bytes:
    return bcrypt.hashpw(b"cycleon-dummy-password", bcrypt.gensalt(rounds))


async def spend_verify_time(password: str, rounds: int) -> None:
    """없는 아이디로 로그인할 때도 비교 한 번만큼 시간을 쓴다. 응답 시간으로 가입 여부를 알아낼 수 없게."""
    dummy = await run_in_threadpool(_dummy_hash, rounds)
    await run_in_threadpool(bcrypt.checkpw, password.encode()[:MAX_BYTES], dummy)
