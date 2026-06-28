from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AuditLog


class AuditLogRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_logs(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        username: str = "",
        module: str = "",
        method: str = "",
        summary: str = "",
        status: int | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> tuple[int, list[AuditLog]]:
        stmt = select(AuditLog)
        count_stmt = select(func.count()).select_from(AuditLog)
        conditions = []
        if username:
            conditions.append(AuditLog.username.ilike(f"%{username}%"))
        if module:
            conditions.append(AuditLog.module.ilike(f"%{module}%"))
        if method:
            conditions.append(AuditLog.method == method)
        if summary:
            conditions.append(AuditLog.summary.ilike(f"%{summary}%"))
        if status is not None:
            conditions.append(AuditLog.status == status)
        if start_time is not None:
            conditions.append(AuditLog.created_at >= start_time)
        if end_time is not None:
            conditions.append(AuditLog.created_at <= end_time)
        for condition in conditions:
            stmt = stmt.where(condition)
            count_stmt = count_stmt.where(condition)
        total_result = await self.db.execute(count_stmt)
        total = int(total_result.scalar_one())
        result = await self.db.execute(
            stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        )
        return total, list(result.scalars().all())
