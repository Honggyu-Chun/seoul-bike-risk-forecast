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
    # 브라우저 호출을 허용할 프론트 주소. 로그인·회원 API의 Origin 확인(CSRF 방지)도 이 목록을 쓴다.
    # 리버스 프록시로 같은 출처에서 서비스할 때도 그 사이트 주소(https://...)를 넣어야 한다.
    cors_origins: list[str] = Field(
        ["http://localhost:5173", "http://127.0.0.1:5173"], validation_alias="CORS_ORIGINS")
    # bcrypt 비용. 운영 12(해시 1회 약 0.2~0.3초). 테스트만 4로 낮춘다
    bcrypt_rounds: int = Field(12, ge=4, le=15, validation_alias="CYCLEON_BCRYPT_ROUNDS")
    # 세션 쿠키 Secure 속성. https로 서비스하는 운영에서는 true, http인 로컬 개발은 false
    cookie_secure: bool = Field(False, validation_alias="CYCLEON_COOKIE_SECURE")
    # 장소 검색(카카오 로컬 API). REST API 키는 서버에만 둔다(브라우저용 JavaScript 키와 다르다)
    kakao_rest_api_key: SecretStr = Field(SecretStr(""), validation_alias="KAKAO_REST_API_KEY")
    kakao_base_url: str = Field("https://dapi.kakao.com", validation_alias="KAKAO_BASE_URL")
    kakao_timeout_seconds: float = Field(3.0, gt=0, validation_alias="KAKAO_TIMEOUT_SECONDS")
    # 메일 발송(이메일 인증, 비밀번호 재설정). 일반 SMTP라 Gmail·Resend·SES 어느 것이든 값만 바꿔 쓴다.
    # 로컬 개발은 Mailpit(docker compose, 127.0.0.1:1025, STARTTLS 없음)으로 실제 발송 없이 확인한다.
    smtp_host: str = Field("", validation_alias="SMTP_HOST")
    smtp_port: int = Field(587, validation_alias="SMTP_PORT")
    smtp_username: str = Field("", validation_alias="SMTP_USERNAME")
    smtp_password: SecretStr = Field(SecretStr(""), validation_alias="SMTP_PASSWORD")
    smtp_starttls: bool = Field(True, validation_alias="SMTP_STARTTLS")
    smtp_timeout_seconds: float = Field(10.0, gt=0, validation_alias="SMTP_TIMEOUT_SECONDS")
    mail_from: str = Field("CycleOn <no-reply@localhost>", validation_alias="MAIL_FROM")
    # 메일 속 링크가 열 프론트 주소(끝에 / 없이). 링크: <주소>/verify-email?token=..., <주소>/reset-password?token=...
    public_base_url: str = Field("http://localhost:5173", validation_alias="CYCLEON_PUBLIC_BASE_URL")
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
