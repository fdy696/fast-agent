from fastapi import APIRouter, Request

from core.config import settings
from core.dependency import CurrentUser, DbSession
from core.rate_limit import limiter
from core.security import create_token_pair, verify_token
from repositories.user import UserRepository
from schemas.base import Fail, Success
from schemas.login import CredentialsSchema, JWTOut, RefreshTokenRequest, RegisterSchema, TokenRefreshOut
from utils.captcha import generate_captcha_id, generate_captcha_svg, generate_captcha_text, store_captcha, verify_captcha

router = APIRouter()


@router.get("/captcha", summary="获取验证码")
@limiter.limit("30/minute")
async def get_captcha(request: Request):
    text = generate_captcha_text()
    captcha_id = generate_captcha_id()
    svg = generate_captcha_svg(text)
    await store_captcha(captcha_id, text)
    return Success(data={"captchaId": captcha_id, "svg": svg})


@router.post("/login", summary="登录获取 token")
@limiter.limit("10/minute")
async def login_access_token(
    request: Request,
    credentials: CredentialsSchema,
    db: DbSession,
):
    # 验证码校验（提供时才校验，兼容 API 客户端）
    if credentials.captcha_id and credentials.code:
        if not await verify_captcha(credentials.captcha_id, credentials.code):
            return Fail(code=400, msg="验证码错误或已过期")

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
        user_id=user.id,
        username=user.username,
        email=user.email,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return Success(data=data.model_dump())


@router.post("/register", summary="用户注册")
@limiter.limit("5/minute")
async def register(
    request: Request,
    params: RegisterSchema,
    db: DbSession,
):
    if not await verify_captcha(params.captcha_id, params.code):
        return Fail(code=400, msg="验证码错误或已过期")

    repo = UserRepository(db)

    if await repo.get_by_username(params.username):
        return Fail(code=400, msg="用户名已存在")

    if params.email and await repo.get_by_email(params.email):
        return Fail(code=400, msg="邮箱已被注册")

    # 创建用户
    from schemas.users import UserCreate
    user_in = UserCreate(
        username=params.username,
        password=params.password,
        alias=params.username,
        email=params.email,
    )
    user = await repo.create_user(user_in)

    access_token, refresh_token = create_token_pair(
        user_id=user.id,
        username=user.username,
        is_superuser=user.is_superuser,
    )
    data = JWTOut(
        access_token=access_token,
        refresh_token=refresh_token,
        user_id=user.id,
        username=user.username,
        email=user.email,
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
    data = current_user.to_dict(exclude={"password"})
    return Success(data=data)
