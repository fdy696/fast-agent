from fastapi import APIRouter, Request

from core.config import settings
from core.dependency import CurrentUser, DbSession
from core.rate_limit import limiter
from core.security import create_token_pair, verify_token
from repositories.user import UserRepository
from schemas.base import Fail, Success
from schemas.login import CredentialsSchema, JWTOut, RefreshTokenRequest, TokenRefreshOut

router = APIRouter()


@router.post("/login", summary="登录获取 token")
@limiter.limit("5/minute")
async def login_access_token(
    request: Request,
    credentials: CredentialsSchema,
    db: DbSession,
):
    repo = UserRepository(db)
    user = await repo.authenticate(credentials)
    await repo.update_last_login(user)
    access_token, refresh_token = create_token_pair(
        user_id=user.id,
        username=user.username,
        is_superuser=user.is_superuser,
    )
    data = JWTOut(
        access_token=access_token,
        refresh_token=refresh_token,
        username=user.username,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return Success(data=data.model_dump())


@router.post("/refresh", summary="刷新 token")
@limiter.limit("10/minute")
async def refresh_access_token(
    request: Request,
    refresh_request: RefreshTokenRequest,
    db: DbSession,
):
    try:
        payload = verify_token(refresh_request.refresh_token, token_type="refresh")
        user = await UserRepository(db).get(int(payload["user_id"]))
        if not user or not user.is_active:
            return Fail(code=401, msg="用户不存在或已被禁用")
        access_token, refresh_token = create_token_pair(
            user_id=user.id,
            username=user.username,
            is_superuser=user.is_superuser,
        )
        data = TokenRefreshOut(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )
        return Success(data=data.model_dump())
    except Exception:
        return Fail(code=401, msg="令牌无效或已过期")


@router.get("/userinfo", summary="当前用户信息")
async def get_userinfo(current_user: CurrentUser):
    return Success(data=current_user.to_dict(exclude={"password"}))
