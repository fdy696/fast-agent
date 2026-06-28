from datetime import datetime

from fastapi import APIRouter, Query

from core.dependency import AdminUser, DbSession
from services.audit_log_service import AuditLogService

router = APIRouter()


@router.get("/", summary="查看操作日志")
async def get_audit_log_list(
    db: DbSession,
    current_user: AdminUser,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页数量"),
    username: str = Query("", description="操作人名称"),
    module: str = Query("", description="功能模块"),
    method: str = Query("", description="请求方法"),
    summary: str = Query("", description="接口描述"),
    status: int | None = Query(None, description="状态码"),
    start_time: datetime | None = Query(None, description="开始时间"),
    end_time: datetime | None = Query(None, description="结束时间"),
):
    return await AuditLogService(db).list_logs(
        page=page,
        page_size=page_size,
        username=username,
        module=module,
        method=method,
        summary=summary,
        status=status,
        start_time=start_time,
        end_time=end_time,
    )
