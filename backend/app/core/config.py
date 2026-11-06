"""환경변수 설정. 비밀값은 backend/.env 또는 서버 환경변수에서만 읽는다."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    ai_base_url: str = Field("http://127.0.0.1:18011", validation_alias="CYCLEON_AI_BASE_URL")
    service_token: SecretStr = Field(SecretStr(""), validation_alias="CYCLEON_SERVICE_TOKEN")
    ai_timeout_seconds: float = Field(5.0, gt=0, validation_alias="CYCLEON_AI_TIMEOUT_SECONDS")
    # AI 응답의 base_at이 백엔드 기준 정각과 같은지 확인. 시계가 고정된 목업을 쓸 때만 false
    ai_clock_check: bool = Field(True, validation_alias="CYCLEON_AI_CLOCK_CHECK")
    cors_origins: list[str] = Field(
        ["http://localhost:5173", "http://127.0.0.1:5173"], validation_alias="CORS_ORIGINS")
    debug_enabled: bool = Field(True, validation_alias="CYCLEON_DEBUG_ENABLED")
    database_url: str = Field("", validation_alias="DATABASE_URL")
    seoul_api_key: SecretStr = Field(SecretStr(""), validation_alias="SEOUL_COLLECTION_API_KEY")
    seoul_api_base_url: str = Field("http://openapi.seoul.go.kr:8088", validation_alias="SEOUL_API_BASE_URL")
    # 재고 수집 간격. 추천에는 300초 안의 재고가 필요하므로 한 번 실패해도 남도록 120초
    collection_interval_seconds: float = Field(120, ge=30, le=240,
                                               validation_alias="CYCLEON_COLLECTION_INTERVAL_SECONDS")
    # 재고 보관 기간. AI 첫 동기화가 최근 170시간을 요청하므로 그보다 길어야 한다(기본 8일)
    retention_hours: float = Field(192, gt=170, validation_alias="CYCLEON_RETENTION_HOURS")


@lru_cache
def get_settings() -> Settings:
    return Settings()
