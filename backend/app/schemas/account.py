"""로그인·회원 API 요청과 응답.

아이디는 앞뒤 공백을 지우고 소문자로 바꿔 다룬다(가입과 로그인 모두). 비밀번호는 손대지 않는다.
비밀번호 규칙(길이, 흔한 비밀번호, 아이디와 같음)은 아이디와 함께 봐야 해서 서비스에서 검사한다.
"""
import re

from pydantic import BaseModel, Field, field_validator

from app.schemas.public import UtcDatetime

LOGIN_ID_PATTERN = re.compile(r"[a-z0-9_]{4,20}")
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PASSWORD_INPUT_MAX = 200  # 규칙(72바이트)보다 넉넉한 입력 한도. 아주 긴 본문으로 서버를 괴롭히지 못하게


def normalize_login_id(value: str) -> str:
    return value.strip().lower()


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class SignupRequest(BaseModel):
    login_id: str = Field(max_length=64)
    password: str = Field(max_length=PASSWORD_INPUT_MAX)
    email: str | None = Field(None, max_length=300)

    @field_validator("login_id")
    @classmethod
    def _login_id(cls, value: str) -> str:
        value = normalize_login_id(value)
        if not LOGIN_ID_PATTERN.fullmatch(value):
            raise ValueError("영문 소문자·숫자·_ 4~20자")
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        value = _blank_to_none(value)
        if value is None:
            return None
        value = value.lower()
        if len(value) > 254 or not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("이메일 형식")
        return value


class LoginRequest(BaseModel):
    """형식 검사를 하지 않는다. 형식이 틀린 아이디도 '아이디 또는 비밀번호가 맞지 않음'으로 똑같이 답한다."""
    login_id: str = Field(max_length=64)
    password: str = Field(max_length=PASSWORD_INPUT_MAX)


class ProfileUpdate(BaseModel):
    """보낸 필드만 바꾼다. 빈 문자열이나 null은 지우기."""
    nickname: str | None = None
    bio: str | None = None

    @field_validator("nickname")
    @classmethod
    def _nickname(cls, value: str | None) -> str | None:
        value = _blank_to_none(value)
        if value is not None and not 2 <= len(value) <= 12:
            raise ValueError("2~12자")
        return value

    @field_validator("bio")
    @classmethod
    def _bio(cls, value: str | None) -> str | None:
        value = _blank_to_none(value)
        if value is not None and len(value) > 50:
            raise ValueError("50자 이하")
        return value


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=PASSWORD_INPUT_MAX)
    new_password: str = Field(max_length=PASSWORD_INPUT_MAX)


class PasswordConfirm(BaseModel):
    """탈퇴처럼 되돌릴 수 없는 일 앞에서 비밀번호를 한 번 더 받는다."""
    password: str = Field(max_length=PASSWORD_INPUT_MAX)


class ProfileResponse(BaseModel):
    login_id: str
    nickname: str | None
    bio: str | None
    email: str | None
    email_verified: bool
    created_at: UtcDatetime

    @classmethod
    def of(cls, user) -> "ProfileResponse":
        return cls(login_id=user.login_id, nickname=user.nickname, bio=user.bio, email=user.email,
                   email_verified=user.email_verified_at is not None, created_at=user.created_at)
