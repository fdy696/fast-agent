from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from passlib.context import CryptContext

from core.config import settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto", argon2__memory_cost=8192)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def generate_password(length: int = 12) -> str:
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_access_token(*, data: Any | None = None, user_id: int | None = None, username: str | None = None, is_superuser: bool = False) -> str:
    if data is not None:
        payload = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    else:
        if user_id is None or username is None:
            raise ValueError("user_id and username are required")
        expire = datetime.now(UTC) + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {
            "user_id": user_id,
            "username": username,
            "is_superuser": is_superuser,
            "exp": expire,
            "token_type": "access",
        }
    payload["token_type"] = "access"
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: int, username: str, is_superuser: bool) -> str:
    expire = datetime.now(UTC) + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "user_id": user_id,
        "username": username,
        "is_superuser": is_superuser,
        "exp": expire,
        "token_type": "refresh",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str, token_type: str = "access") -> dict[str, Any]:
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    if payload.get("token_type") != token_type:
        raise jwt.InvalidTokenError(f"Invalid token type. Expected {token_type}")
    return payload


def decode_token(token: str, token_type: str = "access") -> dict[str, Any]:
    return verify_token(token, token_type=token_type)


def create_token_pair(user_id: int, username: str, is_superuser: bool) -> tuple[str, str]:
    access_token = create_access_token(user_id=user_id, username=username, is_superuser=is_superuser)
    refresh_token = create_refresh_token(user_id=user_id, username=username, is_superuser=is_superuser)
    return access_token, refresh_token
