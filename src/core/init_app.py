from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from sqlalchemy.exc import IntegrityError, NoResultFound, SQLAlchemyError
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from api.v1 import v1_router
from core.config import settings
from core.exceptions import (
    BusinessException,
    BusinessExceptionHandle,
    HttpExcHandle,
    GeneralExceptionHandle,
    IntegrityHandle,
    NotFoundHandle,
    RequestValidationHandle,
    ResponseValidationHandle,
    SQLAlchemyHandle,
)
from core.middlewares import HttpAuditLogMiddleware, RequestLoggingMiddleware
from core.rate_limit import limiter
from core.security import get_password_hash
from db.session import AsyncSessionLocal
from log import logger
from models import User
from repositories.user import UserRepository
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi import _rate_limit_exceeded_handler


def make_middlewares() -> list[Middleware]:
    return [
        Middleware(
            CORSMiddleware,
            allow_origins=settings.CORS_ORIGINS_LIST,
            allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
            allow_methods=settings.CORS_ALLOW_METHODS,
            allow_headers=settings.CORS_ALLOW_HEADERS,
        ),
        Middleware(RequestLoggingMiddleware),
        Middleware(
            HttpAuditLogMiddleware,
            methods=["POST", "PUT", "DELETE"],
            exclude_paths=[
                r"/api/v1/auth/login",
                r"/api/v1/auth/refresh",
                r"/docs",
                r"/redoc",
                r"/openapi.json",
            ],
        ),
        Middleware(SlowAPIMiddleware),
    ]


def register_exceptions(app: FastAPI) -> None:
    app.add_exception_handler(BusinessException, BusinessExceptionHandle)
    app.add_exception_handler(HTTPException, HttpExcHandle)
    app.add_exception_handler(NoResultFound, NotFoundHandle)
    app.add_exception_handler(Exception, GeneralExceptionHandle)
    app.add_exception_handler(IntegrityError, IntegrityHandle)
    app.add_exception_handler(SQLAlchemyError, SQLAlchemyHandle)
    app.add_exception_handler(RequestValidationError, RequestValidationHandle)
    app.add_exception_handler(ResponseValidationError, ResponseValidationHandle)
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def register_routers(app: FastAPI, prefix: str = "/api") -> None:
    app.include_router(v1_router, prefix=f"{prefix}/v1")


async def init_superuser() -> None:
    async with AsyncSessionLocal() as db:
        repo = UserRepository(db)
        user = await repo.get_by_username(settings.FIRST_SUPERUSER_USERNAME)
        if user:
            return
        admin = User(
            username=settings.FIRST_SUPERUSER_USERNAME,
            email=settings.FIRST_SUPERUSER_EMAIL,
            password=get_password_hash(settings.FIRST_SUPERUSER_PASSWORD),
            alias="Administrator",
            is_active=True,
            is_superuser=True,
        )
        db.add(admin)
        await db.commit()
        logger.info("默认管理员账号已创建")


async def init_data() -> None:
    await init_superuser()

    # Agent: 初始化 MCP 连接 + 扫描技能 + 连接缓存
    from agent.mcp_tool import init_mcp_servers
    await init_mcp_servers()

    from agent.skills.registry import registry
    registry.scan_skills()

    from utils.cache import cache_manager
    await cache_manager.connect()
