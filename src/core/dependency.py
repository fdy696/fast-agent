import secrets

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import ForbiddenException
from core.security import verify_token
from db.session import AsyncSessionLocal, get_db
from models import User
from repositories.user import UserRepository

security = HTTPBasic()
bearer_scheme = HTTPBearer()


def get_current_username(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.SWAGGER_UI_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, settings.SWAGGER_UI_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication Required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


class AuthControl:
    @staticmethod
    async def is_authed(
        token: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    ) -> User:
        try:
            payload = verify_token(token.credentials, token_type="access")
            user_id = int(payload.get("user_id"))
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(status_code=401, detail="登录已过期") from exc
        except Exception as exc:
            raise HTTPException(status_code=401, detail="认证失败") from exc

        # 开短 session 查用户，查完即闭，不占 dep 生命周期
        async with AsyncSessionLocal() as db:
            user = await UserRepository(db).get(user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="Authentication failed")
        return user

    @staticmethod
    async def is_admin(current_user: User = Depends(is_authed)) -> User:
        if not current_user.is_superuser:
            raise ForbiddenException("Admin permission required")
        return current_user


from typing import Annotated

DependAuth = Depends(AuthControl.is_authed)
DependAdmin = Depends(AuthControl.is_admin)

DbSession = Annotated[AsyncSession, Depends(get_db)]
CurrentUser = Annotated[User, Depends(AuthControl.is_authed)]
AdminUser = Annotated[User, Depends(AuthControl.is_admin)]
