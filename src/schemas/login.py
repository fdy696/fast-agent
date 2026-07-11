from datetime import datetime

from pydantic import BaseModel, Field


class CredentialsSchema(BaseModel):
    username: str = Field(..., description="用户名或邮箱", examples=["admin", "admin@example.com"])
    password: str = Field(..., description="密码")
    captcha_id: str | None = Field(default=None, description="验证码 ID")
    code: str | None = Field(default=None, description="验证码")


class RegisterSchema(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, description="用户名", examples=["newuser"])
    password: str = Field(..., min_length=8, description="密码")
    email: str | None = Field(default=None, description="邮箱（选填，用于找回密码等）")
    captcha_id: str = Field(..., description="验证码 ID")
    code: str = Field(..., description="验证码")


class JWTOut(BaseModel):
    access_token: str
    refresh_token: str
    user_id: int
    username: str
    email: str | None = None
    token_type: str = "bearer"
    expires_in: int


class JWTPayload(BaseModel):
    user_id: int
    username: str
    is_superuser: bool
    exp: datetime
    token_type: str = "access"


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., description="刷新令牌")


class TokenRefreshOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
