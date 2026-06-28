import re
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

USERNAME_PATTERN = r"^[a-zA-Z0-9_]+$"


class UserBase(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    alias: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    is_active: bool = True
    is_superuser: bool = False


class UserCreate(UserBase):
    username: str = Field(..., min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(..., min_length=8)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not re.fullmatch(USERNAME_PATTERN, value):
            raise ValueError("用户名只能包含字母、数字和下划线")
        return value

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value):
            raise ValueError("密码必须包含字母")
        if not re.search(r"\d", value):
            raise ValueError("密码必须包含数字")
        return value


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    alias: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    password: str | None = Field(default=None, min_length=8)
    is_active: bool | None = None
    is_superuser: bool | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(USERNAME_PATTERN, value):
            raise ValueError("用户名只能包含字母、数字和下划线")
        return value

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not re.search(r"[A-Za-z]", value):
            raise ValueError("密码必须包含字母")
        if not re.search(r"\d", value):
            raise ValueError("密码必须包含数字")
        return value


