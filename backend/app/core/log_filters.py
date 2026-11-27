"""로그에 사용자 위치가 남지 않게 하는 설정.

uvicorn 접속 로그는 기본으로 요청 주소를 쿼리까지 남긴다(/api/stations/nearby?lat=..&lon=.., /api/places?query=..).
위치·검색어가 로그에 쌓이지 않도록 쿼리를 지우고 경로만 남긴다. httpx도 INFO 수준에서 호출 주소를 남기므로
WARNING 이상만 남긴다(카카오 호출 주소에 검색어가 들어간다).
"""
import logging


class HideQueryString(logging.Filter):
    """uvicorn 접속 로그의 경로에서 ?뒤를 지운다. 로그 형식: '%s - "%s %s HTTP/%s" %d'"""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (args[0], args[1], args[2].split("?", 1)[0], args[3], args[4])
        return True


def install_privacy_filters() -> None:
    """여러 번 불러도 필터는 하나만 붙는다(테스트에서 앱을 여러 번 만들 때)."""
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HideQueryString) for f in access.filters):
        access.addFilter(HideQueryString())
    logging.getLogger("httpx").setLevel(logging.WARNING)
