"""테이블 정의.

stations·captures·observations는 내부 데이터 API(AI가 커서로 이어 받는 경로)의 record 모양을 컬럼으로 그대로 갖는다.
users·sessions·login_attempts·favorites·email_tokens는 로그인·회원 기능용이다(맨 아래).
JSON 덩어리(payload)로 저장하지 않고 컬럼으로 나눠, 제약 조건으로 잘못된 값을 DB 단계에서 막는다.

cursor_seq
- 테이블별 '커밋 순서' 번호. AI는 이 값으로 "어디까지 받았는지"를 기억하고 이어 받는다.
- 관측 시각이나 ID가 아니다. 반드시 커밋 순서대로 늘어나야 한다(저장 시 테이블 잠금 안에서 max+1).
- 충돌로 다시 내보내는 기록은 cursor_seq를 새 값으로 바꾼다. 그래서 기본 키(id)와 분리했다.

시각은 모두 timestamptz(시간대 포함)로 저장한다.
"""
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, DateTime, Double, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, false, func, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

Timestamp = DateTime(timezone=True)


class Station(Base):
    """대여소 revision 이력. 수정하지 않고 쌓는다(append-only).

    한 대여소(station_id)에 revision이 여러 개 있을 수 있고, 현재 정보는 cursor_seq가 가장 큰 행이다.
    - 이름·좌표·식별 상태가 바뀌면 새 revision 행을 추가한다.
    - valid_from은 그 대여소를 처음 받은 시각이다. AI는 valid_from 이전 재고를 버리므로,
      이름만 바뀐 경우에는 새 행에도 원래 valid_from을 그대로 쓴다(좌표 이전·폐소·ID 재사용만 새 구간).
    - 좌표가 없거나 서울 범위를 벗어나면 identity_status='unverified', 좌표는 null.
    """
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cursor_seq: Mapped[int] = mapped_column(BigInteger, unique=True)
    station_id: Mapped[str] = mapped_column(String(80))          # 따릉이 API의 ST-숫자 그대로
    source_station_id: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(40), server_default="seoul_bike")
    name: Mapped[str] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Double)
    longitude: Mapped[float | None] = mapped_column(Double)
    identity_status: Mapped[str] = mapped_column(String(20))
    valid_from: Mapped[datetime] = mapped_column(Timestamp)
    valid_to: Mapped[datetime | None] = mapped_column(Timestamp)  # 폐소 등으로 끝난 구간. 현재 운영 중이면 null
    received_at: Mapped[datetime] = mapped_column(Timestamp)
    revision: Mapped[str] = mapped_column(String(80))             # 이 내용을 처음 본 capture_id
    conflict: Mapped[bool] = mapped_column(Boolean, server_default=false())

    __table_args__ = (
        UniqueConstraint("station_id", "revision"),
        CheckConstraint("identity_status IN ('verified', 'unverified')", name="identity_status"),
        CheckConstraint("identity_status = 'unverified' OR (latitude IS NOT NULL AND longitude IS NOT NULL)",
                        name="verified_has_coordinates"),
        CheckConstraint("latitude IS NULL OR latitude BETWEEN 33 AND 39", name="latitude_range"),
        CheckConstraint("longitude IS NULL OR longitude BETWEEN 124 AND 132", name="longitude_range"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="validity_interval"),
        Index(None, "station_id", "cursor_seq"),  # 대여소별 최신 revision 조회
    )


class Capture(Base):
    """완전 수집분 1회. 서울 전체 재고를 끝 페이지까지 다 받았을 때만 행이 생긴다.

    AI는 expected_rows = actual_rows = 그 capture의 고유 대여소 수이고, 모든 재고의 received_at이
    started_at~completed_at 안에 있어야 완료로 본다. 하나라도 어긋나면 그 수집분 전체를 쓰지 않는다.
    """
    __tablename__ = "captures"

    capture_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # UUID 문자열
    source: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(Timestamp)
    completed_at: Mapped[datetime] = mapped_column(Timestamp)
    expected_rows: Mapped[int] = mapped_column(Integer)
    actual_rows: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20))
    summary_json: Mapped[str] = mapped_column(Text, server_default="{}")  # 페이지 수 등 진단 정보(JSON 문자열)

    __table_args__ = (
        CheckConstraint("completed_at >= started_at", name="time_order"),
        CheckConstraint("expected_rows >= 0 AND actual_rows >= 0", name="row_counts"),
        Index(None, "source", "completed_at"),  # 최신 완료 capture 조회
    )


