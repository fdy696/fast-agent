from fastapi import APIRouter

from core.dependency import DependAdmin, DependAuth

from .agent import agent_router
from .auditlog import auditlog_router
from .auth import auth_router
from .base import base_router
from .files import files_router
from .users import users_router

v1_router = APIRouter()

v1_router.include_router(auth_router, prefix="/auth", tags=["认证"])
v1_router.include_router(base_router, prefix="/base", tags=["基础"])
v1_router.include_router(users_router, prefix="/users", tags=["用户"])
v1_router.include_router(files_router, prefix="/files", tags=["文件"], dependencies=[DependAuth])
v1_router.include_router(auditlog_router, prefix="/auditlog", tags=["审计日志"], dependencies=[DependAdmin])
v1_router.include_router(agent_router, prefix="/agent", tags=["AI Agent"], dependencies=[DependAuth])

__all__ = ["v1_router"]
