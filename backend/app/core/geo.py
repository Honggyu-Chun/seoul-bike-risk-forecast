"""좌표 계산. 거리는 모두 직선거리(m)다. 도보 거리가 아니다."""
import math

EARTH_RADIUS_M = 6_371_008.8


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 사이 직선거리(m, 하버사인 공식). AI 서비스와 같은 지구 반지름을 쓴다."""
    a, b = math.radians(lat1), math.radians(lat2)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return EARTH_RADIUS_M * 2 * math.asin(min(1.0, math.sqrt(h)))
