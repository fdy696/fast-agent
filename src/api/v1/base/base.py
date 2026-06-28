import os
from datetime import UTC, datetime

from fastapi import APIRouter

from core.config import settings

router = APIRouter()


@router.get("/health", summary="健康检查")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now(UTC).isoformat(),
        "version": settings.VERSION,
        "environment": settings.APP_ENV,
        "service": "fast-agent",
    }


@router.get("/version", summary="版本信息")
async def get_version():
    return {
        "version": settings.VERSION,
        "app_title": settings.APP_TITLE,
        "project_name": settings.PROJECT_NAME,
        "build": os.getenv("BUILD_NUMBER", "dev"),
        "commit": os.getenv("GIT_COMMIT", "unknown"),
        "python_version": os.getenv("PYTHON_VERSION", "3.11+"),
    }