class Observation(Base):
    """관측값 1건. 지금은 따릉이 재고(source='seoul_bike', variable='inventory')만 쌓는다.

    기상 실황도 같은 테이블에 넣을 수 있게 issued_at·valid_at 컬럼을 미리 둔다(기상 번들이 나온 뒤 사용).
    - 실제 0대: value=0, quality='valid'
    - 값이 비정상·누락: value=null, quality='invalid'/'missing' (0으로 바꾸지 않는다)
    - 따릉이 API에는 관측 시각이 없어 observed_at은 null, timing_basis='received'
    """
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cursor_seq: Mapped[int] = mapped_column(BigInteger, unique=True)
    capture_id: Mapped[str | None] = mapped_column(ForeignKey("captures.capture_id"))
    source: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(80))           # 재고면 station_id
    event_key: Mapped[str] = mapped_column(String(80))           # 재고면 capture_id
    variable: Mapped[str] = mapped_column(String(40))
    value: Mapped[float | None] = mapped_column(Double)
    unit: Mapped[str] = mapped_column(String(20))
    raw_value: Mapped[str | None] = mapped_column(String(160))   # 원본 문자열(비정상 값 추적용)
    quality: Mapped[str] = mapped_column(String(20), server_default="valid")
    timing_basis: Mapped[str] = mapped_column(String(30), server_default="received")
    received_at: Mapped[datetime] = mapped_column(Timestamp)
    observed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    issued_at: Mapped[datetime | None] = mapped_column(Timestamp)
    valid_at: Mapped[datetime | None] = mapped_column(Timestamp)
    revision: Mapped[str] = mapped_column(String(80), server_default="0")
    conflict: Mapped[bool] = mapped_column(Boolean, server_default=false())

    __table_args__ = (
        UniqueConstraint("source", "entity_id", "event_key", "variable", "revision"),
        CheckConstraint("quality IN ('valid', 'missing', 'invalid')", name="quality"),
        CheckConstraint("quality <> 'valid' OR value IS NOT NULL", name="valid_has_value"),
        CheckConstraint("variable <> 'inventory' OR value IS NULL OR (value >= 0 AND value = floor(value))",
                        name="inventory_nonnegative_integer"),
        CheckConstraint("variable <> 'inventory' OR unit = 'bikes'", name="inventory_unit"),
        CheckConstraint("timing_basis IN ('received', 'observed_and_received', 'issued_and_received', "
                        "'historical_proxy')", name="timing_basis"),
        Index(None, "capture_id"),       # capture 하나의 재고 전체 조회
        Index(None, "received_at"),      # 보관 기간이 지난 행 삭제, start 필터
    )


class User(Base):
    """회원. 로그인은 아이디·비밀번호로 하고, 이메일은 선택(인증하면 비밀번호 복구에 쓴다).

    - login_id는 소문자로 바꿔 저장하고 바꿀 수 없다.
    - password_hash는 bcrypt 해시(60자). 원문은 어디에도 저장하지 않는다.
    - email은 인증 전에는 남의 것일 수 있어 중복을 허용하고, 인증된 이메일만 계정끼리 겹치지 않게 한다.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login_id: Mapped[str] = mapped_column(String(20), unique=True)
    password_hash: Mapped[str] = mapped_column(String(60))
    nickname: Mapped[str | None] = mapped_column(String(12))
    bio: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(254))
    email_verified_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())
    password_changed_at: Mapped[datetime] = mapped_column(Timestamp, server_default=func.now())

    __table_args__ = (
        CheckConstraint("login_id ~ '^[a-z0-9_]{4,20}$'", name="login_id_format"),
        CheckConstraint("email_verified_at IS NULL OR email IS NOT NULL", name="verified_has_email"),
        Index("uq_users_verified_email", "email", unique=True, postgresql_where=text("email_verified_at IS NOT NULL")),
    )


class AuthSession(Base):
    """로그인 세션. 쿠키에는 무작위 토큰을, DB에는 그 SHA-256 해시만 둔다(DB가 새도 로그인할 수 없게).

    만료: 마지막 사용 후 14일(last_seen_at) 또는 로그인 후 30일(expires_at) 중 먼저 오는 때.
    회원을 지우면 세션도 함께 지워진다(ON DELETE CASCADE).
    """
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    last_seen_at: Mapped[datetime] = mapped_column(Timestamp)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)

    __table_args__ = (
        Index(None, "user_id"),
    )


class LoginAttempt(Base):
    """로그인·가입 시도 기록. 횟수 제한(비밀번호 대입, 대량 가입)에만 쓰고 1일 지나면 지운다.

    IP는 개인정보라 제한에 필요한 기간만 둔다. 가입 시도는 login_id를 비워 둔다.
    """
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    login_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str] = mapped_column(String(45))
    attempted_at: Mapped[datetime] = mapped_column(Timestamp)
    succeeded: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        CheckConstraint("kind IN ('login', 'signup')", name="kind"),
        Index(None, "login_id", "ip", "attempted_at"),
        Index(None, "ip", "attempted_at"),
    )


class Favorite(Base):
    """회원의 즐겨찾기 대여소. 한 사람당 20개까지, position 순서로 보여 준다.

    station_id는 stations가 revision 이력 테이블이라 외래 키를 걸 수 없다. 추가할 때 현재 운영 대여소인지 확인하고,
    나중에 폐소돼도 지우지 않는다(응답에서 이름·좌표가 null이 된다). 회원을 지우면 함께 지워진다.
    """
    __tablename__ = "favorites"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    station_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    alias: Mapped[str | None] = mapped_column(String(20))     # "집 앞" 같은 별칭(선택)
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(Timestamp)

    __table_args__ = (
        CheckConstraint("position >= 0", name="position_nonnegative"),
    )


class EmailToken(Base):
    """메일 링크용 일회성 토큰(이메일 인증, 비밀번호 재설정). 링크에는 원문을, DB에는 SHA-256 해시만 둔다.

    - 30분 유효, 한 번만 쓴다(used_at). email은 토큰을 만들 때의 주소로, 그 뒤 회원이 이메일을 바꾸면 무효가 된다.
    - 만료 후 하루가 지나면 지운다. 회원을 지우면 함께 지워진다.
    """
    __tablename__ = "email_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(10))
    email: Mapped[str] = mapped_column(String(254))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    used_at: Mapped[datetime | None] = mapped_column(Timestamp)

    __table_args__ = (
        CheckConstraint("purpose IN ('verify', 'reset')", name="purpose"),
        Index(None, "user_id", "purpose", "created_at"),
    )
