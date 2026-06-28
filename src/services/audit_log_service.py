from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from repositories.audit_log import AuditLogRepository
from schemas.base import SuccessExtra


class AuditLogService:
    def __init__(self, db: AsyncSession):
        self.repo = AuditLogRepository(db)

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
    ) -> SuccessExtra:
        total, logs = await self.repo.list_logs(
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
        return SuccessExtra(data=[log.to_dict() for log in logs], total=total, page=page, page_size=page_size)
